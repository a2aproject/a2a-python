import os

from collections.abc import AsyncGenerator

import pytest


# Skip entire test module if SQLAlchemy is not installed
pytest.importorskip('sqlalchemy', reason='Database tests require SQLAlchemy')
pytest.importorskip(
    'cryptography',
    reason='Database tests require Cryptography. Install extra encryption',
)

import pytest_asyncio

from _pytest.mark.structures import ParameterSet

# Now safe to import SQLAlchemy-dependent modules
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.inspection import inspect

from a2a.server.models import (
    Base,
    PushNotificationConfigModel,
)  # Important: To get Base.metadata
from a2a.server.tasks import DatabasePushNotificationConfigStore
from a2a.types import (
    PushNotificationConfig,
    Task,
    TaskState,
    TaskStatus,
)


# DSNs for different databases
SQLITE_TEST_DSN = (
    'sqlite+aiosqlite:///file:testdb?mode=memory&cache=shared&uri=true'
)
POSTGRES_TEST_DSN = os.environ.get(
    'POSTGRES_TEST_DSN'
)  # e.g., "postgresql+asyncpg://user:pass@host:port/dbname"
MYSQL_TEST_DSN = os.environ.get(
    'MYSQL_TEST_DSN'
)  # e.g., "mysql+aiomysql://user:pass@host:port/dbname"

# Parameterization for the db_store fixture
DB_CONFIGS: list[ParameterSet | tuple[str | None, str]] = [
    pytest.param((SQLITE_TEST_DSN, 'sqlite'), id='sqlite')
]

if POSTGRES_TEST_DSN:
    DB_CONFIGS.append(
        pytest.param((POSTGRES_TEST_DSN, 'postgresql'), id='postgresql')
    )
else:
    DB_CONFIGS.append(
        pytest.param(
            (None, 'postgresql'),
            marks=pytest.mark.skip(reason='POSTGRES_TEST_DSN not set'),
            id='postgresql_skipped',
        )
    )

if MYSQL_TEST_DSN:
    DB_CONFIGS.append(pytest.param((MYSQL_TEST_DSN, 'mysql'), id='mysql'))
else:
    DB_CONFIGS.append(
        pytest.param(
            (None, 'mysql'),
            marks=pytest.mark.skip(reason='MYSQL_TEST_DSN not set'),
            id='mysql_skipped',
        )
    )


# Minimal Task object for testing - remains the same
task_status_submitted = TaskStatus(
    state=TaskState.submitted, timestamp='2023-01-01T00:00:00Z'
)
MINIMAL_TASK_OBJ = Task(
    id='task-abc',
    context_id='session-xyz',
    status=task_status_submitted,
    kind='task',
    metadata={'test_key': 'test_value'},
    artifacts=[],
    history=[],
)


@pytest_asyncio.fixture(params=DB_CONFIGS)
async def db_store_parameterized(
    request,
) -> AsyncGenerator[DatabasePushNotificationConfigStore, None]:
    """
    Fixture that provides a DatabaseTaskStore connected to different databases
    based on parameterization (SQLite, PostgreSQL, MySQL).
    """
    db_url, dialect_name = request.param

    if db_url is None:
        pytest.skip(f'DSN for {dialect_name} not set in environment variables.')

    engine = create_async_engine(db_url)
    store = None  # Initialize store to None for the finally block

    try:
        # Create tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # create_table=False as we've explicitly created tables above.
        store = DatabasePushNotificationConfigStore(
            engine=engine,
            create_table=False,
            encryption_key=Fernet.generate_key(),
        )
        # Initialize the store (connects, etc.). Safe to call even if tables exist.
        await store.initialize()

        yield store

    finally:
        if engine:  # If engine was created for setup/teardown
            # Drop tables using the fixture's engine
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()  # Dispose the engine created in the fixture


@pytest.mark.asyncio
async def test_initialize_creates_table(
    db_store_parameterized: DatabasePushNotificationConfigStore,
) -> None:
    """Test that tables are created (implicitly by fixture setup)."""
    # Ensure store is initialized (already done by fixture, but good for clarity)
    await db_store_parameterized._ensure_initialized()

    # Use the store's engine for inspection
    async with db_store_parameterized.engine.connect() as conn:

        def has_table_sync(sync_conn):
            inspector = inspect(sync_conn)
            return inspector.has_table(
                PushNotificationConfigModel.__tablename__
            )

        assert await conn.run_sync(has_table_sync)


@pytest.mark.asyncio
async def test_initialize_is_idempotent(
    db_store_parameterized: DatabasePushNotificationConfigStore,
) -> None:
    """Test that tables are created (implicitly by fixture setup)."""
    # Ensure store is initialized (already done by fixture, but good for clarity)
    await db_store_parameterized.initialize()
    # Call initialize again to check idempotency
    await db_store_parameterized.initialize()


@pytest.mark.asyncio
async def test_set_and_get_info_single_config(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test setting and retrieving a single configuration."""
    task_id = 'task-1'
    config = PushNotificationConfig(id='config-1', url='http://example.com')

    await db_store_parameterized.set_info(task_id, config)
    retrieved_configs = await db_store_parameterized.get_info(task_id)

    assert len(retrieved_configs) == 1
    assert retrieved_configs[0] == config


@pytest.mark.asyncio
async def test_set_and_get_info_multiple_configs(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test setting and retrieving multiple configurations for a single task."""

    task_id = 'task-1'
    config1 = PushNotificationConfig(id='config-1', url='http://example.com/1')
    config2 = PushNotificationConfig(id='config-2', url='http://example.com/2')

    await db_store_parameterized.set_info(task_id, config1)
    await db_store_parameterized.set_info(task_id, config2)
    retrieved_configs = await db_store_parameterized.get_info(task_id)

    assert len(retrieved_configs) == 2
    assert config1 in retrieved_configs
    assert config2 in retrieved_configs


@pytest.mark.asyncio
async def test_set_info_updates_existing_config(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test that setting an existing config ID updates the record."""
    task_id = 'task-1'
    config_id = 'config-1'
    initial_config = PushNotificationConfig(
        id=config_id, url='http://initial.url'
    )
    updated_config = PushNotificationConfig(
        id=config_id, url='http://updated.url'
    )

    await db_store_parameterized.set_info(task_id, initial_config)
    await db_store_parameterized.set_info(task_id, updated_config)
    retrieved_configs = await db_store_parameterized.get_info(task_id)

    assert len(retrieved_configs) == 1
    assert retrieved_configs[0].url == 'http://updated.url'


@pytest.mark.asyncio
async def test_set_info_defaults_config_id_to_task_id(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test that config.id defaults to task_id if not provided."""
    task_id = 'task-1'
    config = PushNotificationConfig(url='http://example.com')  # id is None

    await db_store_parameterized.set_info(task_id, config)
    retrieved_configs = await db_store_parameterized.get_info(task_id)

    assert len(retrieved_configs) == 1
    assert retrieved_configs[0].id == task_id


@pytest.mark.asyncio
async def test_get_info_not_found(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test getting info for a task with no configs returns an empty list."""
    retrieved_configs = await db_store_parameterized.get_info(
        'non-existent-task'
    )
    assert retrieved_configs == []


@pytest.mark.asyncio
async def test_delete_info_specific_config(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test deleting a single, specific configuration."""
    task_id = 'task-1'
    config1 = PushNotificationConfig(id='config-1', url='http://a.com')
    config2 = PushNotificationConfig(id='config-2', url='http://b.com')

    await db_store_parameterized.set_info(task_id, config1)
    await db_store_parameterized.set_info(task_id, config2)

    await db_store_parameterized.delete_info(task_id, 'config-1')
    retrieved_configs = await db_store_parameterized.get_info(task_id)

    assert len(retrieved_configs) == 1
    assert retrieved_configs[0] == config2


@pytest.mark.asyncio
async def test_delete_info_all_for_task(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test deleting all configurations for a task when config_id is None."""

    task_id = 'task-1'
    config1 = PushNotificationConfig(id='config-1', url='http://a.com')
    config2 = PushNotificationConfig(id='config-2', url='http://b.com')

    await db_store_parameterized.set_info(task_id, config1)
    await db_store_parameterized.set_info(task_id, config2)

    await db_store_parameterized.delete_info(task_id, None)
    retrieved_configs = await db_store_parameterized.get_info(task_id)

    assert retrieved_configs == []


@pytest.mark.asyncio
async def test_delete_info_not_found(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test that deleting a non-existent config does not raise an error."""
    # Should not raise
    await db_store_parameterized.delete_info('task-1', 'non-existent-config')


@pytest.mark.asyncio
async def test_data_is_encrypted_in_db(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Verify that the data stored in the database is actually encrypted."""
    task_id = 'encrypted-task'
    config = PushNotificationConfig(
        id='config-1', url='http://secret.url', token='secret-token'
    )
    plain_json = config.model_dump_json()

    await db_store_parameterized.set_info(task_id, config)

    # Directly query the database to inspect the raw data
    async_session = async_sessionmaker(
        db_store_parameterized.engine, expire_on_commit=False
    )
    async with async_session() as session:
        stmt = select(PushNotificationConfigModel).where(
            PushNotificationConfigModel.task_id == task_id
        )
        result = await session.execute(stmt)
        db_model = result.scalar_one()

    assert db_model.config_data != plain_json.encode('utf-8')

    fernet = db_store_parameterized._fernet

    decrypted_data = fernet.decrypt(db_model.config_data)  # type: ignore
    assert decrypted_data.decode('utf-8') == plain_json


@pytest.mark.asyncio
async def test_decryption_error_with_wrong_key(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test that using the wrong key to decrypt raises a ValueError."""
    # 1. Store with one key

    task_id = 'wrong-key-task'
    config = PushNotificationConfig(id='config-1', url='http://secret.url')
    await db_store_parameterized.set_info(task_id, config)

    # 2. Try to read with a different key
    # Directly query the database to inspect the raw data
    wrong_key = Fernet.generate_key()
    store2 = DatabasePushNotificationConfigStore(
        db_store_parameterized.engine, encryption_key=wrong_key
    )

    retrieved_configs = await store2.get_info(task_id)
    assert retrieved_configs == []

    # _from_orm should raise a ValueError
    async_session = async_sessionmaker(
        db_store_parameterized.engine, expire_on_commit=False
    )
    async with async_session() as session:
        db_model = await session.get(
            PushNotificationConfigModel, (task_id, 'config-1')
        )

        with pytest.raises(ValueError):
            store2._from_orm(db_model)  # type: ignore


@pytest.mark.asyncio
async def test_decryption_error_with_no_key(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test that using the wrong key to decrypt raises a ValueError."""
    # 1. Store with one key

    task_id = 'wrong-key-task'
    config = PushNotificationConfig(id='config-1', url='http://secret.url')
    await db_store_parameterized.set_info(task_id, config)

    # 2. Try to read with no key set
    # Directly query the database to inspect the raw data
    store2 = DatabasePushNotificationConfigStore(db_store_parameterized.engine)

    retrieved_configs = await store2.get_info(task_id)
    assert retrieved_configs == []

    # _from_orm should raise a ValueError
    async_session = async_sessionmaker(
        db_store_parameterized.engine, expire_on_commit=False
    )
    async with async_session() as session:
        db_model = await session.get(
            PushNotificationConfigModel, (task_id, 'config-1')
        )

        with pytest.raises(ValueError):
            store2._from_orm(db_model)  # type: ignore


@pytest.mark.asyncio
async def test_custom_table_name(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test that the store works correctly with a custom table name."""
    table_name = 'my_custom_push_configs'
    engine = db_store_parameterized.engine
    custom_store = None
    try:
        # Use a new store with a custom table name
        custom_store = DatabasePushNotificationConfigStore(
            engine=engine,
            create_table=True,
            table_name=table_name,
            encryption_key=Fernet.generate_key(),
        )

        task_id = 'custom-table-task'
        config = PushNotificationConfig(id='config-1', url='http://custom.url')

        # This will create the table on first use
        await custom_store.set_info(task_id, config)
        retrieved_configs = await custom_store.get_info(task_id)

        assert len(retrieved_configs) == 1
        assert retrieved_configs[0] == config

        # Verify the custom table exists and has data
        async with custom_store.engine.connect() as conn:

            def has_table_sync(sync_conn):
                inspector = inspect(sync_conn)
                return inspector.has_table(table_name)

            assert await conn.run_sync(has_table_sync)

            result = await conn.execute(
                select(custom_store.config_model).where(
                    custom_store.config_model.task_id == task_id
                )
            )
            assert result.scalar_one_or_none() is not None
    finally:
        if custom_store:
            # Clean up the dynamically created table from the metadata
            # to prevent errors in subsequent parameterized test runs.
            Base.metadata.remove(custom_store.config_model.__table__)  # type: ignore


@pytest.mark.asyncio
async def test_set_and_get_info_multiple_configs_no_key(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test setting and retrieving multiple configurations for a single task."""

    store = DatabasePushNotificationConfigStore(
        engine=db_store_parameterized.engine,
        create_table=False,
        encryption_key=None,  # No encryption key
    )
    await store.initialize()

    task_id = 'task-1'
    config1 = PushNotificationConfig(id='config-1', url='http://example.com/1')
    config2 = PushNotificationConfig(id='config-2', url='http://example.com/2')

    await store.set_info(task_id, config1)
    await store.set_info(task_id, config2)
    retrieved_configs = await store.get_info(task_id)

    assert len(retrieved_configs) == 2
    assert config1 in retrieved_configs
    assert config2 in retrieved_configs


@pytest.mark.asyncio
async def test_data_is_not_encrypted_in_db_if_no_key_is_set(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test data is not encrypted when no encryption key is set."""

    store = DatabasePushNotificationConfigStore(
        engine=db_store_parameterized.engine,
        create_table=False,
        encryption_key=None,  # No encryption key
    )
    await store.initialize()

    task_id = 'task-1'
    config = PushNotificationConfig(id='config-1', url='http://example.com/1')
    plain_json = config.model_dump_json()

    await store.set_info(task_id, config)

    # Directly query the database to inspect the raw data
    async_session = async_sessionmaker(
        db_store_parameterized.engine, expire_on_commit=False
    )
    async with async_session() as session:
        stmt = select(PushNotificationConfigModel).where(
            PushNotificationConfigModel.task_id == task_id
        )
        result = await session.execute(stmt)
        db_model = result.scalar_one()

    assert db_model.config_data == plain_json.encode('utf-8')


@pytest.mark.asyncio
async def test_decryption_fallback_for_unencrypted_data(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test reading unencrypted data with an encryption-enabled store."""
    # 1. Store unencrypted data using a new store instance without a key
    unencrypted_store = DatabasePushNotificationConfigStore(
        engine=db_store_parameterized.engine,
        create_table=False,  # Table already exists from fixture
        encryption_key=None,
    )
    await unencrypted_store.initialize()

    task_id = 'mixed-encryption-task'
    config = PushNotificationConfig(id='config-1', url='http://plain.url')
    await unencrypted_store.set_info(task_id, config)

    # 2. Try to read with the encryption-enabled store from the fixture
    retrieved_configs = await db_store_parameterized.get_info(task_id)

    # Should fall back to parsing as plain JSON and not fail
    assert len(retrieved_configs) == 1
    assert retrieved_configs[0] == config


@pytest.mark.asyncio
async def test_parsing_error_after_successful_decryption(
    db_store_parameterized: DatabasePushNotificationConfigStore,
):
    """Test that a parsing error after successful decryption is handled."""

    task_id = 'corrupted-data-task'
    config_id = 'config-1'

    # 1. Encrypt data that is NOT valid JSON
    fernet = Fernet(Fernet.generate_key())
    corrupted_payload = b'this is not valid json'
    encrypted_data = fernet.encrypt(corrupted_payload)

    # 2. Manually insert this corrupted data into the DB
    async_session = async_sessionmaker(
        db_store_parameterized.engine, expire_on_commit=False
    )
    async with async_session() as session:
        db_model = PushNotificationConfigModel(
            task_id=task_id,
            config_id=config_id,
            config_data=encrypted_data,
        )
        session.add(db_model)
        await session.commit()

    # 3. get_info should log an error and return an empty list
    retrieved_configs = await db_store_parameterized.get_info(task_id)
    assert retrieved_configs == []

    # 4. _from_orm should raise a ValueError
    async with async_session() as session:
        db_model_retrieved = await session.get(
            PushNotificationConfigModel, (task_id, config_id)
        )

        with pytest.raises(ValueError):
            db_store_parameterized._from_orm(db_model_retrieved)  # type: ignore
