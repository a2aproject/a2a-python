"""HTTP application components for the A2A server."""

from a2a.server.apps.jsonrpc import (
    A2AFastAPIApplication,
    A2AStarletteApplication,
    CallContextBuilder,
    JSONRPCApplication,
)
from a2a.server.apps.rest import A2ARESTFastAPIApplication

try:
    from a2a.server.apps.kafka import KafkaServerApp
except ImportError:
    KafkaServerApp = None  # type: ignore


__all__ = [
    'A2AFastAPIApplication',
    'A2ARESTFastAPIApplication',
    'A2AStarletteApplication',
    'CallContextBuilder',
    'JSONRPCApplication',
    'KafkaServerApp',
]
