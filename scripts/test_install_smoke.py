#!/usr/bin/env python3
"""Smoke test for installations of a2a-sdk with various extras.

This script verifies that the public API modules associated with a
given installation profile can be imported without pulling in modules
that belong to other (uninstalled) optional extras.

It is designed to run WITHOUT pytest or any dev dependencies -- just
a clean venv with `pip install a2a-sdk[<profile>]`.

Usage:
    python scripts/test_install_smoke.py [profile]

    profile defaults to "base" and selects which set of modules to
    smoke-test. Available profiles:
      base        -- `pip install a2a-sdk`
      http-server -- `pip install a2a-sdk[http-server]`
      grpc        -- `pip install a2a-sdk[grpc]`
      telemetry   -- `pip install a2a-sdk[telemetry]`
      sql         -- `pip install a2a-sdk[sql]`

Exit codes:
    0 - All imports for the profile succeeded
    1 - One or more imports failed
"""

from __future__ import annotations

import importlib
import sys


# Core modules that MUST be importable with only base dependencies.
# These are the public API surface that every user gets with
# `pip install a2a-sdk` (no extras).
#
# Do NOT add modules here that require optional extras (grpc,
# http-server, sql, signing, telemetry, vertex, etc.).
# Those modules are expected to fail without their extras installed
# and should use try/except ImportError guards internally.
CORE_MODULES = [
    'a2a',
    'a2a.client',
    'a2a.client.auth',
    'a2a.client.base_client',
    'a2a.client.card_resolver',
    'a2a.client.client',
    'a2a.client.client_factory',
    'a2a.client.errors',
    'a2a.client.interceptors',
    'a2a.client.optionals',
    'a2a.client.transports',
    'a2a.server',
    'a2a.server.agent_execution',
    'a2a.server.context',
    'a2a.server.events',
    'a2a.server.request_handlers',
    'a2a.server.tasks',
    'a2a.types',
    'a2a.utils',
    'a2a.utils.constants',
    'a2a.utils.error_handlers',
    'a2a.utils.version_validator',
    'a2a.utils.proto_utils',
    'a2a.utils.task',
    'a2a.helpers.agent_card',
    'a2a.helpers.proto_helpers',
]

# Modules that MUST be importable with only the base + `http-server`
# extras installed (no `grpc`, `sql`, `signing`, `telemetry`, etc.).
#
# A user building a Starlette/FastAPI A2A server with
# `pip install a2a-sdk[http-server]` should be able to import these
# without the gRPC stack being present on the system.
HTTP_SERVER_MODULES = [
    'a2a.server.routes',
    'a2a.server.routes.agent_card_routes',
    'a2a.server.routes.common',
    'a2a.server.routes.jsonrpc_dispatcher',
    'a2a.server.routes.jsonrpc_routes',
    'a2a.server.routes.rest_dispatcher',
    'a2a.server.routes.rest_routes',
]

# Modules that MUST be importable with only the base + `grpc` extras
# installed (no `http-server`, `sql`, `signing`, `telemetry`, etc.).
GRPC_MODULES = [
    'a2a.server.request_handlers.grpc_handler',
    'a2a.client.transports.grpc',
    'a2a.compat.v0_3.grpc_handler',
    'a2a.compat.v0_3.grpc_transport',
]

# Modules that MUST be importable with only the base + `telemetry`
# extras installed.
TELEMETRY_MODULES = [
    'a2a.utils.telemetry',
]

# Modules that MUST be importable with only the base + `sql` extras
# installed (covers postgresql/mysql/sqlite drivers via SQLAlchemy).
SQL_MODULES = [
    'a2a.server.models',
    'a2a.server.tasks.database_task_store',
    'a2a.server.tasks.database_push_notification_config_store',
]


PROFILES: dict[str, list[str]] = {
    'base': CORE_MODULES,
    'http-server': CORE_MODULES + HTTP_SERVER_MODULES,
    'grpc': CORE_MODULES + GRPC_MODULES,
    'telemetry': CORE_MODULES + TELEMETRY_MODULES,
    'sql': CORE_MODULES + SQL_MODULES,
}


def main() -> int:
    profile = sys.argv[1] if len(sys.argv) > 1 else 'base'
    if profile not in PROFILES:
        print(f'Unknown profile {profile!r}. Available: {sorted(PROFILES)}')
        return 1

    modules = PROFILES[profile]
    failures: list[str] = []
    successes: list[str] = []

    for module_name in modules:
        try:
            importlib.import_module(module_name)
            successes.append(module_name)
        except Exception as e:  # noqa: BLE001, PERF203
            failures.append(f'{module_name}: {e}')

    print(f'Profile: {profile}')
    print(f'Tested {len(modules)} modules')
    print(f'  Passed: {len(successes)}')
    print(f'  Failed: {len(failures)}')

    if failures:
        print('\nFAILED imports:')
        for failure in failures:
            print(f'  - {failure}')
        return 1

    print('\nAll modules imported successfully.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
