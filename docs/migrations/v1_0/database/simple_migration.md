# Simple Migration: v0.3 to v1.0

This guide is for users who can afford a short period of downtime during the migration from A2A protocol v0.3 to v1.0. This is the recommended path for single-instance applications or non-critical services.

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

## 🚀 Migration Steps

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
>
> The v1.0 database stores are designed to be backward compatible by default. After this step, your new v1.0 code will be able to read existing v0.3 entries from the database using a built-in legacy parser.

### Step 2: Verify the Migration

Confirm the schema is at the correct version:

```bash
uv run a2a-db current
```
The output should show the latest revision ID (e.g., `38ce57e08137`).

### Step 3: Update Your Application Code

Upgrade your application to use the v1.0 SDK.

---

## ↩️ Rollback Strategy

If your application fails to start or encounters errors after the migration:

1.  **Revert Application Code**: Revert your application code to use the v0.3 SDK.

    > [!NOTE]
    > Older SDKs are compatible with the new schema (as new columns are nullable). If something breaks, rolling back the application code is usually sufficient.

2.  **Revert Schema (Fallback)**: If you encounter database issues, use the `downgrade` command to step back to the v0.3 structure.
    ```bash
    uv run a2a-db downgrade -1
    ```
3.  **Restart**: Resume operations using the v0.3 SDK.


---

## 🧩 Resources
- **[Zero Downtime Migration](zero_downtime.md)**: If you cannot stop your application.
- **[a2a-db CLI](../../../../src/a2a/migrations/README.md)**: The primary tool for executing schema migrations.
