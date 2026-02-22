"""TLS/SSL configuration for secure agent-to-agent communication.

This module provides TLS configuration classes for securing HTTP and gRPC
transport layers in A2A client-server communication.
"""

import ssl

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class TLSConfig:
    """TLS/SSL configuration for secure A2A communication.

    This class encapsulates all TLS-related settings for both client and
    server-side secure communication, including certificate verification,
    client authentication (mTLS), and custom SSL context configuration.

    Attributes:
        enabled: Whether TLS is enabled. Defaults to True.
        verify: Whether to verify server certificates. Can be a boolean,
            path to CA bundle file, or ssl.SSLContext. Defaults to True.
        cert: Client certificate for mTLS. Can be a tuple of (cert_file, key_file)
            or (cert_file, key_file, password). Defaults to None.
        ca_cert: Path to CA certificate file for server verification.
            Defaults to None (use system defaults).
        min_version: Minimum TLS version. Defaults to TLSv1.2.
        cipher_suites: List of allowed cipher suites. Defaults to None
            (use system defaults).
        verify_hostname: Whether to verify hostname in certificate.
            Defaults to True.
    """

    enabled: bool = True
    verify: bool | str | ssl.SSLContext | None = True
    cert: tuple[str, str] | tuple[str, str, str] | None = None
    ca_cert: str | Path | None = None
    min_version: str = 'TLSv1_2'
    cipher_suites: list[str] | None = None
    verify_hostname: bool = True

    def create_ssl_context(self) -> ssl.SSLContext:
        """Create an SSL context from this configuration.

        Returns:
            A configured ssl.SSLContext instance.
        """
        if isinstance(self.verify, ssl.SSLContext):
            return self.verify

        protocol_map = {
            'TLSv1_2': ssl.PROTOCOL_TLSv1_2,
            'TLSv1_3': ssl.PROTOCOL_TLS_CLIENT,
        }
        protocol = protocol_map.get(self.min_version, ssl.PROTOCOL_TLS_CLIENT)

        context = ssl.SSLContext(protocol)

        if self.ca_cert:
            context.load_verify_locations(cafile=str(self.ca_cert))
        elif isinstance(self.verify, str):
            context.load_verify_locations(cafile=self.verify)

        if self.verify is not False:
            context.verify_mode = ssl.CERT_REQUIRED
            context.check_hostname = self.verify_hostname

        if self.cert:
            if len(self.cert) == 2:
                context.load_cert_chain(self.cert[0], self.cert[1])
            elif len(self.cert) == 3:
                context.load_cert_chain(
                    self.cert[0], self.cert[1], password=self.cert[2].encode()
                )

        if self.cipher_suites:
            context.set_ciphers(':'.join(self.cipher_suites))

        return context

    def get_httpx_verify(self) -> bool | str | ssl.SSLContext:
        """Get the verify parameter for httpx client.

        Returns:
            Value suitable for httpx.AsyncClient verify parameter.
        """
        if not self.enabled:
            return False
        if isinstance(self.verify, ssl.SSLContext):
            return self.verify
        if isinstance(self.verify, str):
            return self.verify
        if self.ca_cert:
            return str(self.ca_cert)
        return self.verify if self.verify is not None else True

    def create_httpx_client(
        self,
        base_url: str | httpx.URL | None = None,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        """Create an httpx AsyncClient with this TLS configuration.

        Args:
            base_url: Base URL for the client.
            **kwargs: Additional arguments passed to httpx.AsyncClient.

        Returns:
            Configured httpx.AsyncClient instance.
        """
        client_kwargs: dict[str, Any] = {**kwargs}
        if base_url is not None:
            client_kwargs['base_url'] = base_url

        if not self.enabled:
            client_kwargs['verify'] = False
            return httpx.AsyncClient(**client_kwargs)

        client_kwargs['verify'] = self.get_httpx_verify()
        client_kwargs['cert'] = self.cert

        return httpx.AsyncClient(**client_kwargs)


def create_grpc_credentials(
    tls_config: TLSConfig | None = None,
    root_certificates: bytes | None = None,
) -> Any:
    """Create gRPC channel credentials from TLS configuration.

    Args:
        tls_config: TLS configuration. If None, creates default SSL credentials.
        root_certificates: Optional root certificates bytes for verification.

    Returns:
        gRPC channel credentials object.
    """
    try:
        import grpc
    except ImportError as e:
        raise ImportError(
            'gRPC credentials require grpcio to be installed. '
            "Install with: 'pip install a2a-sdk[grpc]'"
        ) from e

    if tls_config is None or not tls_config.enabled:
        return grpc.ssl_channel_credentials(root_certificates=root_certificates)

    cert_chain: bytes | None = None
    private_key: bytes | None = None

    if tls_config.cert:
        with open(tls_config.cert[0], 'rb') as f:
            cert_chain = f.read()
        with open(tls_config.cert[1], 'rb') as f:
            private_key = f.read()

    if tls_config.ca_cert:
        with open(tls_config.ca_cert, 'rb') as f:
            root_certificates = f.read()

    return grpc.ssl_channel_credentials(
        root_certificates=root_certificates,
        private_key=private_key,
        certificate_chain=cert_chain,
    )


def create_grpc_channel_factory(
    tls_config: TLSConfig | None = None,
) -> Callable[[str], Any]:
    """Create a gRPC channel factory with TLS configuration.

    Args:
        tls_config: TLS configuration for secure channels.

    Returns:
        A callable that creates gRPC channels for given URLs.
    """
    try:
        import grpc
    except ImportError as e:
        raise ImportError(
            'gRPC channel factory requires grpcio to be installed. '
            "Install with: 'pip install a2a-sdk[grpc]'"
        ) from e

    def factory(url: str) -> Any:
        if tls_config is None or not tls_config.enabled:
            return grpc.aio.insecure_channel(url)

        credentials = create_grpc_credentials(tls_config)
        return grpc.aio.secure_channel(url, credentials)

    return factory


def create_server_ssl_context(
    cert_file: str | Path,
    key_file: str | Path,
    ca_cert: str | Path | None = None,
    require_client_cert: bool = False,
    min_version: str = 'TLSv1_2',
) -> ssl.SSLContext:
    """Create an SSL context for A2A server.

    Args:
        cert_file: Path to server certificate file.
        key_file: Path to server private key file.
        ca_cert: Path to CA certificate for client verification.
        require_client_cert: Whether to require client certificates (mTLS).
        min_version: Minimum TLS version.

    Returns:
        Configured ssl.SSLContext for server use.
    """
    protocol_map = {
        'TLSv1_2': ssl.PROTOCOL_TLSv1_2,
        'TLSv1_3': ssl.PROTOCOL_TLS_SERVER,
    }
    protocol = protocol_map.get(min_version, ssl.PROTOCOL_TLS_SERVER)

    context = ssl.SSLContext(protocol)
    context.load_cert_chain(str(cert_file), str(key_file))

    if ca_cert:
        context.load_verify_locations(cafile=str(ca_cert))

    if require_client_cert:
        context.verify_mode = ssl.CERT_REQUIRED

    return context
