import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from a2a.types.a2a_pb2 import (
    AgentCard,
    AgentInterface,
    SendMessageRequest,
    Message,
    GetTaskRequest,
    AgentCapabilities,
)
from a2a.client.transports import RestTransport, JsonRpcTransport, GrpcTransport
from a2a.client.transports.tenant_decorator import TenantTransportDecorator
from a2a.client import ClientConfig, ClientFactory
from a2a.utils.constants import TransportProtocol


@pytest.fixture
def agent_card():
    return AgentCard(
        supported_interfaces=[
            AgentInterface(
                url='http://example.com/rest',
                protocol_binding=TransportProtocol.HTTP_JSON,
                tenant='tenant-1',
            ),
            AgentInterface(
                url='http://example.com/jsonrpc',
                protocol_binding=TransportProtocol.JSONRPC,
                tenant='tenant-2',
            ),
            AgentInterface(
                url='http://example.com/grpc',
                protocol_binding=TransportProtocol.GRPC,
                tenant='tenant-3',
            ),
        ],
        capabilities=AgentCapabilities(streaming=True),
    )


@pytest.mark.asyncio
async def test_tenant_decorator_rest(agent_card):
    mock_httpx = AsyncMock(spec=httpx.AsyncClient)
    mock_httpx.build_request.return_value = MagicMock()
    mock_httpx.send.return_value = MagicMock(
        status_code=200, json=lambda: {'message': {}}
    )

    config = ClientConfig(
        httpx_client=mock_httpx,
        supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
    )
    factory = ClientFactory(config)
    client = factory.create(agent_card)

    assert isinstance(client._transport, TenantTransportDecorator)
    assert client._transport._tenant == 'tenant-1'

    # Test SendMessage (POST) - Use transport directly to avoid streaming complexity in mock
    request = SendMessageRequest(message=Message(parts=[{'text': 'hi'}]))
    await client._transport.send_message(request)

    # Check that tenant was populated in request
    assert request.tenant == 'tenant-1'

    # Check that path was prepended in the underlying transport
    mock_httpx.build_request.assert_called()
    send_call = next(
        c
        for c in mock_httpx.build_request.call_args_list
        if 'v1/message:send' in c.args[1]
    )
    args, kwargs = send_call
    assert args[1] == 'http://example.com/rest/tenant-1/v1/message:send'
    assert 'tenant' in kwargs['json']


@pytest.mark.asyncio
async def test_tenant_decorator_jsonrpc(agent_card):
    mock_httpx = AsyncMock(spec=httpx.AsyncClient)
    mock_httpx.build_request.return_value = MagicMock()
    mock_httpx.send.return_value = MagicMock(
        status_code=200,
        json=lambda: {'result': {'message': {}}, 'id': '1', 'jsonrpc': '2.0'},
    )

    config = ClientConfig(
        httpx_client=mock_httpx,
        supported_protocol_bindings=[TransportProtocol.JSONRPC],
    )
    factory = ClientFactory(config)
    client = factory.create(agent_card)

    assert isinstance(client._transport, TenantTransportDecorator)
    assert client._transport._tenant == 'tenant-2'

    request = SendMessageRequest(message=Message(parts=[{'text': 'hi'}]))
    await client._transport.send_message(request)

    mock_httpx.build_request.assert_called()
    _, kwargs = mock_httpx.build_request.call_args
    assert kwargs['json']['params']['tenant'] == 'tenant-2'


@pytest.mark.asyncio
async def test_tenant_decorator_grpc(agent_card):
    mock_channel = MagicMock()
    config = ClientConfig(
        grpc_channel_factory=lambda url: mock_channel,
        supported_protocol_bindings=[TransportProtocol.GRPC],
    )

    with patch('a2a.types.a2a_pb2_grpc.A2AServiceStub') as mock_stub_class:
        mock_stub = mock_stub_class.return_value
        mock_stub.SendMessage = AsyncMock(return_value={'message': {}})

        factory = ClientFactory(config)
        client = factory.create(agent_card)

        assert isinstance(client._transport, TenantTransportDecorator)
        assert client._transport._tenant == 'tenant-3'

        await client._transport.send_message(
            SendMessageRequest(message=Message(parts=[{'text': 'hi'}]))
        )

        call_args = mock_stub.SendMessage.call_args
        assert call_args[0][0].tenant == 'tenant-3'


@pytest.mark.asyncio
async def test_tenant_decorator_explicit_override(agent_card):
    mock_httpx = AsyncMock(spec=httpx.AsyncClient)
    mock_httpx.build_request.return_value = MagicMock()
    mock_httpx.send.return_value = MagicMock(
        status_code=200, json=lambda: {'message': {}}
    )

    config = ClientConfig(
        httpx_client=mock_httpx,
        supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
    )
    factory = ClientFactory(config)
    client = factory.create(agent_card)

    request = SendMessageRequest(
        message=Message(parts=[{'text': 'hi'}]), tenant='explicit-tenant'
    )
    await client._transport.send_message(request)

    assert request.tenant == 'explicit-tenant'

    send_call = next(
        c
        for c in mock_httpx.build_request.call_args_list
        if 'v1/message:send' in c.args[1]
    )
    args, _ = send_call
    assert args[1] == 'http://example.com/rest/explicit-tenant/v1/message:send'
