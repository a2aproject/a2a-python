from __future__ import annotations

from typing import Any

from a2a.server.events.redis_queue_manager import RedisQueueManager
from a2a.server.request_handlers.default_request_handler import (
    DefaultRequestHandler,
)


def create_redis_request_handler(
    agent_executor: Any,
    task_store: Any,
    redis_client: Any,
    stream_prefix: str = 'a2a:task',
    **kwargs: Any,
) -> DefaultRequestHandler:
    """Create a DefaultRequestHandler wired to a RedisQueueManager.

    This convenience factory constructs a RedisQueueManager using the
    provided `redis_client` and passes it into `DefaultRequestHandler` so the
    rest of the application can remain unchanged.
    """
    queue_manager = RedisQueueManager(
        redis_client=redis_client, stream_prefix=stream_prefix
    )
    return DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=task_store,
        queue_manager=queue_manager,
        **kwargs,
    )
