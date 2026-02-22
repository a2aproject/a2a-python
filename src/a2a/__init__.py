"""The A2A Python SDK.

This SDK provides tools for building A2A (Agent-to-Agent) protocol clients
and servers with support for:

- TLS/SSL secure communication (see a2a.client.TLSConfig)
- JSON Schema message validation (see a2a.validation)
- High-performance async operations via uvloop (see a2a.performance)

Example usage:

    from a2a.client import Client, ClientConfig, TLSConfig
    from a2a.validation import validate_message
    from a2a.performance import install_uvloop

    # Enable uvloop for better performance
    install_uvloop()

    # Configure TLS
    tls = TLSConfig(
        enabled=True,
        verify='/path/to/ca.pem',
        cert=('/path/to/client.pem', '/path/to/key.pem'),
    )

    config = ClientConfig(tls_config=tls)
"""

from a2a import client, performance, types, validation


__all__ = ['client', 'performance', 'types', 'validation']
