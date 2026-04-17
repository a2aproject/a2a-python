"""Tests for a2a.client.service_parameters module."""

from a2a.client.service_parameters import (
    ServiceParametersFactory,
    with_a2a_extensions,
)
from a2a.extensions.common import HTTP_EXTENSION_HEADER


def test_with_a2a_extensions_sets_header_when_empty():
    """First call on empty parameters sets the joined URIs."""
    parameters = ServiceParametersFactory.create(
        [with_a2a_extensions(['ext-b', 'ext-a'])]
    )

    assert parameters[HTTP_EXTENSION_HEADER] == 'ext-a,ext-b'


def test_with_a2a_extensions_merges_disjoint_calls():
    """A second call with disjoint URIs unions both sets."""
    parameters = ServiceParametersFactory.create(
        [
            with_a2a_extensions(['ext-a']),
            with_a2a_extensions(['ext-b']),
        ]
    )

    assert parameters[HTTP_EXTENSION_HEADER] == 'ext-a,ext-b'


def test_with_a2a_extensions_deduplicates_overlapping():
    """Overlapping URIs do not produce duplicates."""
    parameters = ServiceParametersFactory.create(
        [
            with_a2a_extensions(['ext-a', 'ext-b']),
            with_a2a_extensions(['ext-b', 'ext-c']),
        ]
    )

    assert parameters[HTTP_EXTENSION_HEADER] == 'ext-a,ext-b,ext-c'


def test_with_a2a_extensions_empty_is_noop():
    """Calling with an empty list leaves any existing header untouched."""
    parameters = ServiceParametersFactory.create(
        [
            with_a2a_extensions(['ext-a']),
            with_a2a_extensions([]),
        ]
    )

    assert parameters[HTTP_EXTENSION_HEADER] == 'ext-a'


def test_with_a2a_extensions_empty_does_not_create_header():
    """Calling with an empty list on empty parameters adds nothing."""
    parameters = ServiceParametersFactory.create([with_a2a_extensions([])])

    assert HTTP_EXTENSION_HEADER not in parameters


def test_with_a2a_extensions_output_is_sorted():
    """Output ordering is deterministic (sorted) regardless of input order."""
    parameters = ServiceParametersFactory.create(
        [with_a2a_extensions(['c', 'a', 'b'])]
    )

    assert parameters[HTTP_EXTENSION_HEADER] == 'a,b,c'


def test_with_a2a_extensions_merges_existing_header_value():
    """Existing comma-separated header values are parsed and merged."""
    base = ServiceParametersFactory.create_from(
        {HTTP_EXTENSION_HEADER: 'ext-a, ext-b'},
        [with_a2a_extensions(['ext-c'])],
    )

    assert base[HTTP_EXTENSION_HEADER] == 'ext-a,ext-b,ext-c'
