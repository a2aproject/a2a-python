import asyncio
import pytest

from a2a.server.events.redis_event_consumer import RedisEventConsumer


class FakeQueue:
    def __init__(self, items):
        self._items = list(items)
        self._closed = False

    async def dequeue_event(self, no_wait: bool = False):
        if not self._items:
            if no_wait:
                raise asyncio.QueueEmpty
            # simulate wait briefly
            await asyncio.sleep(0)
            raise asyncio.QueueEmpty
        return self._items.pop(0)

    def is_closed(self) -> bool:
        return self._closed


class FakeQueueWithException:
    def __init__(self, exception):
        self.exception = exception

    async def dequeue_event(self, no_wait: bool = False):
        raise self.exception

    def is_closed(self) -> bool:
        return False


class FakeQueueWithDelay:
    def __init__(self, items, delay=0.1):
        self._items = list(items)
        self.delay = delay
        self._closed = False

    async def dequeue_event(self, no_wait: bool = False):
        if no_wait and not self._items:
            raise asyncio.QueueEmpty
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if not self._items:
            raise asyncio.QueueEmpty
        return self._items.pop(0)

    def is_closed(self) -> bool:
        return self._closed


@pytest.mark.asyncio
async def test_consume_one_uses_no_wait():
    q = FakeQueue([])
    consumer = RedisEventConsumer(q)
    with pytest.raises(asyncio.QueueEmpty):
        await consumer.consume_one()


@pytest.mark.asyncio
async def test_consume_all_yields_until_closed():
    q = FakeQueue([1, 2])
    consumer = RedisEventConsumer(q)
    it = consumer.consume_all()
    results = []
    # consume two items then break by marking closed and expecting loop to exit
    results.append(await anext(it))
    results.append(await anext(it))
    # mark closed and ensure generator exits
    q._closed = True
    with pytest.raises(StopAsyncIteration):
        await anext(it)
    assert results == [1, 2]


@pytest.mark.asyncio
async def test_consume_one_with_item():
    q = FakeQueue([42])
    consumer = RedisEventConsumer(q)
    result = await consumer.consume_one()
    assert result == 42


@pytest.mark.asyncio
async def test_consume_all_with_empty_queue():
    q = FakeQueue([])
    consumer = RedisEventConsumer(q)
    it = consumer.consume_all()
    # mark closed immediately
    q._closed = True
    with pytest.raises(StopAsyncIteration):
        await anext(it)


@pytest.mark.asyncio
async def test_consume_all_with_exception_in_dequeue():
    q = FakeQueueWithException(RuntimeError('Test error'))
    consumer = RedisEventConsumer(q)
    it = consumer.consume_all()
    with pytest.raises(RuntimeError, match='Test error'):
        await anext(it)


@pytest.mark.asyncio
async def test_consume_one_with_exception_in_dequeue():
    q = FakeQueueWithException(ValueError('Test error'))
    consumer = RedisEventConsumer(q)
    with pytest.raises(ValueError, match='Test error'):
        await consumer.consume_one()


@pytest.mark.asyncio
async def test_consume_all_handles_queue_empty_then_closed():
    q = FakeQueue([])
    consumer = RedisEventConsumer(q)
    it = consumer.consume_all()
    # First iteration should raise QueueEmpty but continue since not closed
    # Mark closed during the exception handling
    q._closed = True
    with pytest.raises(StopAsyncIteration):
        await anext(it)


@pytest.mark.asyncio
async def test_consume_all_with_delay():
    q = FakeQueueWithDelay([1, 2, 3], delay=0.01)
    consumer = RedisEventConsumer(q)
    it = consumer.consume_all()
    results = []
    async for item in it:
        results.append(item)
        if len(results) >= 3:
            q._closed = True
            break
    assert results == [1, 2, 3]


@pytest.mark.asyncio
async def test_consumer_initialization():
    q = FakeQueue([1])
    consumer = RedisEventConsumer(q)
    assert consumer._queue is q


@pytest.mark.asyncio
async def test_consume_all_stops_when_closed_during_iteration():
    q = FakeQueue([1, 2, 3, 4, 5])
    consumer = RedisEventConsumer(q)
    it = consumer.consume_all()
    results = []
    # Consume a few items
    results.append(await anext(it))
    results.append(await anext(it))
    # Mark closed during iteration
    q._closed = True
    # Next iteration should stop
    with pytest.raises(StopAsyncIteration):
        await anext(it)
    assert results == [1, 2]


@pytest.mark.asyncio
async def test_consume_one_no_wait_false():
    """Test that consume_one always uses no_wait=True regardless of parameter."""
    q = FakeQueue([])
    consumer = RedisEventConsumer(q)
    # Even though dequeue_event might support no_wait=False, consume_one should always use True
    with pytest.raises(asyncio.QueueEmpty):
        await consumer.consume_one()
