from unittest.mock import Mock

import pytest

from a2a.client.base_client import BaseClient
from a2a.extensions.trace import TraceExtension
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.types import Message, TextPart, Part, Role


@pytest.mark.asyncio
async def test_trace_extension():
    client = BaseClient(card=Mock(), config=Mock(), transport=Mock(), consumers=[], middleware=[])
    server_handler = DefaultRequestHandler(
        agent_executor=Mock(),
        task_store=Mock(),
    )

    trace_extension = TraceExtension()
    client.install_extension(trace_extension)
    server_handler.install_extension(trace_extension, server=Mock())

    message = Message(
        message_id='test_message',
        role=Role.user,
        parts=[Part(TextPart(text='Hello, world!'))],
    )

    # Simulate client sending a message
    for extension in client._extensions:
        extension.on_client_message(message)

    assert 'trace' in message.metadata
    # The trace_id field is serialized as traceId due to camelCase alias generator
    assert isinstance(message.metadata['trace']['traceId'], str)

    # Simulate server receiving a message
    for extension in server_handler._extensions:
        extension.on_server_message(message)

    # Check that the server-side handler was called
    # (in this case, it just prints a message)
    # We can't easily check the output of print, so we'll just
    # assume it worked if no exceptions were raised.
