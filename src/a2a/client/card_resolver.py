import json
import logging

from collections.abc import Callable
from typing import Any

import httpx

from pydantic import ValidationError

from a2a.client.errors import (
    A2AClientHTTPError,
    A2AClientJSONError,
)
from a2a.types import (
    AgentCard,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH


logger = logging.getLogger(__name__)


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
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON
                or validated against the AgentCard schema.
        """
        if not relative_card_path:
            # Use the default public agent card path configured during initialization
            path_segment = self.agent_card_path
        else:
            path_segment = relative_card_path.lstrip('/')

        target_url = f'{self.base_url}/{path_segment}'

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
            agent_card = AgentCard.model_validate(agent_card_data)
            if signature_verifier:
                signature_verifier(agent_card)
        except httpx.HTTPStatusError as e:
            raise A2AClientHTTPError(
                e.response.status_code,
                f'Failed to fetch agent card from {target_url}: {e}',
            ) from e
        except json.JSONDecodeError as e:
            raise A2AClientJSONError(
                f'Failed to parse JSON for agent card from {target_url}: {e}'
            ) from e
        except httpx.RequestError as e:
            raise A2AClientHTTPError(
                503,
                f'Network communication error fetching agent card from {target_url}: {e}',
            ) from e
        except ValidationError as e:  # Pydantic validation error
            raise A2AClientJSONError(
                f'Failed to validate agent card structure from {target_url}: {e.json()}'
            ) from e

        return agent_card
