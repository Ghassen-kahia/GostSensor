-- Uploaded CSV files analysed by the data-quality checker (stored under <data>/uploads/).
CREATE TABLE IF NOT EXISTS uploads (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    status        TEXT NOT NULL,          -- PROCESSING | DONE | FAILED
    error         TEXT,
    uploaded_by   TEXT,
    created_real  INTEGER NOT NULL,
    finished_real INTEGER,
    summary       TEXT,                   -- JSON: dataset-level results
    result        TEXT                    -- JSON: per-series results
);
CREATE INDEX IF NOT EXISTS ix_uploads_created ON uploads(created_real);
