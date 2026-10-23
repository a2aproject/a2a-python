"""Plugin hooks run by DefaultRequestHandlerV2 (request, execution, task, push)."""

import asyncio

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from a2a import plugins
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.plugins import (
    CompletePlugin,
    ExecutionPlugin,
    PushPlugin,
    PushRequest,
    PushResult,
    ServerRequest,
    ServerResponse,
    TaskEventInfo,
    TaskObserverPlugin,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.agent_execution.plugin_agent_executor import (
    PluginAgentExecutor,
)
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import (
    DefaultRequestHandlerV2,
    LegacyRequestHandler,
)
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    TaskUpdater,
)
from a2a.server.tasks.base_push_notification_sender import (
    BasePushNotificationSender,
)
from a2a.types import TaskNotFoundError
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)


CARD = AgentCard(
    name='plugin_test_agent',
    version='1.0',
    capabilities=AgentCapabilities(streaming=True, push_notifications=True),
)


class Recorder(CompletePlugin, ExecutionPlugin, TaskObserverPlugin, PushPlugin):
    """Records (hook, argument) for every hook call."""

    name = 'recorder'

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def hooks(self, name: str) -> list[Any]:
        return [arg for hook, arg in self.calls if hook == name]

    def names(self) -> list[str]:
        return [hook for hook, _ in self.calls]

    async def before_handler_run(self, request: ServerRequest) -> None:
        self.calls.append(('before_handler_run', request))

    async def after_handler_run(self, response: ServerResponse) -> None:
        self.calls.append(('after_handler_run', response))

    async def before_execution(self, request_context: RequestContext) -> None:
        self.calls.append(('before_execution', request_context))

    async def on_execution_event(self, request_context, event) -> None:
        self.calls.append(('on_execution_event', event))

    async def after_execution(self, request_context, error) -> None:
        self.calls.append(('after_execution', error))

    async def on_task_event(self, info: TaskEventInfo) -> None:
        self.calls.append(('on_task_event', info))

    async def before_push_send(self, request: PushRequest) -> None:
        request.headers['x-plugin'] = 'yes'
        self.calls.append(('before_push_send', request))

    async def on_push_sent(self, result: PushResult) -> None:
        self.calls.append(('on_push_sent', result))


class TaskAgent(AgentExecutor):
    """SUBMITTED -> WORKING -> artifact -> COMPLETED."""

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task = context.current_task or new_task_from_user_message(
            context.message
        )
        if not context.current_task:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        await updater.add_artifact(parts=[Part(text='result')], name='out')
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


class MessageAgent(AgentExecutor):
    """Replies with a single Message, creating no task."""

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.enqueue_event(
            Message(
                role=Role.ROLE_AGENT,
                message_id='reply-1',
                parts=[Part(text='hi')],
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


def send_request(
    text: str = 'hello',
    configuration: SendMessageConfiguration | None = None,
) -> SendMessageRequest:
    request = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER, message_id='m-1', parts=[Part(text=text)]
        )
    )
    if configuration is not None:
        request.configuration.CopyFrom(configuration)
    return request


async def wait_for(predicate: Callable[[], bool], timeout: float = 5) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


def make_handler(
    recorder: Recorder | None,
    executor: AgentExecutor | None = None,
    **kwargs: Any,
) -> DefaultRequestHandlerV2:
    return DefaultRequestHandlerV2(
        agent_executor=executor or TaskAgent(),
        task_store=kwargs.pop('task_store', InMemoryTaskStore()),
        agent_card=CARD,
        plugins=plugins.sequential(recorder) if recorder else None,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def restore_global():
    previous = plugins.get_global()
    yield
    plugins.set_global(previous)


def test_no_plugins_leaves_executor_unwrapped():
    executor = TaskAgent()
    handler = DefaultRequestHandlerV2(
        agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=CARD
    )

    assert not handler.plugins.has_server_hooks
    assert handler._active_task_registry._agent_executor is executor
    assert handler.agent_card is CARD


def test_global_manager_is_used_by_default():
    recorder = Recorder()
    manager = plugins.sequential(recorder)
    plugins.set_global(manager)
    executor = TaskAgent()

    handler = DefaultRequestHandlerV2(
        agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=CARD
    )

    assert handler.plugins is manager
    assert handler.agent_executor is executor
    assert isinstance(
        handler._active_task_registry._agent_executor, PluginAgentExecutor
    )


@pytest.mark.asyncio
async def test_no_plugins_request_still_works():
    handler = make_handler(None)
    result = await handler.on_message_send(send_request(), ServerCallContext())
    assert result.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_send_message_runs_all_server_hooks():
    recorder = Recorder()
    handler = make_handler(recorder)
    context = ServerCallContext()

    result = await handler.on_message_send(send_request(), context)
    await wait_for(lambda: 'after_execution' in recorder.names())

    assert isinstance(result, Task)
    assert result.status.state == TaskState.TASK_STATE_COMPLETED

    (request,) = recorder.hooks('before_handler_run')
    assert request.method == 'send_message'
    assert request.context is context
    assert request.agent_card is CARD
    assert request.payload.message.message_id == 'm-1'

    (response,) = recorder.hooks('after_handler_run')
    assert response.method == 'send_message'
    assert response.payload is result
    assert response.error is None
    assert response.is_final
    assert not response.is_stream_event

    # The execution runs with the same call context as the request.
    (request_context,) = recorder.hooks('before_execution')
    assert request_context.call_context is context
    assert recorder.hooks('after_execution') == [None]

    events = recorder.hooks('on_execution_event')
    assert [type(e).__name__ for e in events] == [
        'Task',
        'TaskStatusUpdateEvent',
        'TaskArtifactUpdateEvent',
        'TaskStatusUpdateEvent',
    ]

    infos = recorder.hooks('on_task_event')
    transitions = [(i.from_state, i.to_state) for i in infos]
    assert transitions[0] == (None, TaskState.TASK_STATE_SUBMITTED)
    assert (
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_WORKING,
    ) in transitions
    assert (
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_WORKING,
    ) in transitions
    assert transitions[-1] == (
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_COMPLETED,
    )
    assert all(i.task_id == result.id for i in infos)
    assert all(i.context is context for i in infos)

    names = recorder.names()
    assert names[0] == 'before_handler_run'
    assert names.index('before_execution') < names.index('on_execution_event')


@pytest.mark.asyncio
async def test_streaming_reports_each_event_and_a_final_call():
    recorder = Recorder()
    handler = make_handler(recorder)

    streamed = [
        event
        async for event in handler.on_message_send_stream(
            send_request(), ServerCallContext()
        )
    ]

    responses = recorder.hooks('after_handler_run')
    assert [r.payload for r in responses[:-1]] == streamed
    assert all(r.is_stream_event and not r.is_final for r in responses[:-1])
    final = responses[-1]
    assert final.method == 'send_message_streaming'
    assert final.is_final
    assert not final.is_stream_event
    assert final.payload is None
    assert final.error is None


@pytest.mark.asyncio
async def test_stream_closed_early_still_gets_final_call():
    recorder = Recorder()
    handler = make_handler(recorder)

    stream = handler.on_message_send_stream(send_request(), ServerCallContext())
    await anext(stream)
    await stream.aclose()

    final = recorder.hooks('after_handler_run')[-1]
    assert final.is_final
    assert final.error is None


@pytest.mark.asyncio
async def test_handler_error_reaches_after_handler_run():
    recorder = Recorder()
    handler = make_handler(recorder)

    with pytest.raises(TaskNotFoundError):
        await handler.on_get_task(
            GetTaskRequest(id='missing'), ServerCallContext()
        )

    (response,) = recorder.hooks('after_handler_run')
    assert response.method == 'get_task'
    assert isinstance(response.error, TaskNotFoundError)
    assert response.is_final


@pytest.mark.asyncio
async def test_stream_error_reaches_final_call():
    recorder = Recorder()
    handler = make_handler(recorder)

    with pytest.raises(TaskNotFoundError):
        async for _ in handler.on_message_send_stream(
            SendMessageRequest(
                message=Message(
                    role=Role.ROLE_USER,
                    message_id='m-2',
                    task_id='missing',
                    parts=[Part(text='x')],
                )
            ),
            ServerCallContext(),
        ):
            pass

    final = recorder.hooks('after_handler_run')[-1]
    assert isinstance(final.error, TaskNotFoundError)


@pytest.mark.asyncio
async def test_before_handler_run_can_reject_the_request():
    class Deny(plugins.ServerPlugin):
        async def before_handler_run(self, request):
            raise PermissionError('denied')

    executed = False

    class Spy(TaskAgent):
        async def execute(self, context, event_queue):
            nonlocal executed
            executed = True

    handler = DefaultRequestHandlerV2(
        agent_executor=Spy(),
        task_store=InMemoryTaskStore(),
        agent_card=CARD,
        plugins=plugins.sequential(Deny()),
    )

    with pytest.raises(PermissionError):
        await handler.on_message_send(send_request(), ServerCallContext())
    with pytest.raises(PermissionError):
        async for _ in handler.on_message_send_stream(
            send_request(), ServerCallContext()
        ):
            pass
    assert not executed


@pytest.mark.asyncio
async def test_message_only_agent_reports_event_but_no_task_write():
    recorder = Recorder()
    handler = make_handler(recorder, executor=MessageAgent())

    result = await handler.on_message_send(send_request(), ServerCallContext())
    await wait_for(lambda: 'after_execution' in recorder.names())

    assert isinstance(result, Message)
    (event,) = recorder.hooks('on_execution_event')
    assert isinstance(event, Message)
    assert recorder.hooks('on_task_event') == []


@pytest.mark.asyncio
async def test_before_execution_rejection_fails_the_task():
    class Reject(plugins.ExecutionPlugin):
        async def before_execution(self, request_context):
            raise RuntimeError('not now')

    recorder = Recorder()
    handler = DefaultRequestHandlerV2(
        agent_executor=TaskAgent(),
        task_store=InMemoryTaskStore(),
        agent_card=CARD,
        plugins=plugins.sequential(recorder, Reject()),
    )

    with pytest.raises(RuntimeError, match='not now'):
        await handler.on_message_send(send_request(), ServerCallContext())

    # The recorder ran before the rejecting plugin, so it is unwound.
    assert recorder.hooks('on_execution_event') == []
    (error,) = recorder.hooks('after_execution')
    assert isinstance(error, RuntimeError)
    failed = [
        i
        for i in recorder.hooks('on_task_event')
        if i.to_state == TaskState.TASK_STATE_FAILED
    ]
    assert failed


@pytest.mark.asyncio
async def test_agent_error_reaches_after_execution():
    class Boom(AgentExecutor):
        async def execute(self, context, event_queue):
            raise ValueError('boom')

        async def cancel(self, context, event_queue):
            pass

    recorder = Recorder()
    handler = make_handler(recorder, executor=Boom())

    with pytest.raises(ValueError, match='boom'):
        await handler.on_message_send(send_request(), ServerCallContext())

    (error,) = recorder.hooks('after_execution')
    assert isinstance(error, ValueError)


@pytest.mark.asyncio
async def test_return_immediately_ends_request_before_execution():
    release = asyncio.Event()

    class SlowAgent(TaskAgent):
        async def execute(self, context, event_queue):
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
            await release.wait()
            await TaskUpdater(event_queue, task.id, task.context_id).complete()

    recorder = Recorder()
    handler = make_handler(recorder, executor=SlowAgent())

    result = await handler.on_message_send(
        send_request(
            configuration=SendMessageConfiguration(return_immediately=True)
        ),
        ServerCallContext(),
    )

    assert result.status.state == TaskState.TASK_STATE_SUBMITTED
    assert recorder.hooks('after_handler_run')[0].is_final
    assert 'after_execution' not in recorder.names()

    release.set()
    # after_execution (producer) and the final task write (consumer) run in
    # different tasks, so wait for both.
    await wait_for(
        lambda: (
            'after_execution' in recorder.names()
            and recorder.hooks('on_task_event')[-1].to_state
            == TaskState.TASK_STATE_COMPLETED
        )
    )


@pytest.mark.asyncio
async def test_remote_cancel_reports_task_event():
    recorder = Recorder()
    store = InMemoryTaskStore()
    context = ServerCallContext()
    await store.save(
        Task(
            id='t-remote',
            context_id='c-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        ),
        context,
    )
    handler = make_handler(recorder, task_store=store)

    result = await handler.on_cancel_task(
        CancelTaskRequest(id='t-remote'), context
    )

    assert result.status.state == TaskState.TASK_STATE_CANCELED
    (info,) = recorder.hooks('on_task_event')
    assert info.from_state == TaskState.TASK_STATE_WORKING
    assert info.to_state == TaskState.TASK_STATE_CANCELED
    assert info.event.status.state == TaskState.TASK_STATE_CANCELED


def push_setup(
    responses: dict[str, int],
    manager: plugins.PluginManager | None = None,
) -> tuple[
    InMemoryPushNotificationConfigStore,
    BasePushNotificationSender,
    list[httpx.Request],
]:
    received: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(responses.get(str(request.url), 200))

    config_store = InMemoryPushNotificationConfigStore()
    sender = BasePushNotificationSender(
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        config_store,
        plugins=manager,
    )
    return config_store, sender, received


def push_request(url: str) -> SendMessageRequest:
    return send_request(
        configuration=SendMessageConfiguration(
            task_push_notification_config=TaskPushNotificationConfig(
                url=url, token='tok'
            )
        )
    )


@pytest.mark.asyncio
async def test_push_hooks_run_for_v2_deliveries():
    recorder = Recorder()
    config_store, sender, received = push_setup(
        {'http://hook/fail': 500}, plugins.sequential(recorder)
    )
    handler = make_handler(
        recorder, push_config_store=config_store, push_sender=sender
    )

    await handler.on_message_send(
        push_request('http://hook/ok'), ServerCallContext()
    )
    await wait_for(lambda: len(recorder.hooks('on_push_sent')) >= 3)

    assert received
    assert all(r.headers['x-plugin'] == 'yes' for r in received)
    assert all(r.headers['x-a2a-notification-token'] == 'tok' for r in received)
    results = recorder.hooks('on_push_sent')
    assert all(r.status_code == 200 and r.error is None for r in results)
    assert all(r.duration_s >= 0 for r in results)
    assert all(r.request.url == 'http://hook/ok' for r in results)

    recorder.calls.clear()
    await handler.on_message_send(
        push_request('http://hook/fail'), ServerCallContext()
    )
    await wait_for(lambda: len(recorder.hooks('on_push_sent')) >= 1)
    failed = recorder.hooks('on_push_sent')[0]
    assert failed.status_code == 500
    assert isinstance(failed.error, httpx.HTTPStatusError)


@pytest.mark.asyncio
async def test_push_skip_and_rejection():
    class SkipAll(PushPlugin):
        async def before_push_send(self, request):
            request.skip = True

    class Fail(PushPlugin):
        async def before_push_send(self, request):
            raise RuntimeError('no')

    seen: list[PushResult] = []

    class Seen(PushPlugin):
        async def on_push_sent(self, result):
            seen.append(result)

    for plugin, check in (
        (SkipAll(), lambda r: r.skipped and r.error is None),
        (Fail(), lambda r: isinstance(r.error, RuntimeError)),
    ):
        seen.clear()
        config_store, sender, received = push_setup(
            {}, plugins.sequential(plugin, Seen())
        )
        handler = DefaultRequestHandlerV2(
            agent_executor=TaskAgent(),
            task_store=InMemoryTaskStore(),
            agent_card=CARD,
            push_config_store=config_store,
            push_sender=sender,
        )
        await handler.on_message_send(
            push_request('http://hook/x'), ServerCallContext()
        )
        await wait_for(lambda: len(seen) >= 1)
        assert received == []
        assert all(check(r) for r in seen)


@pytest.mark.asyncio
async def test_push_url_validator_rejection_runs_no_hooks():
    async def reject(url: str) -> bool:
        return False

    received: list[httpx.Request] = []
    recorder = Recorder()
    config_store = InMemoryPushNotificationConfigStore()
    sender = BasePushNotificationSender(
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: received.append(r) or httpx.Response(200)
            )
        ),
        config_store,
        push_url_validator=reject,
        plugins=plugins.sequential(recorder),
    )
    handler = make_handler(
        recorder, push_config_store=config_store, push_sender=sender
    )

    await handler.on_message_send(
        push_request('http://hook/x'), ServerCallContext()
    )
    await wait_for(lambda: 'after_execution' in recorder.names())
    await asyncio.sleep(0.05)

    assert received == []
    assert recorder.hooks('before_push_send') == []


@pytest.mark.asyncio
async def test_legacy_handler_runs_only_push_hooks():
    """The legacy handler is not wired for plugins; the sender is on its own."""
    recorder = Recorder()
    plugins.set_global(plugins.sequential(recorder))
    config_store, sender, received = push_setup({})
    handler = LegacyRequestHandler(
        agent_executor=TaskAgent(),
        task_store=InMemoryTaskStore(),
        agent_card=CARD,
        push_config_store=config_store,
        push_sender=sender,
    )

    result = await handler.on_message_send(
        push_request('http://hook/legacy'), ServerCallContext()
    )
    await wait_for(lambda: bool(recorder.hooks('on_push_sent')))

    assert result.status.state == TaskState.TASK_STATE_COMPLETED
    assert received
    assert all(r.headers['x-plugin'] == 'yes' for r in received)
    assert set(recorder.names()) == {'before_push_send', 'on_push_sent'}


def test_push_sender_uses_global_manager_unless_given_one():
    manager = plugins.sequential(Recorder())
    plugins.set_global(manager)
    other = plugins.sequential()

    default = BasePushNotificationSender(
        httpx.AsyncClient(), InMemoryPushNotificationConfigStore()
    )
    explicit = BasePushNotificationSender(
        httpx.AsyncClient(),
        InMemoryPushNotificationConfigStore(),
        plugins=other,
    )

    assert default._plugins is manager
    assert explicit._plugins is other


@pytest.mark.asyncio
async def test_handler_plugins_do_not_reach_a_sender_with_its_own_manager():
    recorder = Recorder()
    config_store, sender, received = push_setup({})  # global: no plugins
    handler = make_handler(
        recorder, push_config_store=config_store, push_sender=sender
    )

    await handler.on_message_send(
        push_request('http://hook/x'), ServerCallContext()
    )
    await wait_for(lambda: 'after_execution' in recorder.names())
    await asyncio.sleep(0.05)

    assert received
    assert 'x-plugin' not in received[0].headers
    assert recorder.hooks('before_push_send') == []
