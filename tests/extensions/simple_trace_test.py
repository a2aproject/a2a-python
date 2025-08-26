#!/usr/bin/env python3

# Simple test to check ResponseTrace serialization
from a2a.extensions.trace import TraceExtension, ResponseTrace

# Create extension and trace
ext = TraceExtension()
trace = ext.start_trace()

print("Trace object:", trace)
print("Trace type:", type(trace))
print("Trace fields:", trace.__dict__)
print("Model dump:", trace.model_dump(mode='json'))

# Test creating trace data like in the extension
if True:  # message.metadata is None
    metadata = {}
metadata['trace'] = trace.model_dump(mode='json')

print("Metadata:", metadata)
print("Trace in metadata:", metadata['trace'])
print("Keys in trace:", metadata['trace'].keys() if isinstance(metadata['trace'], dict) else "not a dict")
