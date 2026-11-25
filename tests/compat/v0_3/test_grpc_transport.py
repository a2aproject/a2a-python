from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError
from a2a.client.optionals import Channel
from a2a.compat.v0_3 import a2a_v0_3_pb2, a2a_v0_3_pb2_grpc
from a2a.compat.v0_3.grpc_transport import CompatGrpcTransport
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    Message,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    SubscribeToTaskRequest,
    TaskPushNotificationConfig,
)
from a2a.utils.errors import (
    A2A_ERROR_REASONS,
    InvalidParamsError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from google.protobuf import any_pb2, empty_pb2
from google.rpc import error_details_pb2, status_pb2


def _rpc_error(
    code: grpc.StatusCode, message: str, reason: str | None = None
) -> grpc.aio.AioRpcError:
    trailing = grpc.aio.Metadata()
    if reason:
        status = status_pb2.Status(code=code.value[0], message=message)
        detail = any_pb2.Any()
        detail.Pack(
            error_details_pb2.ErrorInfo(
                reason=reason, domain='a2a-protocol.org'
            )
        )
        status.details.append(detail)
        trailing = grpc.aio.Metadata(
            ('grpc-status-details-bin', status.SerializeToString()),
        )
    return grpc.aio.AioRpcError(
        code=code,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=trailing,
        details=message,
    )


def _transport_raising(error: grpc.aio.AioRpcError) -> CompatGrpcTransport:
    transport = CompatGrpcTransport(
        channel=AsyncMock(spec=Channel), agent_card=None
    )
    transport.stub = MagicMock()
    transport.stub.GetTask = AsyncMock(side_effect=error)
    return transport


@pytest.mark.asyncio
async def test_compat_grpc_error_mapped_from_error_info_reason():
    """A v0.3 server that is not a2a-python (a2a-go) names the error in
    ErrorInfo.reason, not in a "ClassName: message" status message."""
    transport = _transport_raising(
        _rpc_error(
            grpc.StatusCode.NOT_FOUND,
            'failed to get task: task not found',
            reason='TASK_NOT_FOUND',
        )
    )

    with pytest.raises(TaskNotFoundError) as excinfo:
        await transport.get_task(GetTaskRequest(id='missing'))

    assert str(excinfo.value) == 'failed to get task: task not found'


@pytest.mark.asyncio
async def test_compat_grpc_error_mapped_from_class_name_message():
    transport = _transport_raising(
        _rpc_error(
            grpc.StatusCode.FAILED_PRECONDITION,
            'UnsupportedOperationError: not supported',
        )
    )

    with pytest.raises(UnsupportedOperationError) as excinfo:
        await transport.get_task(GetTaskRequest(id='t1'))

    assert str(excinfo.value) == 'not supported'


@pytest.mark.asyncio
async def test_compat_grpc_error_unmapped_stays_client_error():
    transport = _transport_raising(
        _rpc_error(grpc.StatusCode.UNAVAILABLE, 'connection refused')
    )

    with pytest.raises(A2AClientError) as excinfo:
        await transport.get_task(GetTaskRequest(id='t1'))

    assert type(excinfo.value) is A2AClientError


@pytest.mark.asyncio
async def test_compat_grpc_transport_send_message_response_msg_parsing():
    mock_channel = AsyncMock(spec=Channel)
    transport = CompatGrpcTransport(channel=mock_channel, agent_card=None)

    mock_stub = MagicMock()

    expected_resp = a2a_v0_3_pb2.SendMessageResponse(
        msg=a2a_v0_3_pb2.Message(
            message_id='msg-123', role=a2a_v0_3_pb2.Role.ROLE_AGENT
        )
    )

    mock_stub.SendMessage = AsyncMock(return_value=expected_resp)
    transport.stub = mock_stub

    req = SendMessageRequest(
        message=Message(message_id='msg-1', role=Role.ROLE_USER)
    )

    response = await transport.send_message(req)

    assert isinstance(response, SendMessageResponse)
    assert response.HasField('message')
    assert response.message.message_id == 'msg-123'

    sent = mock_stub.SendMessage.call_args.args[0]
    assert not sent.HasField('configuration')


def test_compat_grpc_transport_mirrors_extension_metadata():
    """Compat gRPC client must also emit the legacy x-a2a-extensions metadata
    so that v0.3 servers (which only know that name) understand the request."""
    transport = CompatGrpcTransport(
        channel=AsyncMock(spec=Channel), agent_card=None
    )
    context = ClientCallContext(
        service_parameters={'A2A-Extensions': 'foo,bar'}
    )

    metadata = dict(transport._get_grpc_metadata(context))

    assert metadata['a2a-extensions'] == 'foo,bar'
    assert metadata['x-a2a-extensions'] == 'foo,bar'


def test_compat_grpc_transport_no_extension_metadata():
    transport = CompatGrpcTransport(
        channel=AsyncMock(spec=Channel), agent_card=None
    )

    metadata = dict(transport._get_grpc_metadata(None))

    assert 'a2a-extensions' not in metadata
    assert 'x-a2a-extensions' not in metadata


def _unary(response: object) -> AsyncMock:
    return AsyncMock(return_value=response)


def _stream() -> MagicMock:
    call = MagicMock()
    call.read = AsyncMock(return_value=grpc.aio.EOF)
    return MagicMock(return_value=call)


_PUSH_CONFIG_NAME = 'tasks/t1/pushNotificationConfigs/c1'


@pytest.mark.parametrize(
    ('method', 'stub_method', 'stub', 'request_obj'),
    [
        (
            'send_message',
            'SendMessage',
            lambda: _unary(a2a_v0_3_pb2.SendMessageResponse()),
            SendMessageRequest(message=Message(message_id='m1')),
        ),
        (
            'send_message_streaming',
            'SendStreamingMessage',
            _stream,
            SendMessageRequest(message=Message(message_id='m1')),
        ),
        (
            'subscribe',
            'TaskSubscription',
            _stream,
            SubscribeToTaskRequest(id='t1'),
        ),
        (
            'get_task',
            'GetTask',
            lambda: _unary(a2a_v0_3_pb2.Task(id='t1')),
            GetTaskRequest(id='t1'),
        ),
        (
            'cancel_task',
            'CancelTask',
            lambda: _unary(a2a_v0_3_pb2.Task(id='t1')),
            CancelTaskRequest(id='t1'),
        ),
        (
            'create_task_push_notification_config',
            'CreateTaskPushNotificationConfig',
            lambda: _unary(
                a2a_v0_3_pb2.TaskPushNotificationConfig(name=_PUSH_CONFIG_NAME)
            ),
            TaskPushNotificationConfig(
                task_id='t1', id='c1', url='https://example.com/hook'
            ),
        ),
        (
            'get_task_push_notification_config',
            'GetTaskPushNotificationConfig',
            lambda: _unary(
                a2a_v0_3_pb2.TaskPushNotificationConfig(name=_PUSH_CONFIG_NAME)
            ),
            GetTaskPushNotificationConfigRequest(task_id='t1', id='c1'),
        ),
        (
            'list_task_push_notification_configs',
            'ListTaskPushNotificationConfig',
            lambda: _unary(
                a2a_v0_3_pb2.ListTaskPushNotificationConfigResponse()
            ),
            ListTaskPushNotificationConfigsRequest(task_id='t1'),
        ),
        (
            'delete_task_push_notification_config',
            'DeleteTaskPushNotificationConfig',
            lambda: _unary(empty_pb2.Empty()),
            DeleteTaskPushNotificationConfigRequest(task_id='t1', id='c1'),
        ),
        (
            'get_extended_agent_card',
            'GetAgentCard',
            lambda: _unary(
                a2a_v0_3_pb2.AgentCard(name='agent', url='http://agent')
            ),
            GetExtendedAgentCardRequest(),
        ),
    ],
)
@pytest.mark.asyncio
async def test_compat_grpc_transport_passes_context_timeout(
    method: str,
    stub_method: str,
    stub: Callable[[], MagicMock],
    request_obj: object,
) -> None:
    """The per-call timeout in ClientCallContext must reach the gRPC stub,
    as it does for the v1.0 GrpcTransport, or a call to a slow v0.3 server
    never times out."""
    transport = CompatGrpcTransport(
        channel=AsyncMock(spec=Channel), agent_card=None
    )
    transport.stub = MagicMock()
    stub_mock = stub()
    setattr(transport.stub, stub_method, stub_mock)
    context = ClientCallContext(timeout=12.5)

    result = getattr(transport, method)(request_obj, context=context)
    if hasattr(result, '__aiter__'):
        async for _ in result:
            pass
    else:
        await result

    stub_mock.assert_called_once()
    assert stub_mock.call_args.kwargs['timeout'] == 12.5


@pytest.mark.asyncio
@pytest.mark.timeout(10)
@pytest.mark.parametrize('streaming', [False, True], ids=['unary', 'stream'])
@pytest.mark.parametrize('reverse_details', [False, True])
@pytest.mark.parametrize(
    'case', ['metadata', 'validation', 'validation_only', 'foreign', 'empty']
)
async def test_compat_grpc_error_metadata(
    streaming: bool, reverse_details: bool, case: str
) -> None:
    """ErrorInfo metadata and BadRequest violations are preserved in A2AError.data."""
    error_cls = (
        InvalidParamsError
        if case in ('validation', 'validation_only')
        else TaskNotFoundError
    )
    code = (
        grpc.StatusCode.INVALID_ARGUMENT
        if case in ('validation', 'validation_only')
        else grpc.StatusCode.NOT_FOUND
    )
    metadata: dict[str, str] = (
        {} if case in ('empty', 'validation_only') else {'taskId': 'job-1'}
    )
    expected: dict[str, Any] | None = dict(metadata) or None
    error_info = error_details_pb2.ErrorInfo(
        reason=A2A_ERROR_REASONS[error_cls],
        domain='a2a-protocol.org',
        metadata=metadata,
    )
    details = [any_pb2.Any()]
    details[0].Pack(error_info)

    if case == 'validation':
        error_info.metadata['errors'] = 'must-not-replace-field-violations'
        details[0].Pack(error_info)
        bad_request = error_details_pb2.BadRequest()
        bad_request.field_violations.add(field='id', description='invalid id')
        detail = any_pb2.Any()
        detail.Pack(bad_request)
        details.append(detail)
        expected = {
            **metadata,
            'errors': [{'field': 'id', 'message': 'invalid id'}],
        }
    elif case == 'validation_only':
        bad_request = error_details_pb2.BadRequest()
        bad_request.field_violations.add(field='id', description='invalid id')
        detail = any_pb2.Any()
        detail.Pack(bad_request)
        details.append(detail)
        expected = {'errors': [{'field': 'id', 'message': 'invalid id'}]}
    elif case == 'foreign':
        detail = any_pb2.Any()
        detail.Pack(
            error_details_pb2.ErrorInfo(
                reason=A2A_ERROR_REASONS[error_cls],
                domain='provider.example',
                metadata={'provider': 'not-A2A-data'},
            )
        )
        details.append(detail)

    if reverse_details:
        details.reverse()

    message = 'Cannot access task'
    status = status_pb2.Status(
        code=code.value[0], message=message, details=details
    )
    trailing = (('grpc-status-details-bin', status.SerializeToString()),)

    class RejectingV03Agent(a2a_v0_3_pb2_grpc.A2AServiceServicer):
        async def GetTask(
            self,
            request: a2a_v0_3_pb2.GetTaskRequest,
            context: grpc.aio.ServicerContext,
        ) -> a2a_v0_3_pb2.Task:
            context.set_trailing_metadata(trailing)
            await context.abort(code, message)
            return a2a_v0_3_pb2.Task()

        async def TaskSubscription(
            self,
            request: a2a_v0_3_pb2.TaskSubscriptionRequest,
            context: grpc.aio.ServicerContext,
        ) -> AsyncIterator[a2a_v0_3_pb2.StreamResponse]:
            context.set_trailing_metadata(trailing)
            await context.abort(code, message)
            yield a2a_v0_3_pb2.StreamResponse()

    server = grpc.aio.server()
    a2a_v0_3_pb2_grpc.add_A2AServiceServicer_to_server(
        RejectingV03Agent(), server
    )
    port = server.add_insecure_port('127.0.0.1:0')
    assert port > 0
    await server.start()
    try:
        async with grpc.aio.insecure_channel(
            f'127.0.0.1:{port}', options=[('grpc.enable_http_proxy', 0)]
        ) as channel:
            transport = CompatGrpcTransport(channel, None)
            with pytest.raises(error_cls) as exc:
                if streaming:
                    _ = [
                        event
                        async for event in transport.subscribe(
                            SubscribeToTaskRequest(id='job-1')
                        )
                    ]
                else:
                    await transport.get_task(GetTaskRequest(id='job-1'))
            assert str(exc.value) == message
            assert exc.value.data == expected
    finally:
        await server.stop(None)


@pytest.mark.asyncio
async def test_compat_grpc_error_bad_request_without_error_info_stays_client_error() -> (
    None
):
    """A BadRequest detail without a matching A2A ErrorInfo keeps A2AClientError."""
    bad_request = error_details_pb2.BadRequest()
    bad_request.field_violations.add(field='id', description='invalid id')
    detail = any_pb2.Any()
    detail.Pack(bad_request)
    status = status_pb2.Status(
        code=grpc.StatusCode.INVALID_ARGUMENT.value[0],
        message='bad argument',
        details=[detail],
    )
    error = grpc.aio.AioRpcError(
        code=grpc.StatusCode.INVALID_ARGUMENT,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=grpc.aio.Metadata(
            ('grpc-status-details-bin', status.SerializeToString()),
        ),
        details='bad argument',
    )
    transport = _transport_raising(error)

    with pytest.raises(A2AClientError) as exc_info:
        await transport.get_task(GetTaskRequest(id='t1'))

    assert type(exc_info.value) is A2AClientError


@pytest.mark.asyncio
async def test_compat_grpc_error_unknown_a2a_reason_does_not_overwrite_known_error() -> (
    None
):
    """A trailing unknown A2A ErrorInfo reason does not drop an earlier mapped error."""
    known = any_pb2.Any()
    known.Pack(
        error_details_pb2.ErrorInfo(
            reason='TASK_NOT_FOUND',
            domain='a2a-protocol.org',
            metadata={'taskId': 'job-1'},
        )
    )
    unknown = any_pb2.Any()
    unknown.Pack(
        error_details_pb2.ErrorInfo(
            reason='UNKNOWN_FUTURE_REASON',
            domain='a2a-protocol.org',
        )
    )
    debug_info = any_pb2.Any()
    debug_info.Pack(error_details_pb2.DebugInfo(detail='extra context'))
    status = status_pb2.Status(
        code=grpc.StatusCode.NOT_FOUND.value[0],
        message='task missing',
        details=[known, unknown, debug_info],
    )
    error = grpc.aio.AioRpcError(
        code=grpc.StatusCode.NOT_FOUND,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=grpc.aio.Metadata(
            ('grpc-status-details-bin', status.SerializeToString()),
        ),
        details='task missing',
    )
    transport = _transport_raising(error)

    with pytest.raises(TaskNotFoundError) as exc_info:
        await transport.get_task(GetTaskRequest(id='job-1'))

    assert str(exc_info.value) == 'task missing'
    assert exc_info.value.data == {'taskId': 'job-1'}
