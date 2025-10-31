import sys
from pathlib import Path

import pytest

from a2a.client.errors import A2AClientError
from a2a.client.transports.stdio import StdioTransport
from a2a.types import Message, MessageSendParams, Part, Role, TextPart

BASE_DIR = Path(__file__).parent.parent / 'stdio'
FAULTY_STREAM_AGENT = BASE_DIR / 'mock_faulty_stream_agent.py'
EARLY_EXIT_AGENT = BASE_DIR / 'mock_early_exit_agent.py'


@pytest.mark.asyncio
async def test_streaming_ignores_malformed_and_continues():
    msg = Message(
        message_id='m-faulty',
        role=Role.user,
        parts=[Part(root=TextPart(text='hello'))],
    )
    transport = StdioTransport([sys.executable, str(FAULTY_STREAM_AGENT)])
    received = []
    async for evt in transport.send_message_streaming(
        MessageSendParams(message=msg)
    ):
        received.append(evt)
    # We expect two good events despite a malformed line in between.
    assert len(received) == 2
    assert received[0].parts[0].root.text.startswith('good 0')
    assert received[1].parts[0].root.text.startswith('good 1')
    await transport.close()


@pytest.mark.asyncio
async def test_unary_process_exits_early_sets_error():
    msg = Message(
        message_id='m-early',
        role=Role.user,
        parts=[Part(root=TextPart(text='hello'))],
    )
    transport = StdioTransport([sys.executable, str(EARLY_EXIT_AGENT)])
    with pytest.raises(A2AClientError):
        await transport.send_message(MessageSendParams(message=msg))
    await transport.close()
