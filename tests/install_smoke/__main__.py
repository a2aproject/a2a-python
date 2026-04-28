"""Entry point: ``python -m tests.install_smoke <profile>``.

Exit codes:
    0 - All imports and runtime checks for the profile succeeded.
    1 - One or more imports or runtime checks failed.

See README.md for design notes and how to add new runtime checks.
"""

from __future__ import annotations

import importlib
import sys

from typing import TYPE_CHECKING

from tests.install_smoke.runtime import base_send_message


if TYPE_CHECKING:
    from collections.abc import Callable


# Core modules that MUST be importable with only base dependencies.
# These are the public API surface that every user gets with
# `pip install a2a-sdk` (no extras).
#
# Do NOT add modules here that require optional extras (grpc,
# http-server, sql, signing, telemetry, vertex, etc.). Those modules
# are expected to fail without their extras installed and should use
# try/except ImportError guards internally.
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


# Per-profile runtime exercises. Each callable raises on failure and
# returns None on success. These run after the import smoke succeeds
# and are meant to invoke real public-API code paths against the
# dependency versions resolved at install time.
#
# To add a new check: drop a module under `tests.install_smoke.runtime`
# exposing `NAME` and `check()`, then add a tuple here for the
# profile(s) whose extras it needs. See README.md.
RUNTIME_CHECKS: dict[str, list[tuple[str, Callable[[], None]]]] = {
    'base': [
        (base_send_message.NAME, base_send_message.check),
    ],
}


def main(argv: list[str]) -> int:
    profile = argv[1] if len(argv) > 1 else 'base'
    if profile not in PROFILES:
        print(f'Unknown profile {profile!r}. Available: {sorted(PROFILES)}')
        return 1

    modules = PROFILES[profile]
    import_failures: list[str] = []
    for module_name in modules:
        try:
            importlib.import_module(module_name)
        except Exception as e:  # noqa: BLE001, PERF203
            import_failures.append(f'{module_name}: {e}')

    print(f'Profile: {profile}')
    print(f'Tested {len(modules)} modules')
    print(f'  Passed: {len(modules) - len(import_failures)}')
    print(f'  Failed: {len(import_failures)}')

    if import_failures:
        print('\nFAILED imports:')
        for failure in import_failures:
            print(f'  - {failure}')
        return 1

    print('\nAll modules imported successfully.')

    runtime_checks = RUNTIME_CHECKS.get(profile, [])
    if not runtime_checks:
        return 0

    print(f'\nRunning {len(runtime_checks)} runtime check(s):')
    runtime_failures: list[str] = []
    for name, check in runtime_checks:
        try:
            check()
        except Exception as e:  # noqa: BLE001, PERF203
            runtime_failures.append(f'{name}: {type(e).__name__}: {e}')
            print(f'  - FAIL: {name}')
        else:
            print(f'  - OK:   {name}')

    if runtime_failures:
        print('\nFAILED runtime checks:')
        for failure in runtime_failures:
            print(f'  - {failure}')
        return 1

    print('\nAll runtime checks passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
