import json

from collections.abc import Callable
from typing import Any, TypedDict


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


def clean_empty(d: Any) -> Any:
    """Recursively remove empty strings, lists and dicts from a dictionary."""
    if isinstance(d, dict):
        cleaned_dict: dict[Any, Any] = {k: clean_empty(v) for k, v in d.items()}
        return {k: v for k, v in cleaned_dict.items() if v}
    if isinstance(d, list):
        cleaned_list: list[Any] = [clean_empty(v) for v in d]
        return [v for v in cleaned_list if v]
    return d if d not in ['', [], {}] else None


def canonicalize_agent_card(agent_card: AgentCard) -> str:
    """Canonicalizes the Agent Card JSON according to RFC 8785 (JCS)."""
    card_dict = agent_card.model_dump(
        exclude={'signatures'},
        exclude_defaults=True,
        exclude_none=True,
        by_alias=True,
    )
    # Recursively remove empty values
    cleaned_dict = clean_empty(card_dict)
    return json.dumps(cleaned_dict, separators=(',', ':'), sort_keys=True)


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
            headers=protected_header,
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
    """Creates a function that verifies AgentCard signatures.

    Args:
        key_provider: A callable that takes key-id and JSON web key url and returns the verification key.
        algorithms: List of acceptable algorithms for verification used to prevent algorithm confusion attacks.

    Returns:
        A callable that takes an AgentCard, and raises an error if none of the signatures are valid.
    """

    def signature_verifier(
        agent_card: AgentCard,
    ) -> None:
        """Verifies agent card signatures.

        Checks if at least one signature matches the key, otherwise raises an error.
        """
        if not agent_card.signatures:
            raise NoSignatureError('AgentCard has no signatures to verify.')

        last_error = None
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
            except PyJWTError as e:
                last_error = e
                continue
        else:
            # This block runs only if the loop completes without a break
            raise InvalidSignaturesError(
                'No valid signature found'
            ) from last_error

    return signature_verifier
