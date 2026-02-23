"""Constants for well-known URIs used throughout the A2A Python SDK."""

AGENT_CARD_WELL_KNOWN_PATH = '/.well-known/agent-card.json'
PREV_AGENT_CARD_WELL_KNOWN_PATH = '/.well-known/agent.json'
EXTENDED_AGENT_CARD_PATH = '/agent/authenticatedExtendedCard'
DEFAULT_RPC_URL = '/'
DEFAULT_LIST_TASKS_PAGE_SIZE = 50
"""Default page size for the `tasks/list` method."""

MAX_LIST_TASKS_PAGE_SIZE = 100
"""Maximum page size for the `tasks/list` method."""


# Transport protocol constants
# These match the protocol binding values used in AgentCard
TRANSPORT_JSONRPC = 'JSONRPC'
TRANSPORT_HTTP_JSON = 'HTTP+JSON'
TRANSPORT_GRPC = 'GRPC'


class TransportProtocol:
    """Transport protocol string constants."""

    jsonrpc = TRANSPORT_JSONRPC
    http_json = TRANSPORT_HTTP_JSON
    grpc = TRANSPORT_GRPC


DEFAULT_MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB
JSONRPC_PARSE_ERROR_CODE = -32700
