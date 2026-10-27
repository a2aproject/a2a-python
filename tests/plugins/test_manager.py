from unittest.mock import MagicMock

import pytest

from a2a import plugins
from a2a.client.client import ClientCallContext
from a2a.plugins import (
    ClientPlugin,
    ClientRequest,
    ClientResponse,
    CompletePlugin,
    ExecutionPlugin,
    Plugin,
    PushPlugin,
    PushRequest,
    PushResult,
    SequentialPluginManager,
    ServerPlugin,
    ServerRequest,
    ServerResponse,
    TaskEventInfo,
    TaskObserverPlugin,
)
from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import AgentCard, Task, TaskState


class Recorder(CompletePlugin, ExecutionPlugin, TaskObserverPlugin, PushPlugin):
    """Appends '<name>.<hook>' to a shared log for every hook call."""

    def __init__(self, name: str, log: list[str], fail: set[str] = frozenset()):
        self.name = name
        self.log = log
        self.fail = fail

    def _record(self, hook: str) -> None:
        self.log.append(f'{self.name}.{hook}')
        if hook in self.fail:
            raise ValueError(f'{self.name}.{hook} failed')

    async def before_handler_run(self, request):
        self._record('before_handler_run')

    async def after_handler_run(self, response):
        self._record('after_handler_run')

    async def before_client_request(self, request):
        self._record('before_client_request')

    async def after_client_request(self, response):
        self._record('after_client_request')

    async def before_execution(self, request_context):
        self._record('before_execution')

    async def on_execution_event(self, request_context, event):
        self._record('on_execution_event')

    async def after_execution(self, request_context, error):
        self._record('after_execution')

    async def on_task_event(self, info):
        self._record('on_task_event')

    async def before_push_send(self, request):
        self._record('before_push_send')

    async def on_push_sent(self, result):
        self._record('on_push_sent')


def server_request() -> ServerRequest:
    return ServerRequest('GetTask', None, ServerCallContext())


def client_request() -> ClientRequest:
    return ClientRequest('GetTask', None, AgentCard(), ClientCallContext())


def push_request() -> PushRequest:
    return PushRequest('t-1', 'http://hook', {}, Task(id='t-1'))


@pytest.fixture(autouse=True)
def restore_global():
    previous = plugins.get_global()
    yield
    plugins.set_global(previous)


@pytest.mark.asyncio
async def test_before_in_order_after_in_reverse():
    log: list[str] = []
    m = plugins.sequential(Recorder('a', log), Recorder('b', log))

    await m.before_handler_run(server_request())
    await m.after_handler_run(
        ServerResponse('GetTask', None, ServerCallContext())
    )
    await m.before_client_request(client_request())
    await m.after_client_request(
        ClientResponse('GetTask', None, ClientCallContext())
    )
    await m.before_execution(MagicMock())
    await m.after_execution(MagicMock(), None)

    assert log == [
        'a.before_handler_run',
        'b.before_handler_run',
        'b.after_handler_run',
        'a.after_handler_run',
        'a.before_client_request',
        'b.before_client_request',
        'b.after_client_request',
        'a.after_client_request',
        'a.before_execution',
        'b.before_execution',
        'b.after_execution',
        'a.after_execution',
    ]


@pytest.mark.asyncio
async def test_observers_run_in_order():
    log: list[str] = []
    m = plugins.sequential(Recorder('a', log), Recorder('b', log))
    info = TaskEventInfo(
        't-1',
        None,
        TaskState.TASK_STATE_SUBMITTED,
        None,
        Task(id='t-1'),
        ServerCallContext(),
    )

    await m.on_task_event(info)
    await m.on_execution_event(MagicMock(), Task())
    await m.before_push_send(push_request())
    await m.on_push_sent(PushResult(push_request(), 0.1, 200, None))

    assert log == [
        'a.on_task_event',
        'b.on_task_event',
        'a.on_execution_event',
        'b.on_execution_event',
        'a.before_push_send',
        'b.before_push_send',
        'a.on_push_sent',
        'b.on_push_sent',
    ]


@pytest.mark.parametrize(
    ('before', 'after', 'call'),
    [
        (
            'before_handler_run',
            'after_handler_run',
            lambda m: m.before_handler_run(server_request()),
        ),
        (
            'before_client_request',
            'after_client_request',
            lambda m: m.before_client_request(client_request()),
        ),
        (
            'before_execution',
            'after_execution',
            lambda m: m.before_execution(MagicMock()),
        ),
    ],
)
@pytest.mark.asyncio
async def test_rejection_unwinds_plugins_that_ran(before, after, call):
    log: list[str] = []
    m = plugins.sequential(
        Recorder('a', log),
        Recorder('b', log, fail={before}),
        Recorder('c', log),
    )

    with pytest.raises(ValueError, match=f'b.{before} failed'):
        await call(m)

    # c never ran; a ran and is unwound; b raised and gets no after-hook.
    assert log == [f'a.{before}', f'b.{before}', f'a.{after}']


@pytest.mark.asyncio
async def test_unwind_passes_error_to_after_hook():
    seen: list[BaseException | None] = []

    class Capture(ServerPlugin):
        async def after_handler_run(self, response):
            seen.append(response.error)

    class Reject(ServerPlugin):
        async def before_handler_run(self, request):
            raise PermissionError('denied')

    m = plugins.sequential(Capture(), Reject())
    with pytest.raises(PermissionError):
        await m.before_handler_run(server_request())

    assert len(seen) == 1
    assert isinstance(seen[0], PermissionError)


@pytest.mark.parametrize(
    'hook',
    [
        'after_handler_run',
        'after_client_request',
        'after_execution',
        'on_execution_event',
        'on_task_event',
        'on_push_sent',
    ],
)
@pytest.mark.asyncio
async def test_after_and_observer_errors_are_swallowed(hook, caplog):
    log: list[str] = []
    m = plugins.sequential(Recorder('a', log, fail={hook}), Recorder('b', log))
    calls = {
        'after_handler_run': lambda: m.after_handler_run(
            ServerResponse('GetTask', None, ServerCallContext())
        ),
        'after_client_request': lambda: m.after_client_request(
            ClientResponse('GetTask', None, ClientCallContext())
        ),
        'after_execution': lambda: m.after_execution(MagicMock(), None),
        'on_execution_event': lambda: m.on_execution_event(MagicMock(), Task()),
        'on_task_event': lambda: m.on_task_event(MagicMock()),
        'on_push_sent': lambda: m.on_push_sent(MagicMock()),
    }

    await calls[hook]()

    assert f'a.{hook}' in log
    assert f'b.{hook}' in log
    assert f"A2A plugin 'a' failed in {hook}" in caplog.text


@pytest.mark.asyncio
async def test_before_push_send_error_propagates():
    m = plugins.sequential(Recorder('a', [], fail={'before_push_send'}))
    with pytest.raises(ValueError, match='before_push_send'):
        await m.before_push_send(push_request())


def test_plugins_filtered_by_interface():
    class OnlyTask(TaskObserverPlugin):
        pass

    class OnlyClient(ClientPlugin):
        pass

    m = plugins.sequential(OnlyTask(), OnlyClient())

    assert m.has_task_hooks
    assert m.has_client_hooks
    assert not m.has_server_hooks
    assert not m.has_execution_hooks
    assert not m.has_push_hooks


def test_empty_manager_has_no_hooks():
    m = SequentialPluginManager()
    assert not any(
        (
            m.has_server_hooks,
            m.has_client_hooks,
            m.has_execution_hooks,
            m.has_task_hooks,
            m.has_push_hooks,
        )
    )
    assert m.plugins == ()


def test_base_plugin_manager_has_no_hooks():
    m = plugins.PluginManager()
    assert not m.has_server_hooks
    assert not m.has_client_hooks
    assert not m.has_execution_hooks
    assert not m.has_task_hooks
    assert not m.has_push_hooks


@pytest.mark.asyncio
async def test_nested_managers():
    log: list[str] = []
    inner = plugins.sequential(Recorder('b', log), Recorder('c', log))
    outer = plugins.sequential(Recorder('a', log), inner)

    await outer.before_handler_run(server_request())
    await outer.after_handler_run(
        ServerResponse('GetTask', None, ServerCallContext())
    )

    assert log == [
        'a.before_handler_run',
        'b.before_handler_run',
        'c.before_handler_run',
        'c.after_handler_run',
        'b.after_handler_run',
        'a.after_handler_run',
    ]


def test_nested_empty_manager_does_not_count_as_hooks():
    outer = plugins.sequential(plugins.sequential())
    assert not outer.has_server_hooks
    assert not outer.has_push_hooks


def test_rejects_non_plugins():
    with pytest.raises(TypeError, match='Not A2A plugins'):
        plugins.sequential(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_base_hooks_are_no_ops():
    p = CompletePlugin()
    assert await p.before_handler_run(server_request()) is None
    assert (
        await p.after_client_request(
            ClientResponse('GetTask', None, ClientCallContext())
        )
        is None
    )
    assert Plugin.name == 'unnamed'
    e = ExecutionPlugin()
    assert await e.on_execution_event(MagicMock(), Task()) is None
    assert await PushPlugin().before_push_send(push_request()) is None


def test_global_registry():
    assert isinstance(plugins.get_global(), plugins.PluginManager)
    m = plugins.sequential(Recorder('a', []))
    plugins.set_global(m)
    assert plugins.get_global() is m


def test_set_global_requires_manager():
    with pytest.raises(TypeError, match='sequential'):
        plugins.set_global(Recorder('a', []))  # type: ignore[arg-type]
