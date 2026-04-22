"""Constants for well-known URIs used throughout the A2A Python SDK."""

from enum import Enum


AGENT_CARD_WELL_KNOWN_PATH = '/.well-known/agent-card.json'
DEFAULT_RPC_URL = '/'
DEFAULT_LIST_TASKS_PAGE_SIZE = 50
"""Default page size for the `tasks/list` method."""

MAX_LIST_TASKS_PAGE_SIZE = 100
"""Maximum page size for the `tasks/list` method."""


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
