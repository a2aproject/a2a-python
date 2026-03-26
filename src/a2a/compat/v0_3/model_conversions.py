"""Database model conversions for v0.3 compatibility."""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from cryptography.fernet import Fernet


from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.conversions import (
    to_compat_push_notification_config,
    to_compat_task,
    to_core_task,
    to_core_task_push_notification_config,
)
from a2a.server.models import PushNotificationConfigModel, TaskModel
from a2a.types import a2a_pb2 as pb2_v10


def core_to_compat_task_model(task: pb2_v10.Task, owner: str) -> TaskModel:
    """Converts a 1.0 core Task to a TaskModel using v0.3 JSON structure."""
    compat_task = to_compat_task(task)
    data = compat_task.model_dump(mode='json')

    return TaskModel(
        id=task.id,
        context_id=task.context_id,
        owner=owner,
        status=data.get('status'),
        history=data.get('history'),
        artifacts=data.get('artifacts'),
        task_metadata=data.get('metadata'),
        protocol_version='0.3',
    )


def compat_task_model_to_core(task_model: TaskModel) -> pb2_v10.Task:
    """Converts a TaskModel with v0.3 structure to a 1.0 core Task."""
    compat_task = types_v03.Task(
        id=task_model.id,
        context_id=task_model.context_id,
        status=types_v03.TaskStatus.model_validate(task_model.status),
        artifacts=(
            [types_v03.Artifact.model_validate(a) for a in task_model.artifacts]
            if task_model.artifacts
            else []
        ),
        history=(
            [types_v03.Message.model_validate(h) for h in task_model.history]
            if task_model.history
            else []
        ),
        metadata=task_model.task_metadata,
    )
    return to_core_task(compat_task)


def core_to_compat_push_notification_config_model(
    task_id: str,
    config: pb2_v10.TaskPushNotificationConfig,
    owner: str,
    fernet: 'Fernet | None' = None,
) -> PushNotificationConfigModel:
    """Converts a 1.0 core TaskPushNotificationConfig to a PushNotificationConfigModel using v0.3 JSON structure."""
    compat_config = to_compat_push_notification_config(config)

    json_payload = compat_config.model_dump_json().encode('utf-8')
    data_to_store = fernet.encrypt(json_payload) if fernet else json_payload

    return PushNotificationConfigModel(
        task_id=task_id,
        config_id=config.id,
        owner=owner,
        config_data=data_to_store,
        protocol_version='0.3',
    )


def compat_push_notification_config_model_to_core(
    model_instance: str, task_id: str
) -> pb2_v10.TaskPushNotificationConfig:
    """Converts a PushNotificationConfigModel with v0.3 structure back to a 1.0 core TaskPushNotificationConfig."""
    inner_config = types_v03.PushNotificationConfig.model_validate_json(
        model_instance
    )
    return to_core_task_push_notification_config(
        types_v03.TaskPushNotificationConfig(
            task_id=task_id,
            push_notification_config=inner_config,
        )
    )
