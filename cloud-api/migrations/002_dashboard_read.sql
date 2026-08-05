-- Smart Data Collector — read-path indexes for the operator dashboard.
-- Additive only: no existing table/column is altered.

-- Backs GET /readings/v1/latest's DISTINCT ON (plc_id, tag_id) ORDER BY
-- timestamp_utc DESC: without this, "latest reading per tag" degrades to a
-- backward hypertable scan per tag instead of an index-only lookup.
CREATE INDEX IF NOT EXISTS idx_readings_latest_per_tag
    ON readings (plc_id, tag_id, timestamp_utc DESC);
