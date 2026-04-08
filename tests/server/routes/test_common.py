from unittest.mock import MagicMock

import pytest
from starlette.datastructures import Headers

try:
    from starlette.authentication import BaseUser as StarletteBaseUser
except ImportError:
    StarletteBaseUser = MagicMock()  # type: ignore

from a2a.auth.user import UnauthenticatedUser
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.server.context import ServerCallContext
from a2a.server.routes.common import (
    StarletteUser,
    DefaultServerCallContextBuilder,
)


# --- StarletteUser Tests ---


class TestStarletteUser:
    def test_is_authenticated_true(self):
        starlette_user = MagicMock(spec=StarletteBaseUser)
        starlette_user.is_authenticated = True
        proxy = StarletteUser(starlette_user)
        assert proxy.is_authenticated is True

    def test_is_authenticated_false(self):
        starlette_user = MagicMock(spec=StarletteBaseUser)
        starlette_user.is_authenticated = False
        proxy = StarletteUser(starlette_user)
        assert proxy.is_authenticated is False

    def test_user_name(self):
        starlette_user = MagicMock(spec=StarletteBaseUser)
        starlette_user.display_name = 'Test User'
        proxy = StarletteUser(starlette_user)
        assert proxy.user_name == 'Test User'

    def test_user_name_raises_attribute_error(self):
        starlette_user = MagicMock(spec=StarletteBaseUser)
        del starlette_user.display_name
        proxy = StarletteUser(starlette_user)
        with pytest.raises(AttributeError, match='display_name'):
            _ = proxy.user_name


# --- default_user_builder Tests ---


def _make_mock_request(scope=None, headers=None):
    request = MagicMock()
    request.scope = scope or {}
    request.headers = Headers(headers or {})
    return request


class TestDefaultContextBuilder:
    def test_returns_unauthenticated_user_when_no_user_in_scope(self):
        request = _make_mock_request(scope={})
        user = DefaultServerCallContextBuilder().build_user(request)
        assert isinstance(user, UnauthenticatedUser)
        assert user.is_authenticated is False
        assert user.user_name == ''

    def test_returns_proxy_when_user_in_scope(self):
        starlette_user = MagicMock()
        starlette_user.is_authenticated = True
        starlette_user.display_name = 'Alice'
        request = _make_mock_request(scope={'user': starlette_user})
        request.user = starlette_user

        user = DefaultServerCallContextBuilder().build_user(request)
        assert isinstance(user, StarletteUser)
        assert user.is_authenticated is True
        assert user.user_name == 'Alice'

    def test_returns_unauthenticated_proxy_when_user_not_authenticated(self):
        starlette_user = MagicMock()
        starlette_user.is_authenticated = False
        starlette_user.display_name = ''
        request = _make_mock_request(scope={'user': starlette_user})
        request.user = starlette_user

        user = DefaultServerCallContextBuilder().build_user(request)
        assert isinstance(user, StarletteUser)
        assert user.is_authenticated is False


# --- build_server_call_context Tests ---


class TestBuildServerCallContext:
    def test_basic_context_with_default_user_builder(self):
        request = _make_mock_request(
            scope={}, headers={'content-type': 'application/json'}
        )
        ctx = DefaultServerCallContextBuilder().build(request)

        assert isinstance(ctx, ServerCallContext)
        assert isinstance(ctx.user, UnauthenticatedUser)
        assert 'headers' in ctx.state
        assert ctx.state['headers']['content-type'] == 'application/json'
        assert 'auth' not in ctx.state

    def test_auth_populated_when_in_scope(self):
        auth_credentials = MagicMock()
        request = _make_mock_request(scope={'auth': auth_credentials})
        request.auth = auth_credentials

        ctx = DefaultServerCallContextBuilder().build(request)
        assert ctx.state['auth'] is auth_credentials

    def test_auth_not_populated_when_not_in_scope(self):
        request = _make_mock_request(scope={})
        ctx = DefaultServerCallContextBuilder().build(request)
        assert 'auth' not in ctx.state

    def test_headers_captured_in_state(self):
        request = _make_mock_request(
            headers={'x-custom': 'value', 'authorization': 'Bearer tok'}
        )
        ctx = DefaultServerCallContextBuilder().build(request)
        assert ctx.state['headers']['x-custom'] == 'value'
        assert ctx.state['headers']['authorization'] == 'Bearer tok'

    def test_requested_extensions_single(self):
        request = _make_mock_request(headers={HTTP_EXTENSION_HEADER: 'foo'})
        ctx = DefaultServerCallContextBuilder().build(request)
        assert ctx.requested_extensions == {'foo'}

    def test_requested_extensions_comma_separated(self):
        request = _make_mock_request(
            headers={HTTP_EXTENSION_HEADER: 'foo, bar'}
        )
        ctx = DefaultServerCallContextBuilder().build(request)
        assert ctx.requested_extensions == {'foo', 'bar'}

    def test_no_extensions(self):
        request = _make_mock_request()
        ctx = DefaultServerCallContextBuilder().build(request)
        assert ctx.requested_extensions == set()

    def test_custom_user_builder(self):
        custom_user = MagicMock(spec=UnauthenticatedUser)
        custom_user.is_authenticated = True

        class MyContextBuilder(DefaultServerCallContextBuilder):
            def build_user(self, req):
                return custom_user

        request = _make_mock_request()
        ctx = MyContextBuilder().build(request)
        assert ctx.user is custom_user
