"""Agent Card signing and verification sample.

An A2A Agent Card is fetched over the network before any trust has been
established with the agent, so everything inside it -- the transport URLs, the
security schemes, the skills -- is attacker-controlled until it has been
verified. Card signing closes that gap: the agent signs the canonical form of
its own card with a private key (JWS, RFC 7515) and the client verifies that
signature against a public key it trusts before using the card.

This sample runs both halves:

**Server** (`serve`)
  - Signs the public Agent Card with an ES256 key using
    `a2a.utils.signing.create_agent_card_signer`.
  - Serves the signed card at `/.well-known/agent-card.json`.
  - Publishes the matching public key as a JWKS document at
    `/.well-known/jwks.json`, which the signature's `jku` header points to.

**Client** (`verify`)
  - Fetches the card with `A2ACardResolver` and verifies it with
    `a2a.utils.signing.create_signature_verifier`.
  - Resolves the verification key by `kid` from JWKS documents it fetched from
    an explicit allowlist of URLs -- following the card's own `jku` blindly
    would let an attacker serve both a forged card and the key that "verifies"
    it.

Run:

```bash
# End-to-end demo: starts the server, verifies a good card, then shows the
# failures a verifier is there to catch.
uv run python samples/agent_card_signing.py

# Server only.
uv run python samples/agent_card_signing.py serve

# Verify a card served by another process.
uv run python samples/agent_card_signing.py verify --url http://127.0.0.1:41242
```

Requires the `signing`, `encryption` and `fastapi` extras:
`pip install 'a2a-sdk[signing,encryption,fastapi]'`.
"""

import argparse
import asyncio
import contextlib
import copy
import json
import logging

from collections.abc import Callable, Mapping
from typing import Any

import httpx
import uvicorn

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from jwt.algorithms import ECAlgorithm
from jwt.api_jwk import PyJWK, PyJWKSet
from jwt.exceptions import PyJWKError
from jwt.utils import base64url_decode

from a2a.client import A2ACardResolver
from a2a.client.card_resolver import parse_agent_card
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from a2a.utils.signing import (
    InvalidSignaturesError,
    NoSignatureError,
    ProtectedHeader,
    create_agent_card_signer,
    create_signature_verifier,
)


logger = logging.getLogger(__name__)

JWKS_WELL_KNOWN_PATH = '/.well-known/jwks.json'
SIGNING_KEY_ID = 'sample-card-key-1'
SIGNING_ALGORITHM = 'ES256'
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 41242

# The verifier accepts only these algorithms. Pinning them prevents algorithm
# confusion attacks, where a forged card names a weaker algorithm that the
# verifier would otherwise happily accept.
ALLOWED_ALGORITHMS = ['ES256']


# --------------------------------------------------------------------------- #
# Server: sign the card, publish the public key
# --------------------------------------------------------------------------- #


def generate_signing_key(
    kid: str = SIGNING_KEY_ID,
) -> tuple[ec.EllipticCurvePrivateKey, dict[str, Any]]:
    """Generates an ES256 key pair for card signing.

    A real deployment loads a long-lived private key from a key management
    service or secret store instead. The public half is returned as a JWKS
    document, ready to be served at a stable URL.

    Args:
        kid: The key ID advertised in the JWS header and in the JWKS.

    Returns:
        A tuple of (private key, JWKS document).
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk: dict[str, Any] = ECAlgorithm.to_jwk(
        private_key.public_key(), as_dict=True
    )
    jwks = {
        'keys': [
            {**public_jwk, 'kid': kid, 'alg': SIGNING_ALGORITHM, 'use': 'sig'}
        ]
    }
    return private_key, jwks


def build_agent_card(base_url: str) -> AgentCard:
    """Builds the Agent Card that will be signed and served."""
    return AgentCard(
        name='Signed Card Agent',
        description='An agent that serves a signed Agent Card.',
        provider=AgentProvider(
            organization='A2A Samples', url='https://example.com'
        ),
        version='1.0.0',
        capabilities=AgentCapabilities(
            streaming=True, push_notifications=False
        ),
        default_input_modes=['text'],
        default_output_modes=['text'],
        skills=[
            AgentSkill(
                id='signed_card',
                name='Signed Card',
                description='Say hi.',
                tags=['sample', 'signing'],
                examples=['hi'],
            )
        ],
        supported_interfaces=[
            AgentInterface(
                protocol_binding='JSONRPC',
                protocol_version='1.0',
                url=f'{base_url}/a2a/jsonrpc',
            ),
        ],
    )


def sign_agent_card(
    agent_card: AgentCard,
    private_key: ec.EllipticCurvePrivateKey,
    jku: str,
    kid: str = SIGNING_KEY_ID,
) -> AgentCard:
    """Signs a copy of an Agent Card and returns the signed copy.

    The signature covers the JCS-canonicalized (RFC 8785) card with its
    `signatures` field removed, so a client can recompute exactly the same bytes
    from the card it received.

    Args:
        agent_card: The card to sign. It is copied rather than modified: the
            signer appends to `card.signatures`, so signing the same card object
            twice would leave it carrying two signatures.
        private_key: The private key to sign with.
        jku: URL of the JWKS document holding the matching public key. Clients
            use it to select the verification key.
        kid: ID of the signing key within that JWKS.

    Returns:
        A signed copy of the card.
    """
    protected_header: ProtectedHeader = {
        'alg': SIGNING_ALGORITHM,
        'kid': kid,
        'jku': jku,
        # RFC 7515 §4.1.9: "JOSE" marks a JWS using the JSON serialization.
        'typ': 'JOSE',
    }
    signer = create_agent_card_signer(
        signing_key=private_key,
        protected_header=protected_header,
    )
    return signer(copy.deepcopy(agent_card))


def create_app(base_url: str, jwks_path: str = JWKS_WELL_KNOWN_PATH) -> FastAPI:
    """Creates a FastAPI app serving a signed Agent Card and its JWKS.

    Args:
        base_url: Public base URL of this server. It ends up in the card's
            interface URLs and in the `jku` of the signature, so it has to be
            the URL clients actually reach.
        jwks_path: Path the public keys are served from.

    Returns:
        The FastAPI application.
    """
    private_key, jwks = generate_signing_key()
    agent_card = build_agent_card(base_url)
    jku = f'{base_url}{jwks_path}'

    # The card is static here, so it is signed once at startup. `card_modifier`
    # runs on every request, which is the hook to use when the served card is
    # built per request (per-caller skills, dynamic URLs) and therefore has to
    # be re-signed each time.
    signed_card = sign_agent_card(agent_card, private_key, jku=jku)

    async def card_modifier(card: AgentCard) -> AgentCard:
        return signed_card

    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(
            agent_card=agent_card,
            card_modifier=card_modifier,
        ),
    )

    @app.get(jwks_path)
    async def get_jwks() -> dict[str, Any]:
        """Publishes the public keys clients use to verify the card."""
        return jwks

    return app


# --------------------------------------------------------------------------- #
# Client: verify the card before trusting it
# --------------------------------------------------------------------------- #


class JwksKeyProvider:
    """Resolves an Agent Card signature's verification key from trusted JWKS.

    A signature's `jku` header says where its key lives -- but that header is
    part of the card, which is exactly what is not yet trusted. So this provider
    holds keys fetched from an allowlist of JWKS URLs and serves a signature
    only if its `jku` is one of them. Without that check an attacker could serve
    a forged card alongside a JWKS holding their own key, and the signature
    would verify perfectly.

    Lookups are pure in-memory: the verifier is a synchronous callable, often
    invoked from async code, so it must not do blocking network I/O. Fetch and
    refresh the key sets out of band with `from_urls`.
    """

    def __init__(self, jwks_by_url: Mapping[str, PyJWKSet]) -> None:
        """Initializes the key provider.

        Args:
            jwks_by_url: Already-fetched key sets, keyed by their JWKS URL.
                A signature naming any other `jku` is rejected.
        """
        self._jwks_by_url = dict(jwks_by_url)

    @classmethod
    async def from_urls(
        cls, trusted_jku_urls: set[str], http_client: httpx.AsyncClient
    ) -> 'JwksKeyProvider':
        """Fetches every trusted JWKS and builds a provider from them.

        Args:
            trusted_jku_urls: JWKS URLs this client is willing to accept keys
                from, pinned by configuration rather than read off the card.
            http_client: Client used to fetch the key sets.

        Returns:
            A provider serving the fetched keys.
        """
        jwks_by_url = {}
        for url in trusted_jku_urls:
            response = await http_client.get(url)
            response.raise_for_status()
            jwks_by_url[url] = PyJWKSet.from_dict(response.json())
        return cls(jwks_by_url)

    def __call__(self, kid: str | None, jku: str | None) -> PyJWK:
        """Returns the verification key for one signature.

        Failures are raised as `PyJWKError` (a `PyJWTError`) so the verifier
        treats this one signature as unverifiable and moves on to the next,
        rather than aborting on a card that also carries a good signature.

        Args:
            kid: Key ID from the signature's protected header.
            jku: JWKS URL from the signature's protected header.

        Returns:
            The public key to verify the signature with.

        Raises:
            PyJWKError: If the `jku` is not trusted, the `kid` is missing, or no
                key with that `kid` is published at that `jku`.
        """
        if not jku or jku not in self._jwks_by_url:
            raise PyJWKError(f'Signature names an untrusted jku: {jku}')
        if not kid:
            raise PyJWKError('Signature is missing a kid')

        try:
            return self._jwks_by_url[jku][kid]
        except KeyError as e:
            raise PyJWKError(f'No key with kid {kid!r} at {jku}') from e


async def create_card_verifier(
    trusted_jku_urls: set[str], http_client: httpx.AsyncClient
) -> Callable[[AgentCard], None]:
    """Builds a verifier that accepts cards signed by the trusted keys.

    Args:
        trusted_jku_urls: JWKS URLs to accept signing keys from.
        http_client: Client used to fetch those key sets.

    Returns:
        A callable that raises `SignatureVerificationError` for a card it cannot
        verify, and returns `None` for one it can.
    """
    key_provider = await JwksKeyProvider.from_urls(
        trusted_jku_urls, http_client
    )
    return create_signature_verifier(
        key_provider=key_provider,
        algorithms=ALLOWED_ALGORITHMS,
    )


async def verify_remote_card(base_url: str) -> AgentCard:
    """Fetches an Agent Card and verifies its signature before returning it.

    Args:
        base_url: Base URL of the agent serving the card.

    Returns:
        The verified Agent Card.

    Raises:
        SignatureVerificationError: If the card carries no signature, or none of
            its signatures can be verified.
    """
    async with httpx.AsyncClient() as http_client:
        verifier = await create_card_verifier(
            trusted_jku_urls={f'{base_url}{JWKS_WELL_KNOWN_PATH}'},
            http_client=http_client,
        )
        resolver = A2ACardResolver(http_client, base_url)
        # `get_agent_card` propagates the verifier's error, so a card it returns
        # has always been verified.
        return await resolver.get_agent_card(signature_verifier=verifier)


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #


async def _fetch_card_json(
    base_url: str, http_client: httpx.AsyncClient
) -> dict[str, Any]:
    """Fetches the raw Agent Card JSON, bypassing verification."""
    response = await http_client.get(f'{base_url}{AGENT_CARD_WELL_KNOWN_PATH}')
    response.raise_for_status()
    card_json: dict[str, Any] = response.json()
    return card_json


async def run_demo(base_url: str) -> None:
    """Verifies the agent's card, then three cases a verifier must reject."""
    async with httpx.AsyncClient() as http_client:
        verifier = await create_card_verifier(
            trusted_jku_urls={f'{base_url}{JWKS_WELL_KNOWN_PATH}'},
            http_client=http_client,
        )

        print('\n1. The card served by the agent')
        card = await A2ACardResolver(http_client, base_url).get_agent_card(
            signature_verifier=verifier
        )
        # The protected header is base64url-encoded JWS header JSON.
        protected_header = json.loads(
            base64url_decode(card.signatures[0].protected.encode('utf-8'))
        )
        print(f'   verified card for agent: {card.name}')
        print(f'   protected header: {protected_header}')

        print('\n2. A card whose transport URL was rewritten in transit')
        tampered_json = await _fetch_card_json(base_url, http_client)
        tampered_json['supportedInterfaces'][0]['url'] = (
            'http://attacker.example/a2a/jsonrpc'
        )
        try:
            verifier(parse_agent_card(tampered_json))
        except InvalidSignaturesError as e:
            print(f'   rejected as expected: {e}')
        else:
            raise RuntimeError('the tampered card was accepted')

        print('\n3. A card with its signature stripped off')
        unsigned_json = await _fetch_card_json(base_url, http_client)
        unsigned_json.pop('signatures', None)
        try:
            verifier(parse_agent_card(unsigned_json))
        except NoSignatureError as e:
            print(f'   rejected as expected: {e}')
        else:
            raise RuntimeError('the unsigned card was accepted')

        print('\n4. The genuine card, checked by a client that pins other keys')
        # Same keys, published at a URL this client does not trust. The
        # signature itself is fine; it is the key source that is not pinned, so
        # the card is rejected rather than verified against whatever the card
        # points at.
        keys_response = await http_client.get(
            f'{base_url}{JWKS_WELL_KNOWN_PATH}'
        )
        keys_response.raise_for_status()
        pinned_elsewhere = create_signature_verifier(
            key_provider=JwksKeyProvider(
                {
                    'https://keys.example.com/jwks.json': PyJWKSet.from_dict(
                        keys_response.json()
                    )
                }
            ),
            algorithms=ALLOWED_ALGORITHMS,
        )
        try:
            pinned_elsewhere(card)
        except InvalidSignaturesError as e:
            print(f'   rejected as expected: {e}')
        else:
            raise RuntimeError('the card with an untrusted jku was accepted')


async def serve(host: str, port: int) -> None:
    """Runs the signed-card agent server."""
    base_url = f'http://{host}:{port}'
    config = uvicorn.Config(create_app(base_url), host=host, port=port)
    server = uvicorn.Server(config)
    logger.info('Signed Agent Card: %s%s', base_url, AGENT_CARD_WELL_KNOWN_PATH)
    logger.info('JWKS: %s%s', base_url, JWKS_WELL_KNOWN_PATH)
    await server.serve()


async def serve_and_demo(host: str, port: int) -> None:
    """Starts the server, runs the client demo against it, then shuts down."""
    base_url = f'http://{host}:{port}'
    config = uvicorn.Config(
        create_app(base_url), host=host, port=port, log_level='warning'
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    try:
        # uvicorn signals readiness with a flag rather than an event.
        while not server.started:  # noqa: ASYNC110
            await asyncio.sleep(0.05)
        print(f'Serving a signed Agent Card at {base_url}')
        await run_demo(base_url)
    finally:
        server.should_exit = True
        await server_task


def main() -> None:
    """Parses arguments and runs the requested mode."""
    server_args = argparse.ArgumentParser(add_help=False)
    server_args.add_argument('--host', default=DEFAULT_HOST)
    server_args.add_argument('--port', type=int, default=DEFAULT_PORT)

    parser = argparse.ArgumentParser(
        description='A2A Agent Card signing sample'
    )
    parser.set_defaults(mode='demo', host=DEFAULT_HOST, port=DEFAULT_PORT)
    subparsers = parser.add_subparsers(dest='mode')
    subparsers.add_parser(
        'demo',
        parents=[server_args],
        help='Run the server and the client (default)',
    )
    subparsers.add_parser(
        'serve', parents=[server_args], help='Run the server only'
    )
    verify_parser = subparsers.add_parser(
        'verify', help='Verify a card served elsewhere'
    )
    verify_parser.add_argument(
        '--url', default=f'http://{DEFAULT_HOST}:{DEFAULT_PORT}'
    )

    args = parser.parse_args()
    # The client modes print a numbered walkthrough; INFO logging from httpx and
    # the card resolver would bury it.
    logging.basicConfig(
        level=logging.INFO if args.mode == 'serve' else logging.WARNING
    )
    if args.mode == 'serve':
        asyncio.run(serve(args.host, args.port))
    elif args.mode == 'verify':
        card = asyncio.run(verify_remote_card(args.url))
        print(f'Verified card for agent: {card.name}')
    else:
        asyncio.run(serve_and_demo(args.host, args.port))


if __name__ == '__main__':
    with contextlib.suppress(KeyboardInterrupt):
        main()
