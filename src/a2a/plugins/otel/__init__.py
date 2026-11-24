"""OpenTelemetry instrumentation for the A2A SDK.

Importing this package requires the ``telemetry`` extra::

    pip install "a2a-sdk[telemetry]"

`a2a.plugins` does not import it, so installs without that extra are
unaffected.
"""

from a2a.plugins.otel.plugin import OpenTelemetryPlugin


__all__ = ['OpenTelemetryPlugin']
