import asyncio
import logging
import os

from logging.config import fileConfig

from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from a2a.server.models import Base

try:
    from alembic import context
except ImportError as e:
    raise ImportError(
        "Migrations require Alembic. Install with: 'pip install a2a-sdk[db-cli]'."
    ) from e


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Mandatory database configuration
db_url = os.getenv('DATABASE_URL')
if not db_url:
    raise RuntimeError(
        'DATABASE_URL environment variable is not set. '
        "Please set it (e.g., export DATABASE_URL='sqlite+aiosqlite:///./my-database.db') before running migrations "
        'or use the --database-url flag.'
    )
config.set_main_option('sqlalchemy.url', db_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if config.get_main_option('verbose') == 'true':
    logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

# add your model's MetaData object here for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations in 'online' mode.

    This function is called within a synchronous context (via run_sync)
    to configure the migration context with the provided connection
    and target metadata, then execute the migrations within a transaction.

    Args:
        connection: The SQLAlchemy connection to use for the migrations.
    """
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an Engine.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    logging.info('Running migrations in offline mode.')
    run_migrations_offline()
else:
    logging.info('Running migrations in online mode.')
    run_migrations_online()
