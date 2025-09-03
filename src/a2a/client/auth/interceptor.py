import logging  # noqa: I001
from typing import Any

from a2a.client.auth.credentials import CredentialService
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.types import (
    AgentCard,
    APIKeySecurityScheme,
    HTTPAuthSecurityScheme,
    In,
    OAuth2SecurityScheme,
    OpenIdConnectSecurityScheme,
)

logger = logging.getLogger(__name__)


class AuthInterceptor(ClientCallInterceptor):
    """An interceptor that automatically adds authentication details to requests.

    Based on the agent's security schemes.
    """

    def __init__(self, credential_service: CredentialService):
        self._credential_service = credential_service

    async def intercept(
        self,
        method_name: str,
        request_payload: dict[str, Any],
        http_kwargs: dict[str, Any],
        agent_card: AgentCard | None,
        context: ClientCallContext | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Applies authentication headers to the request if credentials are available."""
        if (
            agent_card is None
            or agent_card.security is None
            or agent_card.security_schemes is None
        ):
            return request_payload, http_kwargs

        for requirement in agent_card.security:
            for scheme_name in requirement:
                credential = await self._credential_service.get_credentials(
                    scheme_name, context
                )
                if credential and scheme_name in agent_card.security_schemes:
                    scheme_def_union = agent_card.security_schemes.get(
                        scheme_name
                    )
                    if not scheme_def_union:
                        continue
                    scheme_def = scheme_def_union.root

                    headers = http_kwargs.get('headers', {})

                    match scheme_def:
                        # Case 1a: HTTP Bearer scheme with an if guard
                        case HTTPAuthSecurityScheme() if (
                            scheme_def.scheme.lower() == 'bearer'
                        ):
                            headers['Authorization'] = f'Bearer {credential}'
                            logger.debug(
                                "Added Bearer token for scheme '%s' (type: %s).",
                                scheme_name,
                                scheme_def.type,
                            )
                            http_kwargs['headers'] = headers
                            return request_payload, http_kwargs

                        # Case 1b: OAuth2 and OIDC schemes, which are implicitly Bearer
                        case (
                            OAuth2SecurityScheme()
                            | OpenIdConnectSecurityScheme()
                        ):
                            headers['Authorization'] = f'Bearer {credential}'
                            logger.debug(
                                "Added Bearer token for scheme '%s' (type: %s).",
                                scheme_name,
                                scheme_def.type,
                            )
                            http_kwargs['headers'] = headers
                            return request_payload, http_kwargs

                        # Case 2: API Key in Header
                        case APIKeySecurityScheme(in_=In.header):
                            headers[scheme_def.name] = credential
                            logger.debug(
                                "Added API Key Header for scheme '%s'.",
                                scheme_name,
                            )
                            http_kwargs['headers'] = headers
                            return request_payload, http_kwargs

                # Note: Other cases like API keys in query/cookie are not handled and will be skipped.

        return request_payload, http_kwargs
