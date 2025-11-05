import pytest

from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.client.transports.utils import (
    update_extension_header,
    update_extension_metadata,
)
from a2a.utils import proto_utils


class TestUtils:
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
        self,
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

    def test_update_extension_header_with_other_headers(self):
        extensions = ['test_extension']
        http_kwargs = {'headers': {'X_Other': 'Test'}}
        result_kwargs = update_extension_header(http_kwargs, extensions)
        headers = result_kwargs.get('headers', {})
        assert HTTP_EXTENSION_HEADER in headers
        assert headers[HTTP_EXTENSION_HEADER] == 'test_extension'
        assert headers['X_Other'] == 'Test'

    @pytest.mark.parametrize('extensions', [(None), ([])])
    def test_update_extension_header_no_or_empty_extensions(self, extensions):
        http_kwargs = {'headers': {'X_Other': 'Test'}}
        result_kwargs = update_extension_header(http_kwargs, extensions)
        assert HTTP_EXTENSION_HEADER not in result_kwargs['headers']
        assert result_kwargs['headers']['X_Other'] == 'Test'

    @pytest.mark.parametrize(
        'extensions, existing_metadata, expected_extensions, expected_count',
        [
            (
                ['test_extension_1', 'test_extension_2'],
                None,
                {'test_extension_1', 'test_extension_2'},
                2,
            ),
            (
                ['test_extension_1', 'test_extension_2'],
                {HTTP_EXTENSION_HEADER: 'test_extension_2, test_extension_3'},
                {'test_extension_1', 'test_extension_2', 'test_extension_3'},
                3,
            ),
            (
                ['test_extension_1', 'test_extension_2'],
                {HTTP_EXTENSION_HEADER: 'test_extension_3'},
                {'test_extension_1', 'test_extension_2', 'test_extension_3'},
                3,
            ),
            (
                ['test_extension_1'],
                {'X_Other': 'Test'},
                {'test_extension_1'},
                1,
            ),
        ],
    )
    def test_update_extension_metadata(
        self,
        extensions: list[str],
        existing_metadata: dict[str, str],
        expected_extensions: set[str],
        expected_count: int,
    ):
        result_metadata = update_extension_metadata(
            existing_metadata, extensions
        )
        assert result_metadata is not None
        metadata_dict = proto_utils.FromProto.metadata(result_metadata)
        header_value = metadata_dict.get(HTTP_EXTENSION_HEADER, '')
        actual_extensions_list = [
            e.strip() for e in header_value.split(',') if e.strip()
        ]
        actual_extensions = set(actual_extensions_list)

        assert len(actual_extensions_list) == expected_count
        assert actual_extensions == expected_extensions
        if existing_metadata and 'X_Other' in existing_metadata:
            assert metadata_dict['X_Other'] == existing_metadata['X_Other']

    @pytest.mark.parametrize('extensions', [(None), ([])])
    def test_update_extension_metadata_no_or_empty_extensions(self, extensions):
        metadata = {'X_Other': 'Test'}
        result_metadata = update_extension_metadata(metadata, extensions)
        assert result_metadata is not None
        metadata_dict = proto_utils.FromProto.metadata(result_metadata)
        assert HTTP_EXTENSION_HEADER not in metadata_dict
        assert metadata_dict['X_Other'] == 'Test'
