import json

from collections.abc import Callable
from typing import Any


try:
    from jose import jws
    from jose.backends.base import Key
    from jose.exceptions import JOSEError
    from jose.utils import base64url_decode, base64url_encode
except ImportError as e:
    raise ImportError(
        'A2AUtilsSigning requires python-jose to be installed. '
        'Install with: '
        "'pip install a2a-sdk[signing]'"
    ) from e

from a2a.types import AgentCard, AgentCardSignature


def clean_empty(d: Any) -> Any:
    """Recursively remove empty strings, lists, dicts, and None values from a dictionary."""
    if isinstance(d, dict):
        cleaned_dict: dict[Any, Any] = {k: clean_empty(v) for k, v in d.items()}
        return {
            k: v
            for k, v in cleaned_dict.items()
            if v is not None and (isinstance(v, (bool, int, float)) or v)
        }
    if isinstance(d, list):
        cleaned_list: list[Any] = [clean_empty(v) for v in d]
        return [
            v
            for v in cleaned_list
            if v is not None and (isinstance(v, (bool, int, float)) or v)
        ]
    return d if d not in ['', [], {}, None] else None


def canonicalize_agent_card(agent_card: AgentCard) -> str:
    """Canonicalizes the Agent Card JSON according to RFC 8785 (JCS)."""
    card_dict = agent_card.model_dump(
        exclude={'signatures'},
        exclude_defaults=True,
        by_alias=True,
    )
    # Ensure 'protocol_version' is always included
    protocol_version_alias = (
        AgentCard.model_fields['protocol_version'].alias or 'protocol_version'
    )
    if protocol_version_alias not in card_dict:
        card_dict[protocol_version_alias] = agent_card.protocol_version

    # Recursively remove empty/None values
    cleaned_dict = clean_empty(card_dict)

    return json.dumps(cleaned_dict, separators=(',', ':'), sort_keys=True)


def create_agent_card_signer(
    signing_key: str | bytes | dict[str, Any] | Key,
    kid: str,
    alg: str = 'HS256',
    jku: str | None = None,
) -> Callable[[AgentCard], AgentCard]:
    """Creates a function that signs an AgentCard and adds the signature.

    Args:
        signing_key: The private key for signing.
        kid: Key ID for the signing key.
        alg: The algorithm to use (e.g., "ES256", "RS256").
        jku: Optional URL to the JWKS.

    Returns:
        A callable that takes an AgentCard and returns the modified AgentCard with a signature.
    """

    def agent_card_signer(agent_card: AgentCard) -> AgentCard:
        """The actual card_modifier function."""
        canonical_payload = canonicalize_agent_card(agent_card)

        headers = {'kid': kid, 'typ': 'JOSE'}
        if jku:
            headers['jku'] = jku

        jws_string = jws.sign(
            payload=canonical_payload.encode('utf-8'),
            key=signing_key,
            headers=headers,
            algorithm=alg,
        )

        # The result of jws.sign is a compact serialization: HEADER.PAYLOAD.SIGNATURE
        protected_header, _, signature = jws_string.split('.')

        agent_card_signature = AgentCardSignature(
            protected=protected_header,
            signature=signature,
        )

        agent_card.signatures = (agent_card.signatures or []) + [
            agent_card_signature
        ]
        return agent_card

    return agent_card_signer


def create_signature_verifier(
    key_provider: Callable[
        [str | None, str | None], str | bytes | dict[str, Any] | Key
    ],
) -> Callable[[AgentCard], None]:
    """Creates a function that verifies AgentCard signatures.

    Args:
        key_provider: A callable that takes key-id (kid) and JSON web key url (jku) and returns the verification key.

    Returns:
        A callable that takes an AgentCard, and raises an error if none of the signatures are valid.
    """

    def signature_verifier(
        agent_card: AgentCard,
    ) -> None:
        """The actual signature_verifier function."""
        if not agent_card.signatures:
            raise JOSEError('No signatures found on AgentCard')

        last_error = None
        for agent_card_signature in agent_card.signatures:
            try:
                # fetch kid and jku from protected header
                protected_header_json = base64url_decode(
                    agent_card_signature.protected.encode('utf-8')
                ).decode('utf-8')
                protected_header = json.loads(protected_header_json)
                kid = protected_header.get('kid')
                jku = protected_header.get('jku')
                alg = protected_header.get('alg')
                verification_key = key_provider(kid, jku)

                canonical_payload = canonicalize_agent_card(agent_card)
                encoded_payload = base64url_encode(
                    canonical_payload.encode('utf-8')
                ).decode('utf-8')
                token = f'{agent_card_signature.protected}.{encoded_payload}.{agent_card_signature.signature}'

                jws.verify(
                    token=token,
                    key=verification_key,
                    algorithms=[alg] if alg else None,
                )
                # Found a valid signature, exit the loop and function
                break
            except JOSEError as e:
                last_error = e
                continue
        else:
            # This block runs only if the loop completes without a break
            raise JOSEError('No valid signature found') from last_error

    return signature_verifier
