"""Constants for well-known URIs used throughout the A2A Python SDK."""

from enum import Enum


AGENT_CARD_WELL_KNOWN_PATH = '/.well-known/agent-card.json'
DEFAULT_RPC_URL = '/'
DEFAULT_LIST_TASKS_PAGE_SIZE = 50
"""Default page size for the `tasks/list` method."""

MAX_LIST_TASKS_PAGE_SIZE = 100
"""Maximum page size for the `tasks/list` method."""

MAX_REQUEST_BODY_SIZE = 10 * 1024 * 1024
"""Maximum accepted HTTP request body size in bytes (10 MiB).

Requests with a body larger than this are rejected with HTTP 413
(payload too large) instead of being buffered unboundedly.
"""

SSE_PING_INTERVAL_SECONDS = 15
"""Heartbeat interval for SSE streams, in seconds.

A comment-only ``: ping`` frame is sent on every interval to keep the
connection alive and detect dead clients.
"""

SSE_SEND_TIMEOUT_SECONDS = 300
"""Maximum time in seconds without a successful send before an SSE
stream is torn down, preventing zombie streams from accumulating."""


class TransportProtocol(str, Enum):
    """Transport protocol string constants."""

    JSONRPC = 'JSONRPC'
    HTTP_JSON = 'HTTP+JSON'
    GRPC = 'GRPC'


JSONRPC_PARSE_ERROR_CODE = -32700
VERSION_HEADER = 'A2A-Version'

PROTOCOL_VERSION_1_0 = '1.0'
PROTOCOL_VERSION_0_3 = '0.3'
PROTOCOL_VERSION_CURRENT = PROTOCOL_VERSION_1_0
