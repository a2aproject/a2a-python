"""`a2a.server.routes` must be importable without the `http-server` extra.

The dispatchers guard their `starlette` / `sse-starlette` imports and raise an
`ImportError` that names `a2a-sdk[http-server]` when they are constructed. The
tests that cover that message toggle `_package_starlette_installed` directly,
so they pass even if some module in the import chain imports `starlette` or
`sse-starlette` unguarded. This test blocks the real modules instead.
"""

import subprocess
import sys
import textwrap


_SCRIPT = textwrap.dedent(
    """
    import importlib.abc
    import sys

    BLOCKED = ('starlette', 'sse_starlette')

    class _Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split('.')[0] in BLOCKED:
                raise ModuleNotFoundError(f'No module named {name!r}')
            return None

    for name in list(sys.modules):
        if name.split('.')[0] in BLOCKED:
            del sys.modules[name]
    sys.meta_path.insert(0, _Blocker())

    from a2a.server.routes import create_jsonrpc_routes, create_rest_routes
    from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
    from a2a.server.routes.rest_dispatcher import RestDispatcher

    for dispatcher in (JsonRpcDispatcher, RestDispatcher):
        try:
            dispatcher(request_handler=None)
        except ImportError as e:
            assert 'a2a-sdk[http-server]' in str(e), e
        else:
            raise AssertionError(f'{dispatcher.__name__} did not raise')
    """
)


def test_routes_import_without_http_server_extra() -> None:
    result = subprocess.run(
        [sys.executable, '-c', _SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
