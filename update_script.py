import re


with open('src/a2a/compat/v0_3/grpc_transport.py') as f:
    content = f.read()

# Remove `extensions` from __init__
content = re.sub(
    r'(def __init__\(\s*self,\s*channel: Channel,\s*agent_card: a2a_pb2\.AgentCard \| None,).*?(\):)',
    r'\1\2',
    content,
    flags=re.DOTALL
)

# Remove `self.extensions = extensions` from __init__
content = re.sub(
    r'\s+self\.extensions = extensions\n',
    r'\n',
    content
)

# Replace `extensions: list[str] | None = None,` inside method signatures
content = re.sub(
    r'\s+extensions: list\[str\] \| None = None,\n',
    r'\n',
    content
)

# Fix _get_grpc_metadata body
content = re.sub(
    r'def _get_grpc_metadata\(\s*self,\s*\) -> list\[tuple\[str, str\]\]:',
    r'def _get_grpc_metadata(self, context: ClientCallContext | None = None) -> list[tuple[str, str]]:',
    content
)

content = re.sub(
    r'extensions_to_use = extensions or self\.extensions\n\s+if extensions_to_use:\n\s+metadata\.append\(\n\s+\(HTTP_EXTENSION_HEADER\.lower\(\), \'\,\'\.join\(extensions_to_use\)\)\n\s+\)',
    r'if context and context.service_parameters:\n            for key, value in context.service_parameters.items():\n                metadata.append((key.lower(), value))',
    content
)

# Replace passing `extensions` to `self._get_grpc_metadata(extensions)` with `self._get_grpc_metadata(context)`
content = re.sub(
    r'self\._get_grpc_metadata\(extensions\)',
    r'self._get_grpc_metadata(context)',
    content
)

with open('src/a2a/compat/v0_3/grpc_transport.py', 'w') as f:
    f.write(content)
