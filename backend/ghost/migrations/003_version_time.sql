-- Wall-clock time at which the current pipeline_version was created. The local
-- fallback ages pending work from here (updated_real moves on every detection tick).
ALTER TABLE incidents ADD COLUMN version_real INTEGER;
UPDATE incidents SET version_real = created_real WHERE version_real IS NULL;
