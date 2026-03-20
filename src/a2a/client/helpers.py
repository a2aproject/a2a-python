"""Helper functions for the A2A client."""

from typing import Any
from uuid import uuid4

from google.protobuf.json_format import ParseDict

from a2a.types.a2a_pb2 import AgentCard, Message, Part, Role


def parse_agent_card(agent_card_data: dict[str, Any]) -> AgentCard:
    """Parse AgentCard JSON dictionary and handle backward compatibility."""
    _handle_extended_card_compatibility(agent_card_data)
    _handle_connection_fields_compatibility(agent_card_data)
    _handle_security_compatibility(agent_card_data)

    return ParseDict(agent_card_data, AgentCard(), ignore_unknown_fields=True)


def _handle_extended_card_compatibility(
    agent_card_data: dict[str, Any],
) -> None:
    """Map legacy supportsAuthenticatedExtendedCard to capabilities."""
    if agent_card_data.pop('supportsAuthenticatedExtendedCard', None):
        capabilities = agent_card_data.setdefault('capabilities', {})
        if 'extendedAgentCard' not in capabilities:
            capabilities['extendedAgentCard'] = True


def _handle_connection_fields_compatibility(
    agent_card_data: dict[str, Any],
) -> None:
    """Map legacy connection and transport fields to supportedInterfaces."""
    main_url = agent_card_data.pop('url', None)
    main_transport = agent_card_data.pop('preferredTransport', 'JSONRPC')
    version = agent_card_data.pop('protocolVersion', '0.3.0')
    additional_interfaces = (
        agent_card_data.pop('additionalInterfaces', None) or []
    )

    if 'supportedInterfaces' not in agent_card_data and main_url:
        supported_interfaces = []
        supported_interfaces.append(
            {
                'url': main_url,
                'protocolBinding': main_transport,
                'protocolVersion': version,
            }
        )
        supported_interfaces.extend(
            {
                'url': iface.get('url'),
                'protocolBinding': iface.get('transport'),
                'protocolVersion': version,
            }
            for iface in additional_interfaces
        )
        agent_card_data['supportedInterfaces'] = supported_interfaces


def _map_legacy_security(
    sec_list: list[dict[str, list[str]]],
) -> list[dict[str, Any]]:
    """Convert a legacy security requirement list into the 1.0.0 Protobuf format."""
    return [
        {
            'schemes': {
                scheme_name: {'list': scopes}
                for scheme_name, scopes in sec_dict.items()
            }
        }
        for sec_dict in sec_list
    ]


def _handle_security_compatibility(agent_card_data: dict[str, Any]) -> None:
    """Map legacy security requirements and schemas to their 1.0.0 Protobuf equivalents."""
    legacy_security = agent_card_data.pop('security', None)
    if (
        'securityRequirements' not in agent_card_data
        and legacy_security is not None
    ):
        agent_card_data['securityRequirements'] = _map_legacy_security(
            legacy_security
        )

    for skill in agent_card_data.get('skills', []):
        legacy_skill_sec = skill.pop('security', None)
        if 'securityRequirements' not in skill and legacy_skill_sec is not None:
            skill['securityRequirements'] = _map_legacy_security(
                legacy_skill_sec
            )

    security_schemes = agent_card_data.get('securitySchemes', {})
    if security_schemes:
        type_mapping = {
            'apiKey': 'apiKeySecurityScheme',
            'http': 'httpAuthSecurityScheme',
            'oauth2': 'oauth2SecurityScheme',
            'openIdConnect': 'openIdConnectSecurityScheme',
            'mutualTLS': 'mtlsSecurityScheme',
        }
        for scheme in security_schemes.values():
            scheme_type = scheme.pop('type', None)
            if scheme_type in type_mapping:
                # Map legacy 'in' to modern 'location'
                if scheme_type == 'apiKey' and 'in' in scheme:
                    scheme['location'] = scheme.pop('in')

                mapped_name = type_mapping[scheme_type]
                new_scheme_wrapper = {mapped_name: scheme.copy()}
                scheme.clear()
                scheme.update(new_scheme_wrapper)


def create_text_message_object(
    role: Role = Role.ROLE_USER, content: str = ''
) -> Message:
    """Create a Message object containing a single text Part.

    Args:
        role: The role of the message sender (user or agent). Defaults to Role.ROLE_USER.
        content: The text content of the message. Defaults to an empty string.

    Returns:
        A `Message` object with a new UUID message_id.
    """
    return Message(
        role=role, parts=[Part(text=content)], message_id=str(uuid4())
    )
