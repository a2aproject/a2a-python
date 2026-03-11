"""Tests for a2a.server.models module."""

from unittest.mock import MagicMock

from sqlalchemy.orm import DeclarativeBase

from a2a.server.models import (
    create_push_notification_config_model,
    create_task_model,
)


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
