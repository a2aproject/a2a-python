"""Tests for a2a.client.service_parameters module."""

from a2a.client.service_parameters import (
    ServiceParametersFactory,
    with_a2a_extensions,
)
from a2a.extensions.common import HTTP_EXTENSION_HEADER


def test_with_a2a_extensions_merges_dedupes_and_sorts():
    """Repeated calls accumulate; duplicates collapse; output is sorted."""
    parameters = ServiceParametersFactory.create(
        [
            with_a2a_extensions(['ext-c', 'ext-a']),
            with_a2a_extensions(['ext-b', 'ext-a']),
        ]
    )

    assert parameters[HTTP_EXTENSION_HEADER] == 'ext-a,ext-b,ext-c'


def test_with_a2a_extensions_merges_existing_header_value():
    """Pre-existing comma-separated header values are parsed and merged."""
    parameters = ServiceParametersFactory.create_from(
        {HTTP_EXTENSION_HEADER: 'ext-a, ext-b'},
        [with_a2a_extensions(['ext-c'])],
    )

    assert parameters[HTTP_EXTENSION_HEADER] == 'ext-a,ext-b,ext-c'


def test_with_a2a_extensions_empty_is_noop():
    """An empty extensions list leaves the header untouched / absent."""
    parameters = ServiceParametersFactory.create(
        [
            with_a2a_extensions(['ext-a']),
            with_a2a_extensions([]),
        ]
    )

    assert parameters[HTTP_EXTENSION_HEADER] == 'ext-a'
    assert HTTP_EXTENSION_HEADER not in ServiceParametersFactory.create(
        [with_a2a_extensions([])]
    )


def test_with_a2a_extensions_normalizes_input_strings():
    """Input strings are split on commas and stripped, like header values."""
    parameters = ServiceParametersFactory.create(
        [with_a2a_extensions(['ext-a, ext-b', '  ext-c  '])]
    )

    assert parameters[HTTP_EXTENSION_HEADER] == 'ext-a,ext-b,ext-c'
