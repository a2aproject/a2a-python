import asyncio
import logging

from unittest.mock import AsyncMock, MagicMock, patch

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
    InMemoryTaskLeaseStore,
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


@pytest.mark.asyncio
async def test_lease_settings_reach_the_active_task() -> None:
    leases = InMemoryTaskLeaseStore()
    handler = DefaultRequestHandlerV2(
        agent_executor=NoopExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=AgentCard(),
        lease_store=leases,
        lease_ttl=12.0,
        heartbeat_interval=3.0,
    )
    active_task = await handler._active_task_registry.get_or_create(  # noqa: SLF001
        'task-1', call_context=make_context(), create_task_if_missing=True
    )
    try:
        assert active_task._lease_store is leases  # noqa: SLF001
        assert active_task._lease_ttl == 12.0  # noqa: SLF001
        assert active_task._heartbeat_interval == 3.0  # noqa: SLF001
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('event_stream', 'fail_in_flight', 'expected'),
    [
        (None, None, False),
        (InMemoryTaskEventStream(), None, True),
        (InMemoryTaskEventStream(), False, False),
        (None, True, True),
    ],
)
async def test_aclose_fails_in_flight_tasks_in_cluster_mode_by_default(
    event_stream: TaskEventStream | None,
    fail_in_flight: bool | None,
    expected: bool,
) -> None:
    handler = make_handler(event_stream=event_stream)
    with patch.object(
        handler, '_fail_in_flight_tasks', new_callable=AsyncMock
    ) as fail_in_flight_tasks:
        await handler.aclose(fail_in_flight=fail_in_flight)

    assert fail_in_flight_tasks.await_count == int(expected)


@pytest.mark.asyncio
async def test_aclose_gives_up_on_failing_in_flight_tasks_after_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler = make_handler(event_stream=InMemoryTaskEventStream())

    async def hang() -> None:
        await asyncio.sleep(10)

    with (
        patch.object(handler, '_fail_in_flight_tasks', side_effect=hang),
        caplog.at_level(logging.WARNING),
    ):
        await asyncio.wait_for(handler.aclose(timeout=0.01), timeout=2)

    assert 'Timed out failing in-flight tasks' in caplog.text


@pytest.mark.asyncio
async def test_fail_in_flight_tasks_skips_idle_tasks_and_survives_errors() -> (
    None
):
    handler = make_handler(event_stream=InMemoryTaskEventStream())
    running = MagicMock(task_id='running', request_in_flight=True)
    running.call_context = make_context()
    idle = MagicMock(task_id='idle', request_in_flight=False)
    idle.call_context = make_context()
    no_context = MagicMock(task_id='new', request_in_flight=True)
    no_context.call_context = None
    target = 'a2a.server.request_handlers.default_request_handler_v2.fail_task'

    with (
        patch.object(
            handler._active_task_registry,  # noqa: SLF001
            'snapshot',
            return_value=[running, idle, no_context],
        ),
        patch(target, side_effect=RuntimeError('store down')) as fail_task,
    ):
        await handler._fail_in_flight_tasks()  # noqa: SLF001

    assert [c.args[1] for c in fail_task.await_args_list] == ['running']
    await handler.aclose(fail_in_flight=False)
