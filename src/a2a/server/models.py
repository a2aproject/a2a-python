from datetime import datetime
from typing import TYPE_CHECKING, Any, Generic, TypeVar


if TYPE_CHECKING:
    from typing_extensions import override
else:

    def override(func):  # noqa: ANN001, ANN201
        """Override decorator."""
        return func


from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from google.protobuf.message import Message as ProtoMessage
from pydantic import BaseModel, ValidationError

from a2a.compat.v0_3 import conversions
from a2a.compat.v0_3 import types as types_v03
from a2a.types.a2a_pb2 import Artifact, Message, TaskStatus


try:
    from sqlalchemy import JSON, DateTime, Dialect, Index, LargeBinary, String
    from sqlalchemy.orm import (
        DeclarativeBase,
        Mapped,
        declared_attr,
        mapped_column,
    )
    from sqlalchemy.types import (
        TypeDecorator,
    )
except ImportError as e:
    raise ImportError(
        'Database models require SQLAlchemy. '
        'Install with one of: '
        "'pip install a2a-sdk[postgresql]', "
        "'pip install a2a-sdk[mysql]', "
        "'pip install a2a-sdk[sqlite]', "
        "or 'pip install a2a-sdk[sql]'"
    ) from e


T = TypeVar('T')


class PydanticType(TypeDecorator[T], Generic[T]):
    """SQLAlchemy type that handles Pydantic model and Protobuf message serialization."""

    impl = JSON
    cache_ok = True

    def __init__(self, pydantic_type: type[T], **kwargs: dict[str, Any]):
        """Initialize the PydanticType.

        Args:
            pydantic_type: The Pydantic model or Protobuf message type to handle.
            **kwargs: Additional arguments for TypeDecorator.
        """
        self.pydantic_type = pydantic_type
        super().__init__(**kwargs)

    def process_bind_param(
        self, value: T | None, dialect: Dialect
    ) -> dict[str, Any] | None:
        """Convert Pydantic model or Protobuf message to a JSON-serializable dictionary for the database."""
        if value is None:
            return None
        if isinstance(value, ProtoMessage):
            return MessageToDict(value, preserving_proto_field_name=False)
        if isinstance(value, BaseModel):
            return value.model_dump(mode='json')
        return value  # type: ignore[return-value]

    def process_result_value(
        self, value: dict[str, Any] | None, dialect: Dialect
    ) -> T | None:
        """Convert a JSON-like dictionary from the database back to a Pydantic model or Protobuf message."""
        if value is None:
            return None
        # Check if it's a protobuf message class
        if isinstance(self.pydantic_type, type) and issubclass(
            self.pydantic_type, ProtoMessage
        ):
            try:
                return ParseDict(value, self.pydantic_type())  # type: ignore[return-value]
            except (ParseError, ValueError):
                # Try legacy conversion
                legacy_map = _get_legacy_conversions()
                if self.pydantic_type in legacy_map:
                    legacy_type, convert_func = legacy_map[self.pydantic_type]
                    try:
                        legacy_instance = legacy_type.model_validate(value)
                        return convert_func(legacy_instance)
                    except ValidationError:
                        pass
                raise
        # Assume it's a Pydantic model
        return self.pydantic_type.model_validate(value)  # type: ignore[attr-defined]


class PydanticListType(TypeDecorator, Generic[T]):
    """SQLAlchemy type that handles lists of Pydantic models or Protobuf messages."""

    impl = JSON
    cache_ok = True

    def __init__(self, pydantic_type: type[T], **kwargs: dict[str, Any]):
        """Initialize the PydanticListType.

        Args:
            pydantic_type: The Pydantic model or Protobuf message type for items in the list.
            **kwargs: Additional arguments for TypeDecorator.
        """
        self.pydantic_type = pydantic_type
        super().__init__(**kwargs)

    def process_bind_param(
        self, value: list[T] | None, dialect: Dialect
    ) -> list[dict[str, Any]] | None:
        """Convert a list of Pydantic models or Protobuf messages to a JSON-serializable list for the DB."""
        if value is None:
            return None
        result: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, ProtoMessage):
                result.append(
                    MessageToDict(item, preserving_proto_field_name=False)
                )
            elif isinstance(item, BaseModel):
                result.append(item.model_dump(mode='json'))
            else:
                result.append(item)  # type: ignore[arg-type]
        return result

    def process_result_value(
        self, value: list[dict[str, Any]] | None, dialect: Dialect
    ) -> list[T] | None:
        """Convert a JSON-like list from the DB back to a list of Pydantic models or Protobuf messages."""
        if value is None:
            return None
        # Check if it's a protobuf message class
        if isinstance(self.pydantic_type, type) and issubclass(
            self.pydantic_type, ProtoMessage
        ):
            result = []
            legacy_map = _get_legacy_conversions()
            legacy_info = legacy_map.get(self.pydantic_type)

            for item in value:
                try:
                    result.append(ParseDict(item, self.pydantic_type()))
                except (ParseError, ValueError):  # noqa: PERF203
                    if legacy_info:
                        legacy_type, convert_func = legacy_info
                        try:
                            legacy_instance = legacy_type.model_validate(item)
                            result.append(convert_func(legacy_instance))
                            continue
                        except ValidationError:
                            pass
                    raise
            return result  # type: ignore[return-value]
        # Assume it's a Pydantic model
        return [self.pydantic_type.model_validate(item) for item in value]  # type: ignore[attr-defined]


# Base class for all database models
class Base(DeclarativeBase):
    """Base class for declarative models in A2A SDK."""


# TaskMixin that can be used with any table name
class TaskMixin:
    """Mixin providing standard task columns with proper type handling."""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    context_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default='task'
    )
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    last_updated: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    # Properly typed Pydantic fields with automatic serialization
    status: Mapped[TaskStatus] = mapped_column(PydanticType(TaskStatus))
    artifacts: Mapped[list[Artifact] | None] = mapped_column(
        PydanticListType(Artifact), nullable=True
    )
    history: Mapped[list[Message] | None] = mapped_column(
        PydanticListType(Message), nullable=True
    )
    protocol_version: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )

    # Using declared_attr to avoid conflict with Pydantic's metadata
    @declared_attr
    @classmethod
    def task_metadata(cls) -> Mapped[dict[str, Any] | None]:
        """Define the 'metadata' column, avoiding name conflicts with Pydantic."""
        return mapped_column(JSON, nullable=True, name='metadata')

    @override
    def __repr__(self) -> str:
        """Return a string representation of the task."""
        return (
            f'<{self.__class__.__name__}(id="{self.id}", '
            f'context_id="{self.context_id}", status="{self.status}")>'
        )

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> tuple[Any, ...]:
        """Define a composite index (owner, last_updated) for each table that uses the mixin."""
        tablename = getattr(cls, '__tablename__', 'tasks')
        return (
            Index(
                f'idx_{tablename}_owner_last_updated', 'owner', 'last_updated'
            ),
        )


def create_task_model(
    table_name: str = 'tasks', base: type[DeclarativeBase] = Base
) -> type:
    """Create a TaskModel class with a configurable table name.

    Args:
        table_name: Name of the database table. Defaults to 'tasks'.
        base: Base declarative class to use. Defaults to the SDK's Base class.

    Returns:
        TaskModel class with the specified table name.

    Example:
        .. code-block:: python

            # Create a task model with default table name
            TaskModel = create_task_model()

            # Create a task model with custom table name
            CustomTaskModel = create_task_model('my_tasks')

            # Use with a custom base
            from myapp.database import Base as MyBase

            TaskModel = create_task_model('tasks', MyBase)
    """

    class TaskModel(TaskMixin, base):  # type: ignore
        __tablename__ = table_name

        @override
        def __repr__(self) -> str:
            """Return a string representation of the task."""
            return (
                f'<TaskModel[{table_name}](id="{self.id}", '
                f'context_id="{self.context_id}", status="{self.status}")>'
            )

    # Set a dynamic name for better debugging
    TaskModel.__name__ = f'TaskModel_{table_name}'
    TaskModel.__qualname__ = f'TaskModel_{table_name}'

    return TaskModel


# Default TaskModel for backward compatibility
class TaskModel(TaskMixin, Base):
    """Default task model with standard table name."""

    __tablename__ = 'tasks'


# PushNotificationConfigMixin that can be used with any table name
class PushNotificationConfigMixin:
    """Mixin providing standard push notification config columns."""

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    config_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    config_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    protocol_version: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )

    @override
    def __repr__(self) -> str:
        """Return a string representation of the push notification config."""
        return (
            f'<{self.__class__.__name__}(task_id="{self.task_id}", '
            f'config_id="{self.config_id}")>'
        )


def create_push_notification_config_model(
    table_name: str = 'push_notification_configs',
    base: type[DeclarativeBase] = Base,
) -> type:
    """Create a PushNotificationConfigModel class with a configurable table name."""

    class PushNotificationConfigModel(PushNotificationConfigMixin, base):  # type: ignore
        __tablename__ = table_name

        @override
        def __repr__(self) -> str:
            """Return a string representation of the push notification config."""
            return (
                f'<PushNotificationConfigModel[{table_name}]('
                f'task_id="{self.task_id}", config_id="{self.config_id}")>'
            )

    PushNotificationConfigModel.__name__ = (
        f'PushNotificationConfigModel_{table_name}'
    )
    PushNotificationConfigModel.__qualname__ = (
        f'PushNotificationConfigModel_{table_name}'
    )

    return PushNotificationConfigModel


# Default PushNotificationConfigModel for backward compatibility
class PushNotificationConfigModel(PushNotificationConfigMixin, Base):
    """Default push notification config model with standard table name."""

    __tablename__ = 'push_notification_configs'


def _get_legacy_conversions() -> dict[type, tuple[type, Any]]:
    """Get the mapping of current types to their legacy counterparts and conversion functions."""
    return {
        TaskStatus: (
            types_v03.TaskStatus,
            conversions.to_core_task_status,
        ),
        Message: (types_v03.Message, conversions.to_core_message),
        Artifact: (types_v03.Artifact, conversions.to_core_artifact),
    }
