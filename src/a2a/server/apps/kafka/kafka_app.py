"""Kafka server application for A2A protocol."""

import asyncio
import json
import logging
import signal
from typing import Any, Dict, List, Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError

from a2a.server.request_handlers.kafka_handler import KafkaHandler, KafkaMessage
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.utils.errors import ServerError
from a2a.types import (
    Message,
    Task,
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
)

logger = logging.getLogger(__name__)


class KafkaServerApp:
    """Kafka server application that manages the service lifecycle."""

    def __init__(
        self,
        request_handler: RequestHandler,
        bootstrap_servers: str | List[str] = "localhost:9092",
        request_topic: str = "a2a-requests",
        consumer_group_id: str = "a2a-server",
        **kafka_config: Any,
    ) -> None:
        """Initialize Kafka server application.
        
        Args:
            request_handler: Business logic handler.
            bootstrap_servers: Kafka bootstrap servers.
            request_topic: Topic to consume requests from.
            consumer_group_id: Consumer group ID for the server.
            **kafka_config: Additional Kafka configuration.
        """
        self.request_handler = request_handler
        self.bootstrap_servers = bootstrap_servers
        self.request_topic = request_topic
        self.consumer_group_id = consumer_group_id
        self.kafka_config = kafka_config
        
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.producer: Optional[AIOKafkaProducer] = None
        self.handler: Optional[KafkaHandler] = None
        self._running = False
        self._consumer_task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        """Start the Kafka server application."""
        if self._running:
            return

        try:
            # Initialize protocol handler (Kafka-agnostic) and pass self as response sender
            self.handler = KafkaHandler(
                self.request_handler,
                response_sender=self,
            )

            # Initialize producer
            self.producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                **self.kafka_config,
            )
            await self.producer.start()

            # Initialize consumer
            self.consumer = AIOKafkaConsumer(
                self.request_topic,
                bootstrap_servers=self.bootstrap_servers,
                group_id=self.consumer_group_id,
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                auto_offset_reset='latest',
                enable_auto_commit=True,
                **self.kafka_config
            )
            await self.consumer.start()

            self._running = True
            logger.info(f"Kafka server started, consuming from topic: {self.request_topic}")

        except Exception as e:
            await self.stop()
            raise ServerError(f"Failed to start Kafka server: {e}") from e

    async def stop(self) -> None:
        """Stop the Kafka server application."""
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

        # Stop consumer and producer
        if self.consumer:
            await self.consumer.stop()
        if self.producer:
            await self.producer.stop()

        logger.info("Kafka server stopped")

    async def run(self) -> None:
        """Run the server and start consuming messages.
        
        This method will block until the server is stopped.
        """
        await self.start()
        
        try:
            self._consumer_task = asyncio.create_task(self._consume_requests())
            
            # Set up signal handlers for graceful shutdown (Unix only)
            import platform
            if platform.system() != 'Windows':
                loop = asyncio.get_event_loop()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
            
            # Wait for consumer task to complete
            await self._consumer_task
            
        except asyncio.CancelledError:
            logger.info("Server run cancelled")
        except Exception as e:
            logger.error(f"Error in server run: {e}")
            raise
        finally:
            await self.stop()

    async def _consume_requests(self) -> None:
        """Consume requests from the request topic."""
        if not self.consumer or not self.handler:
            return

        try:
            logger.info("Starting to consume requests...")
            async for message in self.consumer:
                try:
                    # Convert Kafka message to our KafkaMessage format
                    kafka_message = KafkaMessage(
                        headers=message.headers or [],
                        value=message.value
                    )
                    
                    # Handle the request
                    await self.handler.handle_request(kafka_message)
                    
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    # Continue processing other messages even if one fails

        except asyncio.CancelledError:
            logger.debug("Request consumer cancelled")
        except KafkaError as e:
            logger.error(f"Kafka error in consumer: {e}")
            if self._running:
                # Try to restart consumer after a delay
                await asyncio.sleep(5)
                if self._running:
                    logger.info("Attempting to restart consumer...")
                    try:
                        await self.consumer.stop()
                        await self.consumer.start()
                        # Recursively call to continue consuming
                        await self._consume_requests()
                    except Exception as restart_error:
                        logger.error(f"Failed to restart consumer: {restart_error}")
        except Exception as e:
            logger.error(f"Unexpected error in request consumer: {e}")

    # ResponseSender implementation
    async def send_response(
        self,
        reply_topic: str,
        correlation_id: str,
        result: Any,
        response_type: str,
    ) -> None:
        if not self.producer:
            logger.error("Producer not available")
            return
        try:
            response_data = {
                "type": response_type,
                "data": result.model_dump() if hasattr(result, 'model_dump') else result,
            }
            headers = [
                ("correlation_id", correlation_id.encode("utf-8")),
            ]
            await self.producer.send_and_wait(
                reply_topic,
                value=response_data,
                headers=headers,
            )
        except Exception as e:
            logger.error(f"Failed to send response: {e}")

    async def send_stream_complete(
        self,
        reply_topic: str,
        correlation_id: str,
    ) -> None:
        if not self.producer:
            logger.error("Producer not available")
            return
        try:
            response_data = {
                "type": "stream_complete",
                "data": {},
            }
            headers = [
                ("correlation_id", correlation_id.encode("utf-8")),
            ]
            await self.producer.send_and_wait(
                reply_topic,
                value=response_data,
                headers=headers,
            )
        except Exception as e:
            logger.error(f"Failed to send stream completion signal: {e}")

    async def send_error_response(
        self,
        reply_topic: str,
        correlation_id: str,
        error_message: str,
    ) -> None:
        if not self.producer:
            logger.error("Producer not available")
            return
        try:
            response_data = {
                "type": "error",
                "data": {"error": error_message},
            }
            headers = [
                ("correlation_id", correlation_id.encode("utf-8")),
            ]
            await self.producer.send_and_wait(
                reply_topic,
                value=response_data,
                headers=headers,
            )
        except Exception as e:
            logger.error(f"Failed to send error response: {e}")

    async def get_handler(self) -> KafkaHandler:
        """Get the Kafka handler instance.
        
        This can be used to send push notifications.
        """
        if not self.handler:
            raise ServerError("Kafka handler not initialized")
        return self.handler

    async def send_push_notification(
        self,
        reply_topic: str,
        notification: Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> None:
        """Send a push notification to a specific client topic."""
        if not self.producer:
            logger.error("Producer not available for push notification")
            return
        try:
            if isinstance(notification, Task):
                response_type = "task"
            elif isinstance(notification, TaskStatusUpdateEvent):
                response_type = "task_status_update"
            elif isinstance(notification, TaskArtifactUpdateEvent):
                response_type = "task_artifact_update"
            else:
                response_type = "message"

            response_data = {
                "type": f"push_{response_type}",
                "data": notification.model_dump() if hasattr(notification, 'model_dump') else notification,
            }
            headers = [
                ("notification_type", b"push"),
            ]
            await self.producer.send_and_wait(
                reply_topic,
                value=response_data,
                headers=headers,
            )
            logger.debug(f"Sent push notification to {reply_topic}")
        except Exception as e:
            logger.error(f"Failed to send push notification: {e}")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()


async def create_kafka_server(
    request_handler: RequestHandler,
    bootstrap_servers: str | List[str] = "localhost:9092",
    request_topic: str = "a2a-requests",
    consumer_group_id: str = "a2a-server",
    **kafka_config: Any,
) -> KafkaServerApp:
    """Create and return a Kafka server application.
    
    Args:
        request_handler: Business logic handler.
        bootstrap_servers: Kafka bootstrap servers.
        request_topic: Topic to consume requests from.
        consumer_group_id: Consumer group ID for the server.
        **kafka_config: Additional Kafka configuration.
        
    Returns:
        Configured KafkaServerApp instance.
    """
    return KafkaServerApp(
        request_handler=request_handler,
        bootstrap_servers=bootstrap_servers,
        request_topic=request_topic,
        consumer_group_id=consumer_group_id,
        **kafka_config
    )
