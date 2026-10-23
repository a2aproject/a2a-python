import json

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypedDict, cast

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as ProtobufMessage


try:
    import jwt

    from jwt import api_jws
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
from a2a.utils._jcs import CanonicalizationError, canonicalize
from a2a.utils.proto_utils import (
    _field_is_repeated,
    _field_is_required,
    warn_on_missing_required_fields,
)


if TYPE_CHECKING:
    from google.protobuf.descriptor import FieldDescriptor


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
        canonical_payload = _canonicalize_agent_card(agent_card)

        # The JWS payload has to be the canonical bytes themselves. Handing a
        # parsed dict to the JWT layer would let PyJWT re-serialize it with its
        # own json.dumps, which escapes non-ASCII, so the bytes signed would
        # differ from the bytes the verifier canonicalizes and checks.
        jws_string = api_jws.encode(
            payload=canonical_payload.encode('utf-8'),
            key=signing_key,
            algorithm=protected_header.get('alg', 'HS256'),
            headers=dict(protected_header),
        )

        # The result is a compact serialization: HEADER.PAYLOAD.SIGNATURE
        protected, _, signature = jws_string.split('.')

        agent_card_signature = AgentCardSignature(
            header=header,
            protected=protected,
            signature=signature,
        )

        agent_card.signatures.append(agent_card_signature)
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

        # The canonical form does not depend on which signature is being
        # checked, so it is computed once. A card with no canonical form has no
        # verifiable signature, and reporting that as InvalidSignaturesError
        # keeps every failure on this path a SignatureVerificationError.
        try:
            canonical_payload = _canonicalize_agent_card(agent_card)
        except CanonicalizationError as e:
            raise InvalidSignaturesError(
                'AgentCard cannot be canonicalized for verification'
            ) from e
        encoded_payload = base64url_encode(
            canonical_payload.encode('utf-8')
        ).decode('utf-8')

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


def _apply_field_presence(
    message: ProtobufMessage, serialized: dict[str, Any]
) -> None:
    """Apply A2A 8.4.1 field presence to Protobuf's JSON representation.

    REQUIRED fields always participate, explicitly present optionals retain
    their defaults, and implicit optional defaults are omitted. Protobuf
    well-known types have their own JSON representation: in particular,
    extension Struct contents are JSON data rather than A2A message fields.
    Their empty values must reach JCS unchanged, including its nesting bound.
    """
    if message.DESCRIPTOR.full_name.startswith('google.protobuf.'):
        return

    for raw_field in message.DESCRIPTOR.fields:
        field = cast('FieldDescriptor', raw_field)
        key = field.json_name
        value = getattr(message, field.name)
        repeated = _field_is_repeated(field)
        if _field_is_required(field):
            # MessageToDict prints implicit scalar/repeated defaults, but an
            # absent required message still needs its default representation.
            if key not in serialized and field.message_type is not None:
                serialized[key] = MessageToDict(
                    value, always_print_fields_with_no_presence=True
                )
        elif (
            (field.has_presence and not message.HasField(field.name))
            or (not field.has_presence and repeated and not value)
            or (
                not field.has_presence
                and not repeated
                and value == field.default_value
            )
        ):
            serialized.pop(key, None)
            continue

        if key not in serialized or field.message_type is None:
            continue
        if field.message_type.GetOptions().map_entry:
            value_field = field.message_type.fields_by_name['value']
            if value_field.message_type is not None:
                for map_key, item in value.items():
                    _apply_field_presence(item, serialized[key][map_key])
        elif repeated:
            for item, item_dict in zip(value, serialized[key], strict=True):
                _apply_field_presence(item, item_dict)
        else:
            _apply_field_presence(value, serialized[key])


def _canonicalize_agent_card(agent_card: AgentCard) -> str:
    """Canonicalizes the Agent Card JSON according to RFC 8785 (JCS)."""
    warn_on_missing_required_fields(
        agent_card, 'agent_card passed to _canonicalize_agent_card:'
    )
    card_dict = MessageToDict(
        agent_card,
        always_print_fields_with_no_presence=True,
    )
    # Remove signatures field if present
    card_dict.pop('signatures', None)

    _apply_field_presence(agent_card, card_dict)
    return canonicalize(card_dict)
