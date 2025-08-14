from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from a2a.extensions.trace import TraceExtension
from a2a.types import Message, Part, Role, TextPart


@pytest.mark.asyncio
async def test_full_trace_extension():
    trace_extension = TraceExtension()
    
    # Test the trace extension directly
    message = Message(
        message_id='test_message',
        role=Role.user,
        parts=[Part(TextPart(text='Hello, world!'))],
    )

    # Simulate client sending a message - creates trace
    trace_extension.on_client_message(message)
    
    # Verify trace was created and stored in metadata
    assert 'trace' in message.metadata
    trace_data = message.metadata['trace']
    assert 'traceId' in trace_data
    trace_id = trace_data['traceId']
    
    # Simulate server receiving a message - loads trace
    trace_extension.on_server_message(message)
    
    # Verify trace was loaded into extension
    assert trace_id in trace_extension.traces
    trace = trace_extension.traces[trace_id]
    assert len(trace.steps) == 0  # Initially no steps
    
    # Simulate a tool call being made
    from a2a.extensions.trace import StepAction, ToolInvocation, CallTypeEnum
    step_action = StepAction(tool_invocation=ToolInvocation(
        tool_name='test_tool',
        parameters={'param1': 'value1'}
    ))
    
    step = trace_extension.start_step(
        trace_id=trace_id,
        parent_step_id=None,
        call_type=CallTypeEnum.TOOL,
        step_action=step_action
    )
    
    # End the step
    trace_extension.end_step(step.step_id)
    
    # Verify the trace
    assert len(trace_extension.traces) == 1
    trace = trace_extension.traces[trace_id]
    assert len(trace.steps) == 1
    assert trace.steps[0].call_type == CallTypeEnum.TOOL
