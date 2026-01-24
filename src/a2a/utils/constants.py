"""Constants for well-known URIs used throughout the A2A Python SDK."""

AGENT_CARD_WELL_KNOWN_PATH = '/.well-known/agent-card.json'
PREV_AGENT_CARD_WELL_KNOWN_PATH = '/.well-known/agent.json'
EXTENDED_AGENT_CARD_PATH = '/agent/authenticatedExtendedCard'
DEFAULT_RPC_URL = '/'


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
