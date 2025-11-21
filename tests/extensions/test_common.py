import pytest
from a2a.extensions.common import (
    HTTP_EXTENSION_HEADER,
    find_extension_by_uri,
    get_requested_extensions,
    update_extension_header,
)
from a2a.types import AgentCapabilities, AgentCard, AgentExtension


def test_get_requested_extensions():
    assert get_requested_extensions([]) == set()
    assert get_requested_extensions(['foo']) == {'foo'}
    assert get_requested_extensions(['foo', 'bar']) == {'foo', 'bar'}
    assert get_requested_extensions(['foo, bar']) == {'foo', 'bar'}
    assert get_requested_extensions(['foo,bar']) == {'foo', 'bar'}
    assert get_requested_extensions(['foo', 'bar,baz']) == {'foo', 'bar', 'baz'}
    assert get_requested_extensions(['foo,, bar', 'baz']) == {
        'foo',
        'bar',
        'baz',
    }
    assert get_requested_extensions([' foo , bar ', 'baz']) == {
        'foo',
        'bar',
        'baz',
    }


def test_find_extension_by_uri():
    ext1 = AgentExtension(uri='foo', description='The Foo extension')
    ext2 = AgentExtension(uri='bar', description='The Bar extension')
    card = AgentCard(
        name='Test Agent',
        description='Test Agent Description',
        version='1.0',
        url='http://test.com',
        skills=[],
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        capabilities=AgentCapabilities(extensions=[ext1, ext2]),
    )

    assert find_extension_by_uri(card, 'foo') == ext1
    assert find_extension_by_uri(card, 'bar') == ext2
    assert find_extension_by_uri(card, 'baz') is None


def test_find_extension_by_uri_no_extensions():
    card = AgentCard(
        name='Test Agent',
        description='Test Agent Description',
        version='1.0',
        url='http://test.com',
        skills=[],
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        capabilities=AgentCapabilities(extensions=None),
    )

    assert find_extension_by_uri(card, 'foo') is None


@pytest.mark.parametrize(
    'extensions, header, expected_extensions',
    [
        (
            ['ext1', 'ext2'],  # extensions
            '',  # header
            {
                'ext1',
                'ext2',
            },  # expected_extensions
        ),  # Case 1: New extensions provided,  empty header.
        (
            None,  # extensions
            'ext1, ext2',  # header
            {
                'ext1',
                'ext2',
            },  # expected_extensions
        ),  # Case 2: Extensions is None, existing header extensions.
        (
            [],  # extensions
            'ext1',  # header
            {},  # expected_extensions
        ),  # Case 3: New extensions is empty list, existing header extensions.
        (
            ['ext1', 'ext2'],  # extensions
            'ext3',  # header
            {
                'ext1',
                'ext2',
            },  # expected_extensions
        ),  # Case 4: New extensions provided, and an existing header. New extensions should override active extensions.
    ],
)
def test_update_extension_header_merge_with_existing_extensions(
    extensions: list[str],
    header: str,
    expected_extensions: set[str],
):
    http_kwargs = {'headers': {HTTP_EXTENSION_HEADER: header}}
    result_kwargs = update_extension_header(http_kwargs, extensions)
    header_value = result_kwargs['headers'][HTTP_EXTENSION_HEADER]
    if not header_value:
        actual_extensions = {}
    else:
        actual_extensions_list = [e.strip() for e in header_value.split(',')]
        actual_extensions = set(actual_extensions_list)
    assert actual_extensions == expected_extensions


def test_update_extension_header_with_other_headers():
    extensions = ['ext']
    http_kwargs = {'headers': {'X_Other': 'Test'}}
    result_kwargs = update_extension_header(http_kwargs, extensions)
    headers = result_kwargs.get('headers', {})
    assert HTTP_EXTENSION_HEADER in headers
    assert headers[HTTP_EXTENSION_HEADER] == 'ext'
    assert headers['X_Other'] == 'Test'


@pytest.mark.parametrize(
    'http_kwargs',
    [
        None,
        {},
    ],
)
def test_update_extension_header_headers_not_in_kwargs(
    http_kwargs: dict[str, str] | None,
):
    extensions = ['ext']
    http_kwargs = {}
    result_kwargs = update_extension_header(http_kwargs, extensions)
    headers = result_kwargs.get('headers', {})
    assert HTTP_EXTENSION_HEADER in headers
    assert headers[HTTP_EXTENSION_HEADER] == 'ext'


def test_update_extension_header_with_other_headers_extensions_none():
    http_kwargs = {'headers': {'X_Other': 'Test'}}
    result_kwargs = update_extension_header(http_kwargs, None)
    assert HTTP_EXTENSION_HEADER not in result_kwargs['headers']
    assert result_kwargs['headers']['X_Other'] == 'Test'
