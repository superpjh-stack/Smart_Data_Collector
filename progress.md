# Smart Data Collector — Progress Snapshot

Saved before `/re-begin`. Resume by reading this file first, then `docs/smart-data-collector-plan.md`
(has the full design + `## GSTACK REVIEW REPORT` at the bottom) and `TODOS.md`.

## Where things stand

Planning/review phase is fully done (gstack `/spec` → `/plan-eng-review` → outside voice). Implementation
is in progress. Task list (via TaskList tool) has 9 tasks; status as of this save:

| # | Task | Status |
|---|---|---|
| 1 | Scaffold repo structure for collector/ and cloud-api/ | completed |
| 2 | Cloud API: TimescaleDB schema + migrations | completed |
| 3 | Cloud API: ingestion endpoints (readings/alarms/tag_config) | completed |
| 4 | Collector: OPC UA client + subscription + gateway heartbeat | completed |
| 5 | Collector: SQLite local buffer with priority queue | completed |
| 6 | Collector: alarm evaluation engine with hysteresis | completed |
| 7 | Collector: cloud transmission client with retry/backoff | completed |
| 8 | Unit tests: hysteresis, seq continuity, deadband, alarm_id determinism | completed — 17/17 passing |
| 9 | Integration test: OPC UA simulator to cloud API round trip | completed — 2/2 passing (19/19 total incl. unit tests) |

## Status: 9-task implementation scope is COMPLETE + automated cloud-api DB tests added (2026-07-15)

Integration test run and passing on 2026-07-14 (`19 passed` — unit + integration). The
originally-scoped 9-task implementation work is fully done. What's deliberately NOT built yet
(not in the 9 tasks, but named in the plan doc — see "Not yet implemented" below) is the next
thing to scope with the user.

## 2026-07-14 dev-server session: real bugs found by actually running the stack

User asked to run the dev servers to test manually (not just pytest). Standing up a real
TimescaleDB + cloud-api + OPC UA simulator + collector chain surfaced 3 real bugs that the
mocked/in-memory test suite had never exercised — this is exactly the gap `progress.md` had
flagged earlier ("no tests written against a real TimescaleDB"). All three are now fixed:

1. **`cloud-api/migrations/001_init.sql` — `readings` table couldn't become a hypertable.**
   `PRIMARY KEY (plc_id, tag_id, seq)` didn't include the partitioning column
   (`timestamp_utc`), and TimescaleDB requires every unique constraint on a hypertable to
   include it. `create_hypertable` failed silently-ish (errored, but migration script kept
   going), so the continuous aggregates never got created either. Fixed: PK is now
   `(plc_id, tag_id, seq, timestamp_utc)` — harmless for the dedup contract since a retransmit
   of the same buffered row always carries the same source timestamp. `ingestion.py`'s
   `ON CONFLICT (plc_id, tag_id, seq)` updated to match the new constraint shape.
2. **`collector/src/collector/opc_client.py` — `client.get_server_date()` doesn't exist.**
   This was the exact risk spot flagged in the previous save ("written from general knowledge
   of the API, not verified against a live import"). Confirmed: installed asyncua 2.0.1's
   `Client` has no such method. The unit/integration tests never caught this because the
   deadband test's heartbeat window never elapsed, and the gateway-down test's assertion
   happened to still pass for the wrong reason (the `AttributeError` itself was triggering the
   down-detection path). Fixed: heartbeat probe now reads the standard OPC UA
   `Server_ServerStatus_CurrentTime` node (`client.get_node(ua.NodeId(ua.ObjectIds.Server_ServerStatus_CurrentTime, 0)).read_value()`).
3. **`collector/tests/test_integration_opc.py` — gateway-down test hung indefinitely under pytest.**
   Root cause: `await server.stop()` calls `asyncio.Server.wait_closed()`, which on Python
   3.12+ also waits for every active connection's handler task to finish (a CPython behavior
   change asyncua 2.0.1 predates) — with our client still attached, that never returns
   (reproduced with a `faulthandler`/`asyncio.all_tasks()` watchdog; a plain `asyncio.run()`
   script without pytest didn't hit it, so it took a while to isolate to pytest specifically).
   Fixed by replacing the test's `server.stop()` calls with a `_force_stop()` helper that
   directly closes the transports and listening socket instead of going through
   `wait_closed()`/`iserver.stop()` — arguably a *more* realistic gateway-outage simulation
   than a graceful stop anyway. `pyproject.toml` also gained
   `asyncio_default_fixture_loop_scope = "function"` (silences a pytest-asyncio deprecation
   warning; did not by itself fix the hang, kept anyway since it's the correct explicit setting).

After all three fixes: `19 passed` (full suite) and a live manual run — TimescaleDB (docker) +
cloud-api (uvicorn) + `collector/dev_opc_simulator.py` (new file, a standalone in-process OPC UA
tag simulator for manual testing, distinct from the pytest fixture) + the real collector —
produced a stable, non-flapping `gateway status: up` and readings actually landing in
`readings` (58 rows confirmed via `SELECT count(*)`) with `transmitted batch: inserted=N
duplicates=0` log lines. No more `gateway status: DOWN`/`up` flapping (that flapping was
entirely caused by bug #2 above).

## 2026-07-15 functional (E2E) test session: full stack driven manually again

User asked to run a functional test and report what was tested and the results — not just
`pytest`. Repeated the 2026-07-14 manual dev-stack drill (TimescaleDB + cloud-api + OPC UA
simulator + collector, all real processes, no mocks) to verify the pipeline still works after
adding the automated cloud-api pytest suite (see above).

**Finding: the new pytest suite's `clean_tables` autouse fixture had wiped the dev `tag_config`
seed row.** `cloud-api/tests/conftest.py` TRUNCATEs all 4 tables (including `tag_config`) before
every test, and the dev DB (`sdc-timescaledb`) is the *same* container the test suite runs
against — so running `pytest` earlier had silently deleted the PLC-01/TEMP_01 config row that
the "already seeded, no need to reseed" note (below, now corrected) assumed was still there. The
collector came up with `tag_config updated: 0 -> 1 (0 tags changed)` — i.e. no tags to subscribe
to — until this was caught and the row was reseeded manually. **Takeaway for future sessions:
after running the cloud-api pytest suite, the dev `tag_config` seed row needs to be reasserted
before a manual functional test** (see `Environment notes` below for the exact INSERT). This is a
one-time environment gotcha, not a product bug — it only happens because dev and test share one
DB instance/container.

Also hit a second-order sequencing issue while fixing the above: the collector's SQLite buffer
persists its `since_version` cursor, so restarting the collector process (not just reseeding the
DB row) was necessary — otherwise the already-advanced cursor could skip over a newly-inserted
config row whose `updated_version` happened to be <= the cursor. Deleted the collector's local
SQLite buffer file and restarted the collector to force a fresh `since_version=0` poll.

**Results after reseeding tag_config and restarting the collector** — full pipeline verified
working end-to-end via direct DB queries (not just log-watching):

| Check | Result |
|---|---|
| Readings ingested | 7 rows landed in `readings`, `transmitted batch: inserted=N duplicates=0` in every collector log line |
| Duplicate rows | 0 (verified via `GROUP BY plc_id,tag_id,seq,timestamp_utc HAVING count(*)>1`) |
| Alarms | 1 alarm raised (`PLC-01:TEMP_01:...:1`, HIGH, `condition='value >= 80.0'`, `triggered_value=94.82`) |
| Hysteresis | Simulator held TEMP_01 at 91-95 (well above `max_alarm=80.0`) continuously — only 1 alarm fired despite the value never dropping, confirming re-trigger suppression works |
| Gateway status | No `gateway_down`/flapping events — stable `up` the whole run (confirms the 2026-07-14 heartbeat fix still holds) |
| cloud-api endpoints | `/healthz`, `/ingest/v1/readings`, `/ingest/v1/alarms` all returned 200 OK during the run |

All manually-started processes (uvicorn, `dev_opc_simulator.py`, collector) were stopped cleanly
at the end of the session. `sdc-timescaledb` was left running per usual. Ran the collector's
existing pytest suite too as a regression check alongside this manual run: `19 passed`, no
regressions from the new cloud-api tests.

### How to spin up the dev stack again

```bash
# 1. TimescaleDB (persisted container name: sdc-timescaledb, port 5442 — 5433/5434 were
#    already taken/flaky on this machine, see Environment notes)
docker start sdc-timescaledb   # if stopped; migration + tag_config seed already applied
#    (durable again as of 2026-08-03 — pytest now uses its own DB, see below)

# 2. cloud-api
cd cloud-api && source .venv/Scripts/activate
export DATABASE_URL="postgresql://sdc:sdc_dev_pw@127.0.0.1:5442/smart_data_collector"
uvicorn cloud_api.main:app --host 127.0.0.1 --port 8000 --reload

# 3. OPC UA simulator (standalone dev tool, not the pytest fixture)
cd collector && source .venv/Scripts/activate
python dev_opc_simulator.py    # exposes PLC-01/TEMP_01 at ns=2;s=Temp01, random-walks the value

# 4. collector
cd collector && source .venv/Scripts/activate
export OPC_ENDPOINT_URL="opc.tcp://127.0.0.1:48401/freeopcua/collector-dev/"
export CLOUD_API_BASE_URL="http://127.0.0.1:8000"
export COLLECTOR_PLC_IDS="PLC-01"
export COLLECTOR_SQLITE_PATH="./dev_collector_buffer.db"
python -m collector.main
```

`tag_config` for PLC-01/TEMP_01 is **durably seeded again** as of 2026-08-03: the cloud-api pytest
suite now runs against its own database (`smart_data_collector_test`), so it can no longer TRUNCATE
the dev `tag_config` row. No reseeding is needed after running `pytest` — the 2026-07-15 "reseed
before every manual test" instruction is obsolete.

If the row is ever missing anyway (e.g. the container was recreated), reseed with the standalone dev
script (idempotent — upserts on (plc_id, tag_id), always bumps `updated_version` so any collector
cursor picks up the change):
```bash
cd cloud-api && source .venv/Scripts/activate
export DATABASE_URL="postgresql://sdc:sdc_dev_pw@127.0.0.1:5442/smart_data_collector"
python scripts/seed_dev_tag_config.py
```
Then delete the collector's local SQLite buffer file (`COLLECTOR_SQLITE_PATH`) before starting
it, so its `since_version` cursor starts fresh at 0 and definitely picks up the reseeded row.

### Running the cloud-api tests

```bash
cd cloud-api && source .venv/Scripts/activate
python -m pytest tests/ -v
```

`tests/conftest.py` targets `TEST_DATABASE_URL` if set; otherwise it derives the DSN from
`DATABASE_URL` by appending `_test` to the database name (falling back to the dev DSN if
`DATABASE_URL` is unset too), and refuses to run if that resolves to no dedicated database. A
session-scoped `test_database` fixture creates the DB if missing and applies
`cloud-api/migrations/001_init.sql` statement-by-statement (asyncpg wraps a multi-statement
`execute()` in one implicit transaction, which TimescaleDB rejects for `create_hypertable()` and
continuous aggregates). So the only prerequisite is a running `sdc-timescaledb` container.

## What's built so far (file map)

```
collector/
  pyproject.toml              deps: asyncua, aiosqlite, httpx; dev: pytest, pytest-asyncio
  src/collector/
    config.py                 CollectorSettings, TagConfig dataclasses (env-driven via from_env())
    opc_client.py              OpcCollectorClient — subscription, client-side deadband gate
                               (_DeadbandGate), heartbeat-loop gateway-down detection (Issue 3)
    buffer.py                  SqliteBuffer — WAL-mode local buffer, alarms-drain-before-readings
                               priority queue (Issue 2), delete-on-ack commit model
    alarm_engine.py             AlarmEngine — per-tag hysteresis state machine (Issue 5),
                               build_alarm_id() deterministic dedup key (Issue 1)
    tag_config_sync.py          TagConfigStore — periodic since_version pull (Issue 6)
    transmitter.py              Transmitter — batch POST with retry/backoff, alarms-first send,
                               send_gateway_status() for the Issue 3 self-diagnostic event
    main.py                    Collector — wires everything together, asyncio.gather of the
                               three long-running loops (tag_config poll, OPC client, transmit loop)
  tests/
    test_alarm_engine.py       7 tests — hysteresis boundary, alarm_id determinism (passing)
    test_buffer.py             5 tests — seq monotonicity, priority drain, ack semantics (passing)
    test_deadband.py           5 tests — deadband gate behavior (passing)
    test_integration_opc.py    2 tests — real in-process OPC UA server round-trip (passing —
                               uses `_force_stop()` helper, see 2026-07-14 session notes above)
  dev_opc_simulator.py         NEW 2026-07-14 — standalone long-running OPC UA tag simulator for
                               manual dev-server testing (not used by pytest)
  .venv/                       created + `pip install -e ".[dev]"` done

cloud-api/
  pyproject.toml              deps: fastapi, uvicorn, asyncpg, pydantic; dev: pytest, httpx
  migrations/001_init.sql      readings hypertable + 1min/1hour continuous aggregates (Issue 8),
                               alarms table (UNIQUE on plc_id/tag_id/triggered_at_utc/seq — Issue 1),
                               tag_config table + config_version_seq (Issue 6),
                               collector_status_events table (Issue 3).
                               readings PK is (plc_id, tag_id, seq, timestamp_utc) — fixed
                               2026-07-14, see session notes above for why.
  src/cloud_api/
    db.py                      asyncpg pool lifecycle (DATABASE_URL env var)
    schemas.py                  pydantic models: Reading, Alarm, TagConfig, IngestResult, etc.
    main.py                    FastAPI app, wires ingestion/config/status routers
    routers/
      ingestion.py               POST /ingest/v1/readings, /ingest/v1/alarms — ON CONFLICT DO
                                 NOTHING + RETURNING for the "200 + duplicates count" contract (Issue 7)
      config.py                  GET /config/v1/tags?since_version=N
      status.py                  POST /status/v1/collector (gateway_down/gateway_recovered)
  .venv/                       created 2026-07-14 + `pip install -e ".[dev]"` done
  scripts/
    seed_dev_tag_config.py     NEW 2026-07-15 — standalone, idempotent dev-only seed script for
                               the PLC-01/TEMP_01 tag_config row (upsert + version bump). Only
                               needed for a fresh dev DB now: since 2026-08-03 pytest uses a
                               separate test DB and no longer wipes this row.
  tests/
    conftest.py                NEW 2026-07-15 — db_conn/clean_tables/client fixtures against the
                               real sdc-timescaledb container (ASGITransport, bypasses app lifespan,
                               truncates all 4 tables + resets config_version_seq before each test).
                               2026-08-03: isolated from dev — targets TEST_DATABASE_URL, else
                               DATABASE_URL with `_test` appended to the DB name; session-scoped
                               test_database fixture creates the DB and applies 001_init.sql.
    test_ingestion_db.py       NEW 2026-07-15 — 7 tests: readings/alarms insert, retransmit dedup
                               (regression guard for the hypertable PK bug), partial-overlap batches
    test_config_db.py          NEW 2026-07-15 — 3 tests: since_version filtering, current_version
    test_status_db.py          NEW 2026-07-15 — 3 tests: gateway_down/recovered, invalid status 422
  -- Automated pytest against real TimescaleDB: DONE 2026-07-15 — 13/13 passing (requires
     `docker start sdc-timescaledb` first; see "How to spin up the dev stack again" above).
     Full project suite is now 32/32 (19 collector + 13 cloud-api).

TODOS.md                       2 deferred items from the eng review: MES/ERP API spec,
                               cert rotation procedure — untouched, still open
docs/smart-data-collector-plan.md   full plan + review report, unchanged since last session
```

## Key design decisions already made (don't re-litigate — see plan doc review report for full rationale)

- Language/stack: **Python** everywhere. Collector = asyncua + aiosqlite. Cloud API = FastAPI + asyncpg + TimescaleDB.
- Alarm dedup key is deterministic (`plc_id:tag_id:triggered_at_utc:seq`), never a random UUID.
- Local buffer drains alarms before readings (priority queue), not a single FIFO.
- Deadband filtering happens **client-side** in the collector (`_DeadbandGate`), not via OPC UA
  server-side DataChangeFilter — deliberate choice, not every OPC UA server implements server-side
  deadband, and it's easier to test deterministically this way.
- Gateway-down detection is an **explicit heartbeat probe** (`client.get_server_date()` on a timer),
  not inferred from subscription notification silence (a tag that legitimately never changes would
  produce no notifications either way).
- Server dedup response contract: always `200 OK` + `{inserted, duplicates}`, never `409` — at-least-once
  delivery makes duplicates a normal case, not an error branch the collector needs to handle specially.
- tag_config propagates via periodic pull (`since_version`), not push or restart-only.
- 1-year Parquet archival must be copy-then-verify-then-delete (critical gap found in review — do not
  implement as write-then-delete).
- Acceptance criteria were revised: the 5s latency target is measured collector→cloud-TSDB, NOT to the
  dashboard (dashboard is out of scope / separate SLA — outside-voice finding).
- 99.5% success-rate denominator excludes gateway-down time (tracked separately).
- Storage architecture (TSDB + continuous aggregates + Parquet, not a single Postgres) was reaffirmed
  after an outside-voice challenge — user explicitly chose to keep it, do not re-propose simplifying it.

## Not yet implemented (in the plan doc, but outside the current 9-task scope — needs re-scoping with user before starting)

- Docker packaging + remote-update deployment pipeline (plan doc "Deployment & Update Strategy" section)
- Parquet archiving batch job (copy-then-verify-then-delete)
- Dashboard integration (explicitly out of scope per plan doc)
- MES/ERP API, cert rotation — deferred to TODOS.md by design, do not build without new requirements

## Environment notes

- No git repo in this project directory (`git rev-parse` fails) — confirmed at session start, still true.
- `collector/.venv` and `cloud-api/.venv` both exist with deps installed (cloud-api's venv created
  2026-07-14).
- Docker Desktop on this machine: daemon was OFF at the start of the 2026-07-14 session — the user
  started it manually when asked. Once running, `docker ps -a` showed several *other, unrelated*
  projects' containers already on this daemon (`cad_postgres` on 5433, a `kd-global-tec-*` stack
  incl. its own TimescaleDB on 5432, etc.) — ports 5432/5433 were taken, hence port 5442 for this
  project's `sdc-timescaledb` container.
- Hit a real Windows/Docker Desktop port-proxy quirk: the container's first attempt on port 5434
  (after a previous failed/removed container had used that port) had asyncpg connections reset at
  the TCP layer (`WinError 10054`/`10048`, no error at all on the Postgres side — confirmed via
  `docker logs`, zero connection attempts reached the server). Recreating the container fresh on an
  unused port (5442) fixed it immediately. If this recurs: don't assume it's asyncpg/Python's fault —
  try a fresh port first before debugging the app layer.
- `sdc-timescaledb` container: `POSTGRES_USER=sdc POSTGRES_PASSWORD=sdc_dev_pw
  POSTGRES_DB=smart_data_collector`, port 5442. Migration applied — that part is durable, no need
  to re-run it unless the container itself is removed. Left running at the end of the 2026-07-15
  session — `docker start sdc-timescaledb` to resume.
- **`tag_config` is durably seeded** (2026-08-03 — supersedes the 2026-07-15 "NOT durably seeded"
  note): the cloud-api pytest suite used to TRUNCATE `tag_config` in the dev DB because it ran
  against the same `smart_data_collector` database. `tests/conftest.py` now targets a separate
  `smart_data_collector_test` database on the same container (auto-created and migrated by a
  session-scoped fixture), so `pytest` can no longer touch dev data at all. The PLC-01/TEMP_01 row
  survives test runs; no reseeding step before a manual functional test.
  `cloud-api/scripts/seed_dev_tag_config.py` is still kept around as an idempotent way to
  (re-)assert the row if the container is ever recreated.
- Two databases now live in the `sdc-timescaledb` container: `smart_data_collector` (dev, seeded)
  and `smart_data_collector_test` (pytest, truncated before every test). Anything that runs the
  suite in a different environment can point it elsewhere via `TEST_DATABASE_URL`.
