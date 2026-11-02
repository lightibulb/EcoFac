-- Adds per-meter sampling interval.
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

ALTER TABLE meters ADD COLUMN interval_minutes INTEGER NOT NULL DEFAULT 60;
