# cloud_api Design Document

> **Summary**: Cloud ingestion API and storage tier — a FastAPI service that accepts batched readings/alarms/status events from edge collectors, serves tag_config pull-sync, and persists into a TimescaleDB hypertable with continuous aggregates plus a Parquet cold tier.
>
> **Project**: Smart Data Collector
> **Version**: 0.1.0
> **Author**: reverse-derived from implementation + `docs/smart-data-collector-plan.md`
> **Date**: 2026-08-03
> **Status**: Baseline (reverse-derived)
> **Planning Doc**: [smart-data-collector-plan.md](../../smart-data-collector-plan.md)

### Pipeline References

| Phase | Document | Status |
|-------|----------|--------|
| Phase 1 | Schema Definition | N/A — schema lives in this doc §3 and `cloud-api/migrations/001_init.sql` |
| Phase 2 | Coding Conventions | N/A — captured in §10 |
| Phase 3 | Mockup | N/A — headless API, dashboard out of scope |
| Phase 4 | API Spec | This doc §4 |

> **Note**: Reverse-derived after implementation to give the PDCA Check phase a
> design baseline. Describes the design as decided (plan doc + review outcomes).

---

## 1. Overview

### 1.1 Design Goals

- Accept batched time-series readings and threshold alarms from edge collectors over versioned HTTPS endpoints.
- Make duplicate delivery a *normal, non-error* outcome so at-least-once edge retries need no error branch.
- Persist raw readings for 1 year in a hypertable that can answer both live-dashboard and long-range queries without scanning ~630M rows.
- Serve tag_config incrementally so collectors pick up threshold changes without a restart.
- Record collector self-diagnostics so a gateway outage is distinguishable from healthy equipment.
- Age raw data out to Parquet cold storage with a procedure that cannot lose data on partial failure.

### 1.2 Design Principles

- **Idempotent ingestion.** Every write path is `ON CONFLICT DO NOTHING` against a deterministic key; the response reports what actually landed.
- **No error branch for duplicates.** Always `200 OK` + `{inserted, duplicates}`; `409` is deliberately never used for the duplicate case.
- **Versioned surface.** All routes carry `/v1/`, so schema evolution can preserve backward compatibility.
- **Thin routers, explicit SQL.** Routers validate with Pydantic and issue one set-based statement; no ORM, no per-row round trips.
- **Copy-then-verify-then-delete.** No code path deletes retained data without a prior verified copy.

---

## 2. Architecture

### 2.1 Component Diagram

```
   Edge collectors (N sites, outbound only)
        │  POST /ingest/v1/readings
        │  POST /ingest/v1/alarms
        │  POST /status/v1/collector
        │  GET  /config/v1/tags?since_version=N
        ▼
  ┌──────────────────────────────────────────────┐
  │ FastAPI app (cloud_api.main)                 │
  │  lifespan → init_pool / close_pool          │
  │  ┌────────────┐ ┌────────┐ ┌──────────────┐ │
  │  │ ingestion  │ │ config │ │   status     │ │
  │  │  router    │ │ router │ │   router     │ │
  │  └─────┬──────┘ └───┬────┘ └──────┬───────┘ │
  │        └────────────┼─────────────┘         │
  │                 db.get_pool() (asyncpg)     │
  └─────────────────────┬───────────────────────┘
                        ▼
        ┌───────────────────────────────────────┐
        │ TimescaleDB (Hot store, 1 year raw)   │
        │  readings (hypertable on timestamp_utc)│
        │   ├─ readings_1min   (cont. aggregate)│
        │   └─ readings_1hour  (cont. aggregate)│
        │  alarms                                │
        │  tag_config + config_version_seq       │
        │  collector_status_events               │
        └───────────────┬───────────────────────┘
                        │ parquet_archiver (scheduled batch)
                        ▼
        ┌───────────────────────────────────────┐
        │ Cold store: Parquet, day partitions   │
        │  {root}/YYYY/MM/DD.parquet            │
        └───────────────────────────────────────┘
```

### 2.2 Data Flow

```
Ingest:  HTTPS POST → Pydantic validation → unnest() bulk INSERT
         → ON CONFLICT DO NOTHING ... RETURNING
         → inserted = len(returned); duplicates = len(batch) - inserted
         → 200 {inserted, duplicates}

Config:  GET ?since_version=N → SELECT WHERE updated_version > N ORDER BY updated_version
         → {tags: [...], current_version: last_value(config_version_seq)}

Archive: find eligible day partitions (< now - 365d)
         → export to Parquet → read row count back from the file
         → compare to hot-store count → DELETE only on exact match
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `main.py` | `db`, all routers | App assembly, pool lifespan, `/healthz` |
| `db.py` | `asyncpg`, `DATABASE_URL` | Module-level pool (min 2 / max 10) |
| `routers/ingestion.py` | `db`, `schemas` | Readings + alarms batch ingest |
| `routers/config.py` | `db`, `schemas` | tag_config incremental read |
| `routers/status.py` | `db` | Collector status events |
| `schemas.py` | `pydantic` | Wire contracts and enum constraints |
| `archiving/parquet_archiver.py` | `asyncpg`, `pyarrow` | Retention batch job (standalone process) |
| `migrations/001_init.sql` | TimescaleDB extension | Schema, hypertable, aggregates, policies |

---

## 3. Data Model

### 3.1 Database Schema

```sql
-- Raw time-series points; TimescaleDB hypertable partitioned on timestamp_utc.
CREATE TABLE readings (
    plc_id        TEXT NOT NULL,
    tag_id        TEXT NOT NULL,
    tag_name      TEXT NOT NULL,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    value         DOUBLE PRECISION NOT NULL,
    data_type     TEXT NOT NULL,
    unit          TEXT,
    quality       TEXT NOT NULL CHECK (quality IN ('Good','Uncertain','Bad')),
    seq           BIGINT NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (plc_id, tag_id, seq, timestamp_utc)
);
SELECT create_hypertable('readings', 'timestamp_utc', if_not_exists => TRUE);

-- Threshold events; deterministic PK plus an explicit logical-identity constraint.
CREATE TABLE alarms (
    alarm_id         TEXT PRIMARY KEY,   -- plc_id:tag_id:triggered_at_utc:seq
    plc_id           TEXT NOT NULL,
    tag_id           TEXT NOT NULL,
    severity         TEXT NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    condition        TEXT NOT NULL,
    triggered_value  DOUBLE PRECISION NOT NULL,
    triggered_at_utc TIMESTAMPTZ NOT NULL,
    cleared_at_utc   TIMESTAMPTZ,
    ack_status       TEXT NOT NULL DEFAULT 'UNACKED'
                     CHECK (ack_status IN ('UNACKED','ACKED','AUTO_CLEARED')),
    seq              BIGINT NOT NULL,
    config_version   BIGINT NOT NULL,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (plc_id, tag_id, triggered_at_utc, seq)
);
CREATE INDEX idx_alarms_unacked ON alarms (plc_id, tag_id) WHERE ack_status = 'UNACKED';

-- Static per-tag metadata, pulled by collectors.
CREATE TABLE tag_config (
    plc_id               TEXT NOT NULL,
    tag_id               TEXT NOT NULL,
    opc_node_id          TEXT NOT NULL,
    unit                 TEXT,
    data_type            TEXT NOT NULL,
    min_alarm            DOUBLE PRECISION,
    max_alarm            DOUBLE PRECISION,
    clear_margin         DOUBLE PRECISION NOT NULL DEFAULT 0,
    deadband             DOUBLE PRECISION NOT NULL DEFAULT 0,
    severity             TEXT NOT NULL DEFAULT 'MEDIUM'
                         CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    sampling_interval_ms INTEGER NOT NULL DEFAULT 5000,
    updated_version      BIGINT NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (plc_id, tag_id)
);
CREATE INDEX idx_tag_config_version ON tag_config (updated_version);
CREATE SEQUENCE config_version_seq;   -- global monotonic config version

-- Collector self-diagnostics: "we cannot see the equipment" ≠ "equipment is fine".
CREATE TABLE collector_status_events (
    id          BIGSERIAL PRIMARY KEY,
    plc_id      TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('gateway_down','gateway_recovered')),
    occurred_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_collector_status_plc_time
    ON collector_status_events (plc_id, occurred_at DESC);
```

### 3.2 Continuous Aggregates

`readings_1min` and `readings_1hour` are TimescaleDB continuous aggregates over
`readings`, grouping `plc_id, tag_id, time_bucket(...)` and exposing
`avg_value, min_value, max_value, sample_count`. Both are created `WITH NO DATA`
and refreshed by policy:

| View | start_offset | end_offset | schedule_interval |
|------|--------------|------------|-------------------|
| `readings_1min` | 1 hour | 1 minute | 1 minute |
| `readings_1hour` | 1 day | 1 hour | 1 hour |

Long-range dashboard queries must read these, not raw `readings`.

### 3.3 Wire Contracts (Pydantic)

`Reading`, `ReadingsBatch{readings}`, `Alarm`, `AlarmsBatch{alarms}`,
`IngestResult{inserted, duplicates}`, `TagConfig`,
`TagConfigResponse{tags, current_version}`, and `CollectorStatusEvent`
(declared in the status router). Enum-valued fields (`quality`, `severity`,
`ack_status`, `status`) are `Literal` types so an invalid value is rejected at
the boundary with 422 rather than reaching a DB `CHECK`.

---

## 4. API Specification

### 4.1 Endpoint List

| Method | Path | Description | Success | Auth |
|--------|------|-------------|---------|------|
| GET | `/healthz` | Liveness probe | 200 | none |
| POST | `/ingest/v1/readings` | Batch-insert readings | 200 `IngestResult` | none (network-boundary trust) |
| POST | `/ingest/v1/alarms` | Batch-insert alarms | 200 `IngestResult` | none |
| GET | `/config/v1/tags` | tag_config changed since `since_version` | 200 `TagConfigResponse` | none |
| POST | `/status/v1/collector` | Record `gateway_down` / `gateway_recovered` | 201 | none |

Authentication/mTLS is deferred with certificate issuance and rotation — see
`TODOS.md` #2. Until then the trust boundary is network-level.

### 4.2 Detailed Specification

#### `POST /ingest/v1/readings`

Header: `idempotency-key: <uuid>` (per-batch, optional, accepted and logged as
batch identity — dedup correctness does not depend on it, it rests on the row key).

**Request**
```json
{"readings": [{
  "plc_id": "PLC-01", "tag_id": "TEMP_01", "tag_name": "Bearing Temperature",
  "timestamp_utc": "2026-07-13T09:00:05.120Z", "value": 68.4,
  "data_type": "float", "unit": "celsius", "quality": "Good", "seq": 184213
}]}
```

**Response 200**
```json
{"inserted": 1, "duplicates": 0}
```

An empty `readings` array short-circuits to `{"inserted": 0, "duplicates": 0}`
without touching the database. Duplicate rows — same
`(plc_id, tag_id, seq, timestamp_utc)` — are counted, not rejected.

#### `POST /ingest/v1/alarms`

Same shape under an `alarms` key; dedup is on `alarm_id`. `422` on an invalid
`severity` or `ack_status`.

#### `GET /config/v1/tags?since_version=N`

`since_version` is `int >= 0`, default `0`. Returns rows with
`updated_version > N` ordered by `updated_version`, plus `current_version` read
from `config_version_seq.last_value` (`0` when the sequence has never advanced).

#### `POST /status/v1/collector`

Body `{plc_id, status, occurred_at}`; `status` restricted to `gateway_down` /
`gateway_recovered`. Append-only — returns `201 {"status": "recorded"}`.

### 4.3 Error Responses

| Code | Cause | Notes |
|------|-------|-------|
| 422 | Pydantic validation failure (bad enum, missing field, unparseable timestamp) | FastAPI default body |
| 500 | DB unavailable / pool not initialized | `RuntimeError` from `get_pool()` if lifespan never ran |
| — | **Duplicate rows** | Deliberately *not* an error: `200` with a `duplicates` count |

---

## 5. Key Design Decisions

| # | Decision | Rationale | Rejected alternative |
|---|----------|-----------|----------------------|
| 1 | Duplicates → `200 {inserted, duplicates}` | At-least-once edge delivery makes duplicates routine; an error status would force retry-logic branching on the collector | `409 Conflict` |
| 2 | `readings` PK includes `timestamp_utc` | TimescaleDB requires the partitioning column in every unique constraint on a hypertable; without it `create_hypertable` fails and the aggregates never get built | `(plc_id, tag_id, seq)` alone |
| 3 | `alarms` has both an `alarm_id` PK and a `UNIQUE (plc_id, tag_id, triggered_at_utc, seq)` | The logical-identity constraint is stated explicitly rather than relying on the id string happening to collide | PK only |
| 4 | Bulk insert via `unnest($1::text[], ...)` + `RETURNING` | One statement per batch, and `RETURNING` gives the exact inserted count needed for the response contract | Per-row inserts, or `executemany` without counts |
| 5 | Continuous aggregates at 1 min / 1 hour | ~630M raw rows/year; long-range charts must scan thousands of rows, not millions | Query raw with on-the-fly `time_bucket` |
| 6 | Global `config_version_seq`, not per-row timestamps | Gives collectors a single monotonic cursor that is cheap to compare and cannot go backwards under clock skew | `updated_at` timestamp comparison |
| 7 | `collector_status_events` as its own table | Lets the dashboard render "data unavailable" distinctly instead of showing stale values as if healthy | Infer gaps from missing readings |
| 8 | Archival is copy → verify → delete, verification reads the row count back **from the Parquet file** | The only safeguard between an incomplete copy and an irreversible DELETE; trusting the export's own tally would not catch a truncated write | write-then-delete, or trusting file existence |
| 9 | Day-granularity archive partitions | Aligns with TimescaleDB chunk boundaries and keeps re-verification cheap; a month partition would block ~30× more data behind one failure | Month partitions |
| 10 | One partition's failure does not abort the job | A retryable per-day failure must not block newer days and let the hot store grow unbounded | Fail-fast loop |
| 11 | Module-level asyncpg pool via lifespan | Single pool for the process; `get_pool()` raises loudly rather than lazily creating one under load | Per-request connection |
| 12 | Raw SQL, no ORM | Hypertable DDL, `unnest` bulk insert, and `ON CONFLICT ... RETURNING` are all outside what an ORM expresses cleanly | SQLAlchemy ORM |

---

## 6. Retention & Archiving

Implemented as a standalone scheduled process (`cloud-api/archiving/parquet_archiver.py`),
not an API route — it is a batch job with a non-zero exit code so a scheduler can alarm on it.

- `RETENTION = 365 days`. `retention_cutoff()` truncates to a UTC day boundary so a partition is either entirely eligible or entirely retained — never half-exported.
- `find_eligible_partitions()` returns distinct UTC days strictly older than the cutoff that still hold rows, oldest first.
- `archive_partition()` is the **only** public path that can delete: `export_partition_to_parquet()` → `count_partition_rows()` → `verify_partition()` → `_delete_partition()` (private, unreachable without verification).
- `verify_partition()` fails on a missing file, an unreadable file, or a row-count mismatch between `pq.read_metadata(...).num_rows` and the hot-store count. On failure the partition is reported with `verified=False, deleted=False` and its rows stay in the hot store.
- Output layout: `{PARQUET_ARCHIVE_ROOT}/YYYY/MM/DD.parquet`, fixed `PARQUET_SCHEMA` with microsecond UTC timestamps. Local filesystem stands in for object storage; swapping to S3/Blob changes only the write/read target, not the control flow.
- `run_archival_job()` returns a `PartitionResult` per partition; `_main()` exits `1` if any partition was skipped.

Alarms are retained separately from readings for audit purposes and are not
archived by this job.

---

## 7. Security Considerations

- **Input validation** at the boundary: Pydantic models with `Literal` enums, `Query(ge=0)` on `since_version`; DB `CHECK` constraints as a second line.
- **No SQL injection surface**: every value is a bound asyncpg parameter. The one interpolated fragment (`", ".join(COLUMNS)` in the archiver) is a module-level constant tuple, never request-derived.
- **No secrets in code**: `DATABASE_URL` and `PARQUET_ARCHIVE_ROOT` come from the environment.
- **Container hardening**: two-stage build, runs as non-root uid 10001, no source tree in the runtime image.
- **Deferred**: authentication / mutual TLS on the ingest endpoints, and certificate rotation (`TODOS.md` #2). Rate limiting is not implemented — the client population is a known, small set of edge collectors.

---

## 8. Test Plan

| Type | Target | Tool |
|------|--------|------|
| Integration (DB) | Ingest, dedup, config filtering, status events, against a real TimescaleDB | pytest + asyncpg + httpx `ASGITransport` |
| Unit/Integration | Archival export, verification, mismatch handling | pytest |

Key cases:
- Readings and alarms insert and report `inserted`/`duplicates` correctly.
- A full retransmit of the same batch yields `inserted=0` — regression guard for the hypertable PK constraint.
- A partially overlapping batch splits the counts correctly.
- `since_version` filtering returns only newer rows; `current_version` reflects the sequence.
- `gateway_down` / `gateway_recovered` persist; an invalid status is `422`.
- Archival deletes only after verification; a verification failure leaves hot-store rows intact.

**Test isolation requirement:** the suite must never write to the dev database.
`tests/conftest.py` resolves `TEST_DATABASE_URL`, else derives a DSN by appending
`_test` to the `DATABASE_URL` database name, and refuses to run if that resolves
to no dedicated database. A session-scoped fixture creates the database if
missing and applies `001_init.sql` statement-by-statement (asyncpg wraps a
multi-statement `execute()` in one implicit transaction, which TimescaleDB
rejects for `create_hypertable()` and continuous aggregates). Per-test fixtures
truncate all four tables and reset `config_version_seq`.

---

## 9. Module Layout

| Module | Responsibility |
|--------|---------------|
| `src/cloud_api/main.py` | FastAPI app, lifespan pool management, router wiring, `/healthz` |
| `src/cloud_api/db.py` | asyncpg pool lifecycle (`init_pool` / `close_pool` / `get_pool`) |
| `src/cloud_api/schemas.py` | Pydantic wire contracts |
| `src/cloud_api/routers/ingestion.py` | `POST /ingest/v1/readings`, `/alarms` |
| `src/cloud_api/routers/config.py` | `GET /config/v1/tags` |
| `src/cloud_api/routers/status.py` | `POST /status/v1/collector` |
| `migrations/001_init.sql` | Full schema: hypertable, aggregates + policies, alarms, tag_config, status events |
| `archiving/parquet_archiver.py` | Retention batch job (separate entrypoint, not routed) |
| `scripts/seed_dev_tag_config.py` | Idempotent dev-only tag_config seed (upsert + version bump) |

Layering: routers depend on `db` and `schemas` and never on each other;
`schemas` depends on nothing internal; `db` knows nothing about routes. The
archiver connects directly with asyncpg and does not import the app.

---

## 10. Conventions

| Item | Convention |
|------|-----------|
| Language | Python 3.12, PEP 604 unions, type-annotated route handlers |
| Routers | One `APIRouter` per domain with a versioned `prefix` and a `tags=[...]` label |
| Schemas | PascalCase Pydantic models; `Literal` for closed value sets |
| Functions/vars | snake_case; module-private helpers prefixed `_` |
| Constants | UPPER_SNAKE_CASE (`RETENTION`, `COLUMNS`, `PARQUET_SCHEMA`) |
| SQL | Uppercase keywords, positional `$n` parameters, one statement per call |
| Migrations | Numbered, idempotent (`IF NOT EXISTS` / `if_not_exists => TRUE`) |
| Env vars | `DATABASE_URL`, `TEST_DATABASE_URL`, `PARQUET_ARCHIVE_ROOT` |
| Timestamps | `TIMESTAMPTZ` in the DB, timezone-aware UTC in Python |

---

## 11. Deployment

- Two-stage Docker image (`cloud-api/Dockerfile`): deps installed into `/opt/venv` in the builder, runtime copies the venv only, runs as non-root uid 10001, exposes 8000, entrypoint `uvicorn cloud_api.main:app --host 0.0.0.0 --port 8000`.
- Root `docker-compose.yml` is the **cloud** stack: `timescaledb` (`timescale/timescaledb:latest-pg16`, named volume, `001_init.sql` mounted into `docker-entrypoint-initdb.d`, `pg_isready` healthcheck, host port 5442) plus `cloud-api` gated on `service_healthy` with its own `/healthz` healthcheck. Both `restart: unless-stopped`.
- The edge collector is deliberately **absent** from this compose file — it runs at the site on its own lifecycle via `deploy/docker-compose.edge.yml`.
- Migrations run via the postgres entrypoint on first startup only (empty volume). Re-applying a schema change requires `docker compose down -v && up`.
- The archival job runs as a scheduled invocation of `parquet_archiver.py` against `DATABASE_URL`, exiting non-zero when any partition was skipped so the scheduler raises an alarm.

---

## 12. Non-functional Requirements

| Requirement | Target | Mechanism |
|-------------|--------|-----------|
| Ingest throughput | ~20 points/s steady, plus reconnect bursts up to `TRANSMIT_BATCH_MAX` (500) per call | Single `unnest` bulk insert per batch |
| Ingest latency contribution | Small enough to keep end-to-end P95 ≤ 5 s | One round trip, one statement, pooled connections (2–10) |
| Long-range query cost | Thousands of rows, not millions | 1 min / 1 hour continuous aggregates |
| Raw retention | 1 year hot, indefinite cold | Hypertable + Parquet archival |
| Durability under retry | Zero duplicate rows, zero lost rows | Deterministic keys + `ON CONFLICT DO NOTHING` |
| Archival safety | No unverified deletion, ever | copy → verify → delete, verification reads the file back |
| Availability | Restart-safe, no manual steps | `restart: unless-stopped`, healthchecks, idempotent migrations |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-08-03 | Initial baseline, reverse-derived from plan doc + implementation | — |
