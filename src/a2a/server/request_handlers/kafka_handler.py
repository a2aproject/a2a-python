"""Kafka request handler for A2A server (Kafka-agnostic)."""

import logging
from typing import Any, Dict, List, Optional, Protocol

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
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
from a2a.utils.errors import ServerError

logger = logging.getLogger(__name__)


class KafkaMessage:
    """Represents a Kafka message with headers and value."""
    
    def __init__(self, headers: List[tuple[str, bytes]], value: Dict[str, Any]):
        self.headers = headers
        self.value = value
        
    def get_header(self, key: str) -> Optional[str]:
        """Get header value by key."""
        for header_key, header_value in self.headers:
            if header_key == key:
                return header_value.decode('utf-8')
        return None


class ResponseSender(Protocol):
    """Protocol for sending responses back to clients."""

    async def send_response(
        self,
        reply_topic: str,
        correlation_id: str,
        result: Any,
        response_type: str,
    ) -> None: ...

    async def send_error_response(
        self,
        reply_topic: str,
        correlation_id: str,
        error_message: str,
    ) -> None: ...

    async def send_stream_complete(
        self,
        reply_topic: str,
        correlation_id: str,
    ) -> None: ...


class KafkaHandler:
    """Protocol adapter that parses requests and delegates to business logic.

    Note: This class is intentionally Kafka-agnostic. It does not manage producers
    or perform network I/O. All message sending is delegated to `response_sender`.
    """

    def __init__(
        self,
        request_handler: RequestHandler,
        response_sender: ResponseSender,
    ) -> None:
        """Initialize handler.

        Args:
            request_handler: Business logic handler.
            response_sender: Callback provider to send responses.
        """
        self.request_handler = request_handler
        self.response_sender = response_sender

    async def handle_request(self, message: KafkaMessage) -> None:
        """Handle incoming Kafka request message.
        
        This is the core callback function called by the consumer loop.
        It extracts metadata, processes the request, and uses `response_sender`
        to send the response.
        """
        try:
            # Extract metadata from headers
            reply_topic = message.get_header('reply_topic')
            correlation_id = message.get_header('correlation_id')
            agent_id = message.get_header('agent_id')
            trace_id = message.get_header('trace_id')

            if not reply_topic or not correlation_id:
                logger.error("Missing required headers: reply_topic or correlation_id")
                return

            # Parse request data
            request_data = message.value
            method = request_data.get('method')
            params = request_data.get('params', {})
            streaming = request_data.get('streaming', False)
            agent_card_data = request_data.get('agent_card')

            if not method:
                logger.error("Missing method in request")
                await self.response_sender.send_error_response(
                    reply_topic, correlation_id, "Missing method in request"
                )
                return

            # Create server call context
            context = ServerCallContext(
                agent_id=agent_id,
                trace_id=trace_id,
            )

            # Parse agent card if provided
            agent_card = None
            if agent_card_data:
                try:
                    agent_card = AgentCard.model_validate(agent_card_data)
                except Exception as e:
                    logger.error(f"Invalid agent card: {e}")

            # Route request to appropriate handler method
            try:
                if streaming:
                    await self._handle_streaming_request(
                        method, params, reply_topic, correlation_id, context
                    )
                else:
                    await self._handle_single_request(
                        method, params, reply_topic, correlation_id, context
                    )
            except Exception as e:
                logger.error(f"Error handling request {method}: {e}")
                await self.response_sender.send_error_response(
                    reply_topic, correlation_id, f"Request processing error: {e}"
                )

        except Exception as e:
            logger.error(f"Error in handle_request: {e}")

    async def _handle_single_request(
        self,
        method: str,
        params: Dict[str, Any],
        reply_topic: str,
        correlation_id: str,
        context: ServerCallContext,
    ) -> None:
        """Handle a single (non-streaming) request."""
        result = None
        response_type = "message"

        try:
            if method == "message_send":
                request = MessageSendParams.model_validate(params)
                result = await self.request_handler.on_message_send(request, context)
                response_type = "task" if isinstance(result, Task) else "message"

            elif method == "task_get":
                request = TaskQueryParams.model_validate(params)
                result = await self.request_handler.on_get_task(request, context)
                response_type = "task"

            elif method == "task_cancel":
                request = TaskIdParams.model_validate(params)
                result = await self.request_handler.on_cancel_task(request, context)
                response_type = "task"

            elif method == "task_push_notification_config_get":
                request = GetTaskPushNotificationConfigParams.model_validate(params)
                result = await self.request_handler.on_get_task_push_notification_config(request, context)
                response_type = "task_push_notification_config"

            elif method == "task_push_notification_config_list":
                request = ListTaskPushNotificationConfigParams.model_validate(params)
                result = await self.request_handler.on_list_task_push_notification_config(request, context)
                response_type = "task_push_notification_config_list"

            elif method == "task_push_notification_config_set":
                request = TaskPushNotificationConfig.model_validate(params)
                result = await self.request_handler.on_set_task_push_notification_config(request, context)
                response_type = "task_push_notification_config"

            elif method == "task_push_notification_config_delete":
                request = DeleteTaskPushNotificationConfigParams.model_validate(params)
                await self.request_handler.on_delete_task_push_notification_config(request, context)
                result = {"success": True}
                response_type = "success"

            else:
                raise ServerError(f"Unknown method: {method}")

            # Send response
            await self.response_sender.send_response(reply_topic, correlation_id, result, response_type)

        except Exception as e:
            logger.error(f"Error in _handle_single_request for {method}: {e}")
            await self.response_sender.send_error_response(reply_topic, correlation_id, str(e))

    async def _handle_streaming_request(
        self,
        method: str,
        params: Dict[str, Any],
        reply_topic: str,
        correlation_id: str,
        context: ServerCallContext,
    ) -> None:
        """Handle a streaming request."""
        try:
            if method == "message_send":
                request = MessageSendParams.model_validate(params)
                
                # Handle streaming response
                async for event in self.request_handler.on_message_send_stream(request, context):
                    if isinstance(event, TaskStatusUpdateEvent):
                        response_type = "task_status_update"
                    elif isinstance(event, TaskArtifactUpdateEvent):
                        response_type = "task_artifact_update"
                    elif isinstance(event, Task):
                        response_type = "task"
                    else:
                        response_type = "message"
                    
                    await self.response_sender.send_response(reply_topic, correlation_id, event, response_type)
                
                # Send stream completion signal
                await self.response_sender.send_stream_complete(reply_topic, correlation_id)

            elif method == "task_resubscribe":
                request = TaskIdParams.model_validate(params)
                
                # Handle streaming resubscription
                async for event in self.request_handler.on_resubscribe_to_task(request, context):
                    if isinstance(event, TaskStatusUpdateEvent):
                        response_type = "task_status_update"
                    elif isinstance(event, TaskArtifactUpdateEvent):
                        response_type = "task_artifact_update"
                    elif isinstance(event, Task):
                        response_type = "task"
                    else:
                        response_type = "message"
                    
                    await self.response_sender.send_response(reply_topic, correlation_id, event, response_type)
                
                # Send stream completion signal
                await self.response_sender.send_stream_complete(reply_topic, correlation_id)
                
            else:
                raise ServerError(f"Streaming not supported for method: {method}")

        except Exception as e:
            logger.error(f"Error in _handle_streaming_request for {method}: {e}")
            await self.response_sender.send_error_response(reply_topic, correlation_id, str(e))

