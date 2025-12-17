import uuid

import pytest

from pydantic import ValidationError

from a2a.server.id_generator import (
    IDGenerator,
    IDGeneratorContext,
    UUIDGenerator,
)


class TestIDGeneratorContext:
    """Tests for IDGeneratorContext."""

    def test_context_creation_with_all_fields(self):
        """Test creating context with all fields populated."""
        context = IDGeneratorContext(
            task_id='task_123', context_id='context_456'
        )
        assert context.task_id == 'task_123'
        assert context.context_id == 'context_456'

    def test_context_creation_with_defaults(self):
        """Test creating context with default None values."""
        context = IDGeneratorContext()
        assert context.task_id is None
        assert context.context_id is None

    @pytest.mark.parametrize(
        'kwargs, expected_task_id, expected_context_id',
        [
            ({'task_id': 'task_123'}, 'task_123', None),
            ({'context_id': 'context_456'}, None, 'context_456'),
        ],
    )
    def test_context_creation_with_partial_fields(
        self, kwargs, expected_task_id, expected_context_id
    ):
        """Test creating context with only some fields populated."""
        context = IDGeneratorContext(**kwargs)
        assert context.task_id == expected_task_id
        assert context.context_id == expected_context_id

    def test_context_mutability(self):
        """Test that context fields can be updated (Pydantic models are mutable by default)."""
        context = IDGeneratorContext(task_id='task_123')
        context.task_id = 'task_456'
        assert context.task_id == 'task_456'

    def test_context_validation(self):
        """Test that context raises validation error for invalid types."""
        with pytest.raises(ValidationError):
            IDGeneratorContext(task_id={'not': 'a string'})


class TestIDGenerator:
    """Tests for IDGenerator abstract base class."""

    def test_cannot_instantiate_abstract_class(self):
        """Test that IDGenerator cannot be instantiated directly."""
        with pytest.raises(TypeError):
            IDGenerator()

    def test_subclass_must_implement_generate(self):
        """Test that subclasses must implement the generate method."""

        class IncompleteGenerator(IDGenerator):
            pass

        with pytest.raises(TypeError):
            IncompleteGenerator()

    def test_valid_subclass_implementation(self):
        """Test that a valid subclass can be instantiated."""

        class ValidGenerator(IDGenerator):  # pylint: disable=C0115,R0903
            def generate(self, context: IDGeneratorContext) -> str:
                return 'test_id'

        generator = ValidGenerator()
        assert generator.generate(IDGeneratorContext()) == 'test_id'


@pytest.fixture
def generator():
    """Returns a UUIDGenerator instance."""
    return UUIDGenerator()


@pytest.fixture
def context():
    """Returns a IDGeneratorContext instance."""
    return IDGeneratorContext()


class TestUUIDGenerator:
    """Tests for UUIDGenerator implementation."""

    def test_generate_returns_string(self, generator, context):
        """Test that generate returns a valid v4 UUID string."""
        result = generator.generate(context)
        assert isinstance(result, str)
        parsed_uuid = uuid.UUID(result)
        assert parsed_uuid.version == 4

    def test_generate_produces_unique_ids(self, generator, context):
        """Test that multiple calls produce unique IDs."""
        ids = [generator.generate(context) for _ in range(100)]
        # All IDs should be unique
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize(
        'context_arg',
        [
            None,
            IDGeneratorContext(),
        ],
        ids=[
            'none_context',
            'empty_context',
        ],
    )
    def test_generate_works_with_various_contexts(self, context_arg):
        """Test that generate works with various context inputs."""
        generator = UUIDGenerator()
        result = generator.generate(context_arg)
        assert isinstance(result, str)
        parsed_uuid = uuid.UUID(result)
        assert parsed_uuid.version == 4
