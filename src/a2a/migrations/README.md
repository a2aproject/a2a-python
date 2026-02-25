# A2A SDK Database Migrations

This directory handles the database schema updates for the A2A SDK. It uses a bundled CLI tool to simplify the migration process.

## User Guide for Integrators

When you install the `a2a-sdk`, you get a built-in command `a2a-db` which updates your database to make it compatible with the latest version of the SDK.

### 1. Recommended: Back up your database

Before running migrations, it is recommended to back up your database.

### 2. Set your Database URL
Migrations require the `DATABASE_URL` environment variable to be set with an `async-compatible` driver. 
You can set it globally with `export DATABASE_URL`. Examples for SQLite, PostgreSQL and MySQL, respectively:

```bash
export DATABASE_URL="sqlite+aiosqlite://user:pass@host:port/your_database_name"

export DATABASE_URL="postgresql+asyncpg://user:pass@localhost/your_database_name"

export DATABASE_URL="mysql+aiomysql://user:pass@localhost/your_database_name"
```

Or you can use the `-u` flag to specify the database URL for a single command.


### 3. Apply Migrations
Always run this command after installing or upgrading the SDK to ensure your database matches the required schema. This will upgrade the tables `tasks` and `push_notification_configs` in your database by adding columns `owner` and `last_updated` and an index `(owner, last_updated)` to the `tasks` table and a column `owner` to the `push_notification_configs` table.

```bash
uv run a2a-db
```

### 4. Customizing Defaults with Flags
#### -o
Allows you to pass custom values for the new `owner` column. If not set, it will default to the value `unknown`.

```bash
uv run a2a-db -o "admin_user"
```
#### -u
You can use the `-u` flag to specify the database URL for a single command.

```bash
uv run a2a-db -u "sqlite+aiosqlite:///my_database.db"
```
#### -t
By default, tables `tasks` and `push_notification_configs` are updated. Using `-t` flag allows you to choose which tables to update.

```bash
uv run a2a-db -t "my_table1" -t "my_table2"
```
#### -v
Enables verbose output by setting `sqlalchemy.engine` logging to `INFO`.

```bash
uv run a2a-db -v
```
#### --sql
Enables running migrations in `offline` mode. Migrations are generated as SQL scripts instead of being run against the database.

```bash
uv run a2a-db --sql
```

### 5. Rolling Back
If you need to revert a change:

```bash
# Step back one version
uv run a2a-db downgrade -1

# Downgrade to a specific revision ID
uv run a2a-db downgrade <revision_id>

# Revert all migrations
uv run a2a-db downgrade base
```

All flags except the `-o` flag can be used during rollbacks.

---

## Developer Guide for SDK Contributors

If you are modifying the SDK models and need to generate new migration files, use the following workflow.

### Creating a New Migration
Developers should use the raw `alembic` command locally to generate migrations. Ensure you are in the project root.

```bash
# Detect changes in models.py and generate a script
uv run alembic revision --autogenerate -m "description of changes"
```

### Internal Layout
- `env.py`: Configures the migration engine and applies the mandatory `DATABASE_URL` check.
- `versions/`: Contains the migration history.
- `script.py.mako`: The template for all new migration files.
