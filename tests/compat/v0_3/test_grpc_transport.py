from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.client.client import ClientCallContext
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'history_length, expected_ids',
    [
        (None, ['m0', 'm1', 'm2']),
        (0, []),
        (1, ['m2']),
        (3, ['m0', 'm1', 'm2']),
        (5, ['m0', 'm1', 'm2']),
    ],
)
async def test_get_task_history_length(
    history_length: int | None, expected_ids: list[str]
) -> None:
    transport = CompatGrpcTransport(
        channel=AsyncMock(spec=Channel), agent_card=None
    )
    task = a2a_v0_3_pb2.Task(id='task-1', context_id='ctx-1')
    task.status.state = a2a_v0_3_pb2.TaskState.TASK_STATE_WORKING
    for i in range(3):
        task.history.add(message_id=f'm{i}', role=a2a_v0_3_pb2.Role.ROLE_USER)
    transport.stub = MagicMock(GetTask=AsyncMock(return_value=task))
    req = GetTaskRequest(id='task-1')
    if history_length is not None:
        req.history_length = history_length
    result = await transport.get_task(req)
    assert [message.message_id for message in result.history] == expected_ids
