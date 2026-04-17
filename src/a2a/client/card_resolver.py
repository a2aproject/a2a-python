import json
import logging

from collections.abc import Callable
from typing import Any

import httpx

from google.protobuf.json_format import ParseDict, ParseError

from a2a.client.errors import AgentCardResolutionError
from a2a.types.a2a_pb2 import (
    AgentCard,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH


logger = logging.getLogger(__name__)


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


class A2ACardResolver:
    """Agent Card resolver."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        base_url: str,
        agent_card_path: str = AGENT_CARD_WELL_KNOWN_PATH,
    ) -> None:
        """Initializes the A2ACardResolver.

        Args:
            httpx_client: An async HTTP client instance (e.g., httpx.AsyncClient).
            base_url: The base URL of the agent's host.
            agent_card_path: The path to the agent card endpoint, relative to the base URL.
        """
        self.base_url = base_url.rstrip('/')
        self.agent_card_path = agent_card_path.lstrip('/')
        self.httpx_client = httpx_client

    async def get_agent_card(
        self,
        relative_card_path: str | None = None,
        http_kwargs: dict[str, Any] | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        """Fetches an agent card from a specified path relative to the base_url.

        If relative_card_path is None, it defaults to the resolver's configured
        agent_card_path (for the public agent card).

        Args:
            relative_card_path: Optional path to the agent card endpoint,
                relative to the base URL. If None, uses the default public
                agent card path. Use `'/'` for an empty path.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.get request.
            signature_verifier: A callable used to verify the agent card's signatures.

        Returns:
            An `AgentCard` object representing the agent's capabilities.

        Raises:
            AgentCardResolutionError: If an HTTP error occurs during the request, if the
                response body cannot be decoded as JSON, or if it cannot be
                validated against the AgentCard schema.
        """
        if not relative_card_path:
            # Use the default public agent card path configured during initialization
            path_segment = self.agent_card_path
        else:
            path_segment = relative_card_path.lstrip('/')

        target_url = (
            f'{self.base_url}/{path_segment}' if path_segment else self.base_url
        )

        try:
            response = await self.httpx_client.get(
                target_url,
                **(http_kwargs or {}),
            )
            response.raise_for_status()
            agent_card_data = response.json()
            logger.info(
                'Successfully fetched agent card data from %s: %s',
                target_url,
                agent_card_data,
            )
            agent_card = parse_agent_card(agent_card_data)
            if signature_verifier:
                signature_verifier(agent_card)
        except httpx.HTTPStatusError as e:
            raise AgentCardResolutionError(
                f'Failed to fetch agent card from {target_url} (HTTP {e.response.status_code}): {e}',
                status_code=e.response.status_code,
            ) from e
        except json.JSONDecodeError as e:
            raise AgentCardResolutionError(
                f'Failed to parse JSON for agent card from {target_url}: {e}'
            ) from e
        except httpx.RequestError as e:
            raise AgentCardResolutionError(
                f'Network communication error fetching agent card from {target_url}: {e}',
            ) from e
        except ParseError as e:
            raise AgentCardResolutionError(
                f'Failed to validate agent card structure from {target_url}: {e}'
            ) from e

        return agent_card
