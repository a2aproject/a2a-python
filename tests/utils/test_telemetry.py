import asyncio

from collections.abc import Generator
from typing import Any, NoReturn
from unittest import mock

import pytest

from a2a.utils.telemetry import trace_class, trace_function


@pytest.fixture
def mock_span() -> mock.MagicMock:
    return mock.MagicMock()


@pytest.fixture
def mock_tracer(mock_span: mock.MagicMock) -> mock.MagicMock:
    tracer = mock.MagicMock()
    tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
    tracer.start_as_current_span.return_value.__exit__.return_value = False
    return tracer


@pytest.fixture(autouse=True)
def patch_trace_get_tracer(
    mock_tracer: mock.MagicMock,
) -> Generator[None, Any, None]:
    with mock.patch('opentelemetry.trace.get_tracer', return_value=mock_tracer):
        yield


def test_trace_function_sync_success(mock_span: mock.MagicMock) -> None:
    @trace_function
    def foo(x, y):
        return x + y

    result = foo(2, 3)
    assert result == 5
    mock_span.set_status.assert_called()
    mock_span.set_status.assert_any_call(mock.ANY)
    mock_span.record_exception.assert_not_called()


def test_trace_function_sync_exception(mock_span: mock.MagicMock) -> None:
    @trace_function
    def bar() -> NoReturn:
        raise ValueError('fail')

    with pytest.raises(ValueError):
        bar()
    mock_span.record_exception.assert_called()
    mock_span.set_status.assert_any_call(mock.ANY, description='fail')


def test_trace_function_sync_attribute_extractor_called(
    mock_span: mock.MagicMock,
) -> None:
    called = {}

    def attr_extractor(span, args, kwargs, result, exception) -> None:
        called['called'] = True
        assert span is mock_span
        assert exception is None
        assert result == 42

    @trace_function(attribute_extractor=attr_extractor)
    def foo() -> int:
        return 42

    foo()
    assert called['called']


def test_trace_function_sync_attribute_extractor_error_logged(
    mock_span: mock.MagicMock,
) -> None:
    with mock.patch('a2a.utils.telemetry.logger') as logger:

        def attr_extractor(span, args, kwargs, result, exception) -> NoReturn:
            raise RuntimeError('attr fail')

        @trace_function(attribute_extractor=attr_extractor)
        def foo() -> int:
            return 1

        foo()
        logger.exception.assert_any_call(
            'attribute_extractor error in span %s',
            'test_telemetry.foo',
        )


@pytest.mark.asyncio
async def test_trace_function_async_success(mock_span: mock.MagicMock) -> None:
    @trace_function
    async def foo(x):
        await asyncio.sleep(0)
        return x * 2

    result = await foo(4)
    assert result == 8
    mock_span.set_status.assert_called()
    mock_span.record_exception.assert_not_called()


@pytest.mark.asyncio
async def test_trace_function_async_exception(
    mock_span: mock.MagicMock,
) -> None:
    @trace_function
    async def bar() -> NoReturn:
        await asyncio.sleep(0)
        raise RuntimeError('async fail')

    with pytest.raises(RuntimeError):
        await bar()
    mock_span.record_exception.assert_called()
    mock_span.set_status.assert_any_call(mock.ANY, description='async fail')


@pytest.mark.asyncio
async def test_trace_function_async_attribute_extractor_called(
    mock_span: mock.MagicMock,
) -> None:
    called = {}

    def attr_extractor(span, args, kwargs, result, exception) -> None:
        called['called'] = True
        assert exception is None
        assert result == 99

    @trace_function(attribute_extractor=attr_extractor)
    async def foo() -> int:
        return 99

    await foo()
    assert called['called']


def test_trace_function_with_args_and_attributes(
    mock_span: mock.MagicMock,
) -> None:
    @trace_function(span_name='custom.span', attributes={'foo': 'bar'})
    def foo() -> int:
        return 1

    foo()
    mock_span.set_attribute.assert_any_call('foo', 'bar')


def test_trace_class_exclude_list(mock_span: mock.MagicMock) -> None:
    @trace_class(exclude_list=['skip_me'])
    class MyClass:
        def a(self) -> str:
            return 'a'

        def skip_me(self) -> str:
            return 'skip'

        def __str__(self) -> str:
            return 'str'

    obj = MyClass()
    assert obj.a() == 'a'
    assert obj.skip_me() == 'skip'
    # Only 'a' is traced, not 'skip_me' or dunder
    assert hasattr(obj.a, '__wrapped__')
    assert not hasattr(obj.skip_me, '__wrapped__')


def test_trace_class_include_list(mock_span: mock.MagicMock) -> None:
    @trace_class(include_list=['only_this'])
    class MyClass:
        def only_this(self) -> str:
            return 'yes'

        def not_this(self) -> str:
            return 'no'

    obj = MyClass()
    assert obj.only_this() == 'yes'
    assert obj.not_this() == 'no'
    assert hasattr(obj.only_this, '__wrapped__')
    assert not hasattr(obj.not_this, '__wrapped__')


def test_trace_class_dunder_not_traced(mock_span: mock.MagicMock) -> None:
    @trace_class()
    class MyClass:
        def __init__(self) -> None:
            self.x = 1

        def foo(self) -> str:
            return 'foo'

    obj = MyClass()
    assert obj.foo() == 'foo'
    assert hasattr(obj.foo, '__wrapped__')
    assert hasattr(obj, 'x')
