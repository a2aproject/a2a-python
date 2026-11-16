import json

from typing import Any

import httpx
import pytest

from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.client.transports.rest import RestTransport
from a2a.types.a2a_pb2 import (
    AgentCard,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
)
from google.protobuf.json_format import MessageToDict


@pytest.mark.asyncio
@pytest.mark.parametrize('transport_class', [RestTransport, JsonRpcTransport])
@pytest.mark.parametrize(
    ('content_type', 'is_sse'),
    [
        ('text/event-stream', True),
        ('Text/Event-Stream', True),
        ('TEXT/EVENT-STREAM', True),
        ('TeXt/EvEnT-StReAm; charset=utf-8', True),
        ('application/json', False),
    ],
)
async def test_streaming_transport_accepts_media_type_case(
    transport_class: type[RestTransport] | type[JsonRpcTransport],
    content_type: str,
    is_sse: bool,
) -> None:
    """Decode equivalent media types through both public streaming transports."""
    expected = StreamResponse(
        message=Message(
            message_id='reply',
            role=Role.ROLE_AGENT,
            parts=[Part(text='hello')],
        )
    )

    def respond(request: httpx.Request) -> httpx.Response:
        payload: dict[str, Any] = MessageToDict(expected)
        if transport_class is JsonRpcTransport:
            payload = {
                'jsonrpc': '2.0',
                'id': json.loads(request.content)['id'],
                'result': payload,
            }
        serialized = json.dumps(payload)
        body = f'data: {serialized}\n\n' if is_sse else serialized
        return httpx.Response(
            200, headers={'Content-Type': content_type}, text=body
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http_client:
        transport = transport_class(
            httpx_client=http_client,
            agent_card=AgentCard(),
            url='https://agent.example',
            is_shared_httpx=True,
        )
        request = SendMessageRequest(
            message=Message(
                message_id='request',
                role=Role.ROLE_USER,
                parts=[Part(text='hi')],
            )
        )

        events = [
            event async for event in transport.send_message_streaming(request)
        ]

    assert events == [expected]
