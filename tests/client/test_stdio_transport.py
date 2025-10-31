import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from a2a.client.transports.stdio import StdioTransport
from a2a.types import Message, MessageSendParams, TextPart, Part, Role

TEST_AGENT_SCRIPT = Path(__file__).parent.parent / 'stdio' / 'mock_agent.py'


@pytest.mark.asyncio
async def test_send_message_echo():
    msg = Message(
        message_id='m1',
        role=Role.user,
        parts=[Part(root=TextPart(text='hello'))],
    )
    transport = StdioTransport([sys.executable, str(TEST_AGENT_SCRIPT)])
    result = await transport.send_message(MessageSendParams(message=msg))
    assert result.parts[0].root.text.startswith('echo: hello')
    await transport.close()


@pytest.mark.asyncio
async def test_send_message_streaming_minimal():
    """Streaming now implemented: ensure we receive the expected two chunks."""
    msg = Message(
        message_id='m2',
        role=Role.user,
        parts=[Part(root=TextPart(text='hello'))],
    )
    transport = StdioTransport([sys.executable, str(TEST_AGENT_SCRIPT)])
    chunks = []
    async for event in transport.send_message_streaming(
        MessageSendParams(message=msg)
    ):
        chunks.append(event)
    # mock agent yields exactly two events currently
    assert len(chunks) == 2
    assert all(
        hasattr(c, 'parts') and c.parts[0].root.text.startswith('stream chunk')
        for c in chunks
    )
    await transport.close()
