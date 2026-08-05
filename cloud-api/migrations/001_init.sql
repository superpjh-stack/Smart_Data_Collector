-- Smart Data Collector — cloud ingestion schema
-- Requires the TimescaleDB extension.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── Readings (raw time-series points) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS readings (
    plc_id        TEXT        NOT NULL,
    tag_id        TEXT        NOT NULL,
    tag_name      TEXT        NOT NULL,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    value         DOUBLE PRECISION NOT NULL,
    data_type     TEXT        NOT NULL,
    unit          TEXT,
    quality       TEXT        NOT NULL CHECK (quality IN ('Good', 'Uncertain', 'Bad')),
    seq           BIGINT      NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Dedup key: at-least-once delivery from the edge collector retries the
    -- same (plc_id, tag_id, seq) after a crash/reconnect. Server-side
    -- ON CONFLICT DO NOTHING is what makes the "200 OK + duplicates count"
    -- response contract (Issue 7) work without the collector branching on error.
    -- timestamp_utc must be part of this key: TimescaleDB requires the
    -- hypertable partitioning column in every unique constraint on the table.
    -- This is harmless for dedup in practice -- a retransmit of the same
    -- buffered row always carries the same source timestamp.
    PRIMARY KEY (plc_id, tag_id, seq, timestamp_utc)
);

SELECT create_hypertable('readings', 'timestamp_utc', if_not_exists => TRUE);

-- Continuous aggregates for dashboard long-range queries (Issue 8) — raw
-- readings would be ~630M rows/year at 100 points/5s; long-range charts must
-- read these aggregates instead of scanning raw.
CREATE MATERIALIZED VIEW IF NOT EXISTS readings_1min
WITH (timescaledb.continuous) AS
SELECT
    plc_id,
    tag_id,
    time_bucket('1 minute', timestamp_utc) AS bucket,
    avg(value)  AS avg_value,
    min(value)  AS min_value,
    max(value)  AS max_value,
    count(*)    AS sample_count
FROM readings
GROUP BY plc_id, tag_id, bucket
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS readings_1hour
WITH (timescaledb.continuous) AS
SELECT
    plc_id,
    tag_id,
    time_bucket('1 hour', timestamp_utc) AS bucket,
    avg(value)  AS avg_value,
    min(value)  AS min_value,
    max(value)  AS max_value,
    count(*)    AS sample_count
FROM readings
GROUP BY plc_id, tag_id, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy('readings_1min',
    start_offset => INTERVAL '1 hour',
    end_offset   => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE);

SELECT add_continuous_aggregate_policy('readings_1hour',
    start_offset => INTERVAL '1 day',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

-- ── Alarms (threshold events) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alarms (
    alarm_id        TEXT        PRIMARY KEY,  -- deterministic: plc_id:tag_id:triggered_at_utc:seq (Issue 1)
    plc_id          TEXT        NOT NULL,
    tag_id          TEXT        NOT NULL,
    severity        TEXT        NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    condition       TEXT        NOT NULL,
    triggered_value DOUBLE PRECISION NOT NULL,
    triggered_at_utc TIMESTAMPTZ NOT NULL,
    cleared_at_utc  TIMESTAMPTZ,
    ack_status      TEXT        NOT NULL DEFAULT 'UNACKED'
                                CHECK (ack_status IN ('UNACKED', 'ACKED', 'AUTO_CLEARED')),
    seq             BIGINT      NOT NULL,
    config_version  BIGINT      NOT NULL,  -- Issue 3 (outside voice): traceability to the tag_config in effect
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Redundant with alarm_id's own uniqueness, but explicit: a retransmit of
    -- the same logical alarm must be rejected by this constraint, not just by
    -- alarm_id happening to collide.
    UNIQUE (plc_id, tag_id, triggered_at_utc, seq)
);

CREATE INDEX IF NOT EXISTS idx_alarms_unacked
    ON alarms (plc_id, tag_id)
    WHERE ack_status = 'UNACKED';

-- ── Tag configuration (edge collector pull-sync target, Issue 6) ───────────
CREATE TABLE IF NOT EXISTS tag_config (
    plc_id              TEXT NOT NULL,
    tag_id              TEXT NOT NULL,
    opc_node_id         TEXT NOT NULL,
    unit                TEXT,
    data_type           TEXT NOT NULL,
    min_alarm           DOUBLE PRECISION,
    max_alarm           DOUBLE PRECISION,
    clear_margin        DOUBLE PRECISION NOT NULL DEFAULT 0,  -- hysteresis (Issue 5)
    deadband            DOUBLE PRECISION NOT NULL DEFAULT 0,
    severity            TEXT NOT NULL DEFAULT 'MEDIUM' CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    sampling_interval_ms INTEGER NOT NULL DEFAULT 5000,
    updated_version     BIGINT NOT NULL,  -- bumped from config_version_seq on every write
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (plc_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_tag_config_version ON tag_config (updated_version);

-- Global monotonic version counter. GET /config/v1/tags?since_version=N reads
-- rows where updated_version > N; a write bumps this sequence and stamps the
-- new value onto the row(s) it touches.
CREATE SEQUENCE IF NOT EXISTS config_version_seq;

-- ── Collector self-diagnostic status (Issue 3: gateway_down distinct from
--    "reading unavailable because equipment is actually fine") ─────────────
CREATE TABLE IF NOT EXISTS collector_status_events (
    id           BIGSERIAL PRIMARY KEY,
    plc_id       TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('gateway_down', 'gateway_recovered')),
    occurred_at  TIMESTAMPTZ NOT NULL,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_collector_status_plc_time
    ON collector_status_events (plc_id, occurred_at DESC);
