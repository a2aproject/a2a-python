"""Response validation through the client and HTTP/SSE transport layers."""

import json

from typing import Any

import httpx
import pytest

from a2a.client import ClientConfig, ClientFactory
from a2a.client.client import ClientCallContext
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.constants import TransportProtocol
from google.protobuf.json_format import MessageToDict, ParseError


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.parametrize(
        'protocol',
        [TransportProtocol.JSONRPC, TransportProtocol.HTTP_JSON],
        ids=['jsonrpc', 'rest'],
    ),
    pytest.mark.parametrize('streaming', [False, True], ids=['unary', 'sse']),
]


@pytest.fixture(params=['message', 'task'])
def expected_response(request: pytest.FixtureRequest) -> StreamResponse:
    message = Message(
        message_id='reply-1',
        role=Role.ROLE_AGENT,
        parts=[Part(text='hello')],
    )
    if request.param == 'message':
        return StreamResponse(message=message)
    return StreamResponse(
        task=Task(
            id='task-1',
            context_id='context-1',
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            history=[message],
        )
    )


async def _receive_response(
    payload: dict[str, Any],
    protocol: TransportProtocol,
    streaming: bool,
) -> list[StreamResponse]:
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.method == 'POST'
        if protocol == TransportProtocol.JSONRPC:
            rpc_request = json.loads(request.content)
            assert rpc_request['method'] == (
                'SendStreamingMessage' if streaming else 'SendMessage'
            )
            response_body = {
                'jsonrpc': '2.0',
                'id': rpc_request['id'],
                'result': payload,
            }
        else:
            assert request.url.path == (
                '/message:stream' if streaming else '/message:send'
            )
            response_body = payload

        if streaming:
            return httpx.Response(
                200,
                headers={'content-type': 'text/event-stream'},
                content=f'data: {json.dumps(response_body)}\n\n',
            )
        return httpx.Response(200, json=response_body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle_request)
    ) as http_client:
        card = AgentCard(
            name='Response validation agent',
            capabilities=AgentCapabilities(streaming=True),
            supported_interfaces=[
                AgentInterface(
                    url='https://agent.example',
                    protocol_binding=protocol,
                    protocol_version='1.0',
                )
            ],
        )
        client = ClientFactory(
            ClientConfig(
                httpx_client=http_client,
                streaming=streaming,
                supported_protocol_bindings=[protocol],
            )
        ).create(card)
        request = SendMessageRequest(
            message=Message(
                message_id='request-1',
                role=Role.ROLE_USER,
                parts=[Part(text='hi')],
            )
        )
        return [
            event
            async for event in client.send_message(
                request, context=ClientCallContext(timeout=5.0)
            )
        ]


async def test_send_message_preserves_known_response_fields(
    expected_response: StreamResponse,
    protocol: TransportProtocol,
    streaming: bool,
) -> None:
    responses = await _receive_response(
        MessageToDict(expected_response), protocol, streaming
    )
    assert responses == [expected_response]


async def test_send_message_rejects_malformed_known_response_fields(
    expected_response: StreamResponse,
    protocol: TransportProtocol,
    streaming: bool,
) -> None:
    payload = MessageToDict(expected_response)
    message = (
        payload['message']
        if expected_response.HasField('message')
        else payload['task']['history'][0]
    )
    # Unknown-field leniency must not permit a scalar for a repeated field.
    message['parts'] = 'not-a-list'

    with pytest.raises(
        ParseError, match=r'repeated field parts must be in \[\]'
    ):
        await _receive_response(payload, protocol, streaming)
