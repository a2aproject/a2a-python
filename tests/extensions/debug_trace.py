#!/usr/bin/env python3
import sys
from unittest.mock import Mock

from a2a.client.base_client import BaseClient
from a2a.extensions.trace import TraceExtension
from a2a.types import Message, TextPart, Role, Part

def debug_trace():
    print("Starting trace debug...")
    
    # Create the extension
    trace_extension = TraceExtension()
    
    # Create a trace directly to see its structure
    trace = trace_extension.start_trace()
    print(f"Direct trace object: {trace}")
    print(f"Direct trace dict: {trace.model_dump(mode='json')}")
    
    # Create a message
    message = Message(
        message_id='test_message',
        role=Role.user,
        parts=[Part(TextPart(text='Hello, world!'))],
    )
    
    print(f"Initial message metadata: {message.metadata}")
    
    # Call the extension method
    trace_extension.on_client_message(message)
    
    print(f"After extension metadata: {message.metadata}")
    
    if message.metadata and 'trace' in message.metadata:
        trace_data = message.metadata['trace']
        print(f"Trace data type: {type(trace_data)}")
        print(f"Trace data: {trace_data}")
        
        if isinstance(trace_data, dict):
            print(f"Trace data keys: {list(trace_data.keys())}")
            if 'trace_id' in trace_data:
                print(f"Found trace_id: {trace_data['trace_id']}")
            else:
                print("trace_id not found in trace data")
        else:
            print("Trace data is not a dict")
    else:
        print("No trace data found in metadata")

if __name__ == "__main__":
    debug_trace()
