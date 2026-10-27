import pytest

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.cluster import (
    LegacyTaskStoreAdapter,
    TaskEventStream,
)
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.default_request_handler_v2 import (
    DefaultRequestHandlerV2,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import AgentCard

from .conftest import (
    InMemoryTaskEventStream,
    VersionedInMemoryTaskStore,
    make_context,
)


class NoopExecutor(AgentExecutor):
    async def execute(self, context, event_queue) -> None:  # noqa: ANN001
        pass

    async def cancel(self, context, event_queue) -> None:  # noqa: ANN001
        pass


def make_handler(event_stream: TaskEventStream | None = None):
    return DefaultRequestHandlerV2(
        agent_executor=NoopExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=AgentCard(),
        event_stream=event_stream,
    )


def test_plain_store_kept_as_is_and_adapted_for_versioned() -> None:
    # A plain TaskStore is exposed unchanged as `task_store`; internal cluster
    # code sees it wrapped in a LegacyTaskStoreAdapter.
    store = InMemoryTaskStore()
    handler = DefaultRequestHandlerV2(
        agent_executor=NoopExecutor(),
        task_store=store,
        agent_card=AgentCard(),
    )
    assert handler.task_store is store
    assert isinstance(handler._versioned_store, LegacyTaskStoreAdapter)  # noqa: SLF001
    assert handler._versioned_store.store is store  # noqa: SLF001


def test_versioned_store_used_directly_and_not_swapped() -> None:
    # LegacyTaskStoreAdapter is itself a VersionedTaskStore, so this asserts the
    # no-swap contract without a test double.
    store = LegacyTaskStoreAdapter(InMemoryTaskStore())
    handler = DefaultRequestHandlerV2(
        agent_executor=NoopExecutor(),
        task_store=store,
        agent_card=AgentCard(),
    )
    assert handler.task_store is store
    assert handler._versioned_store is store  # noqa: SLF001


def test_default_handler_has_no_stream() -> None:
    # No stream supplied: single-process mode, the handler holds no event stream.
    handler = make_handler()
    assert handler._event_stream is None  # noqa: SLF001


def test_explicit_stream_is_stored() -> None:
    stream = InMemoryTaskEventStream()
    handler = make_handler(event_stream=stream)
    assert handler._event_stream is stream  # noqa: SLF001


def test_stream_is_threaded_to_registry() -> None:
    stream = InMemoryTaskEventStream()
    handler = make_handler(event_stream=stream)
    assert handler._active_task_registry._event_stream is stream  # noqa: SLF001


def test_default_alias_accepts_event_stream() -> None:
    # DefaultRequestHandler is an alias for v2; ensure the kwarg flows through.
    stream = InMemoryTaskEventStream()
    handler = DefaultRequestHandler(
        NoopExecutor(),
        InMemoryTaskStore(),
        AgentCard(),
        event_stream=stream,
    )
    assert handler._event_stream is stream  # noqa: SLF001


@pytest.mark.asyncio
async def test_active_task_receives_stream_from_registry() -> None:
    stream = InMemoryTaskEventStream()
    handler = make_handler(event_stream=stream)
    context = make_context()

    active_task = await handler._active_task_registry.get_or_create(  # noqa: SLF001
        'task-1',
        call_context=context,
        create_task_if_missing=True,
        initial_message=None,
    )
    try:
        assert active_task._event_stream is stream  # noqa: SLF001
    finally:
        await handler.aclose()


@pytest.mark.asyncio
async def test_none_event_stream_propagates_none_to_active_task() -> None:
    # When no stream is passed, the ActiveTask receives None and uses only its
    # in-process queues (single-process behaviour).
    handler = make_handler(event_stream=None)
    context = make_context()
    active_task = await handler._active_task_registry.get_or_create(  # noqa: SLF001
        'task-1',
        call_context=context,
        create_task_if_missing=True,
        initial_message=None,
    )
    try:
        assert active_task._event_stream is None  # noqa: SLF001
    finally:
        await handler.aclose()


def test_versioned_store_without_stream_warns() -> None:
    # A versioned store signals cluster intent; without a shared stream,
    # cross-replica streaming is disabled, so construction warns once.
    with pytest.warns(UserWarning, match='cross-replica streaming is disabled'):
        DefaultRequestHandlerV2(
            agent_executor=NoopExecutor(),
            task_store=VersionedInMemoryTaskStore(),
            agent_card=AgentCard(),
        )


def test_versioned_store_with_stream_does_not_warn(recwarn) -> None:  # noqa: ANN001
    # Supplying a shared stream is the multi-replica configuration: no warning.
    DefaultRequestHandlerV2(
        agent_executor=NoopExecutor(),
        task_store=VersionedInMemoryTaskStore(),
        agent_card=AgentCard(),
        event_stream=InMemoryTaskEventStream(),
    )
    assert not [
        w
        for w in recwarn
        if 'cross-replica streaming is disabled' in str(w.message)
    ]


def test_plain_store_without_stream_does_not_warn(recwarn) -> None:  # noqa: ANN001
    # A plain store is the single-process default: no cluster intent, no warning.
    make_handler()
    assert not [
        w
        for w in recwarn
        if 'cross-replica streaming is disabled' in str(w.message)
    ]
