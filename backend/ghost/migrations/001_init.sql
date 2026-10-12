-- Ghost Sensor schema v1.
-- Conventions:
--   * All timestamps are INTEGER Unix epoch seconds, UTC.
--   * Columns suffixed `_ts` are in *building time* (the replay clock in REPLAY
--     mode, wall-clock in LIVE mode). Columns suffixed `_real` are wall-clock
--     times at which the platform did something (useful for MTTD/MTTR audit).

CREATE TABLE IF NOT EXISTS buildings (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    source        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storeys (
    id            TEXT PRIMARY KEY,           -- IFC storey name, e.g. N0
    building_id   TEXT NOT NULL REFERENCES buildings(id),
    name          TEXT NOT NULL,
    elevation_m   REAL,
    sort_order    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    id            TEXT PRIMARY KEY,           -- room code, e.g. 02-14-N (IfcSpace.Name)
    building_id   TEXT NOT NULL REFERENCES buildings(id),
    storey_id     TEXT REFERENCES storeys(id),
    long_name     TEXT,
    ifc_global_id TEXT UNIQUE,
    area_m2       REAL,
    centroid_x    REAL,                       -- metres, local building frame
    centroid_y    REAL,
    footprint     TEXT,                       -- JSON [[x,y],...] local frame (outer ring)
    wing          TEXT,                       -- suffix of the code (N, S, W...) when present
    zone          TEXT,                       -- organisational zone from BMS export (ESEO, ESTP...)
    stations      TEXT,                       -- JSON list of acquisition stations (MSI device sheet)
    in_ifc        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_rooms_storey ON rooms(storey_id);

CREATE TABLE IF NOT EXISTS room_adjacency (
    room_a          TEXT NOT NULL REFERENCES rooms(id),
    room_b          TEXT NOT NULL REFERENCES rooms(id),
    kind            TEXT NOT NULL,            -- WALL | DOOR | VERTICAL
    shared_length_m REAL,
    gap_m           REAL,
    door_count      INTEGER NOT NULL DEFAULT 0,
    overlap_m2      REAL,
    PRIMARY KEY (room_a, room_b, kind),
    CHECK (room_a < room_b)
);
CREATE INDEX IF NOT EXISTS ix_adj_b ON room_adjacency(room_b);

CREATE TABLE IF NOT EXISTS room_equipment (
    id            TEXT PRIMARY KEY,           -- MSI "Nom equip BOS"
    room_id       TEXT REFERENCES rooms(id),
    type          TEXT,
    bim_code      TEXT,
    elec_feed     TEXT,
    heat_feed     TEXT,
    cool_feed     TEXT,
    air_feed      TEXT
);
CREATE INDEX IF NOT EXISTS ix_equipment_room ON room_equipment(room_id);

CREATE TABLE IF NOT EXISTS sensors (
    id              TEXT PRIMARY KEY,
    external_id     TEXT NOT NULL UNIQUE,     -- BMS point id from the CSV export
    room_id         TEXT REFERENCES rooms(id),
    type            TEXT NOT NULL,            -- TEMPERATURE | CO2 | ENERGY | WATER | OCCUPANCY | HUMIDITY | LIGHTING
    unit            TEXT,
    display_name    TEXT,
    floor_label     TEXT,
    zone            TEXT,
    station         TEXT,                     -- acquisition station (see station_source)
    station_source  TEXT,
    resolution_s    INTEGER NOT NULL,         -- historian bucket size
    stale_after_s   INTEGER NOT NULL,
    offline_after_s INTEGER NOT NULL,
    valid_min       REAL,
    valid_max       REAL,
    meta            TEXT                      -- JSON
);
CREATE INDEX IF NOT EXISTS ix_sensors_room ON sensors(room_id);
CREATE INDEX IF NOT EXISTS ix_sensors_type ON sensors(type);

-- Historian: bucketed physical measurements. A row exists for every bucket in
-- which the sensor *transmitted* at least once; `value` is NULL when every
-- sample in the bucket was empty/invalid.
CREATE TABLE IF NOT EXISTS measurements (
    sensor_id   TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    value       REAL,
    n_samples   INTEGER NOT NULL,
    n_valid     INTEGER NOT NULL,
    origin      TEXT NOT NULL DEFAULT 'HISTORIAN',   -- HISTORIAN | INGEST
    PRIMARY KEY (sensor_id, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_measurements_ts ON measurements(ts);

-- Simulated faults are an overlay on top of the historian; the physical data
-- itself is never modified. Everything derived from an injection is flagged.
CREATE TABLE IF NOT EXISTS fault_injections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_id   TEXT NOT NULL REFERENCES sensors(id),
    kind        TEXT NOT NULL,                -- SILENCE | NULL_VALUES | OFFSET | SPIKE
    start_ts    INTEGER NOT NULL,
    end_ts      INTEGER,
    params      TEXT,
    note        TEXT,
    created_by  TEXT,
    created_real INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_faults_sensor ON fault_injections(sensor_id);

CREATE TABLE IF NOT EXISTS sensor_state (
    sensor_id        TEXT PRIMARY KEY REFERENCES sensors(id),
    status           TEXT NOT NULL,           -- ONLINE | STALE | OFFLINE | INVALID | NO_DATA
    last_tx_ts       INTEGER,
    last_valid_ts    INTEGER,
    last_value       REAL,
    anomaly          TEXT,                    -- category of the active anomaly, if any
    incident_id      TEXT,
    simulated        INTEGER NOT NULL DEFAULT 0,
    evaluated_ts     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS virtual_predictions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_id         TEXT NOT NULL REFERENCES sensors(id),
    target_ts         INTEGER NOT NULL,
    value             REAL,
    lower             REAL,
    upper             REAL,
    interval_level    REAL,
    model_version     TEXT NOT NULL,
    quality           TEXT NOT NULL,          -- GOOD | DEGRADED | INSUFFICIENT
    quality_reasons   TEXT,
    inputs            TEXT,                   -- JSON provenance
    input_freshness_s INTEGER,
    gap_s             INTEGER,
    incident_id       TEXT,
    actual_value      REAL,                   -- historian value when known (simulated outages)
    created_real      INTEGER NOT NULL,
    UNIQUE (sensor_id, target_ts)
);
CREATE INDEX IF NOT EXISTS ix_vp_ts ON virtual_predictions(target_ts);

CREATE TABLE IF NOT EXISTS incidents (
    id                   TEXT PRIMARY KEY,
    dedup_key            TEXT NOT NULL,
    title                TEXT NOT NULL,
    category             TEXT NOT NULL,
    severity             TEXT NOT NULL,
    status               TEXT NOT NULL,       -- OPEN | INVESTIGATING | ACKNOWLEDGED | RESOLVED | CLOSED
    scope                TEXT NOT NULL,       -- SENSOR | GROUP
    primary_sensor_id    TEXT REFERENCES sensors(id),
    primary_room_id      TEXT REFERENCES rooms(id),
    onset_ts             INTEGER,
    detected_ts          INTEGER NOT NULL,
    last_seen_ts         INTEGER NOT NULL,
    condition_cleared_ts INTEGER,
    acknowledged_ts      INTEGER,
    acknowledged_by      TEXT,
    resolved_ts          INTEGER,
    resolved_by          TEXT,
    resolution_note      TEXT,
    closed_ts            INTEGER,
    occurrences          INTEGER NOT NULL DEFAULT 1,
    virtual_sensor_active INTEGER NOT NULL DEFAULT 0,
    simulated            INTEGER NOT NULL DEFAULT 0,
    pipeline_stage       TEXT NOT NULL DEFAULT 'DETECTED',  -- DETECTED | DOCUMENTED | ANALYZED | NOTIFIED
    pipeline_version     INTEGER NOT NULL DEFAULT 1,        -- bumped on significant updates
    processed_version    INTEGER NOT NULL DEFAULT 0,        -- last version fully processed by agents 2-4
    claimed_by           TEXT,
    claimed_real         INTEGER,
    summary              TEXT,
    created_real         INTEGER NOT NULL,
    updated_real         INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_incidents_open_dedup
    ON incidents(dedup_key) WHERE status IN ('OPEN','INVESTIGATING','ACKNOWLEDGED');
CREATE INDEX IF NOT EXISTS ix_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS ix_incidents_detected ON incidents(detected_ts);

CREATE TABLE IF NOT EXISTS incident_sensors (
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    sensor_id   TEXT NOT NULL REFERENCES sensors(id),
    PRIMARY KEY (incident_id, sensor_id)
);
CREATE INDEX IF NOT EXISTS ix_incident_sensors_sensor ON incident_sensors(sensor_id);

CREATE TABLE IF NOT EXISTS anomaly_events (
    id              TEXT PRIMARY KEY,          -- deterministic hash: idempotent
    incident_id     TEXT REFERENCES incidents(id),
    category        TEXT NOT NULL,
    severity        TEXT NOT NULL,
    rule_id         TEXT NOT NULL,
    sensor_id       TEXT,
    room_id         TEXT,
    onset_ts        INTEGER,
    detected_ts     INTEGER NOT NULL,
    observed_value  REAL,
    payload         TEXT NOT NULL,             -- full structured Agent 1 event (JSON)
    simulated       INTEGER NOT NULL DEFAULT 0,
    created_real    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_incident ON anomaly_events(incident_id);

CREATE TABLE IF NOT EXISTS incident_timeline (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    ts          INTEGER,
    real_ts     INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    actor       TEXT NOT NULL,
    message     TEXT NOT NULL,
    data        TEXT
);
CREATE INDEX IF NOT EXISTS ix_timeline_incident ON incident_timeline(incident_id);

CREATE TABLE IF NOT EXISTS incident_reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id   TEXT NOT NULL REFERENCES incidents(id),
    version       INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL,
    markdown      TEXT NOT NULL,
    structured    TEXT NOT NULL,
    ai_narrative  TEXT,
    llm_model     TEXT,
    created_real  INTEGER NOT NULL,
    UNIQUE (incident_id, version)
);

CREATE TABLE IF NOT EXISTS analyses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id    TEXT NOT NULL REFERENCES incidents(id),
    version        INTEGER NOT NULL,
    content_hash   TEXT NOT NULL,
    structured     TEXT NOT NULL,
    risk           TEXT,
    model_versions TEXT,
    ai_narrative   TEXT,
    llm_model      TEXT,
    created_real   INTEGER NOT NULL,
    UNIQUE (incident_id, version)
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id      INTEGER NOT NULL REFERENCES analyses(id),
    incident_id      TEXT NOT NULL REFERENCES incidents(id),
    rank             INTEGER NOT NULL,
    cause_code       TEXT NOT NULL,
    title            TEXT NOT NULL,
    support          TEXT NOT NULL,            -- STRONG | MODERATE | WEAK (qualitative, rule-based)
    score            REAL NOT NULL,            -- rule support score (NOT a probability)
    explanation      TEXT,
    evidence_for     TEXT NOT NULL,
    evidence_against TEXT NOT NULL,
    origin           TEXT NOT NULL,            -- RULE_ENGINE | LLM
    outcome          TEXT,                     -- CONFIRMED | REJECTED (human)
    outcome_by       TEXT,
    outcome_real     INTEGER
);
CREATE INDEX IF NOT EXISTS ix_hyp_incident ON hypotheses(incident_id);

CREATE TABLE IF NOT EXISTS recommendations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id       INTEGER NOT NULL REFERENCES analyses(id),
    incident_id       TEXT NOT NULL REFERENCES incidents(id),
    rank              INTEGER NOT NULL,
    hypothesis_code   TEXT,
    action            TEXT NOT NULL,
    urgency           TEXT NOT NULL,
    expected_impact   TEXT,
    validation_method TEXT,
    rationale         TEXT,
    status            TEXT NOT NULL DEFAULT 'PROPOSED',  -- PROPOSED | ACCEPTED | DONE | DISMISSED
    status_by         TEXT,
    status_note       TEXT,
    status_real       INTEGER,
    origin            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rec_incident ON recommendations(incident_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent            TEXT NOT NULL,           -- DETECTION | DOCUMENTATION | ANALYSIS | NOTIFICATION
    incident_id      TEXT,
    status           TEXT NOT NULL,           -- RUNNING | SUCCESS | SKIPPED | FAILED | INVALID_OUTPUT
    trigger          TEXT NOT NULL,           -- engine | n8n | local-fallback | operator
    n8n_execution_id TEXT,
    n8n_workflow     TEXT,
    started_real     INTEGER NOT NULL,
    finished_real    INTEGER,
    duration_ms      INTEGER,
    summary          TEXT,
    error            TEXT,
    details          TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_incident ON agent_runs(incident_id);
CREATE INDEX IF NOT EXISTS ix_runs_started ON agent_runs(started_real);

CREATE TABLE IF NOT EXISTS notifications (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id        TEXT NOT NULL REFERENCES incidents(id),
    dedup_key          TEXT NOT NULL UNIQUE,
    kind               TEXT NOT NULL,         -- ALERT | ESCALATION | ACKNOWLEDGED | RESOLVED
    severity           TEXT NOT NULL,
    channel            TEXT NOT NULL,         -- EMAIL | DASHBOARD
    recipients         TEXT,
    subject            TEXT,
    status             TEXT NOT NULL,         -- PENDING | SENT | TEST_MODE | FAILED | SUPPRESSED | DASHBOARD_ONLY
    status_reason      TEXT,
    attempts           INTEGER NOT NULL DEFAULT 0,
    next_attempt_real  INTEGER,
    last_error         TEXT,
    provider_response  TEXT,
    artifact_path      TEXT,
    created_real       INTEGER NOT NULL,
    sent_real          INTEGER
);
CREATE INDEX IF NOT EXISTS ix_notif_incident ON notifications(incident_id);

CREATE TABLE IF NOT EXISTS notification_attempts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id   INTEGER NOT NULL REFERENCES notifications(id),
    attempt_no        INTEGER NOT NULL,
    at_real           INTEGER NOT NULL,
    success           INTEGER NOT NULL,
    error             TEXT,
    provider_response TEXT
);

CREATE TABLE IF NOT EXISTS workflow_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,               -- n8n | backend
    workflow     TEXT,
    execution_id TEXT,
    node         TEXT,
    level        TEXT NOT NULL,               -- INFO | WARN | ERROR
    kind         TEXT NOT NULL,
    message      TEXT NOT NULL,
    details      TEXT,
    real_ts      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wf_events_ts ON workflow_events(real_ts);

CREATE TABLE IF NOT EXISTS system_state (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
