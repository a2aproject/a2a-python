"""Kafka transport implementation for A2A client."""

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator
from typing import Any, Dict, List, Optional
from uuid import uuid4

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError

from a2a.client.middleware import ClientCallContext
from a2a.client.transports.base import ClientTransport
from a2a.client.transports.kafka_correlation import CorrelationManager
from a2a.client.errors import A2AClientError
from a2a.types import (
    AgentCard,
    DeleteTaskPushNotificationConfigParams,
    GetTaskPushNotificationConfigParams,
    ListTaskPushNotificationConfigParams,
    Message,
    MessageSendParams,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskStatusUpdateEvent,
)

logger = logging.getLogger(__name__)


class KafkaClientTransport(ClientTransport):
    """Kafka-based client transport for A2A protocol."""

    def __init__(
        self,
        agent_card: AgentCard,
        bootstrap_servers: str | List[str] = "localhost:9092",
        request_topic: str = "a2a-requests",
        reply_topic_prefix: str = "a2a-reply",
        reply_topic: Optional[str] = None,
        consumer_group_id: Optional[str] = None,
        **kafka_config: Any,
    ) -> None:
        """Initialize Kafka client transport.
        
        Args:
            agent_card: The agent card for this client.
            bootstrap_servers: Kafka bootstrap servers.
            request_topic: Topic where requests are sent.
            reply_topic_prefix: Prefix for reply topics.
            reply_topic: Explicit reply topic to use. If not provided, it will be generated on start().
            consumer_group_id: Consumer group ID for the reply consumer.
            **kafka_config: Additional Kafka configuration.
        """
        self.agent_card = agent_card
        self.bootstrap_servers = bootstrap_servers
        self.request_topic = request_topic
        self.reply_topic_prefix = reply_topic_prefix
        # Defer reply_topic generation until start() unless explicitly provided
        self.reply_topic: Optional[str] = reply_topic
        # Defer consumer_group_id defaulting until start()
        self.consumer_group_id = consumer_group_id
        # Per-instance unique ID to ensure unique reply topics even with same agent name
        self._instance_id = uuid4().hex[:8]
        self.kafka_config = kafka_config
        
        self.producer: Optional[AIOKafkaProducer] = None
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.correlation_manager = CorrelationManager()
        self._consumer_task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._auto_start = False

    def _sanitize_topic_name(self, name: str) -> str:
        """Sanitize a name to be valid for Kafka topic names.
        
        Kafka topic names must:
        - Contain only alphanumeric characters, periods, underscores, and hyphens
        - Not be empty
        - Not exceed 249 characters
        
        Args:
            name: The original name to sanitize.
            
        Returns:
            A sanitized name suitable for use in Kafka topic names.
        """
        # Replace invalid characters with underscores
        sanitized = re.sub(r'[^a-zA-Z0-9._-]', '_', name)
        
        # Ensure it's not empty
        if not sanitized:
            sanitized = "unknown_agent"
        
        # Truncate if too long (leave room for prefixes)
        if len(sanitized) > 200:
            sanitized = sanitized[:200]
            
        return sanitized

    async def start(self) -> None:
        """Start the Kafka client transport.
        
        This method is called internally by the client factory and should not be
        exposed to end users. It initializes the Kafka producer and consumer.
        """
        if self._running:
            return

        try:
            # Ensure reply_topic and consumer_group_id are prepared
            if not self.reply_topic:
                sanitized_agent_name = self._sanitize_topic_name(self.agent_card.name)
                self.reply_topic = f"{self.reply_topic_prefix}-{sanitized_agent_name}-{self._instance_id}"
            if not self.consumer_group_id:
                sanitized_agent_name = self._sanitize_topic_name(self.agent_card.name)
                self.consumer_group_id = f"a2a-client-{sanitized_agent_name}-{self._instance_id}"

            # Initialize producer
            self.producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                **self.kafka_config
            )
            await self.producer.start()

            # Initialize consumer
            self.consumer = AIOKafkaConsumer(
                self.reply_topic,
                bootstrap_servers=self.bootstrap_servers,
                group_id=self.consumer_group_id,
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                auto_offset_reset='latest',
                **self.kafka_config
            )
            await self.consumer.start()

            # Start consumer task
            self._consumer_task = asyncio.create_task(self._consume_responses())
            self._running = True
            
            logger.info(f"Kafka client transport started for agent {self.agent_card.name}")

        except Exception as e:
            await self.stop()
            raise A2AClientError(f"Failed to start Kafka client transport: {e}") from e

    async def stop(self) -> None:
        """Stop the Kafka client transport.
        
        This method is called internally by the close() method and should not be
        exposed to end users. It cleans up the Kafka producer and consumer.
        """
        if not self._running:
            return

        self._running = False

        # Cancel consumer task
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

        # Cancel all pending requests
        await self.correlation_manager.cancel_all()

        # Stop producer and consumer
        if self.producer:
            await self.producer.stop()
        if self.consumer:
            await self.consumer.stop()

        logger.info(f"Kafka client transport stopped for agent {self.agent_card.name}")

    async def _ensure_started(self) -> None:
        """Ensure the transport is started, auto-starting if needed."""
        if not self._running and self._auto_start:
            await self.start()

    async def _consume_responses(self) -> None:
        """Consume responses from the reply topic."""
        if not self.consumer:
            return

        try:
            async for message in self.consumer:
                try:
                    # Extract correlation ID from headers
                    correlation_id = None
                    if message.headers:
                        for key, value in message.headers:
                            if key == 'correlation_id':
                                correlation_id = value.decode('utf-8')
                                break

                    if not correlation_id:
                        logger.warning("Received message without correlation_id")
                        continue

                    # Parse response
                    response_data = message.value
                    response_type = response_data.get('type', 'message')
                    
                    # Handle stream completion signal
                    if response_type == 'stream_complete':
                        await self.correlation_manager.complete_streaming(correlation_id)
                        continue
                    
                    # Handle error responses
                    if response_type == 'error':
                        error_message = response_data.get('data', {}).get('error', 'Unknown error')
                        await self.correlation_manager.complete_with_exception(
                            correlation_id, 
                            A2AClientError(error_message)
                        )
                        continue
                    
                    # Parse and complete normal responses
                    response = self._parse_response(response_data)
                    await self.correlation_manager.complete(correlation_id, response)

                except Exception as e:
                    logger.error(f"Error processing response message: {e}")

        except asyncio.CancelledError:
            logger.debug("Response consumer cancelled")
        except Exception as e:
            logger.error(f"Error in response consumer: {e}")

    def _parse_response(self, data: Dict[str, Any]) -> Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent:
        """Parse response data into appropriate type."""
        response_type = data.get('type', 'message')
        
        if response_type == 'task':
            return Task.model_validate(data['data'])
        elif response_type == 'task_status_update':
            return TaskStatusUpdateEvent.model_validate(data['data'])
        elif response_type == 'task_artifact_update':
            return TaskArtifactUpdateEvent.model_validate(data['data'])
        else:
            return Message.model_validate(data['data'])

    async def _send_request(
        self,
        method: str,
        params: Any,
        context: ClientCallContext | None = None,
        streaming: bool = False,
    ) -> str:
        """Send a request and return the correlation ID."""
        await self._ensure_started()
        
        if not self.producer or not self._running:
            raise A2AClientError("Kafka client transport not started")

        correlation_id = self.correlation_manager.generate_correlation_id()
        
        # Prepare request message
        request_data = {
            'method': method,
            'params': params.model_dump() if hasattr(params, 'model_dump') else params,
            'streaming': streaming,
            'agent_card': self.agent_card.model_dump(),
        }

        # Prepare headers
        headers = [
            ('correlation_id', correlation_id.encode('utf-8')),
            ('reply_topic', (self.reply_topic or '').encode('utf-8')),
            ('agent_id', self.agent_card.name.encode('utf-8')),
        ]

        if context:
            # Add context headers if needed
            if context.trace_id:
                headers.append(('trace_id', context.trace_id.encode('utf-8')))

        try:
            await self.producer.send_and_wait(
                self.request_topic,
                value=request_data,
                headers=headers
            )
            return correlation_id
        except KafkaError as e:
            raise A2AClientError(f"Failed to send Kafka message: {e}") from e

    async def send_message(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task | Message:
        """Send a non-streaming message request to the agent."""
        await self._ensure_started()
        correlation_id = await self._send_request('message_send', request, context, streaming=False)
        
        # Register and wait for response
        future = await self.correlation_manager.register(correlation_id)
        
        try:
            # Wait for response with timeout
            timeout = 30.0  # Default timeout
            if context and context.timeout:
                timeout = context.timeout
                
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            await self.correlation_manager.complete_with_exception(
                correlation_id, 
                A2AClientError(f"Request timed out after {timeout} seconds")
            )
            raise A2AClientError(f"Request timed out after {timeout} seconds")

    async def send_message_streaming(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Send a streaming message request to the agent and yield responses as they arrive."""
        await self._ensure_started()
        correlation_id = await self._send_request('message_send', request, context, streaming=True)
        
        # Register streaming request
        streaming_future = await self.correlation_manager.register_streaming(correlation_id)
        
        try:
            timeout = 30.0
            if context and context.timeout:
                timeout = context.timeout
            
            # Yield responses as they arrive
            while not streaming_future.is_done():
                try:
                    # Wait for next response with timeout
                    result = await asyncio.wait_for(streaming_future.get(), timeout=5.0)
                    yield result
                except asyncio.TimeoutError:
                    # Check if stream is done or if we've exceeded total timeout
                    if streaming_future.is_done():
                        break
                    # Continue waiting for more responses
                    continue
                    
        except Exception as e:
            await self.correlation_manager.complete_with_exception(
                correlation_id,
                A2AClientError(f"Streaming request failed: {e}")
            )
            raise A2AClientError(f"Streaming request failed: {e}") from e

    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Get a task by ID."""
        correlation_id = await self._send_request('task_get', request, context)
        future = await self.correlation_manager.register(correlation_id)
        
        try:
            timeout = 30.0
            if context and context.timeout:
                timeout = context.timeout
                
            result = await asyncio.wait_for(future, timeout=timeout)
            if not isinstance(result, Task):
                raise A2AClientError(f"Expected Task, got {type(result)}")
            return result
        except asyncio.TimeoutError:
            await self.correlation_manager.complete_with_exception(
                correlation_id,
                A2AClientError(f"Get task request timed out after {timeout} seconds")
            )
            raise A2AClientError(f"Get task request timed out after {timeout} seconds")

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Cancel a task."""
        correlation_id = await self._send_request('task_cancel', request, context)
        future = await self.correlation_manager.register(correlation_id)
        
        try:
            timeout = 30.0
            if context and context.timeout:
                timeout = context.timeout
                
            result = await asyncio.wait_for(future, timeout=timeout)
            if not isinstance(result, Task):
                raise A2AClientError(f"Expected Task, got {type(result)}")
            return result
        except asyncio.TimeoutError:
            await self.correlation_manager.complete_with_exception(
                correlation_id,
                A2AClientError(f"Cancel task request timed out after {timeout} seconds")
            )
            raise A2AClientError(f"Cancel task request timed out after {timeout} seconds")

    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig | None:
        """Get task push notification configuration."""
        correlation_id = await self._send_request('task_push_notification_config_get', request, context)
        future = await self.correlation_manager.register(correlation_id)
        
        try:
            timeout = 30.0
            if context and context.timeout:
                timeout = context.timeout
                
            result = await asyncio.wait_for(future, timeout=timeout)
            if result is None or isinstance(result, TaskPushNotificationConfig):
                return result
            raise A2AClientError(f"Expected TaskPushNotificationConfig or None, got {type(result)}")
        except asyncio.TimeoutError:
            await self.correlation_manager.complete_with_exception(
                correlation_id,
                A2AClientError(f"Get push notification config request timed out after {timeout} seconds")
            )
            raise A2AClientError(f"Get push notification config request timed out after {timeout} seconds")

    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
    ) -> List[TaskPushNotificationConfig]:
        """List task push notification configurations."""
        correlation_id = await self._send_request('task_push_notification_config_list', request, context)
        future = await self.correlation_manager.register(correlation_id)
        
        try:
            timeout = 30.0
            if context and context.timeout:
                timeout = context.timeout
                
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, list):
                return result
            raise A2AClientError(f"Expected list, got {type(result)}")
        except asyncio.TimeoutError:
            await self.correlation_manager.complete_with_exception(
                correlation_id,
                A2AClientError(f"List push notification configs request timed out after {timeout} seconds")
            )
            raise A2AClientError(f"List push notification configs request timed out after {timeout} seconds")

    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Delete task push notification configuration."""
        correlation_id = await self._send_request('task_push_notification_config_delete', request, context)
        future = await self.correlation_manager.register(correlation_id)
        
        try:
            timeout = 30.0
            if context and context.timeout:
                timeout = context.timeout
                
            await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            await self.correlation_manager.complete_with_exception(
                correlation_id,
                A2AClientError(f"Delete push notification config request timed out after {timeout} seconds")
            )
            raise A2AClientError(f"Delete push notification config request timed out after {timeout} seconds")

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Set task push notification configuration."""
        correlation_id = await self._send_request('task_push_notification_config_set', request, context)
        future = await self.correlation_manager.register(correlation_id)
        
        try:
            timeout = 30.0
            if context and context.timeout:
                timeout = context.timeout
                
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, TaskPushNotificationConfig):
                return result
            raise A2AClientError(f"Expected TaskPushNotificationConfig, got {type(result)}")
        except asyncio.TimeoutError:
            await self.correlation_manager.complete_with_exception(
                correlation_id,
                A2AClientError(f"Set task callback request timed out after {timeout} seconds")
            )
            raise A2AClientError(f"Set task callback request timed out after {timeout} seconds")

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Get task push notification configuration."""
        result = await self.get_task_push_notification_config(request, context=context)
        if result is None:
            raise A2AClientError(f"No task callback configuration found for task {request.task_id}")
        return result

    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Reconnect to get task updates."""
        # For Kafka, we send a resubscribe request to get streaming updates
        correlation_id = await self._send_request('task_resubscribe', request, context, streaming=True)
        
        # Register streaming request
        streaming_future = await self.correlation_manager.register_streaming(correlation_id)
        
        try:
            timeout = 30.0
            if context and context.timeout:
                timeout = context.timeout
            
            # First, get the current task state
            task_request = TaskQueryParams(task_id=request.task_id)
            try:
                task = await self.get_task(task_request, context=context)
                yield task
            except Exception as e:
                logger.warning(f"Failed to get initial task state: {e}")
            
            # Then yield streaming updates as they arrive
            while not streaming_future.is_done():
                try:
                    # Wait for next response with timeout
                    result = await asyncio.wait_for(streaming_future.get(), timeout=5.0)
                    yield result
                except asyncio.TimeoutError:
                    # Check if stream is done or if we've exceeded total timeout
                    if streaming_future.is_done():
                        break
                    # Continue waiting for more responses
                    continue
                    
        except Exception as e:
            await self.correlation_manager.complete_with_exception(
                correlation_id,
                A2AClientError(f"Resubscribe request failed: {e}")
            )
            raise A2AClientError(f"Resubscribe request failed: {e}") from e

    async def get_card(
        self,
        *,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Retrieve the agent card."""
        # For Kafka transport, we return the local agent card
        # In a real implementation, this might query the server
        return self.agent_card

    async def close(self) -> None:
        """Close the transport.
        
        This method stops the Kafka client transport and cleans up all resources.
        It's the public interface for shutting down the transport.
        """
        await self.stop()

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()

    def set_reply_topic(self, topic: str) -> None:
        """Set an explicit reply topic before starting the transport.
        
        Must be called before start(). If called after the transport has
        started, it will have no effect on the already running consumer.
        """
        if self._running:
            logger.warning("set_reply_topic called after start(); ignoring.")
            return
        self.reply_topic = topic

    @classmethod
    def create(
        cls,
        agent_card: AgentCard,
        url: str,
        config: Any,
        interceptors: List[Any],
    ) -> "KafkaClientTransport":
        """Create a Kafka client transport instance.
        
        This method matches the signature expected by ClientFactory.
        For Kafka, the URL should be in the format: kafka://bootstrap_servers/request_topic
        
        Args:
            agent_card: The agent card for this client.
            url: Kafka URL (e.g., kafka://localhost:9092/a2a-requests)
            config: Client configuration (unused for Kafka)
            interceptors: Client interceptors (unused for Kafka)
            
        Returns:
            Configured KafkaClientTransport instance.
        """
        # Parse Kafka URL
        if not url.startswith('kafka://'):
            raise ValueError("Kafka URL must start with 'kafka://'")
        
        # Remove kafka:// prefix
        kafka_part = url[8:]
        
        # Split into bootstrap_servers and topic
        if '/' in kafka_part:
            bootstrap_servers, request_topic = kafka_part.split('/', 1)
        else:
            bootstrap_servers = kafka_part
            request_topic = "a2a-requests"  # default topic
        
        return cls(
            agent_card=agent_card,
            bootstrap_servers=bootstrap_servers,
            request_topic=request_topic,
        )
