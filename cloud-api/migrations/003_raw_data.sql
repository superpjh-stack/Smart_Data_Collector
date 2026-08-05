-- Smart Data Collector — "원본 데이터 수집" (Raw Data Collection) catalog.
-- Plain table, NOT a TimescaleDB hypertable: this is low-volume, project-setup-
-- time registration data (a handful of rows per session), not telemetry.
-- Additive only: no existing table/column is touched.

CREATE TABLE IF NOT EXISTS raw_data_sources (
    id              BIGSERIAL PRIMARY KEY,
    source_type     TEXT NOT NULL
                    CHECK (source_type IN ('excel', 'word', 'scanned_pdf',
                                            'equipment_layout', 'db_sql')),
    name            TEXT NOT NULL,
    description     TEXT,
    equipment_tag   TEXT,               -- free-text 설비/라인 태그, optional, common to all types
    registered_by   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'registered'
                    CHECK (status IN ('registered')),
    -- Single allowed value today (plan explicitly rules out connection-test /
    -- approval states for 1차). Column exists now so a future workflow state
    -- machine is an additive CHECK change, not a new column + backfill.

    -- ── File-backed columns ──────────────────────────────────────────────
    -- Populated for excel / word / scanned_pdf (always) and equipment_layout
    -- (only when a drawing file was attached). Always NULL for db_sql.
    file_name        TEXT,              -- original filename, sanitized for display only
    file_size_bytes  BIGINT,
    file_path        TEXT,              -- path relative to RAW_DATA_STORAGE_ROOT
    content_type     TEXT,              -- client-reported MIME type, display only

    -- ── equipment_layout-only columns ────────────────────────────────────
    layout_line_name       TEXT,
    layout_equipment_name  TEXT,
    layout_location_desc   TEXT,

    -- ── db_sql-only columns ──────────────────────────────────────────────
    -- Registration metadata only. No credential fields exist by design — see
    -- design doc §7. Nothing in this codebase ever reads these columns to
    -- open a connection.
    db_kind          TEXT,              -- free text, e.g. 'MSSQL' / 'Oracle' / 'MySQL' — not enum-constrained, no validation
    db_host          TEXT,
    db_port          INTEGER,
    db_query_text    TEXT,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_raw_data_file_fields CHECK (
        (source_type IN ('excel', 'word', 'scanned_pdf') AND file_path IS NOT NULL)
        OR (source_type = 'equipment_layout')                      -- file_path optional
        OR (source_type = 'db_sql' AND file_path IS NULL)
    )
);

-- Backs the tab's default listing (newest first) and the type filter.
CREATE INDEX IF NOT EXISTS idx_raw_data_sources_type_created
    ON raw_data_sources (source_type, created_at DESC);
