"""A2A JSON-RPC Applications."""

from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.server.apps.jsonrpc.jsonrpc_app import (
    CallContextBuilder,
    DefaultCallContextBuilder,
    JSONRPCApplication,
    StarletteUserProxy,
)
from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication


__all__ = [
    'A2AFastAPIApplication',
    'A2AStarletteApplication',
    'CallContextBuilder',
    'DefaultCallContextBuilder',
    'JSONRPCApplication',
    'StarletteUserProxy',
]
