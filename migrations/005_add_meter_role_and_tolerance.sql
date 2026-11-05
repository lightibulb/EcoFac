-- Server-side meter metadata for the trusted decision path.
--   role:               'IT_LOAD' | 'COOLING' | NULL (unclassified)
--   relative_tolerance: meter accuracy as a fraction (e.g. 0.01 = 1 %); NULL = unknown
-- Both are nullable so existing rows stay valid. The app also applies these
-- additively at startup (persistence.database.ADDITIVE_COLUMNS).
ALTER TABLE meters ADD COLUMN role TEXT;
ALTER TABLE meters ADD COLUMN relative_tolerance REAL;
