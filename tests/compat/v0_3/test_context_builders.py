from unittest.mock import AsyncMock, MagicMock

import grpc

from starlette.datastructures import Headers

from a2a.compat.v0_3.context_builders import (
    V03GrpcServerCallContextBuilder,
    V03ServerCallContextBuilder,
)
from a2a.compat.v0_3.extension_headers import LEGACY_HTTP_EXTENSION_HEADER
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.grpc_handler import (
    DefaultGrpcServerCallContextBuilder,
)
from a2a.server.routes.common import DefaultServerCallContextBuilder


def _make_mock_request(headers=None):
    request = MagicMock()
    request.scope = {}
    request.headers = Headers(headers or {})
    return request


def _make_mock_grpc_context(metadata: list[tuple[str, str]]) -> AsyncMock:
    context = AsyncMock(spec=grpc.aio.ServicerContext)
    context.invocation_metadata.return_value = grpc.aio.Metadata(*metadata)
    return context


class TestV03ServerCallContextBuilder:
    def test_legacy_header_only(self):
        request = _make_mock_request(
            headers={LEGACY_HTTP_EXTENSION_HEADER: 'legacy-ext'}
        )
        builder = V03ServerCallContextBuilder(DefaultServerCallContextBuilder())

        ctx = builder.build(request)

        assert isinstance(ctx, ServerCallContext)
        assert ctx.requested_extensions == {'legacy-ext'}

    def test_spec_header_only(self):
        request = _make_mock_request(
            headers={HTTP_EXTENSION_HEADER: 'spec-ext'}
        )
        builder = V03ServerCallContextBuilder(DefaultServerCallContextBuilder())

        ctx = builder.build(request)

        assert ctx.requested_extensions == {'spec-ext'}

    def test_both_headers_merged(self):
        request = _make_mock_request(
            headers={
                HTTP_EXTENSION_HEADER: 'spec-ext',
                LEGACY_HTTP_EXTENSION_HEADER: 'legacy-ext',
            }
        )
        builder = V03ServerCallContextBuilder(DefaultServerCallContextBuilder())

        ctx = builder.build(request)

        assert ctx.requested_extensions == {'spec-ext', 'legacy-ext'}

    def test_legacy_header_comma_separated(self):
        request = _make_mock_request(
            headers={LEGACY_HTTP_EXTENSION_HEADER: 'foo, bar'}
        )
        builder = V03ServerCallContextBuilder(DefaultServerCallContextBuilder())

        ctx = builder.build(request)

        assert ctx.requested_extensions == {'foo', 'bar'}

    def test_no_extensions(self):
        request = _make_mock_request()
        builder = V03ServerCallContextBuilder(DefaultServerCallContextBuilder())

        ctx = builder.build(request)

        assert ctx.requested_extensions == set()


class TestV03GrpcServerCallContextBuilder:
    def test_legacy_metadata_only(self):
        context = _make_mock_grpc_context(
            [(LEGACY_HTTP_EXTENSION_HEADER.lower(), 'legacy-ext')]
        )
        builder = V03GrpcServerCallContextBuilder(
            DefaultGrpcServerCallContextBuilder()
        )

        ctx = builder.build(context)

        assert isinstance(ctx, ServerCallContext)
        assert ctx.requested_extensions == {'legacy-ext'}

    def test_spec_metadata_only(self):
        context = _make_mock_grpc_context(
            [(HTTP_EXTENSION_HEADER.lower(), 'spec-ext')]
        )
        builder = V03GrpcServerCallContextBuilder(
            DefaultGrpcServerCallContextBuilder()
        )

        ctx = builder.build(context)

        assert ctx.requested_extensions == {'spec-ext'}

    def test_both_metadata_merged(self):
        context = _make_mock_grpc_context(
            [
                (HTTP_EXTENSION_HEADER.lower(), 'spec-ext'),
                (LEGACY_HTTP_EXTENSION_HEADER.lower(), 'legacy-ext'),
            ]
        )
        builder = V03GrpcServerCallContextBuilder(
            DefaultGrpcServerCallContextBuilder()
        )

        ctx = builder.build(context)

        assert ctx.requested_extensions == {'spec-ext', 'legacy-ext'}

    def test_legacy_metadata_comma_separated(self):
        context = _make_mock_grpc_context(
            [(LEGACY_HTTP_EXTENSION_HEADER.lower(), 'foo, bar')]
        )
        builder = V03GrpcServerCallContextBuilder(
            DefaultGrpcServerCallContextBuilder()
        )

        ctx = builder.build(context)

        assert ctx.requested_extensions == {'foo', 'bar'}

    def test_no_extensions(self):
        context = _make_mock_grpc_context([])
        builder = V03GrpcServerCallContextBuilder(
            DefaultGrpcServerCallContextBuilder()
        )

        ctx = builder.build(context)

        assert ctx.requested_extensions == set()

    def test_no_metadata(self):
        context = AsyncMock(spec=grpc.aio.ServicerContext)
        context.invocation_metadata.return_value = None
        builder = V03GrpcServerCallContextBuilder(
            DefaultGrpcServerCallContextBuilder()
        )

        ctx = builder.build(context)

        assert ctx.requested_extensions == set()
