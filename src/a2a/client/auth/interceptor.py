import logging  # noqa: I001
from typing import Any

from a2a.client.auth.credentials import CredentialService
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.types.a2a_pb2 import AgentCard

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
            or not agent_card.security
            or not agent_card.security_schemes
        ):
            return request_payload, http_kwargs

        for requirement in agent_card.security:
            for scheme_name in requirement.schemes:
                credential = await self._credential_service.get_credentials(
                    scheme_name, context
                )
                if credential and scheme_name in agent_card.security_schemes:
                    scheme = agent_card.security_schemes.get(scheme_name)
                    if not scheme:
                        continue

                    headers = http_kwargs.get('headers', {})

                    # HTTP Bearer authentication
                    if (
                        scheme.HasField('http_auth_security_scheme')
                        and scheme.http_auth_security_scheme.scheme.lower()
                        == 'bearer'
                    ):
                        headers['Authorization'] = f'Bearer {credential}'
                        logger.debug(
                            "Added Bearer token for scheme '%s'.",
                            scheme_name,
                        )
                        http_kwargs['headers'] = headers
                        return request_payload, http_kwargs

                    # OAuth2 and OIDC schemes are implicitly Bearer
                    if scheme.HasField(
                        'oauth2_security_scheme'
                    ) or scheme.HasField('open_id_connect_security_scheme'):
                        headers['Authorization'] = f'Bearer {credential}'
                        logger.debug(
                            "Added Bearer token for scheme '%s'.",
                            scheme_name,
                        )
                        http_kwargs['headers'] = headers
                        return request_payload, http_kwargs

                    # API Key in Header
                    if (
                        scheme.HasField('api_key_security_scheme')
                        and scheme.api_key_security_scheme.location.lower()
                        == 'header'
                    ):
                        headers[scheme.api_key_security_scheme.name] = (
                            credential
                        )
                        logger.debug(
                            "Added API Key Header for scheme '%s'.",
                            scheme_name,
                        )
                        http_kwargs['headers'] = headers
                        return request_payload, http_kwargs

                # Note: Other cases like API keys in query/cookie are not handled and will be skipped.

        return request_payload, http_kwargs
