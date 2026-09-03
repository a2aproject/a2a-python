import logging  # noqa: I001

from a2a.client.auth.credentials import CredentialService
from a2a.client.client import ClientCallContext
from a2a.client.interceptors import (
    AfterArgs,
    BeforeArgs,
    ClientCallInterceptor,
)
from a2a.types.a2a_pb2 import SecurityScheme

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
                if await self._apply_credential(args, scheme_name):
                    return

    async def _apply_credential(
        self, args: BeforeArgs, scheme_name: str
    ) -> bool:
        """Fetches and applies a credential for a single scheme. Returns True if request should stop."""
        agent_card = args.agent_card
        credential = await self._credential_service.get_credentials(
            scheme_name, args.context
        )
        if not credential or scheme_name not in agent_card.security_schemes:
            return False

        scheme = agent_card.security_schemes[scheme_name]
        self._ensure_context(args)
        context = args.context
        if context is None:
            return False
        params = context.service_parameters
        if params is None:
            return False
        if self._apply_bearer(params, scheme, scheme_name, credential):
            return True
        return self._apply_api_key(params, scheme, scheme_name, credential)

    def _ensure_context(self, args: BeforeArgs) -> None:
        """Ensures the client call context and service parameters exist."""
        if args.context is None:
            args.context = ClientCallContext()
        if args.context.service_parameters is None:
            args.context.service_parameters = {}

    def _apply_bearer(
        self,
        service_parameters: dict[str, str],
        scheme: SecurityScheme,
        scheme_name: str,
        credential: str,
    ) -> bool:
        """Applies Bearer token for HTTP Bearer, OAuth2, or OIDC schemes. Returns True if applied."""
        is_http_bearer = (
            scheme.HasField('http_auth_security_scheme')
            and scheme.http_auth_security_scheme.scheme.lower() == 'bearer'
        )
        is_oauth2_or_oidc = scheme.HasField(
            'oauth2_security_scheme'
        ) or scheme.HasField('open_id_connect_security_scheme')

        if is_http_bearer or is_oauth2_or_oidc:
            service_parameters['Authorization'] = f'Bearer {credential}'
            logger.debug(
                "Added Bearer token for scheme '%s'.",
                scheme_name,
            )
            return True
        return False

    def _apply_api_key(
        self,
        service_parameters: dict[str, str],
        scheme: SecurityScheme,
        scheme_name: str,
        credential: str,
    ) -> bool:
        """Applies API Key header. Returns True if applied."""
        if (
            scheme.HasField('api_key_security_scheme')
            and scheme.api_key_security_scheme.location.lower() == 'header'
        ):
            service_parameters[scheme.api_key_security_scheme.name] = credential
            logger.debug(
                "Added API Key Header for scheme '%s'.",
                scheme_name,
            )
            return True
        return False

    async def after(self, args: AfterArgs) -> None:
        """Invoked after the method is executed."""
