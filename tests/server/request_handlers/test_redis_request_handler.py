def test_create_redis_request_handler_monkeypatched(monkeypatch):
    class FakeRedisQueueManager:
        def __init__(self, redis_client=None, stream_prefix='a2a:task'):
            self.redis_client = redis_client

    monkeypatch.setenv('A2A_FAKE', '1')

    from a2a.server.request_handlers.redis_request_handler import (
        create_redis_request_handler,
    )
    from a2a.server.request_handlers.default_request_handler import (
        DefaultRequestHandler,
    )

    # Monkeypatch RedisQueueManager to our fake to avoid real redis import
    import a2a.server.events.redis_queue_manager as rqm

    rqm.RedisQueueManager = FakeRedisQueueManager

    handler = create_redis_request_handler(
        agent_executor=object(), task_store=object(), redis_client=None
    )
    assert isinstance(handler, DefaultRequestHandler)
