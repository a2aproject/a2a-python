"""Constants for well-known URIs used throughout the A2A Python SDK."""

from enum import Enum


AGENT_CARD_WELL_KNOWN_PATH = '/.well-known/agent-card.json'
PREV_AGENT_CARD_WELL_KNOWN_PATH = '/.well-known/agent.json'
EXTENDED_AGENT_CARD_PATH = '/agent/authenticatedExtendedCard'
DEFAULT_RPC_URL = '/'
DEFAULT_LIST_TASKS_PAGE_SIZE = 50
"""Default page size for the `tasks/list` method."""


class TransportProtocol(str, Enum):
    """Transport protocol string constants."""

    JSONRPC = 'JSONRPC'
    HTTP_JSON = 'HTTP+JSON'
    GRPC = 'GRPC'


DEFAULT_MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB
JSONRPC_PARSE_ERROR_CODE = -32700
