import logging

import pytest


@pytest.mark.asyncio
async def test_create_or_tap_creates_queue(monkeypatch):
    created = {}

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id
            self.redis_client = redis_client

        def tap(self):
            return FakeRedisEventQueue(self.task_id, self.redis_client)

        async def close(self):
            pass  # No-op for test

    # Monkeypatch import used in RedisQueueManager.create_or_tap by inserting
    # a proper module object with attribute `RedisEventQueue`.
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    monkeypatch.setitem(
        sys.modules, 'a2a.server.events.redis_event_queue', fake_mod
    )

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=None)
    q = await manager.create_or_tap('t1')
    assert hasattr(q, 'task_id')


@pytest.mark.asyncio
async def test_add_not_supported():
    """Test that add() is not supported in RedisQueueManager (distributed setup)."""
    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=None)

    class DummyQueue:
        def __init__(self, id):
            self.id = id

        async def close(self):
            return None

    q = DummyQueue('t2')

    # add() should raise NotImplementedError in distributed Redis setup
    with pytest.raises(
        NotImplementedError,
        match='add\\(\\) is not supported in distributed Redis setup',
    ):
        await manager.add('t2', q)


@pytest.mark.asyncio
async def test_create_or_tap_with_different_task_ids(monkeypatch):
    """Test create_or_tap with different task IDs creates separate queues."""
    created_queues = {}

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id
            self.redis_client = redis_client
            created_queues[task_id] = self

        def tap(self):
            return FakeRedisEventQueue(self.task_id, self.redis_client)

        async def close(self):
            pass

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    monkeypatch.setitem(
        sys.modules, 'a2a.server.events.redis_event_queue', fake_mod
    )

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=None)

    # Create queues for different tasks
    q1 = await manager.create_or_tap('task1')
    q2 = await manager.create_or_tap('task2')
    q3 = await manager.create_or_tap(
        'task1'
    )  # Same task creates new instance (no caching)

    assert q1.task_id == 'task1'
    assert q2.task_id == 'task2'
    assert q3.task_id == 'task1'
    assert (
        q1 is not q3
    )  # Different instances for same task (no caching in Redis)


@pytest.mark.asyncio
async def test_close_operation(monkeypatch):
    """Test close operation on Redis queue manager."""
    closed_queues = []

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id
            self.redis_client = redis_client

        def tap(self):
            return FakeRedisEventQueue(self.task_id, self.redis_client)

        async def close(self):
            closed_queues.append(self.task_id)

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    monkeypatch.setitem(
        sys.modules, 'a2a.server.events.redis_event_queue', fake_mod
    )

    # Also patch the RedisEventQueue reference in redis_queue_manager
    import a2a.server.events.redis_queue_manager as rqm

    rqm.RedisEventQueue = FakeRedisEventQueue

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=None)

    # Create and close a queue
    q1 = await manager.create_or_tap('task1')
    await manager.close('task1')

    assert 'task1' in closed_queues


@pytest.mark.asyncio
async def test_close_nonexistent_task(monkeypatch):
    """Test closing a nonexistent task."""

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id

        def tap(self):
            return FakeRedisEventQueue(self.task_id, None)

        async def close(self):
            pass  # No-op for test

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    monkeypatch.setitem(
        sys.modules, 'a2a.server.events.redis_event_queue', fake_mod
    )

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=None)

    # Should not raise error when closing nonexistent task
    await manager.close('nonexistent_task')


@pytest.mark.asyncio
async def test_get_operation(monkeypatch):
    """Test get operation on Redis queue manager."""

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id

        def tap(self):
            return FakeRedisEventQueue(self.task_id, None)

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    monkeypatch.setitem(
        sys.modules, 'a2a.server.events.redis_event_queue', fake_mod
    )

    # Also patch the RedisEventQueue reference in redis_queue_manager
    import a2a.server.events.redis_queue_manager as rqm

    rqm.RedisEventQueue = FakeRedisEventQueue

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=None)

    # Get operation should raise NotImplementedError in distributed Redis setup
    with pytest.raises(
        NotImplementedError,
        match='get\\(\\) is not supported in distributed Redis setup',
    ):
        await manager.get('nonexistent')

    # Get existing task should also raise NotImplementedError in distributed setup
    q1 = await manager.create_or_tap('task1')
    with pytest.raises(
        NotImplementedError,
        match='get\\(\\) is not supported in distributed Redis setup',
    ):
        await manager.get('task1')


@pytest.mark.asyncio
async def test_tap_operation(monkeypatch):
    """Test tap operation creates new queue instance with same redis_client."""

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id
            self.redis_client = redis_client

        def tap(self):
            # Return a new queue with the same redis_client (matching actual behavior)
            return FakeRedisEventQueue(self.task_id, self.redis_client)

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    monkeypatch.setitem(
        sys.modules, 'a2a.server.events.redis_event_queue', fake_mod
    )

    # Also patch the RedisEventQueue reference in redis_queue_manager
    import a2a.server.events.redis_queue_manager as rqm

    rqm.RedisEventQueue = FakeRedisEventQueue

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client='fake_redis')

    # Create tap
    tapped_queue = await manager.tap('task1')

    assert tapped_queue.task_id == 'task1'
    assert (
        tapped_queue.redis_client == 'fake_redis'
    )  # Tap should have the same redis_client


@pytest.mark.asyncio
async def test_create_or_tap_with_none_redis_client():
    """Test create_or_tap with None redis_client."""
    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=None)

    # Should work with None redis_client (passed to RedisEventQueue)
    q = await manager.create_or_tap('task1')
    assert q.task_id == 'task1'


@pytest.mark.asyncio
async def test_multiple_taps_same_task():
    """Test multiple taps on same task create independent instances."""

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id
            self.redis_client = redis_client

        def tap(self):
            return FakeRedisEventQueue(self.task_id, self.redis_client)

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    sys.modules['a2a.server.events.redis_event_queue'] = fake_mod

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client='fake_redis')

    # Create multiple taps
    tap1 = await manager.tap('task1')
    tap2 = await manager.tap('task1')
    tap3 = await manager.tap('task1')

    # All should be different instances
    assert tap1 is not tap2
    assert tap2 is not tap3
    assert tap1 is not tap3

    # All should have same task_id and redis_client
    assert tap1.task_id == 'task1'
    assert tap2.task_id == 'task1'
    assert tap3.task_id == 'task1'
    assert tap1.redis_client == 'fake_redis'
    assert tap2.redis_client == 'fake_redis'
    assert tap3.redis_client == 'fake_redis'


@pytest.mark.asyncio
async def test_close_multiple_tasks():
    """Test closing multiple tasks."""
    closed_tasks = []

    # Use the FakeRedis from the test file that has xrevrange
    import os
    import sys

    sys.path.append(os.path.dirname(__file__))
    from test_redis_event_queue import FakeRedis

    redis = FakeRedis()

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id
            self.redis_client = redis_client
            self._stream_prefix = stream_prefix or 'a2a:task'
            self._stream_key = f'{self._stream_prefix}:{task_id}'

        def tap(self):
            return FakeRedisEventQueue(
                self.task_id, self.redis_client, self._stream_prefix
            )

        async def close(self):
            closed_tasks.append(self.task_id)
            # Actually write the CLOSE entry to the stream
            await self.redis_client.xadd(self._stream_key, {'type': 'CLOSE'})

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    sys.modules['a2a.server.events.redis_event_queue'] = fake_mod

    # Also patch the RedisEventQueue reference in redis_queue_manager
    import a2a.server.events.redis_queue_manager as rqm

    rqm.RedisEventQueue = FakeRedisEventQueue

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=redis)

    # Create multiple queues
    q1 = await manager.create_or_tap('task1')
    q2 = await manager.create_or_tap('task2')
    q3 = await manager.create_or_tap('task3')

    # Close all
    await manager.close('task1')
    await manager.close('task2')
    await manager.close('task3')

    assert set(closed_tasks) == {'task1', 'task2', 'task3'}


@pytest.mark.asyncio
async def test_close_already_closed_task():
    """Test closing a task that's already closed."""
    # Use the FakeRedis from the test file that has xrevrange
    import os
    import sys

    sys.path.append(os.path.dirname(__file__))
    from test_redis_event_queue import FakeRedis

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    redis = FakeRedis()
    manager = RedisQueueManager(redis_client=redis)

    # Create and close a queue
    await manager.create_or_tap('task1')
    await manager.close('task1')

    # Check that a CLOSE entry was created
    stream_key = 'a2a:task:task1'
    stream_entries = redis.streams.get(stream_key, [])
    close_entries = [
        entry for entry in stream_entries if entry[1].get('type') == 'CLOSE'
    ]
    assert len(close_entries) == 1

    # Close again - should not create another CLOSE entry
    await manager.close('task1')

    # Should still only have one CLOSE entry
    stream_entries = redis.streams.get(stream_key, [])
    close_entries = [
        entry for entry in stream_entries if entry[1].get('type') == 'CLOSE'
    ]
    assert len(close_entries) == 1


@pytest.mark.asyncio
async def test_tap_nonexistent_task():
    """Test tapping a task that doesn't exist."""

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id
            self.redis_client = redis_client

        def tap(self):
            return FakeRedisEventQueue(self.task_id, self.redis_client)

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    sys.modules['a2a.server.events.redis_event_queue'] = fake_mod

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client='fake_redis')

    # Tap nonexistent task should work (creates new queue)
    tapped_queue = await manager.tap('nonexistent')

    assert tapped_queue.task_id == 'nonexistent'
    assert tapped_queue.redis_client == 'fake_redis'


@pytest.mark.asyncio
async def test_manager_initialization():
    """Test RedisQueueManager initialization."""
    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client='test_client')
    assert manager._redis == 'test_client'


@pytest.mark.asyncio
async def test_create_or_tap_with_custom_stream_prefix():
    """Test create_or_tap with custom stream prefix."""

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id
            self.stream_prefix = stream_prefix

        def tap(self):
            return FakeRedisEventQueue(self.task_id, None, self.stream_prefix)

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    sys.modules['a2a.server.events.redis_event_queue'] = fake_mod

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=None)

    # Create queue (RedisEventQueue would use default prefix)
    q = await manager.create_or_tap('task1')
    assert q.task_id == 'task1'
    # Note: In real implementation, stream_prefix would be passed through


@pytest.mark.asyncio
async def test_error_handling_in_close():
    """Test error handling when closing queues."""

    class FailingRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id

        def tap(self):
            return FailingRedisEventQueue(self.task_id, None)

        async def close(self):
            raise Exception('Close failed')

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FailingRedisEventQueue
    sys.modules['a2a.server.events.redis_event_queue'] = fake_mod

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=None)

    # Create queue
    await manager.create_or_tap('task1')

    # Close should handle exceptions gracefully (not raise)
    await manager.close('task1')


@pytest.mark.asyncio
async def test_create_or_tap_logging(monkeypatch, caplog):
    """Test that create_or_tap logs appropriate information."""
    import logging

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id
            self.redis_client = redis_client

        def tap(self):
            return FakeRedisEventQueue(self.task_id, self.redis_client)

        async def close(self):
            pass

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    monkeypatch.setitem(
        sys.modules, 'a2a.server.events.redis_event_queue', fake_mod
    )

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client='fake_redis')

    # Set logging level to INFO to capture the logging statements
    with caplog.at_level(logging.INFO):
        q = await manager.create_or_tap('test_task')

    # Check that logging statements were executed
    assert any(
        'create_or_tap called with task_id: test_task' in record.message
        for record in caplog.records
    ), 'Should log task_id'
    assert any(
        'Creating RedisEventQueue instance' in record.message
        for record in caplog.records
    ), 'Should log queue creation'


@pytest.mark.asyncio
async def test_close_logging_on_redis_check_failure(caplog):
    """Test that close method logs when Redis check fails."""
    import logging
    import os

    # Use the FakeRedis from the test file that has xrevrange
    import sys

    sys.path.append(os.path.dirname(__file__))
    from test_redis_event_queue import FakeRedis

    redis = FakeRedis()

    # Mock xrevrange to raise an exception
    async def failing_xrevrange(*args, **kwargs):
        raise Exception('Redis connection failed')

    redis.xrevrange = failing_xrevrange

    class FakeRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id
            self.redis_client = redis_client

        def tap(self):
            return FakeRedisEventQueue(self.task_id, self.redis_client)

        async def close(self):
            pass

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FakeRedisEventQueue
    sys.modules['a2a.server.events.redis_event_queue'] = fake_mod

    # Also patch the RedisEventQueue reference in redis_queue_manager
    import a2a.server.events.redis_queue_manager as rqm

    rqm.RedisEventQueue = FakeRedisEventQueue

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=redis)

    # Create queue first
    await manager.create_or_tap('test_task')

    # Close should handle Redis check failure gracefully and log it
    with caplog.at_level(logging.DEBUG):
        await manager.close('test_task')

    # Check that debug logging occurred for the Redis check failure
    assert any(
        'Could not check if stream is already closed' in record.message
        for record in caplog.records
    ), 'Should log Redis check failure'


@pytest.mark.asyncio
async def test_close_logging_on_queue_close_failure(caplog):
    """Test that close method logs when queue close fails."""
    import logging

    class FailingRedisEventQueue:
        def __init__(self, task_id, redis_client, stream_prefix=None):
            self.task_id = task_id
            self.redis_client = redis_client

        def tap(self):
            return FailingRedisEventQueue(self.task_id, self.redis_client)

        async def close(self):
            # Make the close method fail by trying to use the redis_client
            if self.redis_client is None:
                raise Exception('Queue close failed - no redis client')
            # This should not be reached in the test

    # Monkeypatch
    import sys
    import types

    fake_mod = types.ModuleType('a2a.server.events.redis_event_queue')
    fake_mod.RedisEventQueue = FailingRedisEventQueue
    sys.modules['a2a.server.events.redis_event_queue'] = fake_mod

    # Also patch the RedisEventQueue reference in redis_queue_manager
    import a2a.server.events.redis_queue_manager as rqm

    rqm.RedisEventQueue = FailingRedisEventQueue

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    manager = RedisQueueManager(redis_client=None)

    # Create queue
    await manager.create_or_tap('test_task')

    # Close should handle queue close failure gracefully and log it
    with caplog.at_level(logging.DEBUG):
        await manager.close('test_task')

    # Check that debug logging occurred for the queue close failure
    assert any(
        'Failed to close queue' in record.message for record in caplog.records
    ), (
        f'Should log queue close failure. Captured logs: {[r.message for r in caplog.records]}'
    )


@pytest.mark.asyncio
async def test_create_or_tap_redis_event_queue_import_failure(
    monkeypatch, caplog
):
    """Test create_or_tap when RedisEventQueue import fails."""
    # Mock the import failure by setting RedisEventQueue to None
    from a2a.server.events import redis_queue_manager

    # Store original value
    original_redis_event_queue = redis_queue_manager.RedisEventQueue

    try:
        # Set RedisEventQueue to None to simulate import failure
        redis_queue_manager.RedisEventQueue = None

        from a2a.server.events.redis_queue_manager import RedisQueueManager

        manager = RedisQueueManager(redis_client=None)

        # Should raise RuntimeError when RedisEventQueue is None
        with pytest.raises(
            RuntimeError, match='RedisEventQueue is not available'
        ):
            with caplog.at_level(logging.ERROR):
                await manager.create_or_tap('test_task')

        # Check that error logging occurred
        assert any(
            'RedisEventQueue is None - import failed' in record.message
            for record in caplog.records
        ), (
            f'Should log import failure. Captured logs: {[r.message for r in caplog.records]}'
        )

    finally:
        # Restore original value
        redis_queue_manager.RedisEventQueue = original_redis_event_queue


@pytest.mark.asyncio
async def test_tap_redis_event_queue_import_failure(monkeypatch, caplog):
    """Test tap when RedisEventQueue import fails."""
    # Store original value
    import a2a.server.events.redis_queue_manager as rqm

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    original_redis_event_queue = rqm.RedisEventQueue

    try:
        # Set RedisEventQueue to None to simulate import failure
        rqm.RedisEventQueue = None

        manager = RedisQueueManager(redis_client=None)

        # Should raise RuntimeError when RedisEventQueue is None
        with pytest.raises(
            RuntimeError,
            match='RedisEventQueue is not available. Cannot create tap',
        ):
            await manager.tap('test_task')

    finally:
        # Restore original value
        rqm.RedisEventQueue = original_redis_event_queue


@pytest.mark.asyncio
async def test_close_redis_event_queue_import_failure(monkeypatch):
    """Test close when RedisEventQueue import fails."""
    # Store original value
    import a2a.server.events.redis_queue_manager as rqm

    from a2a.server.events.redis_queue_manager import RedisQueueManager

    original_redis_event_queue = rqm.RedisEventQueue

    try:
        # Set RedisEventQueue to None to simulate import failure
        rqm.RedisEventQueue = None

        manager = RedisQueueManager(redis_client=None)

        # Should raise RuntimeError when RedisEventQueue is None
        with pytest.raises(
            RuntimeError,
            match='RedisEventQueue is not available. Cannot close stream',
        ):
            await manager.close('test_task')

    finally:
        # Restore original value
        rqm.RedisEventQueue = original_redis_event_queue
