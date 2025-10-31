import sys
import pytest
from pathlib import Path

from a2a.client.transports.stdio import StdioTransport
from a2a.types import (
    Message,
    MessageSendParams,
    TextPart,
    Part,
    Role,
    TaskIdParams,
)

TEST_AGENT_SCRIPT = Path(__file__).parent.parent / 'stdio' / 'mock_agent.py'


@pytest.mark.asyncio
async def test_send_message_streaming_yields_chunks():
    msg = Message(
        message_id='m-stream',
        role=Role.user,
        parts=[Part(root=TextPart(text='hello'))],
    )
    transport = StdioTransport([sys.executable, str(TEST_AGENT_SCRIPT)])
    chunks = []
    async for evt in transport.send_message_streaming(
        MessageSendParams(message=msg)
    ):
        chunks.append(evt)
    assert len(chunks) == 2
    assert all(hasattr(c, 'parts') for c in chunks)
    assert chunks[0].parts[0].root.text.startswith('stream chunk 0')
    await transport.close()


@pytest.mark.asyncio
async def test_resubscribe_streaming_single_event():
    transport = StdioTransport([sys.executable, str(TEST_AGENT_SCRIPT)])
    events = []
    async for evt in transport.resubscribe(TaskIdParams(id='t123')):
        events.append(evt)
    assert len(events) == 1
    assert events[0].parts[0].root.text == 'resubscribe event'
    await transport.close()
