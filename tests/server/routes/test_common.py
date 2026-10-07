from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sse_starlette.sse import EventSourceResponse
from starlette.datastructures import Headers


try:
    from starlette.authentication import BaseUser as StarletteBaseUser
except ImportError:
    StarletteBaseUser = MagicMock()  # type: ignore

from a2a.auth.user import UnauthenticatedUser
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.server.context import ServerCallContext
from a2a.server.routes.common import (
    DefaultServerCallContextBuilder,
    StarletteUser,
    create_event_source_response,
)


class LegacyEventSourceResponse:
    """Models a constructor without cooperative shutdown support."""

    def __init__(self, content: Any) -> None:
        self.content = content


def test_default_grace_period_supports_legacy_sse_starlette() -> None:
    content = []
    with patch(
        'a2a.server.routes.common.EventSourceResponse',
        LegacyEventSourceResponse,
    ):
        response = create_event_source_response(content)

    assert isinstance(response, LegacyEventSourceResponse)
    assert response.content is content


@pytest.mark.parametrize('grace_period', [0, 30.0])
def test_grace_period_constructor_arguments(grace_period: float) -> None:
    content = []
    with patch.object(
        EventSourceResponse, '__init__', autospec=True, return_value=None
    ) as constructor:
        response = create_event_source_response(content, grace_period)

    if grace_period:
        constructor.assert_called_once_with(
            response, content, shutdown_grace_period=grace_period
        )
    else:
        constructor.assert_called_once_with(response, content)


def test_nonzero_grace_period_requires_cooperative_shutdown_support() -> None:
    with (
        patch(
            'a2a.server.routes.common.EventSourceResponse',
            LegacyEventSourceResponse,
        ),
        pytest.raises(
            RuntimeError,
            match=r'cooperative shutdown support.*a2a-sdk\[http-server\]',
        ),
    ):
        create_event_source_response([], 30.0)


@pytest.mark.parametrize(
    'response_class', [LegacyEventSourceResponse, EventSourceResponse]
)
def test_negative_grace_period_is_rejected(response_class: type[Any]) -> None:
    with (
        patch('a2a.server.routes.common.EventSourceResponse', response_class),
        pytest.raises(ValueError, match='shutdown_grace_period must be >= 0'),
    ):
        create_event_source_response([], -1)


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
