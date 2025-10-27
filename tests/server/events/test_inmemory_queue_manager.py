import asyncio

from unittest.mock import MagicMock

import pytest

from a2a.server.events import InMemoryQueueManager
from a2a.server.events.event_queue import EventQueue
from a2a.server.events.queue_manager import (
    NoTaskQueue,
    TaskQueueExists,
)


class TestInMemoryQueueManager:
    @pytest.fixture
    def queue_manager(self) -> InMemoryQueueManager:
        """Fixture to create a fresh InMemoryQueueManager for each test."""
        return InMemoryQueueManager()

    @pytest.fixture
    def event_queue(self) -> MagicMock:
        """Fixture to create a mock EventQueue."""
        queue = MagicMock(spec=EventQueue)

        # Mock the tap method to return itself
        queue.tap.return_value = queue
        return queue

    @pytest.mark.asyncio
    async def test_init(self, queue_manager: InMemoryQueueManager) -> None:
        """Test that the InMemoryQueueManager initializes with empty task queue and a lock."""
        assert queue_manager._task_queue == {}
        assert isinstance(queue_manager._lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_add_new_queue(
        self, queue_manager: InMemoryQueueManager, event_queue: MagicMock
    ) -> None:
        """Test adding a new queue to the manager."""
        task_id = 'test_task_id'
        await queue_manager.add(task_id, event_queue)
        assert queue_manager._task_queue[task_id] == event_queue

    @pytest.mark.asyncio
    async def test_add_existing_queue(
        self, queue_manager: InMemoryQueueManager, event_queue: MagicMock
    ) -> None:
        """Test adding a queue with an existing task_id raises TaskQueueExists."""
        task_id = 'test_task_id'
        await queue_manager.add(task_id, event_queue)

        with pytest.raises(TaskQueueExists):
            await queue_manager.add(task_id, event_queue)

    @pytest.mark.asyncio
    async def test_get_existing_queue(
        self, queue_manager: InMemoryQueueManager, event_queue: MagicMock
    ) -> None:
        """Test getting an existing queue returns the queue."""
        task_id = 'test_task_id'
        await queue_manager.add(task_id, event_queue)

        result = await queue_manager.get(task_id)
        assert result == event_queue

    @pytest.mark.asyncio
    async def test_get_nonexistent_queue(
        self, queue_manager: InMemoryQueueManager
    ) -> None:
        """Test getting a nonexistent queue returns None."""
        result = await queue_manager.get('nonexistent_task_id')
        assert result is None

    @pytest.mark.asyncio
    async def test_tap_existing_queue(
        self, queue_manager: InMemoryQueueManager, event_queue: MagicMock
    ) -> None:
        """Test tapping an existing queue returns the tapped queue."""
        task_id = 'test_task_id'
        await queue_manager.add(task_id, event_queue)

        result = await queue_manager.tap(task_id)
        assert result == event_queue
        event_queue.tap.assert_called_once()

    @pytest.mark.asyncio
    async def test_tap_nonexistent_queue(
        self, queue_manager: InMemoryQueueManager
    ) -> None:
        """Test tapping a nonexistent queue returns None."""
        result = await queue_manager.tap('nonexistent_task_id')
        assert result is None

    @pytest.mark.asyncio
    async def test_close_existing_queue(
        self, queue_manager: InMemoryQueueManager, event_queue: MagicMock
    ) -> None:
        """Test closing an existing queue removes it from the manager."""
        task_id = 'test_task_id'
        await queue_manager.add(task_id, event_queue)

        await queue_manager.close(task_id)
        assert task_id not in queue_manager._task_queue

    @pytest.mark.asyncio
    async def test_close_nonexistent_queue(
        self, queue_manager: InMemoryQueueManager
    ) -> None:
        """Test closing a nonexistent queue raises NoTaskQueue."""
        with pytest.raises(NoTaskQueue):
            await queue_manager.close('nonexistent_task_id')

    @pytest.mark.asyncio
    async def test_create_or_tap_new_queue(
        self, queue_manager: InMemoryQueueManager
    ) -> None:
        """Test create_or_tap with a new task_id creates and returns a new queue."""
        task_id = 'test_task_id'

        result = await queue_manager.create_or_tap(task_id)
        assert isinstance(result, EventQueue)
        assert queue_manager._task_queue[task_id] == result

    @pytest.mark.asyncio
    async def test_create_or_tap_existing_queue(
        self, queue_manager: InMemoryQueueManager, event_queue: MagicMock
    ) -> None:
        """Test create_or_tap with an existing task_id taps and returns the existing queue."""
        task_id = 'test_task_id'
        await queue_manager.add(task_id, event_queue)

        result = await queue_manager.create_or_tap(task_id)

        assert result == event_queue
        event_queue.tap.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrency(
        self, queue_manager: InMemoryQueueManager
    ) -> None:
        """Test concurrent access to the queue manager."""

        async def add_task(task_id):
            queue = EventQueue()
            await queue_manager.add(task_id, queue)
            return task_id

        async def get_task(task_id):
            return await queue_manager.get(task_id)

        # Create 10 different task IDs
        task_ids = [f'task_{i}' for i in range(10)]

        # Add tasks concurrently
        add_tasks = [add_task(task_id) for task_id in task_ids]
        added_task_ids = await asyncio.gather(*add_tasks)

        # Verify all tasks were added
        assert set(added_task_ids) == set(task_ids)

        # Get tasks concurrently
        get_tasks = [get_task(task_id) for task_id in task_ids]
        queues = await asyncio.gather(*get_tasks)

        # Verify all queues are not None
        assert all(queue is not None for queue in queues)

        # Verify all tasks are in the manager
        for task_id in task_ids:
            assert task_id in queue_manager._task_queue
