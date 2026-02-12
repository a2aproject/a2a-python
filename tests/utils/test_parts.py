from google.protobuf.struct_pb2 import Struct, Value
from a2a.types.a2a_pb2 import (
    Part,
)
from a2a.utils.parts import (
    get_data_parts,
    get_file_parts,
    get_text_parts,
)


class TestGetTextParts:
    def test_get_text_parts_single_text_part(self):
        # Setup
        parts = [Part(text='Hello world')]

        # Exercise
        result = get_text_parts(parts)

        # Verify
        assert result == ['Hello world']

    def test_get_text_parts_multiple_text_parts(self):
        # Setup
        parts = [
            Part(text='First part'),
            Part(text='Second part'),
            Part(text='Third part'),
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
        data = Struct()
        data.update({'key': 'value'})
        parts = [Part(data=Value(struct_value=data))]

        # Exercise
        result = get_data_parts(parts)

        # Verify
        assert result == [{'key': 'value'}]

    def test_get_data_parts_multiple_data_parts(self):
        # Setup
        data1 = Struct()
        data1.update({'key1': 'value1'})
        data2 = Struct()
        data2.update({'key2': 'value2'})
        parts = [
            Part(data=Value(struct_value=data1)),
            Part(data=Value(struct_value=data2)),
        ]

        # Exercise
        result = get_data_parts(parts)

        # Verify
        assert result == [{'key1': 'value1'}, {'key2': 'value2'}]

    def test_get_data_parts_mixed_parts(self):
        # Setup
        data1 = Struct()
        data1.update({'key1': 'value1'})
        data2 = Struct()
        data2.update({'key2': 'value2'})
        parts = [
            Part(text='some text'),
            Part(data=Value(struct_value=data1)),
            Part(data=Value(struct_value=data2)),
        ]

        # Exercise
        result = get_data_parts(parts)

        # Verify
        assert result == [{'key1': 'value1'}, {'key2': 'value2'}]

    def test_get_data_parts_no_data_parts(self):
        # Setup
        parts = [
            Part(text='some text'),
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
        parts = [Part(url='file://path/to/file', media_type='text/plain')]

        # Exercise
        result = get_file_parts(parts)

        # Verify
        assert len(result) == 1
        assert result[0].url == 'file://path/to/file'
        assert result[0].media_type == 'text/plain'

    def test_get_file_parts_multiple_file_parts(self):
        # Setup
        parts = [
            Part(url='file://path/to/file1', media_type='text/plain'),
            Part(raw=b'file content', media_type='application/octet-stream'),
        ]

        # Exercise
        result = get_file_parts(parts)

        # Verify
        assert len(result) == 2
        assert result[0].url == 'file://path/to/file1'
        assert result[1].raw == b'file content'

    def test_get_file_parts_mixed_parts(self):
        # Setup
        parts = [
            Part(text='some text'),
            Part(url='file://path/to/file', media_type='text/plain'),
        ]

        # Exercise
        result = get_file_parts(parts)

        # Verify
        assert len(result) == 1
        assert result[0].url == 'file://path/to/file'

    def test_get_file_parts_no_file_parts(self):
        # Setup
        data = Struct()
        data.update({'key': 'value'})
        parts = [
            Part(text='some text'),
            Part(data=Value(struct_value=data)),
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
