# raw_data Design Document

> **Summary**: "원본 데이터 수집" (Raw Data Collection) backend — a new domain router inside the existing
> `cloud_api` FastAPI service that lets project staff register raw, pre-analysis data sources (uploaded
> Excel/Word/scanned-PDF files, equipment layout entries, and DB/SQL connection metadata) into a single
> catalog table, with real file storage on local disk and zero live DB/network connectivity to any source.
>
> **Project**: Smart Data Collector
> **Version**: 0.1.0
> **Author**: Architect (derived from `docs/01-plan/features/raw_data.plan.md`)
> **Date**: 2026-08-05
> **Status**: Draft v1 — ready for implementation
> **Planning Doc**: [raw_data.plan.md](../../01-plan/features/raw_data.plan.md)
> **Sibling Doc**: [cloud_api.design.md](./cloud_api.design.md) (conventions this doc extends)

### Pipeline References

| Phase | Document | Status |
|-------|----------|--------|
| Phase 1 | Schema Definition | This doc §3 and `cloud-api/migrations/003_raw_data.sql` |
| Phase 2 | Coding Conventions | This doc §10 — extends `cloud_api.design.md` §10, no deviations |
| Phase 3 | Mockup | N/A — dashboard tab UI is a separate frontend workstream, out of scope here |
| Phase 4 | API Spec | This doc §4 |

---

## 1. Overview

### 1.1 Design Goals

- Let staff register five kinds of raw data source through one consistent catalog, matching the plan's
  split: **file-backed types (Excel/Word/scanned PDF) really upload, save, and can be downloaded**;
  **equipment layout is metadata registration with an optional file attachment**; **DB/SQL is metadata
  registration only, with no code path that can ever open a DB connection to the registered source.**
- Reuse the existing `cloud_api` service, DB pool, CORS config, and deployment shape rather than standing
  up new infrastructure.
- Store uploaded files on local disk under a dedicated, configurable root — no object storage, no new
  service dependency.
- Keep the catalog queryable enough for the dashboard's summary counts, type filter, and name/tag search
  without needing a search index or extra infrastructure (low volume, project-setup-time activity).
- Make "no real DB/scanner connectivity" a structural property of the code, not just a UI omission — see
  §5 decision 6 and §7.

### 1.2 Design Principles (extends cloud_api.design.md §1.2)

- **Thin router, explicit SQL, no ORM** — same as every other `cloud_api` router.
- **One row per stored file.** A multi-file upload creates N catalog rows sharing the submitted metadata,
  not one row with an embedded file list — keeps list/download semantics identical to the single-file case.
- **Storage path is derived, never trusted from the client.** The on-disk filename is generated
  server-side (UUID + sanitized extension); the client-supplied filename is kept only as a display/download
  label, never as a path component.
- **Registration ≠ connection.** For `db_sql`, the entire feature surface is "write these strings to a
  table." No driver, no socket, no "test connection" button — enforced by what dependencies this feature
  is allowed to add (§5 decision 6).

---

## 2. Architecture

### 2.1 Decision: extend `cloud_api`, do not create a new service

**Decision: extend.** New router `routers/raw_data.py` inside the existing `cloud_api` FastAPI app,
alongside `ingestion`, `config`, `status`, `readings`, `alarms`.

Rationale:

- The dashboard already talks to one API origin (`VITE_API_BASE_URL`); a second service means a second
  origin, a second CORS entry, a second health check, a second compose entry, and a second deploy
  artifact — pure overhead for a feature whose whole backend is "insert a row, save a file."
- `cloud_api` already hosts five domain routers behind the `/도메인/v1` prefix convention specifically so
  new domains can be added this way (see `cloud_api.design.md` §9 module layout) — this is exactly the
  extension point that convention exists for.
- It shares the same asyncpg pool (`db.get_pool()`), the same TimescaleDB instance (a plain, non-hypertable
  table lives in the same database fine), and the same container image/base path — no new infra to stand up
  in this environment, which matches the plan's hard constraint of "no new infrastructure."
- The one real tradeoff — mixing bursty file-upload traffic with latency-sensitive telemetry ingestion in
  one process — is acceptable at this feature's expected volume (a handful of registrations per session,
  not a continuous stream). If upload volume or file size later grows enough to threaten ingestion
  latency, extracting `raw_data` into its own service is a clean follow-up: the table has no foreign keys
  into the telemetry tables, so it can be moved without touching the rest of the schema.

Rejected alternative: separate `raw-data-api` service. Would require its own Dockerfile, compose entry,
port, CORS origin, and health check for no functional gain at this scale — rejected.

### 2.2 Component Diagram

```
   Dashboard ("원본 데이터 수집" tab)
        │  POST   /raw-data/v1/sources          (multipart: fields + 0..N files)
        │  GET    /raw-data/v1/sources          (?source_type=&search=&limit=&offset=)
        │  GET    /raw-data/v1/sources/summary
        │  GET    /raw-data/v1/sources/{id}
        │  GET    /raw-data/v1/sources/{id}/download
        │  DELETE /raw-data/v1/sources/{id}
        ▼
  ┌──────────────────────────────────────────────────────────┐
  │ FastAPI app (cloud_api.main) — unchanged routers untouched │
  │  ┌────────────┐ ┌────────┐ ┌────────┐ ┌──────────────┐   │
  │  │ ingestion  │ │ config │ │ status │ │  raw_data    │◄── new
  │  │  router    │ │ router │ │ router │ │   router     │   │
  │  └────────────┘ └────────┘ └────────┘ └──────┬───────┘   │
  │                                                │           │
  │                          raw_data_storage.py ──┘           │
  │                          (path build, sanitize, size guard)│
  └──────────────────┬─────────────────────────┬───────────────┘
                     │ db.get_pool()           │ filesystem
                     ▼                          ▼
       ┌───────────────────────────┐   ┌──────────────────────────────┐
       │ TimescaleDB                │   │ RAW_DATA_STORAGE_ROOT         │
       │  raw_data_sources (plain   │   │  {source_type}/{yyyy}/{mm}/   │
       │  table, not a hypertable)  │   │   {uuid4}__{safe_filename}    │
       └───────────────────────────┘   └──────────────────────────────┘
```

### 2.3 Data Flow

```
Create (file-backed types):
  multipart POST → validate source_type + required Form fields
  → for each uploaded file: validate extension allowlist for source_type
  → stream file to disk under RAW_DATA_STORAGE_ROOT, abort + 400 if size exceeds limit
  → INSERT one raw_data_sources row per saved file, file_path relative to root
  → 201 {"sources": [...]}

Create (equipment_layout, 0 or 1 file):
  same path; layout_* Form fields required; file optional

Create (db_sql, 0 files always):
  multipart POST with no files → reject with 400 if any file present
  → INSERT one row with db_* fields, file_path = NULL
  → 201 {"sources": [...]}

List / Summary:
  GET → SELECT ... WHERE source_type = $1 (optional) AND (name ILIKE $2 OR equipment_tag ILIKE $2) (optional)
      ORDER BY created_at DESC LIMIT/OFFSET
  GET .../summary → SELECT source_type, count(*) GROUP BY source_type, zero-fill missing types in Python

Download:
  GET .../{id}/download → SELECT file_path, file_name FROM raw_data_sources WHERE id = $1
      → 404 if row missing or file_path IS NULL
      → 404 if file_path does not exist on disk (deleted out-of-band)
      → FileResponse, Content-Disposition uses the stored display file_name, not the on-disk name

Delete:
  DELETE .../{id} → DELETE ... RETURNING file_path → best-effort os.remove() (ignore ENOENT) → 204
```

### 2.4 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `routers/raw_data.py` | `db`, `schemas`, `raw_data_storage` | 6 endpoints listed in §4.1 |
| `raw_data_storage.py` | `os`, `uuid`, `pathlib`, env config | Path building, filename sanitization, streamed size-limited save, allowlist validation |
| `schemas.py` (additions) | `pydantic` | Wire contracts — see §3.3 |
| `migrations/003_raw_data.sql` | none (plain table, no TimescaleDB extension needed) | Schema for `raw_data_sources` |
| `main.py` (one-line addition) | `routers.raw_data` | `app.include_router(raw_data.router)` |
| `pyproject.toml` (one addition) | `python-multipart` | Required by FastAPI/Starlette to parse `multipart/form-data` (Form + UploadFile) — omitting it makes every request to this router fail at runtime |

---

## 3. Data Model

### 3.1 Design: one table, nullable per-type columns — not per-type child tables, not a JSONB blob

A single `raw_data_sources` table holds all five source types, with common columns always populated and
type-specific columns nullable, gated by a `CHECK` constraint on `source_type`. See §5 decision 2 for why
this beats per-type child tables or a JSONB metadata column at this scope.

### 3.2 Migration: `cloud-api/migrations/003_raw_data.sql`

```sql
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
```

No continuous aggregates, no hypertable — this table is read with plain `SELECT`s; volume at this feature's
scale (tens to low thousands of rows) never needs them.

### 3.3 Wire Contracts (Pydantic additions to `schemas.py`)

```python
RawDataSourceType = Literal["excel", "word", "scanned_pdf", "equipment_layout", "db_sql"]

class RawDataSource(BaseModel):
    id: int
    source_type: RawDataSourceType
    name: str
    description: str | None = None
    equipment_tag: str | None = None
    registered_by: str
    status: Literal["registered"]
    file_name: str | None = None
    file_size_bytes: int | None = None
    content_type: str | None = None
    layout_line_name: str | None = None
    layout_equipment_name: str | None = None
    layout_location_desc: str | None = None
    db_kind: str | None = None
    db_host: str | None = None
    db_port: int | None = None
    db_query_text: str | None = None
    created_at: datetime
    # file_path is intentionally NOT exposed on the wire — it is a server-side
    # storage detail; the download endpoint is the only way to reach the bytes.

class RawDataSourceCreateResponse(BaseModel):
    sources: list[RawDataSource]

class RawDataSourceListResponse(BaseModel):
    sources: list[RawDataSource]
    total: int

class RawDataSourceSummary(BaseModel):
    excel: int
    word: int
    scanned_pdf: int
    equipment_layout: int
    db_sql: int
    total: int
```

`file_path` is deliberately excluded from every response model — the dashboard has no legitimate use for
a server filesystem path, and not emitting it removes a class of information-disclosure concern for free.

---

## 4. API Specification

### 4.1 Endpoint List

| Method | Path | Description | Success | Auth |
|--------|------|-------------|---------|------|
| POST | `/raw-data/v1/sources` | Register 1 source metadata + 0..N files (multipart) | 201 `RawDataSourceCreateResponse` | none (matches existing routers) |
| GET | `/raw-data/v1/sources` | List, filter by `source_type`/`search`, paginated | 200 `RawDataSourceListResponse` | none |
| GET | `/raw-data/v1/sources/summary` | Registered count per `source_type` | 200 `RawDataSourceSummary` | none |
| GET | `/raw-data/v1/sources/{id}` | Full metadata for one source | 200 `RawDataSource` | none |
| GET | `/raw-data/v1/sources/{id}/download` | Stream the stored file | 200 file | none |
| DELETE | `/raw-data/v1/sources/{id}` | Remove row + best-effort delete file on disk | 204 | none |

Same trust boundary as the rest of `cloud_api`: authentication is out of scope until `TODOS.md` #2 lands
service-wide; this feature does not introduce its own auth mechanism ahead of that.

### 4.2 Detailed Specification

#### `POST /raw-data/v1/sources`

`Content-Type: multipart/form-data`. Common `Form` fields on every request:

| Field | Required | Notes |
|---|---|---|
| `source_type` | yes | one of the 5 enum values |
| `name` | yes | applied to every row created by this request (see §5 decision 3) |
| `registered_by` | yes | free text |
| `description` | no | |
| `equipment_tag` | no | |
| `layout_line_name`, `layout_equipment_name` | required iff `source_type = equipment_layout` | |
| `layout_location_desc` | optional, `equipment_layout` only | |
| `db_kind`, `db_host`, `db_query_text` | required iff `source_type = db_sql` | |
| `db_port` | optional, `db_sql` only | |
| `files` | see table below | zero or more `UploadFile` parts named `files` |

Per-type file requirement and extension allowlist (case-insensitive on extension):

| `source_type` | file count | allowed extensions |
|---|---|---|
| `excel` | ≥ 1 | `.xlsx`, `.xls` |
| `word` | ≥ 1 | `.docx`, `.doc` |
| `scanned_pdf` | ≥ 1 | `.pdf`, `.png`, `.jpg`, `.jpeg` |
| `equipment_layout` | 0 or 1 | `.pdf`, `.png`, `.jpg`, `.jpeg`, `.xlsx`, `.dwg`, `.dxf` |
| `db_sql` | must be 0 | n/a — presence of any file is a validation error |

**Response 201** (2 files uploaded as `excel`):
```json
{
  "sources": [
    {"id": 41, "source_type": "excel", "name": "3월 정기점검 성적서", "file_name": "3월_점검표.xlsx",
     "file_size_bytes": 48213, "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
     "registered_by": "홍길동", "status": "registered", "created_at": "2026-08-05T02:11:03Z", "...": "..."},
    {"id": 42, "source_type": "excel", "name": "3월 정기점검 성적서", "file_name": "3월_점검표_2공장.xlsx",
     "...": "..."}
  ]
}
```

`db_sql` example (no files, connection metadata only):
```json
{
  "sources": [
    {"id": 43, "source_type": "db_sql", "name": "레거시 MES 생산실적", "db_kind": "MSSQL",
     "db_host": "10.20.30.40", "db_port": 1433,
     "db_query_text": "SELECT * FROM PROD_RESULT WHERE LINE_ID = ?",
     "registered_by": "김철수", "status": "registered", "created_at": "2026-08-05T02:12:40Z"}
  ]
}
```

#### `GET /raw-data/v1/sources?source_type=&search=&limit=&offset=`

- `source_type`: optional enum filter.
- `search`: optional, case-insensitive substring match against `name` OR `equipment_tag`
  (`WHERE name ILIKE $1 OR equipment_tag ILIKE $1`).
- `limit` (`Query(default=50, ge=1, le=200)`), `offset` (`Query(default=0, ge=0)`).
- Ordered `created_at DESC`. `total` in the response is the unfiltered-by-limit match count, for pagination.

#### `GET /raw-data/v1/sources/summary`

No params. Always returns all 5 keys, zero-filled — the dashboard's 5 summary cards render directly off
this without a client-side merge step:
```json
{"excel": 3, "word": 1, "scanned_pdf": 0, "equipment_layout": 2, "db_sql": 1, "total": 7}
```

#### `GET /raw-data/v1/sources/{id}`

404 if `id` does not exist. Otherwise the full `RawDataSource` row (drives the "상세 보기" panel).

#### `GET /raw-data/v1/sources/{id}/download`

- 404 if `id` does not exist, or the row's `source_type` has no file (`file_path IS NULL`), or the file is
  missing on disk (deleted out-of-band — do not 500).
- `Content-Disposition: attachment` carrying the stored display name (not the on-disk UUID-prefixed name).
  For an ASCII-only name this renders as `filename="<file_name>"`; Starlette's `FileResponse` automatically
  switches non-ASCII names (e.g. Korean) to the RFC 5987 form `filename*=utf-8''<percent-encoded>` instead —
  both are the same file_name value, just encoded per the standard for what the browser needs to decode it
  correctly. QA confirmed 2026-08-05 (raw_data.analysis.md 이슈 #3) this is expected `FileResponse` behavior,
  not a defect.
- `Content-Type`: use the stored `content_type` if it's a recognized safe type for the extension, otherwise
  fall back to `application/octet-stream` — never let a client-supplied `Content-Type` cause a browser to
  render an uploaded file inline (see §7).

#### `DELETE /raw-data/v1/sources/{id}`

`DELETE ... WHERE id = $1 RETURNING file_path`. 404 if no row matched. If `file_path` was non-null, attempt
`os.remove()`; a missing file (`FileNotFoundError`) is not an error (the row is already gone from the
catalog, which is the source of truth) — any other OS error is logged but still returns 204, since leaving
an orphaned file is recoverable but leaving a stuck catalog row is not.

### 4.3 Error Responses

| Code | Cause | Notes |
|------|-------|-------|
| 400 | Missing required `Form` field for the given `source_type`; disallowed file extension; wrong file count for type (0 files for a file-required type, ≥1 file for `db_sql`); file exceeds `RAW_DATA_MAX_FILE_SIZE_BYTES` | Explicit `HTTPException(400, detail=...)` with a specific, user-facing message per plan's error-handling acceptance criterion |
| 404 | Unknown `id` on detail/download/delete; download of a source with no file; file missing on disk | |
| 422 | Malformed `source_type` enum value, non-integer `id` path param, out-of-range `limit`/`offset` | FastAPI/Pydantic default |
| 500 | DB unavailable / pool not initialized | Same as rest of `cloud_api` |

---

## 5. Key Design Decisions

| # | Decision | Rationale | Rejected alternative |
|---|----------|-----------|----------------------|
| 1 | Extend `cloud_api` with a new router, not a new service | Same origin, same pool, same deploy artifact; the `/도메인/v1` convention exists precisely for this | Standalone `raw-data-api` service |
| 2 | One table, nullable per-type columns, one `CHECK` gating file-field presence by type | Typed columns keep `CHECK` constraints and simple `SELECT`s working; low column count (5 types × few fields each) doesn't justify normalization overhead | Per-type child tables (more joins, more migrations); JSONB metadata blob (loses `CHECK`-level validation and easy `ILIKE`/filter queries) |
| 3 | One catalog row per uploaded file; a multi-file request creates N rows sharing one `name` | List/download semantics stay identical whether 1 or 10 files were submitted together; no separate "batch" concept to build or explain | Row-per-batch with an embedded file array (needs its own download-as-zip and detail UI) |
| 4 | On-disk filename is `{uuid4}__{sanitized-original}`; DB stores a path relative to `RAW_DATA_STORAGE_ROOT` | UUID prefix removes collision and path-traversal risk from client-controlled filenames; relative storage means the root can move (dev → container path) without a data migration | Storing the client filename verbatim as the on-disk name (collision + traversal risk); storing an absolute path (breaks portability) |
| 5 | Extension allowlist per `source_type`, checked on filename only — no magic-byte/content sniffing | Matches the plan's explicit out-of-scope item ("파일 형식 심층 검증(매직 바이트 검사 등)"); keeps validation trivial to review | Magic-byte sniffing (plan explicitly excludes it for 1차) |
| 6 | `db_sql` registration touches nothing but the DB row — no DB driver library (`pyodbc`/`pymssql`/`sqlalchemy`+driver/`cx_Oracle`/etc.) is added to `pyproject.toml`, and no code path constructs a connection string for a *registered* source | Makes "no real connection" a structural property, not just a missing button — a reviewer can verify it by checking the dependency list, and a test can assert `db_kind`/`db_host`/`db_query_text` are stored-and-returned-only (see §8) | A "connection test" endpoint gated behind a feature flag (plan explicitly forbids the code existing at all, flagged or not) |
| 7 | `status` column exists now, constrained to the single value `'registered'` | A later review/approval workflow becomes an additive `CHECK` change, not a new column + backfill migration | Omitting `status` until it's needed |
| 8 | Upload size is enforced by counting bytes while streaming to disk (abort + delete partial file + 400 past the limit), not by trusting the `Content-Length` header | `Content-Length` can be absent or spoofed on `multipart/form-data`; only the actual byte count read is trustworthy | Rejecting based on the declared header alone |
| 9 | `file_path` is never included in any API response | Removes a filesystem-path disclosure surface for zero functional cost — the download endpoint is the only sanctioned path to file bytes | Exposing `file_path` for "debuggability" |

---

## 6. File Storage Strategy

- **Root**: `RAW_DATA_STORAGE_ROOT` env var. Suggested defaults — local dev (`uvicorn` run directly from
  `cloud-api/`): `./data/raw-uploads` (add to `.gitignore`); containerized: `/data/raw-uploads`, backed by a
  new named volume `raw_data_uploads` in `docker-compose.yml` (mirrors how `timescale_data` is already a
  named volume for the DB — no new volume *kind*, just one more of the same pattern).
- **Path layout**: `{RAW_DATA_STORAGE_ROOT}/{source_type}/{yyyy}/{mm}/{uuid4}__{sanitized_filename}`.
  Day-granularity (as `parquet_archiver` uses) is unnecessary here — month buckets keep directory listing
  sane at this feature's volume without over-fragmenting.
- **Sanitization**: strip all path separators and non-`[A-Za-z0-9._-]` characters from the client filename
  before using it in the on-disk name (display name in the DB keeps the original, untouched, for the UI and
  `Content-Disposition`); cap the sanitized portion at ~150 chars; always keep the original extension
  (already validated against the allowlist).
- **Size limit**: `RAW_DATA_MAX_FILE_SIZE_BYTES` env var, default `52428800` (50 MB) — generous enough for a
  scanned multi-page PDF, small enough that a single upload can't exhaust disk quickly. Enforced by reading
  the upload in chunks and aborting the moment the running total exceeds the limit (see §5 decision 8).
- **Directory creation**: `Path(...).parent.mkdir(parents=True, exist_ok=True)` per save — no pre-provisioning
  step required, consistent with the Parquet archiver's own `YYYY/MM/DD.parquet` layout convention.
- **No object storage.** Local disk only, per the plan's explicit constraint — this is a straight rename of
  the same pattern `parquet_archiver.py` already uses for cold-tier Parquet files, just for raw uploads
  instead of exported readings.

---

## 7. Security Considerations

- **Extension allowlist per `source_type`** at the API boundary (§4.2 table) — the only format gate, by
  design (deep content validation is explicitly out of scope per the plan).
- **No path traversal**: the on-disk filename is server-generated (`uuid4() + sanitized suffix`); the
  client-supplied filename never becomes a path component, only a stored display string.
- **No stored-content execution risk on download**: `Content-Type` on download responses is chosen from a
  small safe set keyed off the validated extension, never blindly echoing the client's original
  `Content-Type` header — this prevents an uploaded file from being served in a way a browser would render
  inline (e.g., as HTML) even if a client lied about its MIME type at upload time.
- **Size-bounded, streamed writes** (§5 decision 8) — bounds worst-case disk usage per request regardless of
  what the client claims about size.
- **No credential fields exist for `db_sql`.** The schema has `db_host`/`db_port`/`db_kind`/`db_query_text`
  and deliberately no `db_username`/`db_password`/connection-string field — there is nothing sensitive to
  encrypt because nothing sensitive is meant to be stored (matches the plan's "자격증명 암호화 저장소" being
  explicitly out of scope).
- **No outbound connectivity added.** No DB driver dependency, no socket code, no "test connection" affordance
  anywhere in this feature — verifiable by diffing `pyproject.toml` and by the absence-of-networking test in
  §8.
- **No secrets in code**: `RAW_DATA_STORAGE_ROOT` and `RAW_DATA_MAX_FILE_SIZE_BYTES` come from the
  environment, same pattern as `DATABASE_URL`/`PARQUET_ARCHIVE_ROOT`.
- **Container hardening**: no change needed — the existing non-root uid 10001 runtime user (`Dockerfile`)
  already applies; the mounted upload volume just needs to be writable by that uid.
- **Deferred, same as the rest of `cloud_api`**: authentication/mTLS (`TODOS.md` #2), rate limiting.

---

## 8. Test Plan

| Type | Target | Tool |
|------|--------|------|
| Integration (DB) | Create (all 5 types), list filters, search, summary counts, detail, delete | pytest + asyncpg + httpx `ASGITransport`, following `tests/conftest.py`'s existing pattern |
| Integration (storage) | Upload → file exists on disk at the expected relative path; download returns the right bytes and filename; delete removes both row and file | pytest, `RAW_DATA_STORAGE_ROOT` pointed at `tmp_path` per test |
| Validation | Wrong extension per type rejected 400; 0 files for a file-required type rejected 400; ≥1 file for `db_sql` rejected 400; oversized file rejected 400 and no partial file left behind | pytest |
| Structural / regression guard | `db_sql` create+list round-trip never touches network — assert no DB-driver package (`pyodbc`, `pymssql`, `cx_Oracle`, `mysqlclient`, `pymysql`, `psycopg`/`psycopg2` beyond the existing `asyncpg`) appears in `pyproject.toml`'s dependency list | pytest reading `pyproject.toml` — cheap, catches an accidental future regression of decision §5.6 |
| Error path | Download of unknown id → 404; download of a `db_sql` row (no file) → 404; detail of unknown id → 404 | pytest |

**Test isolation requirement (extends the existing one in `cloud_api.design.md` §8):** in addition to never
writing to the dev database, storage tests must never write to the real `RAW_DATA_STORAGE_ROOT` — a fixture
overrides the env var to a pytest `tmp_path` for the duration of each test, mirroring how `conftest.py`
already redirects `DATABASE_URL` to a dedicated test database before the app is imported.

---

## 9. Module Layout

| Module | Responsibility |
|--------|---------------|
| `src/cloud_api/routers/raw_data.py` | 6 endpoints in §4.1 — thin, delegates saving/validation to `raw_data_storage` |
| `src/cloud_api/raw_data_storage.py` | Path building, filename sanitization, extension allowlist per type, streamed size-limited save, delete-on-disk helper |
| `src/cloud_api/schemas.py` (additions) | `RawDataSource`, `RawDataSourceCreateResponse`, `RawDataSourceListResponse`, `RawDataSourceSummary` — §3.3 |
| `src/cloud_api/main.py` (one-line addition) | `app.include_router(raw_data.router)` |
| `migrations/003_raw_data.sql` | `raw_data_sources` table + index — §3.2 |
| `tests/test_raw_data_db.py` | Integration tests — create/list/summary/detail/delete against the test DB |
| `tests/test_raw_data_storage.py` | Storage helper unit/integration tests against `tmp_path` |
| `tests/conftest.py` (additions) | `TRUNCATE_TABLES` gains `raw_data_sources`; a new fixture redirects `RAW_DATA_STORAGE_ROOT` to `tmp_path` |
| `pyproject.toml` (addition) | `python-multipart` dependency |
| `docker-compose.yml` (additions) | `raw_data_uploads` named volume; `cloud-api` service gains that volume mount + `RAW_DATA_STORAGE_ROOT` env var |

Layering matches the existing convention exactly: `raw_data.py` depends on `db`, `schemas`, and
`raw_data_storage`; it does not import any other router. `raw_data_storage.py` depends on nothing internal
to `cloud_api` beyond stdlib + its own env config, so it stays trivially unit-testable.

---

## 10. Conventions

Follows `cloud_api.design.md` §10 with no deviations. Notable applications for this feature:

| Item | Convention | Applied here |
|------|-----------|--------------|
| Routers | One `APIRouter` per domain, versioned `prefix`, `tags=[...]` | `prefix="/raw-data/v1", tags=["raw_data"]` |
| Schemas | PascalCase Pydantic models; `Literal` for closed value sets | `RawDataSourceType = Literal[...]`, `status: Literal["registered"]` |
| SQL | Uppercase keywords, positional `$n` parameters, one statement per call | Same throughout `raw_data.py` |
| Migrations | Numbered, idempotent (`IF NOT EXISTS`) | `003_raw_data.sql`, `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS` |
| Env vars | Uppercase, service-scoped | `RAW_DATA_STORAGE_ROOT`, `RAW_DATA_MAX_FILE_SIZE_BYTES` |
| Constants | UPPER_SNAKE_CASE | `ALLOWED_EXTENSIONS_BY_TYPE` (module-level dict in `raw_data_storage.py`) |

---

## 11. Deployment

- No new Docker image, no new compose service. `cloud-api`'s existing two-stage `Dockerfile` needs no
  change beyond the `python-multipart` dependency landing in `pyproject.toml` (already installed by the
  existing `RUN pip install .` step).
- `docker-compose.yml`: add a named volume `raw_data_uploads`, mount it at `/data/raw-uploads` on the
  `cloud-api` service, and set `RAW_DATA_STORAGE_ROOT=/data/raw-uploads` in that service's `environment:`
  block — same pattern already used for `timescale_data`.
- Local (non-Docker) dev: running `uvicorn cloud_api.main:app` directly from `cloud-api/` picks up the
  default `./data/raw-uploads` (created on first upload); no manual setup step required, matching how the
  Parquet archiver's output root is likewise created on demand.
- Migration `003_raw_data.sql` is picked up automatically the same way `001_init.sql`/`002_dashboard_read.sql`
  are today: mounted into `docker-entrypoint-initdb.d/` for a fresh volume, or applied manually
  (`psql ... -f cloud-api/migrations/003_raw_data.sql`) against an already-initialized dev database — it is
  purely additive so it's safe to run once against the existing dev DB without a `down -v`.

---

## 12. Non-functional Requirements

| Requirement | Target | Mechanism |
|-------------|--------|-----------|
| Upload size | Up to 50 MB per file (configurable) | Streamed, size-limited save — §5 decision 8 |
| Upload volume | Low — project-setup-time registrations, not continuous telemetry | Plain table, no hypertable/aggregate machinery needed |
| List/search latency | Sub-second at expected row counts (tens–low thousands) | Single index on `(source_type, created_at DESC)`; `ILIKE` search unindexed but acceptable at this volume |
| Download correctness | Bytes returned match bytes stored, byte-for-byte | Local filesystem read, no transformation |
| No live external connectivity | Zero DB/scanner connections ever opened by this feature | Structural — no driver dependency added (§5 decision 6), verified by the test in §8 |
| Data safety | Deleting a catalog row never silently leaves the DB inconsistent with disk in a way the API surfaces | Delete returns 204 regardless of on-disk outcome, but a missing file only ever manifests as a clean 404 on download, never a 500 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-08-05 | Initial design, derived from `raw_data.plan.md` | Architect |
