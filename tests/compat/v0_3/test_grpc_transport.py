from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.client.client import ClientCallContext
from a2a.client.optionals import Channel
from a2a.compat.v0_3 import a2a_v0_3_pb2
from a2a.compat.v0_3.grpc_transport import CompatGrpcTransport
from a2a.types.a2a_pb2 import (
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


@pytest.mark.asyncio
async def test_compat_grpc_transport_send_message_without_configuration():
    """A request without a configuration must not send one.

    The v0.3 `blocking` field has no presence, so an empty configuration is
    read by the server as `blocking: false` and the call returns before the
    task finishes. Without a configuration the server blocks by default, as
    it does for the v0.3 JSON-RPC transport.
    """
    transport = CompatGrpcTransport(
        channel=AsyncMock(spec=Channel), agent_card=None
    )
    mock_stub = MagicMock()
    mock_stub.SendMessage = AsyncMock(
        return_value=a2a_v0_3_pb2.SendMessageResponse()
    )
    transport.stub = mock_stub

    await transport.send_message(
        SendMessageRequest(
            message=Message(message_id='msg-1', role=Role.ROLE_USER)
        )
    )

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
