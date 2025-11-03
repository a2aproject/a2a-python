import pytest

from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.client.transports.utils import update_extension_header


class TestUtils:
    def test_update_extension_header_no_initial_headers(self):
        extensions = ['test_extension_1', 'test_extension_2']

        http_kwargs = {}
        result_kwargs = update_extension_header(http_kwargs, extensions)
        header_value = result_kwargs['headers'][HTTP_EXTENSION_HEADER]
        actual_extensions_list = [e.strip() for e in header_value.split(',')]
        actual_extensions = set(actual_extensions_list)

        expected_extensions = {
            'test_extension_1',
            'test_extension_2',
        }
        assert len(actual_extensions_list) == 2
        assert actual_extensions == expected_extensions

    @pytest.mark.parametrize(
        'existing_header, expected_count',
        [
            ('test_extension_1, test_extension_2', 3),
            ('test_extension_1,test_extension_2', 3),
            ('test_extension_1', 3),
        ],
    )
    def test_update_extension_header_merge_with_existing_extensions(
        self,
        existing_header: str,
        expected_count: int,
    ):
        extensions = ['test_extension_2', 'test_extension_3']
        http_kwargs = {'headers': {HTTP_EXTENSION_HEADER: existing_header}}
        result_kwargs = update_extension_header(http_kwargs, extensions)

        header_value = result_kwargs['headers'][HTTP_EXTENSION_HEADER]
        actual_extensions_list = [e.strip() for e in header_value.split(',')]
        actual_extensions = set(actual_extensions_list)

        expected_extensions = {
            'test_extension_1',
            'test_extension_2',
            'test_extension_3',
        }
        assert len(actual_extensions_list) == expected_count
        assert actual_extensions == expected_extensions

    def test_update_extension_header_with_other_headers(self):
        extensions = ['test_extension_1']
        http_kwargs = {'headers': {'X_Other': 'Test'}}
        result_kwargs = update_extension_header(http_kwargs, extensions)
        headers = result_kwargs.get('headers', {})
        assert HTTP_EXTENSION_HEADER in headers
        assert headers[HTTP_EXTENSION_HEADER] == 'test_extension_1'
        assert headers['X_Other'] == 'Test'

    def test_update_extension_header_with_existing_other_headers(self):
        extensions = ['test_extension_1']
        http_kwargs = {'headers': {'X_Other': 'Test'}}
        result_kwargs = update_extension_header(http_kwargs, extensions)
        assert (
            result_kwargs['headers'][HTTP_EXTENSION_HEADER]
            == 'test_extension_1'
        )
        assert result_kwargs['headers']['X_Other'] == 'Test'

    def test_update_extension_header_no_extensions(self):
        http_kwargs = {'headers': {'X_Other': 'Test'}}
        result_kwargs = update_extension_header(http_kwargs, None)
        assert HTTP_EXTENSION_HEADER not in result_kwargs['headers']
        assert result_kwargs['headers']['X_Other'] == 'Test'

    def test_update_extension_header_empty_extensions(self):
        http_kwargs = {'headers': {'X_Other': 'Test'}}
        result_kwargs = update_extension_header(http_kwargs, [])
        assert HTTP_EXTENSION_HEADER not in result_kwargs['headers']
        assert result_kwargs['headers']['X_Other'] == 'Test'
