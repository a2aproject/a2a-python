# Zero Downtime Migration: v0.3 to v1.0

This guide outlines the strategy for migrating your Agent application from A2A protocol v0.3 to v1.0 without service interruption, even when running multiple distributed instances sharing a single database.

---

> [!WARNING]
> **Safety First:**
> Before proceeding, ensure you have a backup of your database.

---

## 🛠 Prerequisites

### Install Migration Tools
The migration CLI is not included in the base package. Install the `db-cli` extra:

```bash
uv add "a2a-sdk[db-cli]"
# OR
pip install "a2a-sdk[db-cli]"
```

---

## 🏗️ The 3-Step Strategy

Zero-downtime migration requires an "Expand, Migrate, Contract" pattern. It means we first expand the schema, then migrate the code to coexist with the old format, and finally transition fully to the new v1.0 standards.

### Step 1: Apply Schema Updates

Run the `a2a-db` migration tool to update your tables. This adds new columns (`owner`, `protocol_version`, `last_updated`) while leaving existing v0.3 data intact.

```bash
# Run migration against your target database
uv run a2a-db --database-url "your-database-url"
```

> [!NOTE]
>
>For more details on the CLI migration tool, including flags, see the [A2A SDK Database Migrations README](../../../../src/a2a/migrations/README.md).

> [!NOTE]
> All new columns are nullable. Your existing v0.3 code will continue to work normally after this step is completed.
>
> The v1.0 database stores are designed to be backward compatible by default. After this step, your new v1.0 code will be able to read existing v0.3 entries from the database using a built-in legacy parser.

#### ✅ How to Verify
Confirm the schema is at the correct version:

```bash
uv run a2a-db current
```
The output should show the latest revision ID (e.g., `38ce57e08137`).

### Step 2: Rolling Deployment in Compatibility Mode

In this step, you deploy the v1.0 SDK code but configure it to **write** data in the legacy v0.3 format. This ensures that any v0.3 instances still running in your cluster can read data produced by the new v1.0 instances.

#### Update Initialization Code
Enable the v0.3 conversion utilities in your application entry point (e.g., `main.py`).

```python
from a2a.server.tasks import DatabaseTaskStore, DatabasePushNotificationConfigStore
from a2a.compat.v0_3.model_conversions import (
    core_to_compat_task_model,
    core_to_compat_push_notification_config_model,
)

# Initialize stores with compatibility conversion
# The '... # other' represents your existing configuration (engine, table_name, etc.)
task_store = DatabaseTaskStore(
    ... # other arguments
    core_to_model_conversion=core_to_compat_task_model
)

config_store = DatabasePushNotificationConfigStore(
    ... # other arguments
    core_to_model_conversion=core_to_compat_push_notification_config_model
)
```

#### Perform a Rolling Restart
Deploy the new code by restarting your instances one by one.

#### ✅ How to Verify
Verify that v1.0 instances are successfully writing to the database. In the `tasks` and `push_notification_configs` tables, new rows created during this phase should have `protocol_version` set to `0.3`.

### Step 3: Transition to v1.0 Mode

Once **100%** of your application instances are running v1.0 code (with compatibility mode enabled), you can switch to the v1.0 write format.

> [!CAUTION]
> **CRITICAL PRE-REQUISITE**: Do NOT start Step 3 until you have confirmed that no v0.3 instances remain. Old v0.3 code cannot parse the new v1.0 native database entries.

#### Disable Compatibility Logic
Remove the `core_to_model_conversion` arguments from your Store constructors.

```python
# Revert to native v1.0 write behavior
task_store = DatabaseTaskStore(engine=engine, ...)
config_store = DatabasePushNotificationConfigStore(engine=engine, ...)
```

#### Perform a Final Rolling Restart

Restart your instances again.

#### ✅ How to Verify
Inspect the `tasks` and `push_notification_configs` tables. New entries should now show `protocol_version` as `1.0`.

---

## 🛠️ Why it Works

The A2A `DatabaseStore` classes follow a version-aware read/write pattern:

1.  **Write Logic**: If `core_to_model_conversion` is provided, it is used. Otherwise, it defaults to the v1.0 Protobuf JSON format.
2.  **Read Logic**: The store automatically inspects the `protocol_version` column for every row. 
    *   If `NULL` or `0.3`, it uses the internal **v0.3 legacy parser**.
    *   If `1.0`, it uses the modern **Protobuf parser**.

This allows v1.0 instances to read *all* existing data regardless of when it was written.

---

## 🧩 Resources
- **[a2a-db CLI](../../../../src/a2a/migrations/README.md)**: The primary tool for executing schema migrations.
- **[Compatibility Conversions](../../../../src/a2a/compat/v0_3/model_conversions.py)**: Source for model conversion functions `core_to_compat_task_model` and `core_to_compat_push_notification_config_model` used in Step 2.
- **[Task Store Implementation](../../../../src/a2a/server/tasks/database_task_store.py)**: The `DatabaseTaskStore` which handles the version-aware read/write logic.
- **[Push Notification Store Implementation](../../../../src/a2a/server/tasks/database_push_notification_config_store.py)**: The `DatabasePushNotificationConfigStore` which handles the version-aware read/write logic.

