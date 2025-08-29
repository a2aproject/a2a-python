"""Correlation manager for Kafka request-response pattern."""

import asyncio
import uuid
from typing import Any, Dict, Optional, Set

from a2a.types import Message, Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent


class StreamingFuture:
    """A future-like object for handling streaming responses."""
    
    def __init__(self):
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self._done = False
        self._exception: Optional[Exception] = None
    
    async def put(self, item: Any) -> None:
        """Add an item to the stream."""
        if not self._done:
            await self.queue.put(item)
    
    async def get(self) -> Any:
        """Get the next item from the stream."""
        if self._exception:
            raise self._exception
        return await self.queue.get()
    
    def set_exception(self, exception: Exception) -> None:
        """Set an exception for the stream."""
        self._exception = exception
        self._done = True
    
    def set_done(self) -> None:
        """Mark the stream as complete."""
        self._done = True
    
    def is_done(self) -> bool:
        """Check if the stream is complete."""
        return self._done
    
    def empty(self) -> bool:
        """Check if the queue is empty."""
        return self.queue.empty()


class CorrelationManager:
    """Manages correlation IDs and futures for Kafka request-response pattern."""

    def __init__(self) -> None:
        self._pending_requests: Dict[str, asyncio.Future[Any]] = {}
        self._streaming_requests: Dict[str, StreamingFuture] = {}
        self._lock = asyncio.Lock()

    def generate_correlation_id(self) -> str:
        """Generate a unique correlation ID."""
        return str(uuid.uuid4())

    async def register(
        self, correlation_id: str
    ) -> asyncio.Future[Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Register a new request with correlation ID and return a future for the response."""
        async with self._lock:
            future: asyncio.Future[Any] = asyncio.Future()
            self._pending_requests[correlation_id] = future
            return future

    async def register_streaming(self, correlation_id: str) -> StreamingFuture:
        """Register a new streaming request and return a streaming future."""
        async with self._lock:
            streaming_future = StreamingFuture()
            self._streaming_requests[correlation_id] = streaming_future
            return streaming_future

    async def complete(
        self,
        correlation_id: str,
        result: Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> bool:
        """Complete a pending request with the given result."""
        async with self._lock:
            # Check regular requests first
            future = self._pending_requests.pop(correlation_id, None)
            if future and not future.done():
                future.set_result(result)
                return True
            
            # Check streaming requests
            streaming_future = self._streaming_requests.get(correlation_id)
            if streaming_future and not streaming_future.is_done():
                await streaming_future.put(result)
                return True
                
            return False

    async def complete_streaming(self, correlation_id: str) -> bool:
        """Mark a streaming request as complete."""
        async with self._lock:
            streaming_future = self._streaming_requests.pop(correlation_id, None)
            if streaming_future:
                streaming_future.set_done()
                return True
            return False

    async def complete_with_exception(self, correlation_id: str, exception: Exception) -> bool:
        """Complete a pending request with an exception."""
        async with self._lock:
            # Check regular requests first
            future = self._pending_requests.pop(correlation_id, None)
            if future and not future.done():
                future.set_exception(exception)
                return True
                
            # Check streaming requests
            streaming_future = self._streaming_requests.pop(correlation_id, None)
            if streaming_future:
                streaming_future.set_exception(exception)
                return True
                
            return False

    async def cancel_all(self) -> None:
        """Cancel all pending requests."""
        async with self._lock:
            for future in self._pending_requests.values():
                if not future.done():
                    future.cancel()
            self._pending_requests.clear()
            
            for streaming_future in self._streaming_requests.values():
                streaming_future.set_exception(asyncio.CancelledError("Request cancelled"))
            self._streaming_requests.clear()

    def get_pending_count(self) -> int:
        """Get the number of pending requests."""
        return len(self._pending_requests) + len(self._streaming_requests)
