from a2a.types import (
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
    Part,
    TextPart,
)
from a2a.utils.parts import (
    get_data_parts,
    get_file_parts,
    get_text_parts,
)


class TestGetTextParts:
    def test_get_text_parts_single_text_part(self):
        # Setup
        parts = [Part(root=TextPart(text='Hello world'))]

        # Exercise
        result = get_text_parts(parts)

        # Verify
        assert result == ['Hello world']

    def test_get_text_parts_multiple_text_parts(self):
        # Setup
        parts = [
            Part(root=TextPart(text='First part')),
            Part(root=TextPart(text='Second part')),
            Part(root=TextPart(text='Third part')),
        ]

        # Exercise
        result = get_text_parts(parts)

        # Verify
        assert result == ['First part', 'Second part', 'Third part']

    def test_get_text_parts_empty_list(self):
        # Setup
        parts = []

        # Exercise
        result = get_text_parts(parts)

        # Verify
        assert result == []


class TestGetDataParts:
    def test_get_data_parts_single_data_part(self):
        # Setup
        parts = [Part(root=DataPart(data={'key': 'value'}))]

        # Exercise
        result = get_data_parts(parts)

        # Verify
        assert result == [{'key': 'value'}]

    def test_get_data_parts_multiple_data_parts(self):
        # Setup
        parts = [
            Part(root=DataPart(data={'key1': 'value1'})),
            Part(root=DataPart(data={'key2': 'value2'})),
        ]

        # Exercise
        result = get_data_parts(parts)

        # Verify
        assert result == [{'key1': 'value1'}, {'key2': 'value2'}]

    def test_get_data_parts_mixed_parts(self):
        # Setup
        parts = [
            Part(root=TextPart(text='some text')),
            Part(root=DataPart(data={'key1': 'value1'})),
            Part(root=DataPart(data={'key2': 'value2'})),
        ]

        # Exercise
        result = get_data_parts(parts)

        # Verify
        assert result == [{'key1': 'value1'}, {'key2': 'value2'}]

    def test_get_data_parts_no_data_parts(self):
        # Setup
        parts = [
            Part(root=TextPart(text='some text')),
        ]

        # Exercise
        result = get_data_parts(parts)

        # Verify
        assert result == []

    def test_get_data_parts_empty_list(self):
        # Setup
        parts = []

        # Exercise
        result = get_data_parts(parts)

        # Verify
        assert result == []


class TestGetFileParts:
    def test_get_file_parts_single_file_part(self):
        # Setup
        file_with_uri = FileWithUri(
            uri='file://path/to/file', mimeType='text/plain'
        )
        parts = [Part(root=FilePart(file=file_with_uri))]

        # Exercise
        result = get_file_parts(parts)

        # Verify
        assert result == [file_with_uri]

    def test_get_file_parts_multiple_file_parts(self):
        # Setup
        file_with_uri1 = FileWithUri(
            uri='file://path/to/file1', mime_type='text/plain'
        )
        file_with_bytes = FileWithBytes(
            bytes='ZmlsZSBjb250ZW50',
            mime_type='application/octet-stream',  # 'file content'
        )
        parts = [
            Part(root=FilePart(file=file_with_uri1)),
            Part(root=FilePart(file=file_with_bytes)),
        ]

        # Exercise
        result = get_file_parts(parts)

        # Verify
        assert result == [file_with_uri1, file_with_bytes]

    def test_get_file_parts_mixed_parts(self):
        # Setup
        file_with_uri = FileWithUri(
            uri='file://path/to/file', mime_type='text/plain'
        )
        parts = [
            Part(root=TextPart(text='some text')),
            Part(root=FilePart(file=file_with_uri)),
        ]

        # Exercise
        result = get_file_parts(parts)

        # Verify
        assert result == [file_with_uri]

    def test_get_file_parts_no_file_parts(self):
        # Setup
        parts = [
            Part(root=TextPart(text='some text')),
            Part(root=DataPart(data={'key': 'value'})),
        ]

        # Exercise
        result = get_file_parts(parts)

        # Verify
        assert result == []

    def test_get_file_parts_empty_list(self):
        # Setup
        parts = []

        # Exercise
        result = get_file_parts(parts)

        # Verify
        assert result == []
