"""Patched version of a2a/client/card_resolver.py

Fix for A2A-SSRF-01: validate AgentCard.url before returning the card.

Diff summary vs. original (v0.3.25):
  + import A2ASSRFValidationError, validate_agent_card_url from a2a.utils.url_validation
  + call validate_agent_card_url(agent_card.url) after model_validate()
  + wrap in try/except to raise A2AClientJSONError with a clear SSRF message
  + validate additional_interfaces[*].url as well (same attack surface)

Target file: src/a2a/client/card_resolver.py
"""

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
# ---- NEW IMPORT (fix for A2A-SSRF-01) ----
from a2a.utils.url_validation import A2ASSRFValidationError, validate_agent_card_url
# -------------------------------------------


logger = logging.getLogger(__name__)


class A2ACardResolver:
    """Agent Card resolver."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        base_url: str,
        agent_card_path: str = AGENT_CARD_WELL_KNOWN_PATH,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.agent_card_path = agent_card_path.lstrip('/')
        self.httpx_client = httpx_client

    async def get_agent_card(
        self,
        relative_card_path: str | None = None,
        http_kwargs: dict[str, Any] | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        if not relative_card_path:
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

            # ---- FIX: A2A-SSRF-01 — validate card.url before returning ----
            # Without this check, any caller who controls the card endpoint
            # can redirect all subsequent RPC calls to an internal address.
            try:
                validate_agent_card_url(agent_card.url)
                # Also validate any additional transport URLs declared in the card.
                for iface in agent_card.additional_interfaces or []:
                    validate_agent_card_url(iface.url)
            except A2ASSRFValidationError as e:
                raise A2AClientJSONError(
                    f'AgentCard from {target_url} failed SSRF URL validation: {e}'
                ) from e
            # -----------------------------------------------------------------

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
        except ValidationError as e:
            raise A2AClientJSONError(
                f'Failed to validate agent card structure from {target_url}: {e.json()}'
            ) from e

        return agent_card
