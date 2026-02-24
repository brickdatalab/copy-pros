-- Migration 001: Rename copy-pros → copy_pros
-- The hyphenated schema name requires quoting everywhere; underscore is cleaner.
ALTER SCHEMA "copy-pros" RENAME TO copy_pros;
