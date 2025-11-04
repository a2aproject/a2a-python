from unittest.mock import AsyncMock, MagicMock

import grpc
import grpc.aio
import pytest

from a2a import types
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.grpc import a2a_pb2
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import GrpcHandler, RequestHandler
from a2a.utils.errors import ServerError


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
        url='http://localhost',
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
        request=a2a_pb2.Message(message_id='msg-1')
    )
    response_model = types.Task(
        id='task-1',
        context_id='ctx-1',
        status=types.TaskStatus(state=types.TaskState.completed),
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
    """Test SendMessage call when handler raises a ServerError."""
    request_proto = a2a_pb2.SendMessageRequest()
    error = ServerError(error=types.InvalidParamsError(message='Bad params'))
    mock_request_handler.on_message_send.side_effect = error

    await grpc_handler.SendMessage(request_proto, mock_grpc_context)

    mock_grpc_context.abort.assert_awaited_once_with(
        grpc.StatusCode.INVALID_ARGUMENT, 'InvalidParamsError: Bad params'
    )


@pytest.mark.asyncio
async def test_get_task_success(
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test successful GetTask call."""
    request_proto = a2a_pb2.GetTaskRequest(name='tasks/task-1')
    response_model = types.Task(
        id='task-1',
        context_id='ctx-1',
        status=types.TaskStatus(state=types.TaskState.working),
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
    request_proto = a2a_pb2.GetTaskRequest(name='tasks/task-1')
    mock_request_handler.on_get_task.return_value = None

    await grpc_handler.GetTask(request_proto, mock_grpc_context)

    mock_grpc_context.abort.assert_awaited_once_with(
        grpc.StatusCode.NOT_FOUND, 'TaskNotFoundError: Task not found'
    )


@pytest.mark.asyncio
async def test_cancel_task_server_error(
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test CancelTask call when handler raises ServerError."""
    request_proto = a2a_pb2.CancelTaskRequest(name='tasks/task-1')
    error = ServerError(error=types.TaskNotCancelableError())
    mock_request_handler.on_cancel_task.side_effect = error

    await grpc_handler.CancelTask(request_proto, mock_grpc_context)

    mock_grpc_context.abort.assert_awaited_once_with(
        grpc.StatusCode.UNIMPLEMENTED,
        'TaskNotCancelableError: Task cannot be canceled',
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
            status=types.TaskStatus(state=types.TaskState.working),
        )

    mock_request_handler.on_message_send_stream.return_value = mock_stream()
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
async def test_get_agent_card(
    grpc_handler: GrpcHandler,
    sample_agent_card: types.AgentCard,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test GetAgentCard call."""
    request_proto = a2a_pb2.GetAgentCardRequest()
    response = await grpc_handler.GetAgentCard(request_proto, mock_grpc_context)

    assert response.name == sample_agent_card.name
    assert response.version == sample_agent_card.version


@pytest.mark.asyncio
async def test_get_agent_card_with_modifier(
    mock_request_handler: AsyncMock,
    sample_agent_card: types.AgentCard,
    mock_grpc_context: AsyncMock,
) -> None:
    """Test GetAgentCard call with a card_modifier."""

    def modifier(card: types.AgentCard) -> types.AgentCard:
        modified_card = card.model_copy(deep=True)
        modified_card.name = 'Modified gRPC Agent'
        return modified_card

    grpc_handler_modified = GrpcHandler(
        agent_card=sample_agent_card,
        request_handler=mock_request_handler,
        card_modifier=modifier,
    )

    request_proto = a2a_pb2.GetAgentCardRequest()
    response = await grpc_handler_modified.GetAgentCard(
        request_proto, mock_grpc_context
    )

    assert response.name == 'Modified gRPC Agent'
    assert response.version == sample_agent_card.version


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'server_error, grpc_status_code, error_message_part',
    [
        (
            ServerError(error=types.JSONParseError()),
            grpc.StatusCode.INTERNAL,
            'JSONParseError',
        ),
        (
            ServerError(error=types.InvalidRequestError()),
            grpc.StatusCode.INVALID_ARGUMENT,
            'InvalidRequestError',
        ),
        (
            ServerError(error=types.MethodNotFoundError()),
            grpc.StatusCode.NOT_FOUND,
            'MethodNotFoundError',
        ),
        (
            ServerError(error=types.InvalidParamsError()),
            grpc.StatusCode.INVALID_ARGUMENT,
            'InvalidParamsError',
        ),
        (
            ServerError(error=types.InternalError()),
            grpc.StatusCode.INTERNAL,
            'InternalError',
        ),
        (
            ServerError(error=types.TaskNotFoundError()),
            grpc.StatusCode.NOT_FOUND,
            'TaskNotFoundError',
        ),
        (
            ServerError(error=types.TaskNotCancelableError()),
            grpc.StatusCode.UNIMPLEMENTED,
            'TaskNotCancelableError',
        ),
        (
            ServerError(error=types.PushNotificationNotSupportedError()),
            grpc.StatusCode.UNIMPLEMENTED,
            'PushNotificationNotSupportedError',
        ),
        (
            ServerError(error=types.UnsupportedOperationError()),
            grpc.StatusCode.UNIMPLEMENTED,
            'UnsupportedOperationError',
        ),
        (
            ServerError(error=types.ContentTypeNotSupportedError()),
            grpc.StatusCode.UNIMPLEMENTED,
            'ContentTypeNotSupportedError',
        ),
        (
            ServerError(error=types.InvalidAgentResponseError()),
            grpc.StatusCode.INTERNAL,
            'InvalidAgentResponseError',
        ),
        (
            ServerError(error=types.JSONRPCError(code=99, message='Unknown')),
            grpc.StatusCode.UNKNOWN,
            'Unknown error',
        ),
    ],
)
async def test_abort_context_error_mapping(  # noqa: PLR0913
    grpc_handler: GrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
    server_error: ServerError,
    grpc_status_code: grpc.StatusCode,
    error_message_part: str,
) -> None:
    mock_request_handler.on_get_task.side_effect = server_error
    request_proto = a2a_pb2.GetTaskRequest(name='tasks/any')
    await grpc_handler.GetTask(request_proto, mock_grpc_context)

    mock_grpc_context.abort.assert_awaited_once()
    call_args, _ = mock_grpc_context.abort.call_args
    assert call_args[0] == grpc_status_code
    assert error_message_part in call_args[1]


@pytest.mark.asyncio
class TestGrpcExtensions:
    async def test_send_message_with_extensions(
        self,
        grpc_handler: GrpcHandler,
        mock_request_handler: AsyncMock,
        mock_grpc_context: AsyncMock,
    ) -> None:
        mock_grpc_context.invocation_metadata = grpc.aio.Metadata(
            (HTTP_EXTENSION_HEADER, 'foo'),
            (HTTP_EXTENSION_HEADER, 'bar'),
        )

        def side_effect(request, context: ServerCallContext):
            context.activated_extensions.add('foo')
            context.activated_extensions.add('baz')
            return types.Task(
                id='task-1',
                context_id='ctx-1',
                status=types.TaskStatus(state=types.TaskState.completed),
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
            (HTTP_EXTENSION_HEADER, 'foo'),
            (HTTP_EXTENSION_HEADER, 'baz'),
        }

    async def test_send_message_with_comma_separated_extensions(
        self,
        grpc_handler: GrpcHandler,
        mock_request_handler: AsyncMock,
        mock_grpc_context: AsyncMock,
    ) -> None:
        mock_grpc_context.invocation_metadata = grpc.aio.Metadata(
            (HTTP_EXTENSION_HEADER, 'foo ,, bar,'),
            (HTTP_EXTENSION_HEADER, 'baz  , bar'),
        )
        mock_request_handler.on_message_send.return_value = types.Message(
            message_id='1',
            role=types.Role.agent,
            parts=[types.Part(root=types.TextPart(text='test'))],
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
        mock_grpc_context.invocation_metadata = grpc.aio.Metadata(
            (HTTP_EXTENSION_HEADER, 'foo'),
            (HTTP_EXTENSION_HEADER, 'bar'),
        )

        async def side_effect(request, context: ServerCallContext):
            context.activated_extensions.add('foo')
            context.activated_extensions.add('baz')
            yield types.Task(
                id='task-1',
                context_id='ctx-1',
                status=types.TaskStatus(state=types.TaskState.working),
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
            (HTTP_EXTENSION_HEADER, 'foo'),
            (HTTP_EXTENSION_HEADER, 'baz'),
        }
