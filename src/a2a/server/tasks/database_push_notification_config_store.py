# ruff: noqa: PLC0415
import json
import logging

from typing import TYPE_CHECKING

from pydantic import ValidationError


try:
    from sqlalchemy import (
        Table,
        delete,
        select,
    )
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

from a2a.server.models import (
    Base,
    PushNotificationConfigModel,
    create_push_notification_config_model,
)
from a2a.server.tasks.push_notification_config_store import (
    PushNotificationConfigStore,
)
from a2a.types import PushNotificationConfig


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

    def __init__(
        self,
        engine: AsyncEngine,
        create_table: bool = True,
        table_name: str = 'push_notification_configs',
        encryption_key: str | bytes | None = None,
    ) -> None:
        """Initializes the DatabasePushNotificationConfigStore.

        Args:
            engine: An existing SQLAlchemy AsyncEngine to be used by the store.
            create_table: If true, create the table on initialization.
            table_name: Name of the database table. Defaults to 'push_notification_configs'.
            encryption_key: A key for encrypting sensitive configuration data.
                If provided, `config_data` will be encrypted in the database.
                The key must be a URL-safe base64-encoded 32-byte key.
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
        self.config_model = (
            PushNotificationConfigModel
            if table_name == 'push_notification_configs'
            else create_push_notification_config_model(table_name)
        )
        self._fernet = None

        if encryption_key:
            try:
                from cryptography.fernet import Fernet
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
        self, task_id: str, config: PushNotificationConfig
    ) -> PushNotificationConfigModel:
        """Maps a Pydantic PushNotificationConfig to a SQLAlchemy model instance.

        The config data is serialized to JSON bytes, and encrypted if a key is configured.
        """
        json_payload = config.model_dump_json().encode('utf-8')

        if self._fernet:
            data_to_store = self._fernet.encrypt(json_payload)
        else:
            data_to_store = json_payload

        return self.config_model(
            task_id=task_id,
            config_id=config.id,
            config_data=data_to_store,
        )

    def _from_orm(
        self, model_instance: PushNotificationConfigModel
    ) -> PushNotificationConfig:
        """Maps a SQLAlchemy model instance to a Pydantic PushNotificationConfig.

        Handles decryption if a key is configured, with a fallback to plain JSON.
        """
        payload = model_instance.config_data

        if self._fernet:
            from cryptography.fernet import InvalidToken

            try:
                decrypted_payload = self._fernet.decrypt(payload)
                return PushNotificationConfig.model_validate_json(
                    decrypted_payload
                )
            except (json.JSONDecodeError, ValidationError) as e:
                logger.exception(
                    'Failed to parse decrypted push notification config for task %s, config %s. '
                    'Data is corrupted or not valid JSON after decryption.',
                    model_instance.task_id,
                    model_instance.config_id,
                )
                raise ValueError(
                    'Failed to parse decrypted push notification config data'
                ) from e
            except InvalidToken:
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

        # Try to parse as plain JSON.
        try:
            return PushNotificationConfig.model_validate_json(payload)
        except (json.JSONDecodeError, ValidationError) as e:
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
        self, task_id: str, notification_config: PushNotificationConfig
    ) -> None:
        """Sets or updates the push notification configuration for a task."""
        await self._ensure_initialized()

        config_to_save = notification_config.model_copy()
        if config_to_save.id is None:
            config_to_save.id = task_id

        db_config = self._to_orm(task_id, config_to_save)
        async with self.async_session_maker.begin() as session:
            await session.merge(db_config)
            logger.debug(
                'Push notification config for task %s with config id %s saved/updated.',
                task_id,
                config_to_save.id,
            )

    async def get_info(self, task_id: str) -> list[PushNotificationConfig]:
        """Retrieves all push notification configurations for a task."""
        await self._ensure_initialized()
        async with self.async_session_maker() as session:
            stmt = select(self.config_model).where(
                self.config_model.task_id == task_id
            )
            result = await session.execute(stmt)
            models = result.scalars().all()

            configs = []
            for model in models:
                try:
                    configs.append(self._from_orm(model))
                except ValueError:  # noqa: PERF203
                    logger.exception(
                        'Could not deserialize push notification config for task %s, config %s',
                        model.task_id,
                        model.config_id,
                    )
            return configs

    async def delete_info(
        self, task_id: str, config_id: str | None = None
    ) -> None:
        """Deletes push notification configurations for a task.

        If config_id is provided, only that specific configuration is deleted.
        If config_id is None, all configurations for the task are deleted.
        """
        await self._ensure_initialized()
        async with self.async_session_maker.begin() as session:
            stmt = delete(self.config_model).where(
                self.config_model.task_id == task_id
            )
            if config_id is not None:
                stmt = stmt.where(self.config_model.config_id == config_id)

            result = await session.execute(stmt)

            if result.rowcount > 0:
                logger.info(
                    'Deleted %s push notification config(s) for task %s.',
                    result.rowcount,
                    task_id,
                )
            else:
                logger.warning(
                    'Attempted to delete push notification config for task %s with config_id: %s that does not exist.',
                    task_id,
                    config_id,
                )
