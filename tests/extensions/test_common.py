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
    'extensions, existing_header, expected_extensions, expected_count',
    [
        (
            ['test_extension_1', 'test_extension_2'],
            '',
            {
                'test_extension_1',
                'test_extension_2',
            },
            2,
        ),
        (
            ['test_extension_1', 'test_extension_2'],
            'test_extension_2, test_extension_3',
            {
                'test_extension_1',
                'test_extension_2',
                'test_extension_3',
            },
            3,
        ),
        (
            ['test_extension_1', 'test_extension_2'],
            'test_extension_3',
            {
                'test_extension_1',
                'test_extension_2',
                'test_extension_3',
            },
            3,
        ),
    ],
)
def test_update_extension_header_merge_with_existing_extensions(
    extensions: list[str],
    existing_header: str,
    expected_extensions: set[str],
    expected_count: int,
):
    http_kwargs = {'headers': {HTTP_EXTENSION_HEADER: existing_header}}
    result_kwargs = update_extension_header(http_kwargs, extensions)
    header_value = result_kwargs['headers'][HTTP_EXTENSION_HEADER]
    actual_extensions_list = [e.strip() for e in header_value.split(',')]
    actual_extensions = set(actual_extensions_list)
    assert len(actual_extensions_list) == expected_count
    assert actual_extensions == expected_extensions


def test_update_extension_header_with_other_headers():
    extensions = ['test_extension']
    http_kwargs = {'headers': {'X_Other': 'Test'}}
    result_kwargs = update_extension_header(http_kwargs, extensions)
    headers = result_kwargs.get('headers', {})
    assert HTTP_EXTENSION_HEADER in headers
    assert headers[HTTP_EXTENSION_HEADER] == 'test_extension'
    assert headers['X_Other'] == 'Test'


@pytest.mark.parametrize('extensions', [(None), ([])])
def test_update_extension_header_no_or_empty_extensions(extensions):
    http_kwargs = {'headers': {'X_Other': 'Test'}}
    result_kwargs = update_extension_header(http_kwargs, extensions)
    assert HTTP_EXTENSION_HEADER not in result_kwargs['headers']
    assert result_kwargs['headers']['X_Other'] == 'Test'
