from unittest.mock import MagicMock

import pytest

from starlette.testclient import TestClient

from a2a.server.apps.jsonrpc.jsonrpc_app import JSONRPCApplicationBuilder
from a2a.server.apps.jsonrpc.starlette_app import (
    A2AStarletteApplication,
    StarletteBuilder,
    StarletteRouteBuilder,
    StarletteRouteConfig,
    _get_path_from_url,
    _join_url,
)
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import AgentCard


class TestA2AStarletteApplication:
    """
    Unit tests for the `A2AStarletteApplication` implementation.
    """

    def test_builder_protocol(self):
        """
        Tests that `A2AStarletteApplication` matches the `JSONRPCApplicationBuilder` protocol.

        This ensures that `A2AStarletteApplication` can be used interchangeably where
        `JSONRPCApplicationBuilder` is expected.
        """
        card = MagicMock(spec=AgentCard)
        card.url = 'http://mockurl.com'
        card.supports_authenticated_extended_card = False
        handler = MagicMock(spec=RequestHandler)
        builder = A2AStarletteApplication(card, handler)
        assert isinstance(builder, JSONRPCApplicationBuilder)


class TestStarletteRouteConfig:
    """
    Unit tests for the `StarletteRouteConfig` dataclass.
    """

    def test_starlette_route_config_defaults(self):
        """
        Verifies that `StarletteRouteConfig` initializes with the expected default values
        for all route paths.
        """
        config = StarletteRouteConfig()
        assert config.agent_card_path == '/agent.json'
        assert (
            config.extended_agent_card_path
            == '/agent/authenticatedExtendedCard'
        )
        assert config.rpc_path == '/'

    def test_starlette_route_config_custom_values(self):
        """
        Verifies that custom values passed to `StarletteRouteConfig` are correctly
        assigned and retained.
        """
        config = StarletteRouteConfig(
            agent_card_path='/custom/agent.json',
            extended_agent_card_path='/custom/agent/authenticatedExtendedCard',
            rpc_path='/custom',
        )
        assert config.agent_card_path == '/custom/agent.json'
        assert (
            config.extended_agent_card_path
            == '/custom/agent/authenticatedExtendedCard'
        )
        assert config.rpc_path == '/custom'


class TestStarletteRouteBuilder:
    """
    Unit tests for the `StarletteRouteBuilder` class.
    """

    def _mock_agent_card(self, supports_extended=False):
        """
        Creates a mocked `AgentCard` with the given `supports_authenticated_extended_card` setting.
        """
        card = MagicMock(spec=AgentCard)
        card.supports_authenticated_extended_card = supports_extended
        return card

    def test_build_routes_with_defaults(self):
        """
        Tests that `StarletteRouteBuilder` creates the correct default routes
        when no custom configuration is provided and the extended card is not supported.
        """
        card = self._mock_agent_card(supports_extended=False)
        handler = MagicMock(spec=RequestHandler)
        builder = StarletteRouteBuilder(card, handler)
        routes = builder.build()
        assert isinstance(routes, list)
        assert len(routes) == 2
        paths = {route.path for route in routes}
        assert '/' in paths
        assert '/agent.json' in paths

    def test_build_routes_with_authenticated_extended_card(self):
        """
        Tests that the authenticated extended card route is included
        when `supports_authenticated_extended_card` is set to True.
        """
        card = self._mock_agent_card(supports_extended=True)
        handler = MagicMock(spec=RequestHandler)
        builder = StarletteRouteBuilder(card, handler)
        routes = builder.build()
        assert len(routes) == 3
        paths = {route.path for route in routes}
        assert '/agent/authenticatedExtendedCard' in paths

    def test_build_routes_with_custom_config(self):
        """
        Tests that custom route paths specified via `StarletteRouteConfig`
        are respected and correctly used in the constructed routes.
        """
        card = self._mock_agent_card(supports_extended=True)
        handler = MagicMock(spec=RequestHandler)
        config = StarletteRouteConfig(
            agent_card_path='/custom/agent.json',
            extended_agent_card_path='/custom/agent/authenticatedExtendedCard',
            rpc_path='/custom',
        )
        builder = StarletteRouteBuilder(card, handler, config=config)
        routes = builder.build()
        assert len(routes) == 3
        paths = {route.path for route in routes}
        assert '/custom/agent.json' in paths
        assert '/custom/agent/authenticatedExtendedCard' in paths
        assert '/custom' in paths

    def test_route_handlers_are_bound(self):
        """
        Tests that all constructed routes are associated with the expected handler methods,
        and that the route names are correctly set.
        """
        card = self._mock_agent_card(supports_extended=True)
        handler = MagicMock(spec=RequestHandler)
        builder = StarletteRouteBuilder(card, handler)
        routes = builder.build()
        assert len(routes) == 3
        route_names = {route.name for route in routes}
        assert 'a2a_handler' in route_names
        assert 'agent_card' in route_names
        assert 'authenticated_extended_agent_card' in route_names


class TestURLUtils:
    """
    Unit tests for URL utility functions `_join_url` and `_get_path_from_url`.
    """

    @pytest.mark.parametrize(
        'base, paths, expected',
        [
            ('http://example.com', ['a', 'b'], 'http://example.com/a/b'),
            ('http://example.com/', ['a', 'b'], 'http://example.com/a/b'),
            (
                'http://example.com/base/',
                ['a', 'b'],
                'http://example.com/base/a/b',
            ),
            (
                'http://example.com/base',
                ['a', 'b/'],
                'http://example.com/base/a/b',
            ),
            ('http://example.com/', [], 'http://example.com/'),
            ('http://example.com', [], 'http://example.com/'),
            (
                'http://example.com//',
                ['//a//', '//b//'],
                'http://example.com/a/b',
            ),
        ],
    )
    def test_join_url(self, base, paths, expected):
        """
        Tests that `_join_url` correctly joins a base URL with multiple path fragments,
        normalizing slashes and producing a well-formed absolute URL.
        """
        assert _join_url(base, *paths) == expected

    def test_join_url_preserves_query_and_fragment(self):
        """
        Tests that `_join_url` preserves query parameters and fragment identifiers
        from the base URL when joining with additional paths.
        """
        base = 'http://example.com/base?foo=1#section'
        result = _join_url(base, 'x', 'y')
        assert result.startswith('http://example.com/base/x/y')
        assert '?foo=1' in result or '#section' in result

    @pytest.mark.parametrize(
        'url, expected',
        [
            ('http://example.com', '/'),
            ('http://example.com/', '/'),
            ('http://example.com/api/v1/resource', '/api/v1/resource'),
            ('http://example.com/api/', '/api/'),
            ('http://example.com?x=1', '/'),
            ('http://example.com#section', '/'),
        ],
    )
    def test_get_path_from_url(self, url, expected):
        """
        Tests that `_get_path_from_url` correctly extracts the path component from a full URL.
        Ensures that an empty or missing path defaults to '/'.
        """
        assert _get_path_from_url(url) == expected


class TestStarletteBuilder:
    """
    Unit tests for the `StarletteBuilder` class.
    """

    def _mock_route_builder(
        self,
        extended=False,
        same_url=True,
    ):
        """
        Helper to create a mock `StarletteRouteBuilder` with configurable
        agent card, extended card, and route config for testing.

        Args:
            extended: Whether to include an authenticated extended agent card.
            same_url: Whether agent_card.url and extended_agent_card.url are the same.

        Returns:
            A mocked `StarletteRouteBuilder` instance.
        """
        card = MagicMock(spec=AgentCard)
        card.url = 'http://example.com/agent'
        card.supports_authenticated_extended_card = extended
        if extended:
            extended_card = MagicMock(spec=AgentCard)
            extended_card.url = card.url if same_url else 'http://other.com'
        else:
            extended_card = None
        config = StarletteRouteConfig(
            agent_card_path='/agent.json',
            rpc_path='/',
            extended_agent_card_path='/agent/authenticatedExtendedCard',
        )
        handler = MagicMock(spec=RequestHandler)
        builder = StarletteRouteBuilder(
            agent_card=card,
            http_handler=handler,
            extended_agent_card=extended_card,
            config=config,
        )
        return builder

    def test_mount_and_build_app(self):
        """
        Tests that a route builder can be mounted and that the resulting
        Starlette application responds with a valid Agent Catalog document
        at `/.well-known/api-catalog`.
        """
        route_builder = self._mock_route_builder()
        app_builder = StarletteBuilder()
        app = app_builder.mount(route_builder).build()
        client = TestClient(app)
        response = client.get('/.well-known/api-catalog')
        assert response.status_code == 200
        data = response.json()
        assert 'linkset' in data
        assert isinstance(data['linkset'], list)
        assert data['linkset'][0]['anchor'].endswith('/')

    def test_api_catalog_content_type_header(self):
        """
        Tests that the Agent Catalog endpoint returns the correct Content-Type
        with the RFC 9727 profile.
        """
        route_builder = self._mock_route_builder()
        app_builder = StarletteBuilder()
        app = app_builder.mount(route_builder).build()
        client = TestClient(app)
        response = client.get('/.well-known/api-catalog')
        assert response.status_code == 200
        content_type = response.headers.get('Content-Type')
        assert content_type is not None
        assert content_type.startswith('application/linkset+json')
        assert (
            'profile="https://www.rfc-editor.org/info/rfc9727"' in content_type
        )

    def test_duplicate_mount_raises(self):
        """
        Tests that attempting to mount two route builders at the same path
        raises a `ValueError`, ensuring path uniqueness enforcement.
        """
        route_builder_1 = self._mock_route_builder()
        route_builder_2 = self._mock_route_builder()
        app_builder = StarletteBuilder()
        app_builder.mount(route_builder_1)
        with pytest.raises(ValueError, match='already exists'):
            app_builder.mount(route_builder_2)

    def test_inconsistent_card_urls_raises(self):
        """
        Tests that mounting a route builder with mismatched `agent_card.url`
        and `extended_agent_card.url` raises a `ValueError`.
        """
        route_builder = self._mock_route_builder(extended=True, same_url=False)
        app_builder = StarletteBuilder()
        with pytest.raises(ValueError, match='must be the same'):
            app_builder.mount(route_builder)

    def test_agent_catalog_includes_extended_card(self):
        """
        Tests that when an extended agent card is provided,
        its URL is correctly included in the `describedby` section
        of the generated Agent Catalog.
        """
        route_builder = self._mock_route_builder(extended=True)
        app_builder = StarletteBuilder()
        app = app_builder.mount(route_builder).build()
        client = TestClient(app)
        response = client.get('/.well-known/api-catalog')
        assert response.status_code == 200
        data = response.json()
        links = data['linkset'][0]['describedby']
        assert any('/agent/auth' in entry['href'] for entry in links)
