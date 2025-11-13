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
    'active_extensions, new_extensions, existing_header, expected_extensions, expected_count, expected_returned_extensions',
    [
        (
            ['ext1', 'ext2'],  # active_extensions
            None,  # new_extensions
            '',  # existing_header
            {
                'ext1',
                'ext2',
            },  # expected_extensions
            2,  # expected_count
            ['ext1', 'ext2'],  # expected_returned_extensions
        ),  # Case 1: Active extensions, no new extensions, and no existing header.
        (
            ['ext1', 'ext2'],  # active_extensions
            None,  # new_extensions
            'ext2, ext3',  # existing_header
            {
                'ext1',
                'ext2',
                'ext3',
            },  # expected_extensions
            3,  # expected_count
            ['ext1', 'ext2'],  # expected_returned_extensions
        ),  # Case 2: Active extensions, no new extensions, with an existing header containing overlapping and new extensions.
        (
            ['ext1', 'ext2'],  # active_extensions
            None,  # new_extensions
            'ext3',  # existing_header
            {
                'ext1',
                'ext2',
                'ext3',
            },  # expected_extensions
            3,  # expected_count
            ['ext1', 'ext2'],  # expected_returned_extensions
        ),  # Case 3: Active extensions, no new extensions, with an existing header containing different extensions.
        (
            ['ext1', 'ext2'],  # active_extensions
            ['ext3'],  # new_extensions
            'ext4',  # existing_header
            {
                'ext3',
                'ext4',
            },  # expected_extensions
            2,  # expected_count
            ['ext3'],  # expected_returned_extensions
        ),  # Case 4: Active extensions, new extensions provided, and an existing header. New extensions should override active and merge with existing.
    ],
)
def test_update_extension_header_merge_with_existing_extensions(
    active_extensions: list[str],
    new_extensions: list[str],
    existing_header: str,
    expected_extensions: set[str],
    expected_count: int,
    expected_returned_extensions: list[str],
):
    http_kwargs = {'headers': {HTTP_EXTENSION_HEADER: existing_header}}
    result_kwargs, actual_returned_extensions = update_extension_header(
        http_kwargs, active_extensions, new_extensions
    )
    header_value = result_kwargs['headers'][HTTP_EXTENSION_HEADER]
    actual_extensions_list = [e.strip() for e in header_value.split(',')]
    actual_extensions = set(actual_extensions_list)
    assert len(actual_extensions_list) == expected_count
    assert actual_extensions == expected_extensions
    assert actual_returned_extensions == expected_returned_extensions


def test_update_extension_header_with_other_headers():
    extensions = ['ext']
    http_kwargs = {'headers': {'X_Other': 'Test'}}
    result_kwargs, _ = update_extension_header(http_kwargs, extensions, None)
    headers = result_kwargs.get('headers', {})
    assert HTTP_EXTENSION_HEADER in headers
    assert headers[HTTP_EXTENSION_HEADER] == 'ext'
    assert headers['X_Other'] == 'Test'


@pytest.mark.parametrize('extensions', [(None), ([])])
def test_update_extension_header_no_or_empty_extensions(extensions):
    http_kwargs = {'headers': {'X_Other': 'Test'}}
    result_kwargs, _ = update_extension_header(http_kwargs, extensions, None)
    assert HTTP_EXTENSION_HEADER not in result_kwargs['headers']
    assert result_kwargs['headers']['X_Other'] == 'Test'
