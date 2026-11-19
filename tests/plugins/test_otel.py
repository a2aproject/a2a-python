"""The OpenTelemetry plugin's construction and switches."""

import builtins
import importlib

from unittest.mock import MagicMock

import pytest

from a2a import plugins
from a2a.plugins.otel import OpenTelemetryPlugin, _plugin as otel_plugin
from opentelemetry.sdk.trace import TracerProvider


def test_plugin_is_a_client_and_server_plugin():
    assert isinstance(OpenTelemetryPlugin(), plugins.CompletePlugin)


def test_plugin_registers_with_the_manager():
    manager = plugins.sequential(OpenTelemetryPlugin())

    assert manager.has_client_hooks
    assert manager.has_server_hooks


def test_tracer_is_taken_from_the_given_provider():
    provider = MagicMock(spec=TracerProvider)

    plugin = OpenTelemetryPlugin(provider)

    assert plugin._tracer is provider.get_tracer.return_value
    name, version = provider.get_tracer.call_args.args
    assert name == 'a2a-python-sdk.otel'
    assert version == otel_plugin._sdk_version()


def test_no_provider_falls_back_to_the_global_one():
    """The application owns the provider, so None means whatever it set."""
    plugin = OpenTelemetryPlugin()

    assert plugin._enabled
    assert plugin._tracer is not None


@pytest.mark.parametrize('value', ['false', 'False', 'FALSE', 'no'])
def test_env_var_disables_the_plugin(monkeypatch, value):
    monkeypatch.setenv(otel_plugin.ENABLED_ENV_VAR, value)

    plugin = OpenTelemetryPlugin()

    assert not plugin._enabled
    assert not hasattr(plugin, '_tracer')


def test_plugin_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv(otel_plugin.ENABLED_ENV_VAR, raising=False)

    assert OpenTelemetryPlugin()._enabled


def test_missing_opentelemetry_raises_with_an_install_hint(monkeypatch):
    """Asking for this plugin is intent, so a silent no-op would hide it."""
    monkeypatch.setattr(otel_plugin, '_otel_installed', False)

    with pytest.raises(ImportError, match=r'a2a-sdk\[telemetry\]'):
        OpenTelemetryPlugin()


def test_module_imports_without_opentelemetry(monkeypatch):
    """The module must import cleanly when the extra is absent."""
    real_import = builtins.__import__

    def without_otel(name, *args, **kwargs):
        if name.startswith('opentelemetry'):
            raise ImportError('simulated missing extra')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', without_otel)
    reloaded = importlib.reload(otel_plugin)
    monkeypatch.undo()

    try:
        assert reloaded._otel_installed is False
    finally:
        importlib.reload(otel_plugin)
