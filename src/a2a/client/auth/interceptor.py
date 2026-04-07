import logging  # noqa: I001

from a2a.client.auth.credentials import CredentialService
from a2a.client.client import ClientCallContext
from a2a.client.interceptors import (
    AfterArgs,
    BeforeArgs,
    ClientCallInterceptor,
)

logger = logging.getLogger(__name__)


class AuthInterceptor(ClientCallInterceptor):
    """An interceptor that automatically adds authentication details to requests.

    Based on the agent's security schemes.
    """

    def __init__(self, credential_service: CredentialService):
        self._credential_service = credential_service

    async def before(self, args: BeforeArgs) -> None:
        """Applies authentication headers to the request if credentials are available."""
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
            for scheme_name in requirement.schemes:
                credential = await self._credential_service.get_credentials(
                    scheme_name, args.context
                )
                if credential and scheme_name in agent_card.security_schemes:
                    scheme = agent_card.security_schemes[scheme_name]

                    if args.context is None:
                        args.context = ClientCallContext()

                    if args.context.service_parameters is None:
                        args.context.service_parameters = {}

                    # HTTP Bearer authentication
                    if (
                        scheme.HasField('http_auth_security_scheme')
                        and scheme.http_auth_security_scheme.scheme.lower()
                        == 'bearer'
                    ):
                        args.context.service_parameters['Authorization'] = (
                            f'Bearer {credential}'
                        )
                        logger.debug(
                            "Added Bearer token for scheme '%s'.",
                            scheme_name,
                        )
                        return

                    # OAuth2 and OIDC schemes are implicitly Bearer
                    if scheme.HasField(
                        'oauth2_security_scheme'
                    ) or scheme.HasField('open_id_connect_security_scheme'):
                        args.context.service_parameters['Authorization'] = (
                            f'Bearer {credential}'
                        )
                        logger.debug(
                            "Added Bearer token for scheme '%s'.",
                            scheme_name,
                        )
                        return

                    # API Key in Header
                    if (
                        scheme.HasField('api_key_security_scheme')
                        and scheme.api_key_security_scheme.location.lower()
                        == 'header'
                    ):
                        args.context.service_parameters[
                            scheme.api_key_security_scheme.name
                        ] = credential
                        logger.debug(
                            "Added API Key Header for scheme '%s'.",
                            scheme_name,
                        )
                        return

                # Note: Other cases like API keys in query/cookie are not handled and will be skipped.

    async def after(self, args: AfterArgs) -> None:
        """Invoked after the method is executed."""
