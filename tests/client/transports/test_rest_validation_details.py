import json

from types import ModuleType
from typing import Any

import pytest

from a2a.client.transports.rest import RestTransport
from a2a.types.a2a_pb2 import AgentCard, SendMessageRequest
from a2a.utils.errors import InvalidParamsError
from google.protobuf import any_pb2
from google.protobuf.json_format import MessageToDict
from google.rpc import error_details_pb2, status_pb2


@pytest.fixture(params=['httpx', 'httpx2'])
def http_module(request: pytest.FixtureRequest) -> ModuleType:
    return pytest.importorskip(request.param)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mode', ['unary', 'stream_http', 'sse_error', 'sse_data']
)
@pytest.mark.parametrize('reverse_details', [False, True])
@pytest.mark.parametrize('include_metadata', [False, True])
async def test_rest_preserves_validation_details(
    http_module: ModuleType,
    mode: str,
    reverse_details: bool,
    include_metadata: bool,
) -> None:
    metadata = (
        {
            'requestId': 'request-1',
            'errors': 'must-not-replace-field-violations',
        }
        if include_metadata
        else {}
    )
    error_info = error_details_pb2.ErrorInfo(
        reason='INVALID_PARAMS', domain='a2a-protocol.org', metadata=metadata
    )
    bad_request = error_details_pb2.BadRequest()
    bad_request.field_violations.add(
        field='message.parts', description='At least one part is required'
    )
    bad_request.field_violations.add(
        field='configuration.historyLength', description='Must be non-negative'
    )
    details = [any_pb2.Any(), any_pb2.Any()]
    details[0].Pack(error_info)
    details[1].Pack(bad_request)
    if reverse_details:
        details.reverse()
    body = {
        'error': MessageToDict(
            status_pb2.Status(
                code=400, message='Invalid parameters', details=details
            )
        )
    }

    def respond(request: Any) -> Any:
        if mode in {'unary', 'stream_http'}:
            return http_module.Response(400, json=body)
        prefix = 'event: error\n' if mode == 'sse_error' else ''
        return http_module.Response(
            200,
            headers={'Content-Type': 'text/event-stream'},
            text=f'{prefix}data: {json.dumps(body)}\n\n',
        )

    async with http_module.AsyncClient(
        transport=http_module.MockTransport(respond)
    ) as http_client:
        client = RestTransport(
            http_client,
            AgentCard(),
            'https://agent.example',
            is_shared_httpx=True,
        )
        with pytest.raises(InvalidParamsError) as exc:
            if mode == 'unary':
                await client.send_message(SendMessageRequest())
            else:
                _ = [
                    event
                    async for event in client.send_message_streaming(
                        SendMessageRequest()
                    )
                ]

    assert str(exc.value) == 'Invalid parameters'
    assert exc.value.data == {
        **({'requestId': 'request-1'} if include_metadata else {}),
        'errors': [
            {
                'field': 'message.parts',
                'message': 'At least one part is required',
            },
            {
                'field': 'configuration.historyLength',
                'message': 'Must be non-negative',
            },
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'detail', [None, {}, {'fieldViolations': 'not-a-list'}]
)
async def test_rest_validation_detail_fallback(
    http_module: ModuleType, detail: dict[str, Any] | None
) -> None:
    details: list[Any] = [
        {
            '@type': 'type.googleapis.com/google.rpc.ErrorInfo',
            'reason': 'INVALID_PARAMS',
            'domain': 'a2a-protocol.org',
            'metadata': {'requestId': 'request-1'},
        }
    ]
    expected: dict[str, Any] = {'requestId': 'request-1'}
    if detail is not None:
        details.append(
            {'@type': 'type.googleapis.com/google.rpc.BadRequest', **detail}
        )
        if not detail:
            expected['errors'] = []
    body = {'error': {'message': 'Invalid parameters', 'details': details}}
    async with http_module.AsyncClient(
        transport=http_module.MockTransport(
            lambda request: http_module.Response(400, json=body)
        )
    ) as http_client:
        client = RestTransport(
            http_client,
            AgentCard(),
            'https://agent.example',
            is_shared_httpx=True,
        )
        with pytest.raises(InvalidParamsError) as exc:
            await client.send_message(SendMessageRequest())
    assert exc.value.data == expected
