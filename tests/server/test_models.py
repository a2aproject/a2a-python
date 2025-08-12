"""Tests for a2a.server.models module."""

from unittest.mock import MagicMock

from sqlalchemy.orm import DeclarativeBase

from a2a.server.models import (
    PydanticListType,
    PydanticType,
    create_push_notification_config_model,
    create_task_model,
)
from a2a.types import Artifact, TaskState, TaskStatus, TextPart


class TestPydanticType:
    """Tests for PydanticType SQLAlchemy type decorator."""

    def test_process_bind_param_with_pydantic_model(self):
        pydantic_type = PydanticType(TaskStatus)
        status = TaskStatus(state=TaskState.working)
        dialect = MagicMock()

        result = pydantic_type.process_bind_param(status, dialect)
        assert result['state'] == 'working'
        assert result['message'] is None
        # TaskStatus may have other optional fields

    def test_process_bind_param_with_none(self):
        pydantic_type = PydanticType(TaskStatus)
        dialect = MagicMock()

        result = pydantic_type.process_bind_param(None, dialect)
        assert result is None

    def test_process_result_value(self):
        pydantic_type = PydanticType(TaskStatus)
        dialect = MagicMock()

        result = pydantic_type.process_result_value(
            {'state': 'completed', 'message': None}, dialect
        )
        assert isinstance(result, TaskStatus)
        assert result.state == 'completed'


class TestPydanticListType:
    """Tests for PydanticListType SQLAlchemy type decorator."""

    def test_process_bind_param_with_list(self):
        pydantic_list_type = PydanticListType(Artifact)
        artifacts = [
            Artifact(
                artifact_id='1', parts=[TextPart(type='text', text='Hello')]
            ),
            Artifact(
                artifact_id='2', parts=[TextPart(type='text', text='World')]
            ),
        ]
        dialect = MagicMock()

        result = pydantic_list_type.process_bind_param(artifacts, dialect)
        assert len(result) == 2
        assert result[0]['artifactId'] == '1'  # JSON mode uses camelCase
        assert result[1]['artifactId'] == '2'

    def test_process_result_value_with_list(self):
        pydantic_list_type = PydanticListType(Artifact)
        dialect = MagicMock()
        data = [
            {'artifact_id': '1', 'parts': [{'type': 'text', 'text': 'Hello'}]},
            {'artifact_id': '2', 'parts': [{'type': 'text', 'text': 'World'}]},
        ]

        result = pydantic_list_type.process_result_value(data, dialect)
        assert len(result) == 2
        assert all(isinstance(art, Artifact) for art in result)
        assert result[0].artifact_id == '1'
        assert result[1].artifact_id == '2'


def test_create_task_model():
    """Test dynamic task model creation."""

    # Create a fresh base to avoid table conflicts
    class TestBase(DeclarativeBase):
        pass

    # Create with default table name
    default_task_model = create_task_model('test_tasks_1', TestBase)
    assert default_task_model.__tablename__ == 'test_tasks_1'
    assert default_task_model.__name__ == 'TaskModel_test_tasks_1'

    # Create with custom table name
    custom_task_model = create_task_model('test_tasks_2', TestBase)
    assert custom_task_model.__tablename__ == 'test_tasks_2'
    assert custom_task_model.__name__ == 'TaskModel_test_tasks_2'


def test_create_push_notification_config_model():
    """Test dynamic push notification config model creation."""

    # Create a fresh base to avoid table conflicts
    class TestBase(DeclarativeBase):
        pass

    # Create with default table name
    default_model = create_push_notification_config_model(
        'test_push_configs_1', TestBase
    )
    assert default_model.__tablename__ == 'test_push_configs_1'

    # Create with custom table name
    custom_model = create_push_notification_config_model(
        'test_push_configs_2', TestBase
    )
    assert custom_model.__tablename__ == 'test_push_configs_2'
    assert 'test_push_configs_2' in custom_model.__name__
