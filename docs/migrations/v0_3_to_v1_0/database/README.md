# Database Migration Guide: v0.3 to v1.0

The A2A SDK v1.0 introduces significant updates to the database persistence layer, including a new schema for tracking task ownership and protocol versions. This guide provides the necessary steps to migrate your database from v0.3 to the v1.0 persistence model without data loss.

---

## ⚡ Choose Your Migration Strategy

Depending on your application's availability requirements, choose one of the following paths:

| Strategy | Downtime | Complexity | Best For |
| :--- | :--- | :--- | :--- |
| **[Simple Migration](simple_migration.md)** | Short (Restart) | Low | Single-instance apps, non-critical services. |
| **[Zero Downtime Migration](zero_downtime.md)** | None | Medium | Multi-instance, high-availability production environments. |

---

## 🏗️ Technical Overview

The v1.0 database migration involves:
1.  **Schema Updates**: Adding `protocol_version`, `owner`, and `last_updated` columns to the `tasks` and `push_notification_configs` tables.
2.  **Storage Model**: Transitioning from Pydantic-based JSON to Protobuf-based JSON serialization for better interoperability and performance.

### Rollback & Safety
> [!IMPORTANT]
> 
> **Data Backup**: Always perform a full snapshot of your database before starting the migration.
> **Rollback**: To revert the schema, use `uv run a2a-db downgrade -1`. 

### Verification
To verify the migration was successful:
- Tables should be updated with new columns: owner, protocol_version, last_updated.
- Inspect the `protocol_version` column in the `tasks` table; entries created before the migration should have `protocol_version` set to `NULL`, entries created during the migration should have `protocol_version` set to `0.3` (if using the zero-downtime migration strategy) and entries created after the migration should have `protocol_version` set to `1.0`.

---
