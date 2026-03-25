#!/usr/bin/env python3
"""Smoke test for minimal (base-only) installation of a2a-sdk.

This script verifies that all core public API modules can be imported
when only the base dependencies are installed (no optional extras).

It is designed to run WITHOUT pytest or any dev dependencies -- just
a clean venv with `pip install a2a-sdk`.

Usage:
    python scripts/test_minimal_install.py

Exit codes:
    0 - All core imports succeeded
    1 - One or more core imports failed
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
    'a2a.types',
    'a2a.utils',
    'a2a.utils.constants',
    'a2a.utils.helpers',
    'a2a.utils.proto_utils',
    'a2a.utils.artifact',
    'a2a.utils.message',
    'a2a.utils.parts',
    'a2a.utils.task',
    'a2a.utils.error_handlers',
    'a2a.client',
    'a2a.client.client_factory',
    'a2a.client.base_client',
    'a2a.client.card_resolver',
    'a2a.client.client',
    'a2a.client.errors',
    'a2a.client.helpers',
    'a2a.client.interceptors',
    'a2a.client.optionals',
    'a2a.client.auth',
    'a2a.client.transports',
    'a2a.server',
    'a2a.server.context',
    'a2a.server.events',
    'a2a.server.agent_execution',
    'a2a.server.request_handlers',
    'a2a.server.tasks',
]


def main() -> int:
    failures: list[str] = []
    successes: list[str] = []

    for module_name in CORE_MODULES:
        try:
            importlib.import_module(module_name)
            successes.append(module_name)
        except Exception as e:  # noqa: BLE001, PERF203
            failures.append(f'{module_name}: {e}')

    print(f'Tested {len(CORE_MODULES)} core modules')
    print(f'  Passed: {len(successes)}')
    print(f'  Failed: {len(failures)}')

    if failures:
        print('\nFAILED imports:')
        for failure in failures:
            print(f'  - {failure}')
        return 1

    print('\nAll core modules imported successfully.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
