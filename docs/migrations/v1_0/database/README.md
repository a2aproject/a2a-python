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
1.  **Schema Updates**: Adding the `protocol_version`, `owner`, and `last_updated` columns to the `tasks` table, and the `protocol_version` and `owner` columns to the `push_notification_configs` table.
2.  **Storage Model**: Transitioning from Pydantic-based JSON to Protobuf-based JSON serialization for better interoperability and performance.
