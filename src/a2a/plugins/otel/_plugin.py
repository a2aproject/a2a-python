"""The OpenTelemetry plugin."""

from __future__ import annotations

import logging
import os

from importlib.metadata import version
from typing import TYPE_CHECKING, Any

from a2a.plugins.base import CompletePlugin


if TYPE_CHECKING:
    from opentelemetry.trace import Tracer, TracerProvider


logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace

    _otel_installed = True
except ImportError:
    _otel_installed = False


ENABLED_ENV_VAR = 'A2A_OTEL_PLUGIN_ENABLED'

INSTRUMENTING_MODULE_NAME = 'a2a-python-sdk.otel'


def _env_enabled() -> bool:
    return os.getenv(ENABLED_ENV_VAR, 'true').lower() == 'true'


def _sdk_version() -> str:
    return version('a2a-sdk')


class OpenTelemetryPlugin(CompletePlugin):
    """Reports A2A client and server calls as OpenTelemetry spans.

    Register it before creating clients and request handlers::

        from a2a import plugins
        from a2a.plugins.otel import OpenTelemetryPlugin

        plugins.set_global(plugins.sequential(OpenTelemetryPlugin()))

    The application configures the OpenTelemetry SDK and its exporter; this
    plugin only creates spans. Set ``A2A_OTEL_PLUGIN_ENABLED=false`` to turn
    it into a no-op without removing it.

    Attributes follow the A2A semantic conventions, which are not yet
    stable, so span contents may change.
    """

    name = 'opentelemetry'

    def __init__(self, tracer_provider: TracerProvider | None = None) -> None:
        """Initializes the plugin.

        Args:
            tracer_provider: Provider the tracer is taken from. Defaults to
                the global provider, which the application configures.

        Raises:
            ImportError: If OpenTelemetry is not installed. Asking for this
                plugin states intent, so a silent no-op would hide the
                missing dependency.
        """
        if not _otel_installed:
            raise ImportError(
                'OpenTelemetryPlugin requires OpenTelemetry. Install it '
                'with: \'pip install "a2a-sdk[telemetry]"\''
            )

        self._enabled = _env_enabled()
        if not self._enabled:
            logger.debug(
                'A2A OpenTelemetry plugin disabled via %s. No spans will '
                'be reported.',
                ENABLED_ENV_VAR,
            )
            return

        provider: Any = tracer_provider if tracer_provider else trace
        self._tracer: Tracer = provider.get_tracer(
            INSTRUMENTING_MODULE_NAME, _sdk_version()
        )
