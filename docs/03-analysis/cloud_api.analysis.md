# cloud_api Analysis Report

> **Analysis Type**: Gap Analysis (PDCA Check phase)
>
> **Project**: Smart Data Collector
> **Version**: 0.1.0
> **Analyst**: bkit gap-detector
> **Date**: 2026-08-03
> **Design Doc**: [cloud_api.design.md](../02-design/features/cloud_api.design.md)

### Scope note on template adaptation

`cloud_api` is a headless Python (FastAPI) service. The following sections of bkit's `analysis.template.md` do not apply and have been **replaced**, not placeholder-filled:

| Template section | Disposition |
|------------------|-------------|
| 2.3 Component Structure (UI) | Replaced by **2.7 Module Structure** (Python modules) |
| 6 Clean Architecture (Presentation/Application/Domain/Infrastructure) | Replaced by **6 Python Module Layering** (design §9's stated layering rule) |
| 7.1–7.3 Naming/Import/Folder checks (npm/TSX-centric) | Replaced by **7 Convention Compliance** against design §10 (PEP 8 / snake_case / PascalCase Pydantic) |
| 4 Performance Analysis (measured latency) | Kept as **4 Non-functional Mechanism Verification** — no load test was run, so mechanisms are verified, not numbers |
| Phase 1/2/4/8 pipeline reference table | Replaced — this project has no separate schema/conventions docs; design §3/§10 are authoritative (as the design's own "Pipeline References" table states) |

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Verify, item by item and against read source, that the reverse-derived design baseline (`cloud_api.design.md` v0.1) is an accurate description of the shipped implementation — so the design can be trusted as the reference for future change, and so that any place where document and code disagree is recorded rather than silently inherited.

Because the design was reverse-derived *after* implementation, the expected failure mode is not "unimplemented features" but **document drift**: claims the author believed were true but that the code does not actually make good on.

### 1.2 Analysis Scope

- **Design Document**: `docs/02-design/features/cloud_api.design.md` (§1–§12)
- **Implementation Paths**:
  - `cloud-api/src/cloud_api/` — `main.py`, `db.py`, `schemas.py`, `routers/{ingestion,config,status}.py`
  - `cloud-api/migrations/001_init.sql`
  - `cloud-api/archiving/parquet_archiver.py`
  - `cloud-api/tests/` — `conftest.py`, `test_ingestion_db.py`, `test_config_db.py`, `test_status_db.py`, `test_parquet_archiver.py`
  - `cloud-api/scripts/seed_dev_tag_config.py`, `cloud-api/Dockerfile`, `cloud-api/pyproject.toml`
  - `docker-compose.yml` (repo root), `deploy/docker-compose.edge.yml` (existence only)
- **Analysis Date**: 2026-08-03
- **Method**: every file above read in full; no inference from filenames. Status values are `✅ Match`, `⚠️ Missing in design` (implemented, undocumented), `❌ Not implemented` (documented, absent), `🔵 Changed` (both exist but disagree).

---

## 2. Gap Analysis (Design vs Implementation)

### 2.1 Database Schema — design §3.1 vs `migrations/001_init.sql`

Compared column by column, including types, CHECK constraints, defaults, PK/UNIQUE, and indexes.

| # | Design item (§3.1) | Implementation evidence | Status |
|---|--------------------|-------------------------|--------|
| 1 | `readings` columns + types (`plc_id`/`tag_id`/`tag_name` TEXT NOT NULL, `timestamp_utc` TIMESTAMPTZ, `value` DOUBLE PRECISION, `data_type` TEXT, `unit` TEXT nullable, `seq` BIGINT, `ingested_at` DEFAULT now()) | `001_init.sql:8-17` — identical set, identical nullability | ✅ Match |
| 2 | `readings.quality` CHECK IN ('Good','Uncertain','Bad') | `001_init.sql:15` | ✅ Match |
| 3 | `readings` PK `(plc_id, tag_id, seq, timestamp_utc)` | `001_init.sql:26` | ✅ Match |
| 4 | `create_hypertable('readings','timestamp_utc', if_not_exists => TRUE)` | `001_init.sql:29` | ✅ Match |
| 5 | `alarms` columns + types, `alarm_id` TEXT PRIMARY KEY | `001_init.sql:76-88` — identical, incl. `cleared_at_utc` nullable, `config_version` BIGINT NOT NULL | ✅ Match |
| 6 | `alarms.severity` CHECK; `ack_status` DEFAULT 'UNACKED' + CHECK IN ('UNACKED','ACKED','AUTO_CLEARED') | `001_init.sql:79`, `001_init.sql:84-85` | ✅ Match |
| 7 | `alarms` UNIQUE `(plc_id, tag_id, triggered_at_utc, seq)` | `001_init.sql:92` | ✅ Match |
| 8 | `idx_alarms_unacked ON alarms (plc_id, tag_id) WHERE ack_status='UNACKED'` | `001_init.sql:95-97` | ✅ Match |
| 9 | `tag_config` columns, defaults (`clear_margin` 0, `deadband` 0, `severity` 'MEDIUM', `sampling_interval_ms` 5000), severity CHECK, PK `(plc_id, tag_id)` | `001_init.sql:101-114` | ✅ Match |
| 10 | `idx_tag_config_version ON tag_config (updated_version)` | `001_init.sql:117` | ✅ Match |
| 11 | `CREATE SEQUENCE config_version_seq` | `001_init.sql:122` (`IF NOT EXISTS`) | ✅ Match |
| 12 | `collector_status_events` (`id` BIGSERIAL PK, `plc_id`, `status` CHECK IN ('gateway_down','gateway_recovered'), `occurred_at`, `ingested_at` DEFAULT now()) | `001_init.sql:127-131` | ✅ Match |
| 13 | `idx_collector_status_plc_time ON (plc_id, occurred_at DESC)` | `001_init.sql:134-135` | ✅ Match |
| 14 | — | `001_init.sql:4` `CREATE EXTENSION IF NOT EXISTS timescaledb;` — the §3.1 SQL block omits it (§2.3 lists the extension as a dependency, but the DDL listing does not) | ⚠️ Missing in design |

**Zero column-level divergence.** The §3.1 block is a faithful transcription of the migration, modulo the extension statement and the migration's `IF NOT EXISTS` guards (the latter covered by design §10 "Migrations: numbered, idempotent").

### 2.2 Continuous Aggregates — design §3.2 vs `001_init.sql`

| # | Design item | Implementation evidence | Status |
|---|-------------|-------------------------|--------|
| 15 | `readings_1min`: group `plc_id, tag_id, time_bucket('1 minute', timestamp_utc)`; expose `avg_value`/`min_value`/`max_value`/`sample_count`; `WITH NO DATA` | `001_init.sql:34-46` (bucket column named `bucket`; design does not name it) | ✅ Match |
| 16 | `readings_1hour`: same, `'1 hour'` bucket, `WITH NO DATA` | `001_init.sql:48-60` | ✅ Match |
| 17 | `readings_1min` policy: start_offset 1 hour / end_offset 1 minute / schedule 1 minute | `001_init.sql:62-66` | ✅ Match |
| 18 | `readings_1hour` policy: start_offset 1 day / end_offset 1 hour / schedule 1 hour | `001_init.sql:68-72` | ✅ Match |

The refresh-policy table in §3.2 matches all six values exactly.

### 2.3 Wire Contracts — design §3.3 vs `schemas.py` / `routers/status.py`

| # | Design item | Implementation evidence | Status |
|---|-------------|-------------------------|--------|
| 19 | `Reading`, `ReadingsBatch{readings}` | `schemas.py:7-20` — field set matches the §4.2 example payload exactly | ✅ Match |
| 20 | `Alarm`, `AlarmsBatch{alarms}` | `schemas.py:23-38` | ✅ Match |
| 21 | `IngestResult{inserted, duplicates}` | `schemas.py:41-43` | ✅ Match |
| 22 | `TagConfig`, `TagConfigResponse{tags, current_version}` | `schemas.py:46-63` — mirrors the `tag_config` SELECT list at `routers/config.py:22-24`; DB-only `updated_at` correctly excluded | ✅ Match |
| 23 | `CollectorStatusEvent` **declared in the status router**; enum fields (`quality`, `severity`, `ack_status`, `status`) are `Literal` so bad values 422 at the boundary | `routers/status.py:12-15`; `schemas.py:15, 27, 32`; 422 proven by `tests/test_status_db.py:45-54` | ✅ Match |

### 2.4 API Endpoints — design §4 vs routers

| # | Design item (§4) | Implementation evidence | Status |
|---|------------------|-------------------------|--------|
| 24 | `GET /healthz` → 200 | `main.py:22-24` returns `{"status": "ok"}` | ✅ Match |
| 25 | `POST /ingest/v1/readings` → 200 `IngestResult` | `routers/ingestion.py:6, 9-13` (`prefix="/ingest/v1"`, `response_model=IngestResult`) | ✅ Match |
| 26 | `POST /ingest/v1/alarms` → 200 `IngestResult` | `routers/ingestion.py:60-64` | ✅ Match |
| 27 | `GET /config/v1/tags` → 200 `TagConfigResponse` | `routers/config.py:6, 9-10` | ✅ Match |
| 28 | `POST /status/v1/collector` → **201** `{"status":"recorded"}` | `routers/status.py:18` (`status_code=201`), `routers/status.py:34` | ✅ Match |
| 29 | No auth on any route; trust is network-level; mTLS deferred to `TODOS.md` #2 | No auth dependency or middleware in `main.py:16-19` or any of the three routers; `TODOS.md:19-31` is indeed the certificate issuance/rotation TODO | ✅ Match |
| 30 | `idempotency-key` header accepted on `/readings` (optional, per-batch) | `routers/ingestion.py:12` `idempotency_key: str \| None = Header(default=None)` | ✅ Match |
| 31 | idempotency-key is "accepted **and logged** as batch identity" | **Not logged.** `routers/ingestion.py` imports no `logging` and never references `idempotency_key` after binding it — accepted and silently discarded in both handlers | ❌ Not implemented |
| 32 | — | `/alarms` also accepts `idempotency-key` (`routers/ingestion.py:63`); §4.2 documents the header only under `/readings` | ⚠️ Missing in design |
| 33 | Empty `readings` array short-circuits to `{0, 0}` without touching the DB | `routers/ingestion.py:22-23`; proven by `tests/test_ingestion_db.py:35-38` | ✅ Match |
| 34 | Alarms "same shape" (implies the same short-circuit) | `routers/ingestion.py:65-66`; `tests/test_ingestion_db.py:71-74` | ✅ Match |
| 35 | Readings dedup on `(plc_id, tag_id, seq, timestamp_utc)`; duplicates counted, not rejected | `routers/ingestion.py:50` `ON CONFLICT ... DO NOTHING`, `:51` `RETURNING`, `:56-57` count arithmetic | ✅ Match |
| 36 | Alarms dedup on `alarm_id` | `routers/ingestion.py:97` `ON CONFLICT (alarm_id) DO NOTHING` | ✅ Match |
| 37 | `since_version` is `int >= 0`, default `0` | `routers/config.py:10` `Query(default=0, ge=0)` | ✅ Match |
| 38 | Returns `updated_version > N` ordered by `updated_version` | `routers/config.py:26-27` | ✅ Match |
| 39 | `current_version` from `config_version_seq.last_value`, "**`0` when the sequence has never advanced**" | `routers/config.py:31-33, 36`. The read is correct, but the parenthetical is **false**: a fresh/`RESTART`ed sequence reports `last_value = 1` with `is_called = false`, so the never-advanced case yields **1**, not 0. `tests/test_config_db.py:44-50` asserts `current_version == 1` and documents this in a comment. The `or 0` fallback only fires on SQL `NULL`, which this query never returns | 🔵 Changed |
| 40 | `422` on invalid enum / missing field / unparseable timestamp (FastAPI default body) | `Literal` types (`schemas.py:15, 27, 32`; `routers/status.py:14`); verified for `status` by `tests/test_status_db.py:54` | ✅ Match |
| 41 | `500` when the pool was never initialised (`RuntimeError` from `get_pool()`) | `db.py:23-26` raises `RuntimeError("DB pool not initialized …")`; no exception handler intercepts it, so FastAPI returns 500 | ✅ Match |

### 2.5 Key Design Decisions — design §5 (all 12)

| # | Decision | Implementation evidence | Status |
|---|----------|-------------------------|--------|
| 42 | 1. Duplicates → `200 {inserted, duplicates}`, never `409` | `routers/ingestion.py:56-57, 103-104`; no `409` or `HTTPException` anywhere in the router | ✅ Match |
| 43 | 2. `readings` PK includes `timestamp_utc` (hypertable requirement) | `001_init.sql:22-26` with the rationale comment; regression-guarded by `tests/test_ingestion_db.py:51-60` | ✅ Match |
| 44 | 3. `alarms` has both an `alarm_id` PK and an explicit logical UNIQUE | `001_init.sql:76` + `001_init.sql:92` | ✅ Match |
| 45 | 4. Bulk insert via `unnest($1::text[], …)` + `RETURNING` | `routers/ingestion.py:46-51` (readings, 9 arrays), `:92-98` (alarms, 11 arrays); one statement per batch via `*zip(*rows)` | ✅ Match |
| 46 | 5. Continuous aggregates at 1 min / 1 hour | `001_init.sql:34-72` | ✅ Match |
| 47 | 6. Global `config_version_seq`, not `updated_at` comparison | `001_init.sql:122`; `routers/config.py:26` filters on `updated_version`, never `updated_at`; writers bump via `nextval` (`scripts/seed_dev_tag_config.py:46`) | ✅ Match |
| 48 | 7. `collector_status_events` as its own table | `001_init.sql:126-132`; dedicated router `routers/status.py` | ✅ Match |
| 49 | 8. copy → verify → delete, verification reads the row count **from the Parquet file** | `archiving/parquet_archiver.py:210-223`; `:177` `pq.read_metadata(parquet_path).num_rows` | ✅ Match |
| 50 | 9. Day-granularity partitions | `archiving/parquet_archiver.py:95-106` (`partition_bounds` spans 1 day; path `YYYY/MM/DD.parquet`) | ✅ Match |
| 51 | 10. One partition's failure does not abort the job | `archiving/parquet_archiver.py:243-256` per-partition `try/except`; proven by `tests/test_parquet_archiver.py:141-162` | ✅ Match |
| 52 | 11. Module-level asyncpg pool via lifespan; `get_pool()` raises loudly | `db.py:5, 8-13, 23-26`; `main.py:9-16` | ✅ Match |
| 53 | 12. Raw SQL, no ORM | No ORM dependency in `pyproject.toml:6-12`; all access is `conn.fetch` / `fetchval` / `execute` | ✅ Match |

All twelve decisions are honoured by the code, including the two that exist purely as safety constraints (#2 and #8) — both have dedicated regression tests.

### 2.6 Retention & Archiving — design §6 vs `parquet_archiver.py`

| # | Design item | Implementation evidence | Status |
|---|-------------|-------------------------|--------|
| 54 | Standalone scheduled process, not an API route | `archiving/parquet_archiver.py:281-282` `__main__` entrypoint; not imported by `main.py:1-6`, and lives outside `src/` (`pyproject.toml:24-26`) | ✅ Match |
| 55 | `RETENTION = 365 days` | `archiving/parquet_archiver.py:31` `RETENTION = timedelta(days=365)` | ✅ Match |
| 56 | `retention_cutoff()` truncates to a UTC day boundary | `:85-92` `datetime.combine((now - RETENTION).date(), time.min, tzinfo=timezone.utc)` | ✅ Match |
| 57 | `find_eligible_partitions()` → distinct UTC days strictly older than the cutoff that still hold rows, oldest first | `:109-123` (`DISTINCT date_trunc('day', …)`, `WHERE timestamp_utc < $1`, `ORDER BY day`); proven by `tests/test_parquet_archiver.py:61-74` | ✅ Match |
| 58 | `archive_partition()` is the only public delete path: export → count → verify → `_delete_partition` | `:206-230`, in exactly that order | ✅ Match |
| 59 | `_delete_partition()` private, unreachable without verification | `:195-203`, called only from `:223`, after the `:213` guard | ✅ Match |
| 60 | `verify_partition()` fails on a missing file, an unreadable file, or a row-count mismatch; failure → `verified=False, deleted=False`, rows retained | `:171-192` (three branches); `:214-221`; proven by `tests/test_parquet_archiver.py:97-138` (mismatch keeps 5 rows; missing file → False) | ✅ Match |
| 61 | Layout `{PARQUET_ARCHIVE_ROOT}/YYYY/MM/DD.parquet`; fixed `PARQUET_SCHEMA` with microsecond UTC timestamps; local FS stands in for object storage | `:77-82`, `:100-106`, `:47-60` (`pa.timestamp("us", tz="UTC")` for both timestamp columns); asserted by `tests/test_parquet_archiver.py:86-88` | ✅ Match |
| 62 | `run_archival_job()` returns a `PartitionResult` per partition; `_main()` exits `1` if any partition was skipped | `:233-267`, `:278` `return 1 if any(not r.deleted for r in results) else 0` | ✅ Match |
| 63 | Alarms retained separately, not archived by this job | No `alarms` reference anywhere in the archiver (`COLUMNS` at `:34-45` is readings-only) | ✅ Match |
| 64 | — | `class VerificationFailed(Exception)` is defined at `:63-64` but **never raised, caught, or exported** — verification failure is signalled by the `PartitionResult.verified` flag instead. Dead code, and design §6 does not mention it | ⚠️ Missing in design |

### 2.7 Module Structure — design §9

| # | Design module | Actual file | Status |
|---|---------------|-------------|--------|
| 65 | `src/cloud_api/main.py` — app, lifespan pool, router wiring, `/healthz` | `main.py:9-24` — all four responsibilities present, nothing else | ✅ Match |
| 66 | `src/cloud_api/db.py` — `init_pool` / `close_pool` / `get_pool` | `db.py:8, 16, 23` | ✅ Match |
| 67 | `src/cloud_api/schemas.py` — Pydantic wire contracts | `schemas.py:1-63` | ✅ Match |
| 68 | `src/cloud_api/routers/ingestion.py` | exists, both routes | ✅ Match |
| 69 | `src/cloud_api/routers/config.py` | exists, one route | ✅ Match |
| 70 | `src/cloud_api/routers/status.py` | exists, one route | ✅ Match |
| 71 | `migrations/001_init.sql` — full schema | exists, 136 lines | ✅ Match |
| 72 | `archiving/parquet_archiver.py` — separate entrypoint, not routed | exists, `:281-282` | ✅ Match |
| 73 | `scripts/seed_dev_tag_config.py` — idempotent dev seed (upsert + version bump) | `:46` `nextval`, `:53-64` `ON CONFLICT (plc_id, tag_id) DO UPDATE` incl. `updated_version` and `updated_at = now()` | ✅ Match |
| 74 | Layering rule: routers → `db` + `schemas` only, never each other; `schemas` has no internal deps; `db` knows nothing about routes; archiver does not import the app | Verified by import lists — see §6 below | ✅ Match |

### 2.8 Match Rate Summary

```
┌──────────────────────────────────────────────────────┐
│  Overall Match Rate: 92.1%   (105 / 114 items)       │
├──────────────────────────────────────────────────────┤
│  ✅ Match:              105 items  (92.1%)           │
│  ⚠️ Missing in design:    4 items  ( 3.5%)           │
│  🔵 Changed (disagree):   3 items  ( 2.6%)           │
│  ❌ Not implemented:      2 items  ( 1.8%)           │
└──────────────────────────────────────────────────────┘
```

Derivation of the 114-item denominator (every item is enumerated above or below):

| Design section | Items | ✅ | ⚠️ | 🔵 | ❌ |
|----------------|:-----:|:--:|:--:|:--:|:--:|
| §3.1 DB schema | 14 | 13 | 1 | 0 | 0 |
| §3.2 Continuous aggregates | 4 | 4 | 0 | 0 | 0 |
| §3.3 Wire contracts | 5 | 5 | 0 | 0 | 0 |
| §4 API spec | 18 | 15 | 1 | 1 | 1 |
| §5 Design decisions | 12 | 12 | 0 | 0 | 0 |
| §6 Retention & archiving | 10 | 9 | 1 | 0 | 0 |
| §7 Security | 5 | 4 | 0 | 1 | 0 |
| §8 Test plan | 11 | 10 | 0 | 0 | 1 |
| §9 Module layout | 10 | 10 | 0 | 0 | 0 |
| §10 Conventions | 9 | 8 | 0 | 1 | 0 |
| §11 Deployment | 9 | 8 | 1 | 0 | 0 |
| §12 Non-functional | 7 | 7 | 0 | 0 | 0 |
| **Total** | **114** | **105** | **4** | **3** | **2** |

Match Rate = 105 / 114 = **92.1%** — above the 90% PDCA gate.

---

## 3. Code Quality Analysis

### 3.1 Complexity

| File | Function | Branches | Status | Note |
|------|----------|:--------:|--------|------|
| `routers/ingestion.py` | `ingest_readings` | 1 | ✅ Good | 1 guard, 1 statement |
| `routers/ingestion.py` | `ingest_alarms` | 1 | ✅ Good | structurally identical to the above |
| `routers/config.py` | `get_tag_config` | 1 | ✅ Good | |
| `archiving/parquet_archiver.py` | `verify_partition` | 3 | ✅ Good | one branch per documented failure mode |
| `archiving/parquet_archiver.py` | `run_archival_job` | 2 | ✅ Good | loop + per-item `except` |
| `tests/conftest.py` | `_derive_test_dsn` | 3 | ✅ Good | |

No function exceeds ~35 lines. No file exceeds 283 lines.

### 3.2 Code Smells

| Type | File | Location | Description | Severity |
|------|------|----------|-------------|----------|
| Dead code | `archiving/parquet_archiver.py` | L63-64 | `VerificationFailed` defined, never raised or caught | 🟡 |
| Unused parameter | `routers/ingestion.py` | L12, L63 | `idempotency_key` bound but never read — the design says it is logged (see item #31) | 🟡 |
| Duplicated shape | `routers/ingestion.py` | L9-57 vs L60-104 | The two handlers share an identical guard/zip/fetch/count skeleton. Accepted here: the SQL, column tuple and conflict target all differ, and §5 #4 / §10 prescribe one explicit statement per call — abstracting it would hide the SQL | 🟢 (accepted) |
| Hardcoded dev DSN | `tests/conftest.py`, `scripts/seed_dev_tag_config.py` | L28, L22 | Dev credentials as literal fallbacks (see item #77) | 🟢 |
| Docstring drift risk | `routers/*.py` | passim | Handlers cite "Issue 3/6/7" numbers from an external review thread not indexed in this design doc | 🟢 |

### 3.3 Security Findings — design §7

| # | Design claim (§7) | Verification | Status |
|---|-------------------|--------------|--------|
| 75 | Input validation at the boundary: Pydantic `Literal` enums, `Query(ge=0)`; DB `CHECK` as second line | `schemas.py:15, 27, 32`; `routers/status.py:14`; `routers/config.py:10`; `001_init.sql:15, 79, 85, 110, 129` | ✅ Match |
| 76 | No SQL-injection surface: every value is a bound `$n` parameter; the single interpolated fragment (`", ".join(COLUMNS)`) is a module-level constant, never request-derived | Verified across all four app SQL call sites (`routers/ingestion.py:53, 100`; `routers/config.py:29`; `routers/status.py:30-32`) and the archiver. The only f-string SQL is `archiving/parquet_archiver.py:132`, interpolating `COLUMNS` from `:34-45`. `tests/conftest.py:75` and `:107` also interpolate — but from a `TEST_DSN`-derived name and a hardcoded literal list, in test scope only | ✅ Match |
| 77 | "**No secrets in code**": `DATABASE_URL` and `PARQUET_ARCHIVE_ROOT` come from the environment | Env sourcing is correct (`db.py:11`, `archiving/parquet_archiver.py:82, 272`). But plaintext dev credentials *are* committed: `tests/conftest.py:28` and `scripts/seed_dev_tag_config.py:22` hardcode `postgresql://sdc:sdc_dev_pw@…`, and `docker-compose.yml:21-23, 43` sets `POSTGRES_PASSWORD: sdc_dev_pw` inline. All dev-only and non-production, but the blanket §7 claim overstates the actual position | 🔵 Changed |
| 78 | Container hardening: two-stage build, non-root uid 10001, no source tree in the runtime image | `Dockerfile:2` (`AS builder`) / `:18`; `:24` `useradd --uid 10001` + `:29` `USER appuser`; runtime copies only `/opt/venv` (`:26`) — no `COPY src` in stage 2 | ✅ Match |
| 79 | Deferred: authN/mTLS + cert rotation (`TODOS.md` #2); rate limiting intentionally absent (small known client population) | No auth and no rate-limit middleware exists — consistent with the design; `TODOS.md:19-31` is the matching TODO | ✅ Match |

No hardcoded production secret, no injection vector, and no unvalidated input path found.

---

## 4. Non-functional Mechanism Verification — design §12

No load test was executed, so throughput and latency numbers are **not** claimed here; each row verifies that the stated *mechanism* exists.

| # | Requirement | Target | Mechanism verified at | Status |
|---|-------------|--------|-----------------------|--------|
| 80 | Ingest throughput | ~20 pts/s + bursts to 500/call | Single `unnest` bulk insert, one statement per batch — `routers/ingestion.py:42-54` | ✅ Match |
| 81 | Ingest latency contribution | keeps end-to-end P95 ≤ 5 s | One round trip per batch, pooled connections — `routers/ingestion.py:41`, `db.py:12` | ✅ Match |
| 82 | Pool sizing 2–10 | as designed | `db.py:12` `min_size=2, max_size=10` | ✅ Match |
| 83 | Long-range query cost | thousands, not millions of rows | `readings_1min` / `readings_1hour` — `001_init.sql:34-72` | ✅ Match |
| 84 | Raw retention | 1 yr hot, indefinite cold | Hypertable `001_init.sql:29` + `RETENTION` `archiving/parquet_archiver.py:31` | ✅ Match |
| 85 | Durability under retry | zero duplicate, zero lost rows | Deterministic keys + `ON CONFLICT DO NOTHING` — `001_init.sql:26, 76`; `routers/ingestion.py:50, 97` | ✅ Match |
| 86 | Archival safety | no unverified deletion | `archiving/parquet_archiver.py:213-223` | ✅ Match |

**Observation (not counted as a gap):** nothing server-side caps batch size. §12 frames `TRANSMIT_BATCH_MAX = 500` as an *edge* constant and never claims server enforcement, so this is internally consistent — but a malformed or hostile client can post an arbitrarily large `readings` array into a single `unnest`. Worth an explicit `max_length` on `ReadingsBatch.readings` when the trust boundary tightens.

---

## 5. Test Coverage — design §8

### 5.1 Test Plan Conformance

| # | Design §8 item | Implementation evidence | Status |
|---|----------------|-------------------------|--------|
| 87 | Tooling: pytest + asyncpg + httpx `ASGITransport` against a real TimescaleDB | `pyproject.toml:14-19, 21-22`; `tests/conftest.py:26, 119-121` | ✅ Match |
| 88 | Readings insert and report `inserted`/`duplicates` correctly | `tests/test_ingestion_db.py:41-48` | ✅ Match |
| 89 | Alarms insert and report correctly | `tests/test_ingestion_db.py:77-85` | ✅ Match |
| 90 | Full retransmit yields `inserted=0` — hypertable-PK regression guard | `tests/test_ingestion_db.py:51-60` (readings), `:88-96` (alarms) | ✅ Match |
| 91 | Partially overlapping batch splits the counts | `tests/test_ingestion_db.py:63-68` → `{inserted: 2, duplicates: 1}` | ✅ Match |
| 92 | `since_version` filtering returns only newer rows; `current_version` reflects the sequence | `tests/test_config_db.py:21-31, 34-41` | ✅ Match |
| 93 | `gateway_down` / `gateway_recovered` persist; an invalid status is `422` | `tests/test_status_db.py:6-42`, `:45-54` | ✅ Match |
| 94 | Archival deletes only after verification; a verification failure leaves hot-store rows intact | `tests/test_parquet_archiver.py:77-94`, `:97-122`, `:125-138`, `:141-162` | ✅ Match |
| 95 | Isolation: resolve `TEST_DATABASE_URL`, else derive by appending `_test`, refuse if that resolves to no dedicated database | `tests/conftest.py:32-48` — `_derive_test_dsn()` raises on `""` / `"/"` / `"/postgres"` (`:40-43`) and overwrites `os.environ["DATABASE_URL"]` at `:48` *before* the app import at `:50-51` | ✅ Match |
| 96 | Session fixture creates the DB if missing and applies `001_init.sql` **statement by statement** (asyncpg's implicit transaction breaks `create_hypertable` and CAGGs) | `tests/conftest.py:56-61` (comment-strip + `;` split), `:64-89` | ✅ Match |
| 97 | Per-test fixtures truncate all four tables and reset `config_version_seq` | `tests/conftest.py:53`, `:99-109` (`TRUNCATE … RESTART IDENTITY CASCADE` + `ALTER SEQUENCE config_version_seq RESTART`), `autouse=True` | ✅ Match |
| 98 | §4.2: "`422` on an invalid `severity` or `ack_status`" for `/ingest/v1/alarms` | **No test exercises this.** `tests/test_ingestion_db.py` has no 422 case; the only enum-rejection test is for `status` (`tests/test_status_db.py:45-54`). The §8 case list also omits it, so the documented §4.2 behaviour ships unverified | ❌ Not implemented (test) |

### 5.2 Untested Areas (coverage notes, not design gaps)

- `GET /healthz` (`main.py:22-24`) — no test, despite being the compose healthcheck target (`docker-compose.yml:52-53`).
- `get_pool()`'s `RuntimeError` path (`db.py:24-25`) — the 500 documented in §4.3 is never asserted.
- `_main()` exit code (`archiving/parquet_archiver.py:270-278`) — the §6/§11 "exit 1 so the scheduler alarms" contract is untested; `run_archival_job` is covered, the wrapper is not.
- `find_eligible_partitions` returning `[]` is covered only indirectly (`tests/test_parquet_archiver.py:165-170`).
- No coverage tool is configured in `pyproject.toml`, so no percentage figure can be reported honestly.

---

## 6. Python Module Layering — design §9

*(Replaces the template's frontend Clean Architecture section; design §9's stated layering rule is the contract being checked.)*

### 6.1 Layer Dependency Verification

| Layer | Allowed dependencies per §9 | Actual imports | Status |
|-------|-----------------------------|----------------|--------|
| App assembly (`main.py`) | `db`, all routers | `cloud_api.db`, `cloud_api.routers.{config, ingestion, status}` (`main.py:5-6`) | ✅ |
| Routers (`routers/*.py`) | `db`, `schemas`; **never each other** | `ingestion.py:3-4` → `db`, `schemas`; `config.py:3-4` → `db`, `schemas`; `status.py:5, 7` → `pydantic`, `db`. Zero router-to-router imports | ✅ |
| Schemas (`schemas.py`) | nothing internal | `datetime`, `typing`, `pydantic` only (`schemas.py:1-4`) | ✅ |
| DB (`db.py`) | knows nothing about routes | `os`, `asyncpg` only (`db.py:1-3`) | ✅ |
| Archiver (`archiving/parquet_archiver.py`) | connects directly via asyncpg, does **not** import the app | `asyncio, logging, os, dataclasses, datetime, pathlib, asyncpg, pyarrow` (`:16-27`) — no `cloud_api` import | ✅ |

### 6.2 Dependency Violations

None found. Notably the archiver is kept off the served package's import graph by construction: it sits outside `src/` and is put on the path only for tests (`pyproject.toml:24-26`), so the runtime image's venv cannot import it into the app.

### 6.3 Module Placement

| Module | Designed role | Actual location | Status |
|--------|---------------|-----------------|--------|
| App assembly | entrypoint | `src/cloud_api/main.py` | ✅ |
| Infrastructure (pool) | shared | `src/cloud_api/db.py` | ✅ |
| Contracts | shared | `src/cloud_api/schemas.py` (+ `CollectorStatusEvent` in `routers/status.py`, exactly as §3.3 states) | ✅ |
| Route handlers | per domain | `src/cloud_api/routers/` | ✅ |
| Schema | migration | `cloud-api/migrations/` | ✅ |
| Batch job | outside the served package | `cloud-api/archiving/` | ✅ |
| Dev tooling | outside the served package | `cloud-api/scripts/` | ✅ |

### 6.4 Architecture Score

```
┌──────────────────────────────────────────────────────┐
│  Module Layering Compliance: 100%                    │
├──────────────────────────────────────────────────────┤
│  ✅ Correct placement:        9 / 9 modules           │
│  ⚠️ Dependency violations:    0                       │
│  ❌ Wrong layer:              0                       │
└──────────────────────────────────────────────────────┘
```

---

## 7. Convention Compliance — design §10

*(Python conventions per design §10; the template's npm/TSX naming and import-order checks do not apply and are omitted.)*

| # | §10 convention | Verification | Status |
|---|----------------|--------------|--------|
| 99 | Language: Python 3.12, PEP 604 unions, type-annotated handlers | PEP 604 used throughout (`db.py:5`, `schemas.py:14`, `routers/ingestion.py:12`, `archiving/parquet_archiver.py:71`); every handler annotated (`main.py:23`, `routers/ingestion.py:13`, `routers/config.py:10`, `routers/status.py:19`). **But** `pyproject.toml:5` declares `requires-python = ">=3.11"`, not 3.12 — the floor is one minor version below what the design states (the Dockerfile does pin `python:3.12-slim`, `Dockerfile:2, 18`) | 🔵 Changed |
| 100 | One `APIRouter` per domain with a versioned `prefix` and a `tags=[…]` label | `routers/ingestion.py:6` `prefix="/ingest/v1", tags=["ingestion"]`; `routers/config.py:6`; `routers/status.py:9` | ✅ Match |
| 101 | PascalCase Pydantic models; `Literal` for closed value sets | `schemas.py:7, 19, 23, 37, 41, 46, 61`; `routers/status.py:12`; `Literal` at `schemas.py:15, 27, 32` and `routers/status.py:14` | ✅ Match |
| 102 | snake_case functions/vars; module-private helpers prefixed `_` | `init_pool`, `get_tag_config`, `find_eligible_partitions`; privates `_delete_partition`, `_main`, `_provision`, `_derive_test_dsn`, `_migration_statements`, `_reading`, `_alarm`, `_insert_readings` | ✅ Match |
| 103 | Constants UPPER_SNAKE_CASE (`RETENTION`, `COLUMNS`, `PARQUET_SCHEMA`) | `archiving/parquet_archiver.py:31, 34, 47`; also `DEV_DATABASE_URL`, `MIGRATION_SQL`, `TEST_DSN`, `TRUNCATE_TABLES` (`tests/conftest.py:28, 29, 47, 53`), `DEV_DSN`, `TAGS` (`scripts/seed_dev_tag_config.py:22, 24`) | ✅ Match |
| 104 | SQL: uppercase keywords, positional `$n` parameters, one statement per call | All call sites conform (`routers/ingestion.py:43-52, 88-99`; `routers/config.py:21-28, 32`; `routers/status.py:26-29`; `archiving/parquet_archiver.py:115-120, 131-137, 155, 199`) | ✅ Match |
| 105 | Migrations numbered and idempotent | `001_init.sql` — `IF NOT EXISTS` on every `CREATE` (L4, 7, 34, 48, 75, 95, 100, 117, 122, 126, 134); `if_not_exists => TRUE` on `create_hypertable` (L29) and both CAGG policies (L66, 72) | ✅ Match |
| 106 | Env vars: `DATABASE_URL`, `TEST_DATABASE_URL`, `PARQUET_ARCHIVE_ROOT` | Exactly these three, no others: `db.py:11`, `tests/conftest.py:33`, `archiving/parquet_archiver.py:82`; `docker-compose.yml:43` supplies `DATABASE_URL`; `deploy/.env.example` covers the separate edge component | ✅ Match |
| 107 | Timestamps: `TIMESTAMPTZ` in the DB, timezone-aware UTC in Python | All 8 DB timestamp columns are `TIMESTAMPTZ`; Python uses `tzinfo=timezone.utc` throughout (`archiving/parquet_archiver.py:91-97`) and `pa.timestamp("us", tz="UTC")` (`:52, 58`) | ✅ Match |

### 7.1 Convention Score

```
┌──────────────────────────────────────────────────────┐
│  Convention Compliance: 88.9%   (8 / 9)              │
├──────────────────────────────────────────────────────┤
│  Naming (models/functions/constants):  100%          │
│  Router structure:                     100%          │
│  SQL style:                            100%          │
│  Migration idempotence:                100%          │
│  Env var set:                          100%          │
│  Timestamp handling:                   100%          │
│  Language version pin:                  3.11 floor   │
└──────────────────────────────────────────────────────┘
```

---

## 8. Deployment — design §11

| # | Design item | Implementation evidence | Status |
|---|-------------|-------------------------|--------|
| 108 | Two-stage image; deps installed into `/opt/venv` in the builder; runtime copies the venv only | `Dockerfile:2, 9-15, 18, 26` | ✅ Match |
| 109 | Runs as non-root uid 10001; no source tree in the runtime image | `Dockerfile:24, 29`; stage 2 has no `COPY src` | ✅ Match |
| 110 | Exposes 8000; entrypoint `uvicorn cloud_api.main:app --host 0.0.0.0 --port 8000` | `Dockerfile:31, 33` — expressed as `CMD` rather than `ENTRYPOINT`; the command line itself is verbatim as designed | ✅ Match |
| 111 | `timescaledb`: `timescale/timescaledb:latest-pg16`, named volume, `001_init.sql` mounted into `docker-entrypoint-initdb.d`, `pg_isready` healthcheck, host port 5442 | `docker-compose.yml:19, 24-25, 29, 31, 33`; volume declared `:60-61` | ✅ Match |
| 112 | `cloud-api` gated on `service_healthy`, with its own `/healthz` healthcheck | `docker-compose.yml:44-46`, `:49-57` | ✅ Match |
| 113 | Both services `restart: unless-stopped` | `docker-compose.yml:38, 58` | ✅ Match |
| 114 | Edge collector deliberately absent; runs via `deploy/docker-compose.edge.yml` | No `collector` service in the compose file (only `timescaledb`, `cloud-api`); rationale in the header comment `:9-12`; `deploy/docker-compose.edge.yml` exists | ✅ Match |
| 115 | Migrations run via the postgres entrypoint on first startup only; re-applying requires `down -v && up` | `docker-compose.yml:26-29` states exactly this | ✅ Match |
| 116 | Archival job = scheduled invocation of `parquet_archiver.py` against `DATABASE_URL`, exiting non-zero when any partition was skipped | `archiving/parquet_archiver.py:272, 278, 281-282`. (No scheduler/cron unit is committed — consistent with §11's wording, which describes an external invocation) | ✅ Match |
| 117 | — | `cloud-api` publishes host port `8000:8000` (`docker-compose.yml:47-48`); §11 mentions `EXPOSE 8000` on the image and port 5442 for the DB, but never states that the API is published on the host | ⚠️ Missing in design |

---

## 9. Overall Score

```
┌──────────────────────────────────────────────────────┐
│  Overall Score: 93 / 100                             │
├──────────────────────────────────────────────────────┤
│  Design Match:        92 points  (105/114 items)     │
│  Code Quality:        92 points  (2 minor smells)    │
│  Security:            90 points  (dev creds in repo, │
│                                   auth deferred)     │
│  Testing:             88 points  (1 documented       │
│                                   behaviour untested)│
│  NFR mechanisms:      100 points (all 7 present)     │
│  Module Layering:     100 points                     │
│  Convention:          89 points  (3.11 vs 3.12 pin)  │
└──────────────────────────────────────────────────────┘
```

**Verdict:** the ≥ 90% gate is passed. Design and implementation agree closely; every divergence found is a *documentation* inaccuracy or a small omission, not a missing capability. The two hardest safety invariants in the design (§5 #2 hypertable PK, §5 #8 verify-before-delete) are both implemented *and* regression-tested.

---

## 10. Recommended Actions

### 10.1 Immediate (correctness of the record)

| Priority | Item | Location | Action |
|----------|------|----------|--------|
| 🔴 1 | `current_version` "0 when never advanced" is false — it is `1` | design §4.2; `routers/config.py:36`; `tests/test_config_db.py:44-50` | Fix the design sentence — the code and test are right and deliberate. If a literal `0` for "never configured" is genuinely wanted, read `is_called` from `pg_sequences`, but that is a behaviour change and would break the existing test |
| 🔴 2 | `idempotency-key` documented as "logged", never logged | design §4.2; `routers/ingestion.py:12, 63` | Either add the one-line `logger.info` the design promises (cheap, and gives real batch traceability), or drop "and logged" from §4.2. **This is the only item on this list that is a functional decision rather than a doc edit** |

### 10.2 Short-term (within 1 week)

| Priority | Item | Location | Expected impact |
|----------|------|----------|-----------------|
| 🟡 1 | Add a 422 test for invalid alarm `severity` / `ack_status` | `tests/test_ingestion_db.py` | Closes the only documented-but-unverified behaviour |
| 🟡 2 | Remove the unused `VerificationFailed` class (or raise it and let `run_archival_job` catch it) | `archiving/parquet_archiver.py:63-64` | Removes a misleading signal about the archiver's error protocol |
| 🟡 3 | Align the Python floor: `requires-python = ">=3.12"` | `pyproject.toml:5` | Makes the §10 "Python 3.12" claim enforceable, matching the Dockerfile |
| 🟡 4 | Soften §7's "no secrets in code" to "no *production* secrets; dev credentials are intentionally literal in `tests/conftest.py:28`, `scripts/seed_dev_tag_config.py:22`, `docker-compose.yml`" | design §7 | Keeps the security section audit-truthful |

### 10.3 Long-term (backlog)

| Item | Location | Notes |
|------|----------|-------|
| Cap `ReadingsBatch.readings` / `AlarmsBatch.alarms` length | `schemas.py:19, 37` | Currently unbounded; matters once the network trust boundary is replaced by real authN (`TODOS.md` #2) |
| Test `_main()`'s exit code | `archiving/parquet_archiver.py:270-278` | The scheduler-alarm contract in §6/§11 rests entirely on it |
| Configure a coverage tool | `pyproject.toml` | Would let future analyses report a real coverage figure instead of omitting one |
| Index the "Issue N" references used in handler docstrings | design doc | The numbers at `routers/ingestion.py:17`, `routers/config.py:11`, `routers/status.py:20` point at an external review thread |

---

## 11. Design Document Updates Needed

- [ ] §4.2 — correct the `current_version` never-advanced value from `0` to `1` (a fresh sequence reports `last_value = 1`, `is_called = false`).
- [ ] §4.2 — either drop "and logged" from the idempotency-key description or implement the logging.
- [ ] §4.2 — note that `idempotency-key` is accepted on `/ingest/v1/alarms` too, not only `/readings`.
- [ ] §3.1 — add `CREATE EXTENSION IF NOT EXISTS timescaledb;` to the DDL listing.
- [ ] §6 — mention (or delete) the `VerificationFailed` exception class, and state that failure is signalled via `PartitionResult.verified`, not by an exception.
- [ ] §7 — qualify "No secrets in code" to exclude the committed dev credentials.
- [ ] §10 — reconcile "Python 3.12" with `pyproject.toml`'s `>=3.11`.
- [ ] §11 — record that `cloud-api` publishes host port 8000.
- [ ] §8 — add "invalid alarm `severity`/`ack_status` is 422" to the key-cases list once the test exists.

---

## 12. Next Steps

- [ ] Apply the nine design-document corrections in §11 (documentation-only; no implementation change is required to hold the gate).
- [ ] Implement or delete the idempotency-key logging (§10.1 #2) — the only functional decision this analysis leaves open.
- [ ] Add the missing alarm-enum 422 test.
- [ ] Match Rate is 92.1% (≥ 90%), so `/pdca iterate` is not required — proceed to `/pdca report cloud_api`.

---

## Related Documents

- Design: [cloud_api.design.md](../02-design/features/cloud_api.design.md)
- Planning: [smart-data-collector-plan.md](../smart-data-collector-plan.md)
- Deferred work: [TODOS.md](../../TODOS.md)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-08-03 | Initial gap analysis — 114 items verified against read source | bkit gap-detector |
