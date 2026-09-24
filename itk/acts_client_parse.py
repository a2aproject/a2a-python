"""The `tck-client-parse` behaviour: ACTS §10 client tests.

Every other ACTS step drives the SUT as a **server** — send bytes, assert on
what comes back. A client test inverts that: it supplies a canonical wire
payload and asks whether *this SDK's client* parses it correctly, which no A2A
operation can ask a server. §10 defines the file format and says nothing about
the mechanism, so the runner cannot reach the client at all and skips the
eight `CLIENT-*` tests.

This closes that gap from the agent side. The runner sends an ordinary
`send_message` naming `tck-client-parse` with `{operation, wire_payload}` in a
data part; the agent builds a real SDK client whose HTTP transport returns
that payload verbatim, performs the operation, and hands back whatever its own
client produced.

**A mock transport rather than a bare deserializer.** Calling
`SendMessageResponse.FromJson(...)` directly would be far less code and would
prove much less: it skips the JSON-RPC envelope, the error mapping and the
response plumbing, which is most of what a client test is about.
`CLIENT-PARSE-004` makes that concrete — it feeds a JSON-RPC *error* envelope
and expects the client to surface `{error: {code, message}}`. Unwrapping that
by hand in the agent would be reimplementing the code under test.

The reply is shaped like a dispatcher's `payload` — the §4.2 assertion root
for the operation — so `expect_parsed` reads exactly like `expect.body` and
the runner needs no special assertion path.
"""

from __future__ import annotations

import json
import logging

from typing import Any

import httpx

from google.protobuf.json_format import MessageToDict

from a2a.client import ClientConfig, create_client
from a2a.client.card_resolver import A2ACardResolver
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    GetExtendedAgentCardRequest,
    GetTaskRequest,
    Message,
    SendMessageRequest,
)
from a2a.utils import TransportProtocol


logger = logging.getLogger(__name__)

BEHAVIOR = 'tck-client-parse'

#: Where the fake server lives. Nothing dials it — the mock transport answers
#: before a socket is opened — but the client needs a syntactically valid base.
_BASE_URL = 'http://acts-client-parse.invalid'

_CARD_PATH = '/.well-known/agent-card.json'
_EXTENDED_CARD_PATH = '/extendedAgentCard'


def _is_enveloped(payload: Any) -> bool:
    """Is this payload a JSON-RPC envelope rather than a bare object?"""
    return isinstance(payload, dict) and (
        'jsonrpc' in payload or 'result' in payload or 'error' in payload
    )


def _transport(payload: Any) -> httpx.MockTransport:
    """An HTTP transport that answers every request with ``payload``.

    The response's JSON-RPC ``id`` is rewritten to echo the request's, which
    is what a real server does. The corpus's canned payloads carry a fixed id
    (``req-001``, ``1``, …) that cannot match one the client invented at call
    time, so a client validating the correlation — as JSON-RPC 2.0 requires —
    rejects the payload before parsing any of it. Echoing keeps the test about
    parsing rather than about a correlation the canned-payload model cannot
    express.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = payload
        if _is_enveloped(payload):
            try:
                sent = json.loads(request.content)
            except ValueError:
                sent = None
            if isinstance(sent, dict) and 'id' in sent:
                body = {**payload, 'id': sent['id']}
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _scaffold_card() -> AgentCard:
    """A minimal card advertising JSON-RPC, so `create_client` can bind.

    A client needs a card before it will talk to anything. Passing the card
    object rather than a URL is what keeps the mock transport free to answer
    with the payload under test: were the client to resolve a card over that
    transport it would be handed the `wire_payload` instead.

    JSON-RPC because that is the binding every non-card `wire_payload` in the
    corpus is written in.
    """
    return AgentCard(
        name='acts-client-parse',
        version='1.0.0',
        capabilities=AgentCapabilities(
            streaming=False, extended_agent_card=True
        ),
        supported_interfaces=[
            AgentInterface(
                url=_BASE_URL,
                protocol_binding='JSONRPC',
                protocol_version='1.0',
            )
        ],
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
    )


async def _client(payload: Any) -> Any:
    config = ClientConfig()
    config.streaming = False
    config.supported_protocol_bindings = [TransportProtocol.JSONRPC]
    config.httpx_client = httpx.AsyncClient(transport=_transport(payload))
    return await create_client(_scaffold_card(), client_config=config)


async def _parse_card(payload: Any, path: str) -> dict[str, Any]:
    """Run a bare card payload through the SDK's own card handling.

    Both card operations land here when the payload is a bare card, which is
    how the corpus writes them — and correctly so: a card is fetched over
    plain HTTP on every binding, so there is no envelope to unwrap. The two
    differ only in the path they are served from.
    """
    async with httpx.AsyncClient(transport=_transport(payload)) as http:
        resolver = A2ACardResolver(httpx_client=http, base_url=_BASE_URL)
        card = await resolver.get_agent_card(relative_card_path=path)
        return MessageToDict(card)


async def _parse_extended_card_envelope(payload: Any) -> dict[str, Any]:
    """The enveloped form of `get_extended_agent_card`, via the RPC client."""
    async with httpx.AsyncClient(transport=_transport(payload)) as http:
        config = ClientConfig()
        config.streaming = False
        config.supported_protocol_bindings = [TransportProtocol.JSONRPC]
        config.httpx_client = http
        client = await create_client(_scaffold_card(), client_config=config)
        try:
            card = await client.get_extended_agent_card(
                GetExtendedAgentCardRequest()
            )
        finally:
            await client.close()
        return MessageToDict(card)


async def parse(operation: str, payload: Any) -> dict[str, Any]:
    """Feed ``payload`` to this SDK's client and return what it produced.

    The result is the §4.2 assertion root for ``operation``: a
    `SendMessageResponse` keeps its `task`/`message` discriminator, `get_task`
    returns the Task's own fields, and a card operation returns the card.

    An error the client raises comes back as ``{'error': {...}}`` rather than
    propagating, because for `CLIENT-PARSE-004` that *is* the expected parse.
    """
    try:
        parsed = await _parse_operation(operation, payload)
    except Exception as exc:  # noqa: BLE001 - the error IS the parse result
        return _as_error(exc, payload)
    return parsed


async def _parse_operation(operation: str, payload: Any) -> dict[str, Any]:
    """Route ``operation`` to whichever half of the client handles it."""
    if operation == 'get_agent_card':
        return await _parse_card(payload, _CARD_PATH)

    if operation == 'get_extended_agent_card':
        # The corpus writes this one as a bare card, matching the wire:
        # `supportedInterfaces` on its own payload names REST, where the
        # extended card is a plain GET. Accept an envelope too, since a
        # JSON-RPC binding does wrap it.
        if _is_enveloped(payload):
            return await _parse_extended_card_envelope(payload)
        return await _parse_card(payload, _EXTENDED_CARD_PATH)

    return await _parse_via_rpc(operation, payload)


async def _parse_via_rpc(operation: str, payload: Any) -> dict[str, Any]:
    """The operations that go through the RPC client rather than the card."""
    client = await _client(payload)
    try:
        if operation == 'send_message':
            async for event in client.send_message(
                SendMessageRequest(
                    message=Message(role='ROLE_USER', message_id='acts')
                )
            ):
                # StreamResponse keeps the oneof, which is exactly the
                # discriminator `expect_parsed: {task: ...}` addresses.
                return MessageToDict(event)
            return {}
        if operation == 'get_task':
            task = await client.get_task(GetTaskRequest(id='acts'))
            return MessageToDict(task)
    finally:
        await client.close()

    return {'error': {'message': f'unsupported client operation {operation!r}'}}


def _as_error(exc: Exception, payload: Any) -> dict[str, Any]:
    """Render a client-raised error the way `expect_parsed` addresses it.

    `CLIENT-PARSE-004` asserts `error.code` and `error.message`. An SDK error
    object does not necessarily carry the JSON-RPC code, so the envelope's own
    `error` is preferred when the payload had one — the assertion is about the
    client having surfaced *that* error, and inventing a code here would pass
    the test without the client having done anything.
    """
    if isinstance(payload, dict) and isinstance(payload.get('error'), dict):
        return {'error': dict(payload['error']), 'raised': type(exc).__name__}
    return {'error': {'message': str(exc)}, 'raised': type(exc).__name__}


def request_from(part_data: Any) -> tuple[str, Any] | None:
    """Read `{operation, wire_payload}` out of the step's data part."""
    if not isinstance(part_data, dict):
        return None
    operation = part_data.get('operation')
    if not isinstance(operation, str):
        return None
    return operation, part_data.get('wire_payload')


__all__ = ['BEHAVIOR', 'parse', 'request_from']
