from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError
from a2a.client.optionals import Channel
from a2a.compat.v0_3 import a2a_v0_3_pb2
from a2a.compat.v0_3.grpc_transport import CompatGrpcTransport
from a2a.types.a2a_pb2 import (
    GetTaskRequest,
    Message,
    Role,
    SendMessageRequest,
    SendMessageResponse,
)
from a2a.utils.errors import TaskNotFoundError, UnsupportedOperationError
from google.protobuf import any_pb2
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
