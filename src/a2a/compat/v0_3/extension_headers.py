"""Shared header name constants for v0.3 extension compatibility.

The current spec uses ``A2A-Extensions``. v0.3 used the ``X-`` prefixed
``X-A2A-Extensions`` form. v0.3 compat servers and clients accept/emit both
names so they can interoperate with peers that only know the legacy one.
"""

from a2a.client.service_parameters import ServiceParameters
from a2a.extensions.common import HTTP_EXTENSION_HEADER


LEGACY_HTTP_EXTENSION_HEADER = f'X-{HTTP_EXTENSION_HEADER}'


def add_legacy_extension_header(parameters: ServiceParameters) -> None:
    """Mirrors the ``A2A-Extensions`` parameter under its legacy name in-place.

    Used by v0.3 compat client transports so that requests can be understood
    by older v0.3 servers that only recognize ``X-A2A-Extensions``.
    """
    if (
        HTTP_EXTENSION_HEADER in parameters
        and LEGACY_HTTP_EXTENSION_HEADER not in parameters
    ):
        parameters[LEGACY_HTTP_EXTENSION_HEADER] = parameters[
            HTTP_EXTENSION_HEADER
        ]
