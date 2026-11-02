# Migrations

The initial schema lives in `src/ecofac/persistence/schema.sql` and is
idempotent (uses `CREATE TABLE IF NOT EXISTS`). The app calls it on every
first connection.

For persistent databases that must evolve across releases, apply the numbered
`.sql` files in this directory in order, then record the version:

    sqlite3 ecofac_api.db < migrations/002_add_meter_interval.sql
    sqlite3 ecofac_api.db "INSERT OR REPLACE INTO _schema_version (version, applied_at) VALUES (2, datetime('now'))"

Keep `001_initial.sql` in sync with `schema.sql`.
