# Install-smoke harness

This package is **not** a pytest test suite. It is invoked as a
standalone module from a freshly-installed, dev-deps-free venv:

```bash
python -m tests.install_smoke <profile>
```

The smoke venv is created by either
[`scripts/test_install_smoke.sh`](../../scripts/test_install_smoke.sh)
(local) or
[`.github/workflows/install-smoke.yml`](../../.github/workflows/install-smoke.yml)
(CI). The harness has no pytest dependency and uses only the Python
standard library plus the freshly-installed `a2a-sdk` wheel for the
profile under test.

For a given install profile (`base`, `http-server`, `grpc`,
`telemetry`, `sql`) it runs two phases:

1. **Imports** — every module listed for the profile in `__main__.py`
   must import cleanly. Catches missing deps and accidental top-level
   imports of optional extras.
2. **Runtime checks** (`runtime/`) — small public-API exercises that
   actually call into the SDK. These catch regressions where imports
   succeed but a real call fails.

## Adding a new runtime check

1. Drop a module under `tests/install_smoke/runtime/` exposing two
   names:
   - `NAME: str` — short human-readable label.
   - `check() -> None` — callable that raises on failure.
2. Register it in `RUNTIME_CHECKS` in
   [`__main__.py`](./__main__.py) under each profile whose extras it
   needs.

Use only the dependencies guaranteed by the target profile. Do not import 
`pytest` or any dev-deps.
