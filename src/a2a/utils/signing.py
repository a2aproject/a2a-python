import json

from collections.abc import Callable
from typing import Any, TypedDict

from a2a.utils.helpers import canonicalize_agent_card


try:
    import jwt

    from jwt.api_jwk import PyJWK
    from jwt.exceptions import PyJWTError
    from jwt.utils import base64url_decode, base64url_encode
except ImportError as e:
    raise ImportError(
        'A2A Signing requires PyJWT to be installed. '
        'Install with: '
        "'pip install a2a-sdk[signing]'"
    ) from e

from a2a.types import AgentCard, AgentCardSignature


class SignatureVerificationError(Exception):
    """Base exception for signature verification errors."""


class NoSignatureError(SignatureVerificationError):
    """Exception raised when no signature is found on an AgentCard."""


class InvalidSignaturesError(SignatureVerificationError):
    """Exception raised when all signatures are invalid."""


class ProtectedHeader(TypedDict):
    """Protected header parameters for JWS (JSON Web Signature)."""

    kid: str
    """ Key identifier. """
    alg: str | None
    """ Algorithm used for signing. """
    jku: str | None
    """ JSON Web Key Set URL. """
    typ: str | None
    """ Token type.

    Best practice: SHOULD be "JOSE" for JWS tokens.
    """


def create_agent_card_signer(
    signing_key: PyJWK | str | bytes,
    protected_header: ProtectedHeader,
    header: dict[str, Any] | None = None,
) -> Callable[[AgentCard], AgentCard]:
    """Creates a function that signs an AgentCard and adds the signature.

    Args:
        signing_key: The private key for signing.
        protected_header: The protected header parameters.
        header: Unprotected header parameters.

    Returns:
        A callable that takes an AgentCard and returns the modified AgentCard with a signature.
    """

    def agent_card_signer(agent_card: AgentCard) -> AgentCard:
        """Signs agent card."""
        canonical_payload = canonicalize_agent_card(agent_card)
        payload_dict = json.loads(canonical_payload)

        jws_string = jwt.encode(
            payload=payload_dict,
            key=signing_key,
            algorithm=protected_header.get('alg', 'HS256'),
            headers=dict(protected_header),
        )

        # The result of jwt.encode is a compact serialization: HEADER.PAYLOAD.SIGNATURE
        protected, _, signature = jws_string.split('.')

        agent_card_signature = AgentCardSignature(
            header=header,
            protected=protected,
            signature=signature,
        )

        agent_card.signatures = (agent_card.signatures or []) + [
            agent_card_signature
        ]
        return agent_card

    return agent_card_signer


def create_signature_verifier(
    key_provider: Callable[[str | None, str | None], PyJWK | str | bytes],
    algorithms: list[str],
) -> Callable[[AgentCard], None]:
    """Creates a function that verifies the signatures on an AgentCard.

    The verifier succeeds if at least one signature is valid. Otherwise, it raises an error.

    Args:
        key_provider: A callable that accepts a key ID (kid) and a JWK Set URL (jku) and returns the verification key.
                      This function is responsible for fetching the correct key for a given signature.
        algorithms: A list of acceptable algorithms (e.g., ['ES256', 'RS256']) for verification used to prevent algorithm confusion attacks.

    Returns:
        A function that takes an AgentCard as input, and raises an error if none of the signatures are valid.
    """

    def signature_verifier(
        agent_card: AgentCard,
    ) -> None:
        """Verifies agent card signatures."""
        if not agent_card.signatures:
            raise NoSignatureError('AgentCard has no signatures to verify.')

        for agent_card_signature in agent_card.signatures:
            try:
                # get verification key
                protected_header_json = base64url_decode(
                    agent_card_signature.protected.encode('utf-8')
                ).decode('utf-8')
                protected_header = json.loads(protected_header_json)
                kid = protected_header.get('kid')
                jku = protected_header.get('jku')
                verification_key = key_provider(kid, jku)

                canonical_payload = canonicalize_agent_card(agent_card)
                encoded_payload = base64url_encode(
                    canonical_payload.encode('utf-8')
                ).decode('utf-8')

                token = f'{agent_card_signature.protected}.{encoded_payload}.{agent_card_signature.signature}'
                jwt.decode(
                    jwt=token,
                    key=verification_key,
                    algorithms=algorithms,
                )
                # Found a valid signature, exit the loop and function
                break
            except PyJWTError:
                continue
        else:
            # This block runs only if the loop completes without a break
            raise InvalidSignaturesError('No valid signature found')

    return signature_verifier
