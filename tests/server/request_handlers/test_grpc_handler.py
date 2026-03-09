from typing import Any
from unittest.mock import AsyncMock, MagicMock

import grpc
import grpc.aio
import pytest

from google.rpc import error_details_pb2, status_pb2
from a2a import types
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import GrpcHandler, RequestHandler
from a2a.types import a2a_pb2


# --- Fixtures ---


@pytest.fixture
def mock_request_handler() -> AsyncMock:
    return AsyncMock(spec=RequestHandler)


@pytest.fixture
def mock_grpc_context() -> AsyncMock:
    context = AsyncMock(spec=grpc.aio.ServicerContext)
    context.abort = AsyncMock()
    context.set_trailing_metadata = MagicMock()
    return context


@pytest.fixture
def sample_agent_card() -> types.AgentCard:
    return types.AgentCard(
        name='Test Agent',
        description='A test agent',
        supported_interfaces=[
            types.AgentInterface(
                protocol_binding='GRPC', url='http://localhost'
            )
        ],
        version='1.0.0',
        capabilities=types.AgentCapabilities(
            streaming=True, push_notifications=True
        ),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[],
    )


@pytest.fixture
def grpc_handler(
    mock_request_handler: AsyncMock, sample_agent_card: types.AgentCard
) -> GrpcHandler:
    return GrpcHandler(
        agent_card=sample_agent_card, request_handler=mock_request_handler
    )


# --- Test Cases ---


@pytest.mark.asyncio
async def test_send_message_success(
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test successful SendMessage call."""
    request_proto = a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(message_id='msg-1')
    )
    response_model = types.Task(
        id='task-1',
        context_id='ctx-1',
        status=types.TaskStatus(state=types.TaskState.TASK_STATE_COMPLETED),
    )
    mock_request_handler.on_message_send.return_value = response_model

    response = await grpc_handler.SendMessage(request_proto, mock_grpc_context)

    mock_request_handler.on_message_send.assert_awaited_once()
    assert isinstance(response, a2a_pb2.SendMessageResponse)
    assert response.HasField('task')
    assert response.task.id == 'task-1'


@pytest.mark.asyncio
async def test_send_message_server_error(
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test SendMessage call when handler raises an A2AError."""
    request_proto = a2a_pb2.SendMessageRequest()
    error = types.InvalidParamsError(message='Bad params')
    mock_request_handler.on_message_send.side_effect = error

    await grpc_handler.SendMessage(request_proto, mock_grpc_context)

    mock_grpc_context.abort.assert_awaited_once_with(
        grpc.StatusCode.INVALID_ARGUMENT, 'Bad params'
    )


@pytest.mark.asyncio
async def test_get_task_success(
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test successful GetTask call."""
    request_proto = a2a_pb2.GetTaskRequest(id='task-1')
    response_model = types.Task(
        id='task-1',
        context_id='ctx-1',
        status=types.TaskStatus(state=types.TaskState.TASK_STATE_WORKING),
    )
    mock_request_handler.on_get_task.return_value = response_model

    response = await grpc_handler.GetTask(request_proto, mock_grpc_context)

    mock_request_handler.on_get_task.assert_awaited_once()
    assert isinstance(response, a2a_pb2.Task)
    assert response.id == 'task-1'


@pytest.mark.asyncio
async def test_get_task_not_found(
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test GetTask call when task is not found."""
    request_proto = a2a_pb2.GetTaskRequest(id='task-1')
    mock_request_handler.on_get_task.return_value = None

    await grpc_handler.GetTask(request_proto, mock_grpc_context)

    mock_grpc_context.abort.assert_awaited_once_with(
        grpc.StatusCode.NOT_FOUND, 'Task not found'
    )


@pytest.mark.asyncio
async def test_cancel_task_server_error(
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test CancelTask call when handler raises A2AError."""
    request_proto = a2a_pb2.CancelTaskRequest(id='task-1')
    error = types.TaskNotCancelableError()
    mock_request_handler.on_cancel_task.side_effect = error

    await grpc_handler.CancelTask(request_proto, mock_grpc_context)

    mock_grpc_context.abort.assert_awaited_once_with(
        grpc.StatusCode.UNIMPLEMENTED,
        'Task cannot be canceled',
    )


@pytest.mark.asyncio
async def test_send_streaming_message(
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test successful SendStreamingMessage call."""

    async def mock_stream():
        yield types.Task(
            id='task-1',
            context_id='ctx-1',
            status=types.TaskStatus(state=types.TaskState.TASK_STATE_WORKING),
        )

    # Use MagicMock because on_message_send_stream is an async generator,
    # and we iterate over it directly. AsyncMock would return a coroutine.
    mock_request_handler.on_message_send_stream = MagicMock(
        return_value=mock_stream()
    )
    request_proto = a2a_pb2.SendMessageRequest()

    results = [
        result
        async for result in grpc_handler.SendStreamingMessage(
            request_proto, mock_grpc_context
        )
    ]

    assert len(results) == 1
    assert results[0].HasField('task')
    assert results[0].task.id == 'task-1'


@pytest.mark.asyncio
async def test_get_extended_agent_card(
    grpc_handler: GrpcHandler,
    sample_agent_card: types.AgentCard,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test GetExtendedAgentCard call."""
    request_proto = a2a_pb2.GetExtendedAgentCardRequest()
    response = await grpc_handler.GetExtendedAgentCard(
        request_proto, mock_grpc_context
    )

    assert response.name == sample_agent_card.name
    assert response.version == sample_agent_card.version


@pytest.mark.asyncio
async def test_get_extended_agent_card_with_modifier(
    mock_request_handler: AsyncMock,
    sample_agent_card: types.AgentCard,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test GetExtendedAgentCard call with a card_modifier."""

    async def modifier(card: types.AgentCard) -> types.AgentCard:
        modified_card = types.AgentCard()
        modified_card.CopyFrom(card)
        modified_card.name = 'Modified gRPC Agent'
        return modified_card

    grpc_handler_modified = GrpcHandler(
        agent_card=sample_agent_card,
        request_handler=mock_request_handler,
        card_modifier=modifier,
    )

    request_proto = a2a_pb2.GetExtendedAgentCardRequest()
    response = await grpc_handler_modified.GetExtendedAgentCard(
        request_proto, mock_grpc_context
    )

    assert response.name == 'Modified gRPC Agent'
    assert response.version == sample_agent_card.version


@pytest.mark.asyncio
async def test_get_agent_card_with_modifier_sync(
    mock_request_handler: AsyncMock,
    sample_agent_card: types.AgentCard,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test GetAgentCard call with a synchronous card_modifier."""

    def modifier(card: types.AgentCard) -> types.AgentCard:
        # For proto, we need to create a new message with modified fields
        modified_card = types.AgentCard()
        modified_card.CopyFrom(card)
        modified_card.name = 'Modified gRPC Agent'
        return modified_card

    grpc_handler_modified = GrpcHandler(
        agent_card=sample_agent_card,
        request_handler=mock_request_handler,
        card_modifier=modifier,
    )

    request_proto = a2a_pb2.GetExtendedAgentCardRequest()
    response = await grpc_handler_modified.GetExtendedAgentCard(
        request_proto, mock_grpc_context
    )

    assert response.name == 'Modified gRPC Agent'
    assert response.version == sample_agent_card.version


@pytest.mark.asyncio
async def test_list_tasks_success(
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
):
    """Test successful ListTasks call."""
    mock_request_handler.on_list_tasks.return_value = a2a_pb2.ListTasksResponse(
        next_page_token='123',
        tasks=[
            types.Task(
                id='task-1',
                context_id='ctx-1',
                status=types.TaskStatus(
                    state=types.TaskState.TASK_STATE_COMPLETED
                ),
            ),
            types.Task(
                id='task-2',
                context_id='ctx-1',
                status=types.TaskStatus(
                    state=types.TaskState.TASK_STATE_WORKING
                ),
            ),
        ],
    )

    response = await grpc_handler.ListTasks(
        a2a_pb2.ListTasksRequest(page_size=2), mock_grpc_context
    )

    mock_request_handler.on_list_tasks.assert_awaited_once()
    assert isinstance(response, a2a_pb2.ListTasksResponse)
    assert len(response.tasks) == 2
    assert response.tasks[0].id == 'task-1'
    assert response.tasks[1].id == 'task-2'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'a2a_error, grpc_status_code, error_message_part',
    [
        (
            types.InvalidRequestError(),
            grpc.StatusCode.INVALID_ARGUMENT,
            'InvalidRequestError',
        ),
        (
            types.MethodNotFoundError(),
            grpc.StatusCode.NOT_FOUND,
            'MethodNotFoundError',
        ),
        (
            types.InvalidParamsError(),
            grpc.StatusCode.INVALID_ARGUMENT,
            'InvalidParamsError',
        ),
        (
            types.InternalError(),
            grpc.StatusCode.INTERNAL,
            'InternalError',
        ),
        (
            types.TaskNotFoundError(),
            grpc.StatusCode.NOT_FOUND,
            'TaskNotFoundError',
        ),
        (
            types.TaskNotCancelableError(),
            grpc.StatusCode.UNIMPLEMENTED,
            'TaskNotCancelableError',
        ),
        (
            types.PushNotificationNotSupportedError(),
            grpc.StatusCode.UNIMPLEMENTED,
            'PushNotificationNotSupportedError',
        ),
        (
            types.UnsupportedOperationError(),
            grpc.StatusCode.UNIMPLEMENTED,
            'UnsupportedOperationError',
        ),
        (
            types.ContentTypeNotSupportedError(),
            grpc.StatusCode.UNIMPLEMENTED,
            'ContentTypeNotSupportedError',
        ),
        (
            types.InvalidAgentResponseError(),
            grpc.StatusCode.INTERNAL,
            'InvalidAgentResponseError',
        ),
    ],
)
async def test_abort_context_error_mapping(  # noqa: PLR0913
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
    a2a_error: Exception,
    grpc_status_code: grpc.StatusCode,
    error_message_part: str,
) -> None:
    mock_request_handler.on_get_task.side_effect = a2a_error
    request_proto = a2a_pb2.GetTaskRequest(id='any')
    await grpc_handler.GetTask(request_proto, mock_grpc_context)

    mock_grpc_context.abort.assert_awaited_once()
    call_args, _ = mock_grpc_context.abort.call_args
    assert call_args[0] == grpc_status_code

    # We shouldn't rely on the legacy ExceptionName: message string format
    # But for backward compatability fallback it shouldn't fail
    mock_grpc_context.set_trailing_metadata.assert_called_once()
    metadata = mock_grpc_context.set_trailing_metadata.call_args[0][0]

    assert any(key == 'grpc-status-details-bin' for key, _ in metadata)


@pytest.mark.asyncio
async def test_abort_context_rich_error_format(
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:

    error = types.TaskNotFoundError('Could not find the task')
    mock_request_handler.on_get_task.side_effect = error
    request_proto = a2a_pb2.GetTaskRequest(id='any')
    await grpc_handler.GetTask(request_proto, mock_grpc_context)

    mock_grpc_context.set_trailing_metadata.assert_called_once()
    metadata = mock_grpc_context.set_trailing_metadata.call_args[0][0]

    bin_values = [v for k, v in metadata if k == 'grpc-status-details-bin']
    assert len(bin_values) == 1

    status = status_pb2.Status.FromString(bin_values[0])
    assert status.code == grpc.StatusCode.NOT_FOUND.value[0]
    assert status.message == 'Could not find the task'

    assert len(status.details) == 1

    error_info = error_details_pb2.ErrorInfo()
    status.details[0].Unpack(error_info)

    assert error_info.reason == 'TASK_NOT_FOUND'
    assert error_info.domain == 'a2a-protocol.org'


@pytest.mark.asyncio
class TestGrpcExtensions:
    async def test_send_message_with_extensions(
        self,
        grpc_handler: GrpcHandler,
        mock_request_handler: AsyncMock,
        mock_grpc_context: AsyncMock,
    ) -> None:
        mock_grpc_context.invocation_metadata.return_value = grpc.aio.Metadata(
            (HTTP_EXTENSION_HEADER.lower(), 'foo'),
            (HTTP_EXTENSION_HEADER.lower(), 'bar'),
        )

        def side_effect(request, context: ServerCallContext):
            context.activated_extensions.add('foo')
            context.activated_extensions.add('baz')
            return types.Task(
                id='task-1',
                context_id='ctx-1',
                status=types.TaskStatus(
                    state=types.TaskState.TASK_STATE_COMPLETED
                ),
            )

        mock_request_handler.on_message_send.side_effect = side_effect

        await grpc_handler.SendMessage(
            a2a_pb2.SendMessageRequest(), mock_grpc_context
        )

        mock_request_handler.on_message_send.assert_awaited_once()
        call_context = mock_request_handler.on_message_send.call_args[0][1]
        assert isinstance(call_context, ServerCallContext)
        assert call_context.requested_extensions == {'foo', 'bar'}

        mock_grpc_context.set_trailing_metadata.assert_called_once()
        called_metadata = (
            mock_grpc_context.set_trailing_metadata.call_args.args[0]
        )
        assert set(called_metadata) == {
            (HTTP_EXTENSION_HEADER.lower(), 'foo'),
            (HTTP_EXTENSION_HEADER.lower(), 'baz'),
        }

    async def test_send_message_with_comma_separated_extensions(
        self,
        grpc_handler: GrpcHandler,
        mock_request_handler: AsyncMock,
        mock_grpc_context: AsyncMock,
    ) -> None:
        mock_grpc_context.invocation_metadata.return_value = grpc.aio.Metadata(
            (HTTP_EXTENSION_HEADER.lower(), 'foo ,, bar,'),
            (HTTP_EXTENSION_HEADER.lower(), 'baz  , bar'),
        )
        mock_request_handler.on_message_send.return_value = types.Message(
            message_id='1',
            role=types.Role.ROLE_AGENT,
            parts=[types.Part(text='test')],
        )

        await grpc_handler.SendMessage(
            a2a_pb2.SendMessageRequest(), mock_grpc_context
        )

        mock_request_handler.on_message_send.assert_awaited_once()
        call_context = mock_request_handler.on_message_send.call_args[0][1]
        assert isinstance(call_context, ServerCallContext)
        assert call_context.requested_extensions == {'foo', 'bar', 'baz'}

    async def test_send_streaming_message_with_extensions(
        self,
        grpc_handler: GrpcHandler,
        mock_request_handler: AsyncMock,
        mock_grpc_context: AsyncMock,
    ) -> None:
        mock_grpc_context.invocation_metadata.return_value = grpc.aio.Metadata(
            (HTTP_EXTENSION_HEADER.lower(), 'foo'),
            (HTTP_EXTENSION_HEADER.lower(), 'bar'),
        )

        async def side_effect(request, context: ServerCallContext):
            context.activated_extensions.add('foo')
            context.activated_extensions.add('baz')
            yield types.Task(
                id='task-1',
                context_id='ctx-1',
                status=types.TaskStatus(
                    state=types.TaskState.TASK_STATE_WORKING
                ),
            )

        mock_request_handler.on_message_send_stream.side_effect = side_effect

        results = [
            result
            async for result in grpc_handler.SendStreamingMessage(
                a2a_pb2.SendMessageRequest(), mock_grpc_context
            )
        ]
        assert results

        mock_request_handler.on_message_send_stream.assert_called_once()
        call_context = mock_request_handler.on_message_send_stream.call_args[0][
            1
        ]
        assert isinstance(call_context, ServerCallContext)
        assert call_context.requested_extensions == {'foo', 'bar'}

        mock_grpc_context.set_trailing_metadata.assert_called_once()
        called_metadata = (
            mock_grpc_context.set_trailing_metadata.call_args.args[0]
        )
        assert set(called_metadata) == {
            (HTTP_EXTENSION_HEADER.lower(), 'foo'),
            (HTTP_EXTENSION_HEADER.lower(), 'baz'),
        }


@pytest.mark.asyncio
class TestTenantExtraction:
    @pytest.mark.parametrize(
        'method_name, request_proto, handler_method_name, return_value',
        [
            (
                'SendMessage',
                a2a_pb2.SendMessageRequest(tenant='my-tenant'),
                'on_message_send',
                types.Message(),
            ),
            (
                'CancelTask',
                a2a_pb2.CancelTaskRequest(tenant='my-tenant', id='1'),
                'on_cancel_task',
                types.Task(id='1'),
            ),
            (
                'GetTask',
                a2a_pb2.GetTaskRequest(tenant='my-tenant', id='1'),
                'on_get_task',
                types.Task(id='1'),
            ),
            (
                'ListTasks',
                a2a_pb2.ListTasksRequest(tenant='my-tenant'),
                'on_list_tasks',
                a2a_pb2.ListTasksResponse(),
            ),
            (
                'GetTaskPushNotificationConfig',
                a2a_pb2.GetTaskPushNotificationConfigRequest(
                    tenant='my-tenant', task_id='1', id='c1'
                ),
                'on_get_task_push_notification_config',
                a2a_pb2.TaskPushNotificationConfig(),
            ),
            (
                'CreateTaskPushNotificationConfig',
                a2a_pb2.CreateTaskPushNotificationConfigRequest(
                    tenant='my-tenant',
                    task_id='1',
                    config=a2a_pb2.PushNotificationConfig(),
                ),
                'on_create_task_push_notification_config',
                a2a_pb2.TaskPushNotificationConfig(),
            ),
            (
                'ListTaskPushNotificationConfigs',
                a2a_pb2.ListTaskPushNotificationConfigsRequest(
                    tenant='my-tenant', task_id='1'
                ),
                'on_list_task_push_notification_configs',
                a2a_pb2.ListTaskPushNotificationConfigsResponse(),
            ),
            (
                'DeleteTaskPushNotificationConfig',
                a2a_pb2.DeleteTaskPushNotificationConfigRequest(
                    tenant='my-tenant', task_id='1', id='c1'
                ),
                'on_delete_task_push_notification_config',
                None,
            ),
        ],
    )
    async def test_non_streaming_tenant_extraction(
        self,
        grpc_handler: GrpcHandler,
        mock_request_handler: AsyncMock,
        mock_grpc_context: AsyncMock,
        method_name: str,
        request_proto: Any,
        handler_method_name: str,
        return_value: Any,
    ) -> None:
        handler_mock = getattr(mock_request_handler, handler_method_name)
        handler_mock.return_value = return_value

        grpc_method = getattr(grpc_handler, method_name)
        await grpc_method(request_proto, mock_grpc_context)

        handler_mock.assert_awaited_once()
        call_args = handler_mock.call_args
        server_context = call_args[0][1]
        assert isinstance(server_context, ServerCallContext)
        assert server_context.tenant == 'my-tenant'

    @pytest.mark.parametrize(
        'method_name, request_proto, handler_method_name',
        [
            (
                'SendStreamingMessage',
                a2a_pb2.SendMessageRequest(tenant='my-tenant'),
                'on_message_send_stream',
            ),
            (
                'SubscribeToTask',
                a2a_pb2.SubscribeToTaskRequest(tenant='my-tenant', id='1'),
                'on_subscribe_to_task',
            ),
        ],
    )
    async def test_streaming_tenant_extraction(
        self,
        grpc_handler: GrpcHandler,
        mock_request_handler: AsyncMock,
        mock_grpc_context: AsyncMock,
        method_name: str,
        request_proto: Any,
        handler_method_name: str,
    ) -> None:
        async def mock_stream(*args, **kwargs):
            yield types.Message(message_id='msg-1')

        handler_mock_attr = MagicMock(return_value=mock_stream())
        setattr(mock_request_handler, handler_method_name, handler_mock_attr)

        grpc_method = getattr(grpc_handler, method_name)

        async for _ in grpc_method(request_proto, mock_grpc_context):
            pass

        handler_mock_attr.assert_called_once()
        call_args = handler_mock_attr.call_args
        server_context = call_args[0][1]
        assert isinstance(server_context, ServerCallContext)
        assert server_context.tenant == 'my-tenant'

    @pytest.mark.parametrize(
        'method_name, request_proto, handler_method_name, return_value',
        [
            (
                'SendMessage',
                a2a_pb2.SendMessageRequest(),
                'on_message_send',
                types.Message(),
            ),
            (
                'CancelTask',
                a2a_pb2.CancelTaskRequest(id='1'),
                'on_cancel_task',
                types.Task(id='1'),
            ),
            (
                'GetTask',
                a2a_pb2.GetTaskRequest(id='1'),
                'on_get_task',
                types.Task(id='1'),
            ),
            (
                'ListTasks',
                a2a_pb2.ListTasksRequest(),
                'on_list_tasks',
                a2a_pb2.ListTasksResponse(),
            ),
            (
                'GetTaskPushNotificationConfig',
                a2a_pb2.GetTaskPushNotificationConfigRequest(
                    task_id='1', id='c1'
                ),
                'on_get_task_push_notification_config',
                a2a_pb2.TaskPushNotificationConfig(),
            ),
            (
                'CreateTaskPushNotificationConfig',
                a2a_pb2.CreateTaskPushNotificationConfigRequest(
                    task_id='1',
                    config=a2a_pb2.PushNotificationConfig(),
                ),
                'on_create_task_push_notification_config',
                a2a_pb2.TaskPushNotificationConfig(),
            ),
            (
                'ListTaskPushNotificationConfigs',
                a2a_pb2.ListTaskPushNotificationConfigsRequest(task_id='1'),
                'on_list_task_push_notification_configs',
                a2a_pb2.ListTaskPushNotificationConfigsResponse(),
            ),
            (
                'DeleteTaskPushNotificationConfig',
                a2a_pb2.DeleteTaskPushNotificationConfigRequest(
                    task_id='1', id='c1'
                ),
                'on_delete_task_push_notification_config',
                None,
            ),
        ],
    )
    async def test_non_streaming_no_tenant_extraction(
        self,
        grpc_handler: GrpcHandler,
        mock_request_handler: AsyncMock,
        mock_grpc_context: AsyncMock,
        method_name: str,
        request_proto: Any,
        handler_method_name: str,
        return_value: Any,
    ) -> None:
        handler_mock = getattr(mock_request_handler, handler_method_name)
        handler_mock.return_value = return_value

        grpc_method = getattr(grpc_handler, method_name)
        await grpc_method(request_proto, mock_grpc_context)

        handler_mock.assert_awaited_once()
        call_args = handler_mock.call_args
        server_context = call_args[0][1]
        assert isinstance(server_context, ServerCallContext)
        assert server_context.tenant == ''

    @pytest.mark.parametrize(
        'method_name, request_proto, handler_method_name',
        [
            (
                'SendStreamingMessage',
                a2a_pb2.SendMessageRequest(),
                'on_message_send_stream',
            ),
            (
                'SubscribeToTask',
                a2a_pb2.SubscribeToTaskRequest(id='1'),
                'on_subscribe_to_task',
            ),
        ],
    )
    async def test_streaming_no_tenant_extraction(
        self,
        grpc_handler: GrpcHandler,
        mock_request_handler: AsyncMock,
        mock_grpc_context: AsyncMock,
        method_name: str,
        request_proto: Any,
        handler_method_name: str,
    ) -> None:
        async def mock_stream(*args, **kwargs):
            yield types.Message(message_id='msg-1')

        handler_mock_attr = MagicMock(return_value=mock_stream())
        setattr(mock_request_handler, handler_method_name, handler_mock_attr)

        grpc_method = getattr(grpc_handler, method_name)

        async for _ in grpc_method(request_proto, mock_grpc_context):
            pass

        handler_mock_attr.assert_called_once()
        call_args = handler_mock_attr.call_args
        server_context = call_args[0][1]
        assert isinstance(server_context, ServerCallContext)
        assert server_context.tenant == ''
