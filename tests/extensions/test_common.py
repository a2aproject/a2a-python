from a2a.extensions.common import (
    find_extension_by_uri,
    get_requested_extensions,
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
