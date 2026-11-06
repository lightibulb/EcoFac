# Migrations

The initial schema lives in `src/ecofac/persistence/schema.sql` and is
idempotent (uses `CREATE TABLE IF NOT EXISTS`). The app calls it on every
first connection.

Numbered `.sql` files in this directory are applied **automatically** by
`persistence.database.init_db` for file-backed databases. Each migration's
version is recorded in the `_schema_version` table, so subsequent runs skip
them.

## Rules for authoring a migration

1. **Every statement must be idempotent** — or tolerate being re-run. The
   fallback runner replays a migration statement-by-statement when the
   default ``executescript`` fails with ``duplicate column name`` (e.g. when
   ``schema.sql`` already created the column the migration is trying to add).
2. **Avoid semicolons in comments.** The fallback splits on ``;``, so a ``;``
   inside a ``-- ...`` comment would produce a bogus statement. The splitter
   strips ``--`` and ``/* */`` comments before splitting, but keeping
   comments simple avoids surprises.
3. **Migrations that create triggers or views** are skipped by the fallback
   (they cannot be split safely). Current trigger migrations (003, 004) are
   safe because they use ``CREATE TRIGGER IF NOT EXISTS`` and succeed on the
   first pass.

If you need to apply a migration manually to a deployed database:

    sqlite3 ecofac_api.db < migrations/002_add_meter_interval.sql
    sqlite3 ecofac_api.db "INSERT OR REPLACE INTO _schema_version \
        (version, applied_at) VALUES (2, datetime('now'))"

Keep `001_initial.sql` in sync with `schema.sql`.
