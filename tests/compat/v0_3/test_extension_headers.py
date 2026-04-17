from a2a.compat.v0_3.extension_headers import (
    LEGACY_HTTP_EXTENSION_HEADER,
    add_legacy_extension_header,
)
from a2a.extensions.common import HTTP_EXTENSION_HEADER


def test_legacy_header_constant_value():
    assert LEGACY_HTTP_EXTENSION_HEADER == 'X-A2A-Extensions'


def test_mirrors_spec_header_under_legacy_name():
    params = {HTTP_EXTENSION_HEADER: 'foo,bar'}

    add_legacy_extension_header(params)

    assert params == {
        HTTP_EXTENSION_HEADER: 'foo,bar',
        LEGACY_HTTP_EXTENSION_HEADER: 'foo,bar',
    }


def test_no_op_when_spec_header_absent():
    params = {'Other': 'value'}

    add_legacy_extension_header(params)

    assert params == {'Other': 'value'}


def test_does_not_overwrite_existing_legacy_header():
    params = {
        HTTP_EXTENSION_HEADER: 'spec',
        LEGACY_HTTP_EXTENSION_HEADER: 'legacy-original',
    }

    add_legacy_extension_header(params)

    assert params[LEGACY_HTTP_EXTENSION_HEADER] == 'legacy-original'
