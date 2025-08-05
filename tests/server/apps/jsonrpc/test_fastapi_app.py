from typing import Any
from unittest.mock import MagicMock

import pytest

from a2a.server.apps.jsonrpc import fastapi_app
from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.server.request_handlers.request_handler import (
    RequestHandler,  # For mock spec
)
from a2a.types import AgentCard  # For mock spec


# --- A2AFastAPIApplication Tests ---


class TestA2AFastAPIApplicationOptionalDeps:
    # Running tests in this class requires the optional dependency fastapi to be
    # present in the test environment.

    @pytest.fixture(scope='class', autouse=True)
    def ensure_pkg_fastapi_is_present(self):
        try:
            import fastapi as _fastapi  # noqa: F401
        except ImportError:
            pytest.fail(
                f'Running tests in {self.__class__.__name__} requires'
                ' the optional dependency fastapi to be present in the test'
                ' environment. Run `uv sync --dev ...` before running the test'
                ' suite.'
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
    def mark_pkg_fastapi_not_installed(self):
        pkg_fastapi_installed_flag = fastapi_app._package_fastapi_installed
        fastapi_app._package_fastapi_installed = False
        yield
        fastapi_app._package_fastapi_installed = pkg_fastapi_installed_flag

    def test_create_a2a_fastapi_app_with_present_deps_succeeds(
        self, mock_app_params: dict
    ):
        try:
            _app = A2AFastAPIApplication(**mock_app_params)
        except ImportError:
            pytest.fail(
                'With the fastapi package present, creating a'
                ' A2AFastAPIApplication instance should not raise ImportError'
            )

    def test_create_a2a_fastapi_app_with_missing_deps_raises_importerror(
        self,
        mock_app_params: dict,
        mark_pkg_fastapi_not_installed: Any,
    ):
        with pytest.raises(
            ImportError,
            match=(
                'The `fastapi` package is required to use the'
                ' `A2AFastAPIApplication`'
            ),
        ):
            _app = A2AFastAPIApplication(**mock_app_params)


if __name__ == '__main__':
    pytest.main([__file__])
