# ruff: noqa: PLC0415
import logging

from typing import TYPE_CHECKING

from google.protobuf.json_format import MessageToJson, Parse


try:
    from sqlalchemy import Table, and_, delete, select
    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
    )
    from sqlalchemy.orm import class_mapper
except ImportError as e:
    raise ImportError(
        'DatabasePushNotificationConfigStore requires SQLAlchemy and a database driver. '
        'Install with one of: '
        "'pip install a2a-sdk[postgresql]', "
        "'pip install a2a-sdk[mysql]', "
        "'pip install a2a-sdk[sqlite]', "
        "or 'pip install a2a-sdk[sql]'"
    ) from e

from collections.abc import Callable

from a2a.compat.v0_3.model_conversions import (
    compat_push_notification_config_model_to_core,
)
from a2a.server.context import ServerCallContext
from a2a.server.models import (
    Base,
    PushNotificationConfigModel,
    create_push_notification_config_model,
)
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.server.tasks.push_notification_config_store import (
    PushNotificationConfigStore,
)
from a2a.types.a2a_pb2 import TaskPushNotificationConfig


if TYPE_CHECKING:
    from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class DatabasePushNotificationConfigStore(PushNotificationConfigStore):
    """SQLAlchemy-based implementation of PushNotificationConfigStore.

    Stores push notification configurations in a database supported by SQLAlchemy.
    """

    engine: AsyncEngine
    async_session_maker: async_sessionmaker[AsyncSession]
    create_table: bool
    _initialized: bool
    config_model: type[PushNotificationConfigModel]
    _fernet: 'Fernet | None'
    owner_resolver: OwnerResolver
    core_to_model_conversion: (
        Callable[
            [str, TaskPushNotificationConfig, str, 'Fernet | None'],
            PushNotificationConfigModel,
        ]
        | None
    )
    model_to_core_conversion: (
        Callable[[PushNotificationConfigModel], TaskPushNotificationConfig]
        | None
    )

    def __init__(  # noqa: PLR0913
        self,
        engine: AsyncEngine,
        create_table: bool = True,
        table_name: str = 'push_notification_configs',
        encryption_key: str | bytes | None = None,
        owner_resolver: OwnerResolver = resolve_user_scope,
        core_to_model_conversion: Callable[
            [str, TaskPushNotificationConfig, str, 'Fernet | None'],
            PushNotificationConfigModel,
        ]
        | None = None,
        model_to_core_conversion: Callable[
            [PushNotificationConfigModel], TaskPushNotificationConfig
        ]
        | None = None,
    ) -> None:
        """Initializes the DatabasePushNotificationConfigStore.

        Args:
            engine: An existing SQLAlchemy AsyncEngine to be used by the store.
            create_table: If true, create the table on initialization.
            table_name: Name of the database table. Defaults to 'push_notification_configs'.
            encryption_key: A key for encrypting sensitive configuration data.
                If provided, `config_data` will be encrypted in the database.
                The key must be a URL-safe base64-encoded 32-byte key.
            owner_resolver: Function to resolve the owner from the context.
            core_to_model_conversion: Optional function to convert a TaskPushNotificationConfig to a TaskPushNotificationConfigModel.
            model_to_core_conversion: Optional function to convert a TaskPushNotificationConfigModel to a TaskPushNotificationConfig.
        """
        logger.debug(
            'Initializing DatabasePushNotificationConfigStore with existing engine, table: %s',
            table_name,
        )
        self.engine = engine
        self.async_session_maker = async_sessionmaker(
            self.engine, expire_on_commit=False
        )
        self.create_table = create_table
        self._initialized = False
        self.owner_resolver = owner_resolver
        self.config_model = (
            PushNotificationConfigModel
            if table_name == 'push_notification_configs'
            else create_push_notification_config_model(table_name)
        )
        self._fernet = None
        self.core_to_model_conversion = core_to_model_conversion
        self.model_to_core_conversion = model_to_core_conversion

        if encryption_key:
            try:
                from cryptography.fernet import (
                    Fernet,
                )
            except ImportError as e:
                raise ImportError(
                    "DatabasePushNotificationConfigStore with encryption requires the 'cryptography' "
                    'library. Install with: '
                    "'pip install a2a-sdk[encryption]'"
                ) from e

            if isinstance(encryption_key, str):
                encryption_key = encryption_key.encode('utf-8')
            self._fernet = Fernet(encryption_key)
            logger.debug(
                'Encryption enabled for push notification config store.'
            )

    async def initialize(self) -> None:
        """Initialize the database and create the table if needed."""
        if self._initialized:
            return

        logger.debug(
            'Initializing database schema for push notification configs...'
        )
        if self.create_table:
            async with self.engine.begin() as conn:
                mapper = class_mapper(self.config_model)
                tables_to_create = [
                    table for table in mapper.tables if isinstance(table, Table)
                ]
                await conn.run_sync(
                    Base.metadata.create_all, tables=tables_to_create
                )
        self._initialized = True
        logger.debug(
            'Database schema for push notification configs initialized.'
        )

    async def _ensure_initialized(self) -> None:
        """Ensure the database connection is initialized."""
        if not self._initialized:
            await self.initialize()

    def _to_orm(
        self, task_id: str, config: TaskPushNotificationConfig, owner: str
    ) -> PushNotificationConfigModel:
        """Maps a TaskPushNotificationConfig proto to a SQLAlchemy model instance.

        The config data is serialized to JSON bytes, and encrypted if a key is configured.
        """
        if self.core_to_model_conversion:
            return self.core_to_model_conversion(
                task_id, config, owner, self._fernet
            )

        json_payload = MessageToJson(config).encode('utf-8')

        if self._fernet:
            data_to_store = self._fernet.encrypt(json_payload)
        else:
            data_to_store = json_payload

        return self.config_model(
            task_id=task_id,
            config_id=config.id,
            owner=owner,
            config_data=data_to_store,
            protocol_version='1.0',
        )

    def _from_orm(
        self, model_instance: PushNotificationConfigModel
    ) -> TaskPushNotificationConfig:
        """Maps a SQLAlchemy model instance to a TaskPushNotificationConfig proto.

        Handles decryption if a key is configured, with a fallback to plain JSON.
        """
        if self.model_to_core_conversion:
            return self.model_to_core_conversion(model_instance)

        payload = model_instance.config_data

        if self._fernet:
            from cryptography.fernet import (
                InvalidToken,
            )

            try:
                decrypted_payload = self._fernet.decrypt(payload)
                return self._parse_config(
                    decrypted_payload.decode('utf-8'),
                    model_instance.task_id,
                    model_instance.protocol_version,
                )
            except Exception as e:
                if isinstance(e, InvalidToken):
                    # Decryption failed. This could be because the data is not encrypted.
                    # We'll log a warning and try to parse it as plain JSON as a fallback.
                    logger.warning(
                        'Failed to decrypt push notification config for task %s, config %s. '
                        'Attempting to parse as unencrypted JSON. '
                        'This may indicate an incorrect encryption key or unencrypted data in the database.',
                        model_instance.task_id,
                        model_instance.config_id,
                    )
                    # Fall through to the unencrypted parsing logic below.
                else:
                    logger.exception(
                        'Failed to parse decrypted push notification config for task %s, config %s. '
                        'Data is corrupted or not valid JSON after decryption.',
                        model_instance.task_id,
                        model_instance.config_id,
                    )
                    raise ValueError(  # noqa: TRY004
                        'Failed to parse decrypted push notification config data'
                    ) from e

        # Try to parse as plain JSON.
        try:
            payload_str = (
                payload.decode('utf-8')
                if isinstance(payload, bytes)
                else payload
            )
            return self._parse_config(
                payload_str,
                model_instance.task_id,
                model_instance.protocol_version,
            )

        except Exception as e:
            if self._fernet:
                logger.exception(
                    'Failed to parse push notification config for task %s, config %s. '
                    'Decryption failed and the data is not valid JSON. '
                    'This likely indicates the data is corrupted or encrypted with a different key.',
                    model_instance.task_id,
                    model_instance.config_id,
                )
            else:
                # if no key is configured and the payload is not valid JSON.
                logger.exception(
                    'Failed to parse push notification config for task %s, config %s. '
                    'Data is not valid JSON and no encryption key is configured.',
                    model_instance.task_id,
                    model_instance.config_id,
                )
            raise ValueError(
                'Failed to parse push notification config data. '
                'Data is not valid JSON, or it is encrypted with the wrong key.'
            ) from e

    async def set_info(
        self,
        task_id: str,
        notification_config: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> None:
        """Sets or updates the push notification configuration for a task."""
        await self._ensure_initialized()
        owner = self.owner_resolver(context)

        # Create a copy of the config using proto CopyFrom
        config_to_save = TaskPushNotificationConfig()
        config_to_save.CopyFrom(notification_config)
        if not config_to_save.id:
            config_to_save.id = task_id

        db_config = self._to_orm(task_id, config_to_save, owner)
        async with self.async_session_maker.begin() as session:
            await session.merge(db_config)
            logger.debug(
                'Push notification config for task %s with config id %s for owner %s saved/updated.',
                task_id,
                config_to_save.id,
                owner,
            )

    async def get_info(
        self,
        task_id: str,
        context: ServerCallContext,
    ) -> list[TaskPushNotificationConfig]:
        """Retrieves all push notification configurations for a task, for the given owner."""
        await self._ensure_initialized()
        owner = self.owner_resolver(context)
        async with self.async_session_maker() as session:
            stmt = select(self.config_model).where(
                and_(
                    self.config_model.task_id == task_id,
                    self.config_model.owner == owner,
                )
            )
            result = await session.execute(stmt)
            models = result.scalars().all()

            configs = []
            for model in models:
                try:
                    configs.append(self._from_orm(model))
                except ValueError:  # noqa: PERF203
                    logger.exception(
                        'Could not deserialize push notification config for task %s, config %s, owner %s',
                        model.task_id,
                        model.config_id,
                        owner,
                    )
            return configs

    async def delete_info(
        self,
        task_id: str,
        context: ServerCallContext,
        config_id: str | None = None,
    ) -> None:
        """Deletes push notification configurations for a task.

        If config_id is provided, only that specific configuration is deleted.
        If config_id is None, all configurations for the task for the owner are deleted.
        """
        await self._ensure_initialized()
        owner = self.owner_resolver(context)
        async with self.async_session_maker.begin() as session:
            stmt = delete(self.config_model).where(
                and_(
                    self.config_model.task_id == task_id,
                    self.config_model.owner == owner,
                )
            )
            if config_id is not None:
                stmt = stmt.where(self.config_model.config_id == config_id)

            result = await session.execute(stmt)

            if result.rowcount > 0:  # type: ignore[attr-defined]
                logger.info(
                    'Deleted %s push notification config(s) for task %s, owner %s.',
                    result.rowcount,  # type: ignore[attr-defined]
                    task_id,
                    owner,
                )
            else:
                logger.warning(
                    'Attempted to delete push notification config for task %s, owner %s with config_id: %s that does not exist.',
                    task_id,
                    owner,
                    config_id,
                )

    def _parse_config(
        self,
        json_payload: str,
        task_id: str | None = None,
        protocol_version: str | None = None,
    ) -> TaskPushNotificationConfig:
        """Parses a JSON payload into a TaskPushNotificationConfig proto.

        Args:
            json_payload: The JSON payload to parse.
            task_id: The unique identifier of the task. Only required for legacy
                (0.3) protocol versions.
            protocol_version: The protocol version used for serialization.
        """
        if protocol_version == '1.0':
            return Parse(json_payload, TaskPushNotificationConfig())

        return compat_push_notification_config_model_to_core(
            json_payload, task_id or ''
        )
