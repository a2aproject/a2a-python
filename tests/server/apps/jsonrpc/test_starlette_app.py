from typing import Any
from unittest.mock import MagicMock

import pytest

from a2a.server.apps.jsonrpc import starlette_app
from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.request_handlers.request_handler import (
    RequestHandler,  # For mock spec
)
from a2a.types import AgentCard  # For mock spec


# --- A2AStarletteApplication Tests ---


class TestA2AStarletteApplicationOptionalDeps:
    # Running tests in this class requires optional dependencies starlette and
    # sse-starlette to be present in the test environment.

    @pytest.fixture(scope='class', autouse=True)
    def ensure_pkg_starlette_is_present(self):
        try:
            import sse_starlette as _sse_starlette  # noqa: F401
            import starlette as _starlette  # noqa: F401
        except ImportError:
            pytest.fail(
                f'Running tests in {self.__class__.__name__} requires'
                ' optional dependencies starlette and sse-starlette to be'
                ' present in the test environment. Run `uv sync --dev ...`'
                ' before running the test suite.'
            )

    @pytest.fixture(scope='class')
    def mock_app_params(self) -> dict:
        # Mock http_handler
        mock_handler = MagicMock(spec=RequestHandler)
        # Mock agent_card with essential attributes accessed in __init__
        mock_agent_card = MagicMock(spec=AgentCard)
        # Ensure 'url' attribute exists on the mock_agent_card, as it's accessed
        # in __init__
        mock_agent_card.url = 'http://example.com'
        # Ensure 'supports_authenticated_extended_card' attribute exists
        mock_agent_card.supports_authenticated_extended_card = False
        return {'agent_card': mock_agent_card, 'http_handler': mock_handler}

    @pytest.fixture(scope='class')
    def mark_pkg_starlette_not_installed(self):
        pkg_starlette_installed_flag = (
            starlette_app._package_starlette_installed
        )
        starlette_app._package_starlette_installed = False
        yield
        starlette_app._package_starlette_installed = (
            pkg_starlette_installed_flag
        )

    def test_create_a2a_starlette_app_with_present_deps_succeeds(
        self, mock_app_params: dict
    ):
        try:
            _app = A2AStarletteApplication(**mock_app_params)
        except ImportError:
            pytest.fail(
                'With packages starlette and see-starlette present, creating an'
                ' A2AStarletteApplication instance should not raise ImportError'
            )

    def test_create_a2a_starlette_app_with_missing_deps_raises_importerror(
        self,
        mock_app_params: dict,
        mark_pkg_starlette_not_installed: Any,
    ):
        with pytest.raises(
            ImportError,
            match='Packages `starlette` and `sse-starlette` are required',
        ):
            _app = A2AStarletteApplication(**mock_app_params)


if __name__ == '__main__':
    pytest.main([__file__])
