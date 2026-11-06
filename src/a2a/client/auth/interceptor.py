from __future__ import annotations

import logging

from typing import TYPE_CHECKING

from a2a.client.client import ClientCallContext
from a2a.client.interceptors import (
    AfterArgs,
    BeforeArgs,
    ClientCallInterceptor,
)


if TYPE_CHECKING:
    from a2a.client.auth.credentials import CredentialService
    from a2a.types.a2a_pb2 import SecurityScheme

logger = logging.getLogger(__name__)


class AuthInterceptor(ClientCallInterceptor):
    """An interceptor that automatically adds authentication details to requests.

    Based on the agent's security schemes.
    """

    def __init__(self, credential_service: CredentialService):
        self._credential_service = credential_service

    async def before(self, args: BeforeArgs) -> None:
        """Applies authentication headers to the request if credentials are available.

        Follows OpenAPI Security Requirement semantics:
        - The outer ``security_requirements`` list uses **OR** semantics:
          satisfying any single requirement is sufficient.
        - Multiple schemes **within** a single requirement use **AND**
          semantics: all of them must be satisfied together.

        A two-pass approach is used per requirement to avoid partial
        application when one scheme's credential is unavailable.
        """
        agent_card = args.agent_card

        # Proto3 repeated fields (security) and maps (security_schemes) do not track presence.
        # HasField() raises ValueError for them.
        # We check for truthiness to see if they are non-empty.
        if (
            not agent_card.security_requirements
            or not agent_card.security_schemes
        ):
            return

        for requirement in agent_card.security_requirements:
            # Pass 1: collect credentials for every scheme in this
            # requirement.  If any scheme cannot be satisfied the whole
            # requirement is skipped (AND semantics).
            collected: dict[str, tuple[str, str]] = {}
            satisfiable = True

            for scheme_name in requirement.schemes:
                credential = await self._credential_service.get_credentials(
                    scheme_name, args.context
                )
                if (
                    not credential
                    or scheme_name not in agent_card.security_schemes
                ):
                    satisfiable = False
                    break

                scheme = agent_card.security_schemes[scheme_name]
                header = self._resolve_header(scheme_name, scheme, credential)

                if header is None:
                    # Unsupported scheme type (e.g. API key in query/cookie).
                    # Treat as unsatisfiable so the requirement is skipped.
                    satisfiable = False
                    break

                collected[scheme_name] = header

            if not satisfiable:
                continue  # OR: try the next requirement

            # Pass 2: apply all collected credentials at once.
            if args.context is None:
                args.context = ClientCallContext()

            if args.context.service_parameters is None:
                args.context.service_parameters = {}

            for scheme_name, (header_name, header_value) in collected.items():
                args.context.service_parameters[header_name] = header_value
                logger.debug(
                    "Applied credential for scheme '%s' (header '%s').",
                    scheme_name,
                    header_name,
                )

            return  # One requirement fully satisfied — done.

    @staticmethod
    def _resolve_header(
        scheme_name: str,
        scheme: SecurityScheme,
        credential: str,
    ) -> tuple[str, str] | None:
        """Map a security scheme + credential to a ``(header_name, value)`` pair.

        Returns ``None`` when the scheme type is not supported (e.g. API
        key in query or cookie), signalling that the enclosing requirement
        cannot be satisfied via HTTP headers alone.
        """
        # HTTP Bearer authentication
        if (
            scheme.HasField('http_auth_security_scheme')
            and scheme.http_auth_security_scheme.scheme.lower() == 'bearer'
        ):
            return ('Authorization', f'Bearer {credential}')

        # OAuth2 and OIDC schemes are implicitly Bearer
        if scheme.HasField('oauth2_security_scheme') or scheme.HasField(
            'open_id_connect_security_scheme'
        ):
            return ('Authorization', f'Bearer {credential}')

        # API Key in Header
        if (
            scheme.HasField('api_key_security_scheme')
            and scheme.api_key_security_scheme.location.lower() == 'header'
        ):
            return (scheme.api_key_security_scheme.name, credential)

        # Unsupported scheme type
        logger.debug(
            "Scheme '%s' has an unsupported type or location; skipping.",
            scheme_name,
        )
        return None

    async def after(self, args: AfterArgs) -> None:
        """Invoked after the method is executed."""
