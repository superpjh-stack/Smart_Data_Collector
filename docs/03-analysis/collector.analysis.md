# collector Analysis Report

> **Analysis Type**: Gap Analysis (PDCA Check phase)
>
> **Project**: Smart Data Collector
> **Version**: 0.1.0
> **Analyst**: bkit gap-detector
> **Date**: 2026-08-04
> **Design Doc**: [collector.design.md](../02-design/features/collector.design.md)
> **Supersedes**: v0.2 of this document (2026-08-04, 89.4%)

### Pipeline References (for verification)

| Phase | Document | Verification Target |
|-------|----------|---------------------|
| Phase 1 | N/A — data model lives in design §3 | Terminology consistency |
| Phase 2 | N/A — conventions captured in design §10 | Convention compliance |
| Phase 4 | [cloud_api.design.md](../02-design/features/cloud_api.design.md) §4 | Outbound HTTP client/server contract match |

> **Template note**: This is a headless Python asyncio service. The bkit analysis
> template's UI / frontend Clean Architecture / npm convention sections do not apply
> and have been replaced by **§6 Python Module Layering** (verifying design §9) and a
> Python-oriented **§7 Convention Compliance** (verifying design §10). Performance
> (§4) is assessed against design §12 non-functional targets by mechanism rather than
> measured endpoint latency, since no load run exists in the repository.

> **Independence note**: Every item below was re-derived by reading the current source
> directly. No status was carried forward from the v0.2 edition, and no claim in that
> edition about what the code does was treated as still true — including the claims
> about items the v0.2 pass marked closed. The pytest suite was executed as part of this
> analysis (`.venv\Scripts\python.exe -m pytest -q` → **34 passed, 0 failed**, 6.9 s,
> no warnings).

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Re-verify the reverse-derived design baseline (`collector.design.md`) against the shipped
edge collector after the Act iteration that closed gap #15, and determine whether the
PDCA Check gate (≥ 90% Match Rate) is now met.

Three questions drive this pass:

1. Is gap #15 — the non-atomic `seq` assignment — genuinely closed, and closed *correctly*
   (not merely made untestable)?
2. Did the #15 fix disturb adjacent behaviour in `SqliteBuffer` or its callers?
3. Are the remaining ten items from the v0.2 edition still open as recorded, or has any
   of them silently changed status?

The answers: #15 is closed with a correct single-statement atomic increment and a
regression test that would fail against the old code; nothing adjacent regressed; and all
ten remaining items are confirmed still open, unchanged, because this Act iteration was
scoped to the buffer fix alone.

### 1.2 Analysis Scope

- **Design Document**: `docs/02-design/features/collector.design.md` (§3–§12)
- **Implementation Path**:
  - `collector/src/collector/{config,opc_client,buffer,alarm_engine,tag_config_sync,transmitter,main}.py`
  - `collector/tests/{test_alarm_engine,test_buffer,test_deadband,test_source_timestamp,test_transmitter,test_integration_opc}.py`
  - `collector/Dockerfile`, `collector/pyproject.toml`
  - `deploy/docker-compose.edge.yml`, `deploy/.env.example`, `deploy/README.md`
  - Cross-checked against `docs/02-design/features/cloud_api.design.md` §4 for the server-side contract
- **Analysis Date**: 2026-08-04
- **Method**: every design item read against the named file and line; test suite executed.

### 1.3 Status of the v0.2 Findings

| Prior gap | Subject | Verified status today | Evidence |
|-----------|---------|----------------------|----------|
| #15 | `SqliteBuffer._next_seq` non-atomic read-modify-write across two `await` points | ✅ **Fixed** | `buffer.py:78-80` — a single `UPDATE seq_counter SET value = value + 1 WHERE id = 1 RETURNING value`; regression test `test_buffer.py:66-75` |
| #24 | 4xx permanent rejections retried forever, blocking the alarm channel | ✅ Fixed in code, design still silent | `transmitter.py:21,25,126-136`; counted as a ⚠️ documentation gap |
| #32 | Deadband keyed on `tag_id` only → cross-PLC value bleed | ✅ Fixed | `opc_client.py:73,76`; tests `test_deadband.py` |
| #34 | Naive `.timestamp()` applying host timezone | ✅ Fixed | `opc_client.py:43-55`; `test_source_timestamp.py` |
| #56 | `json.JSONDecodeError` escaping the retry loop | ✅ Fixed | `transmitter.py:147` |
| #74 | `requires-python` not pinned to 3.12 | ✅ Fixed | `pyproject.toml:5` |
| #100 | ≤ 5 s P95 latency vs the drain interval | ✅ Fixed in code, design text stale | `config.py:34` — `2.0`; charged once at #41 |
| #33 | OPC subscription not re-established when the monitored-item set changes | ❌ **STILL OPEN** | `main.py:117-127` still builds `tags_by_plc` once, before `OpcCollectorClient` is constructed; `opc_client.py` still exposes no update/re-subscribe entry point |
| #72 | Integration test does not reach `SqliteBuffer` | ❌ **Still open** | `test_integration_opc.py:97-100` — `on_reading` still appends to a plain list; neither `SqliteBuffer` nor `Collector` is constructed in that module |
| #2, #4, #7, #8, #10, #16, #24, #41 | Design-document omissions / stale text | ⚠️ **Still open** | No design-document edit was made in this iteration; see §11 |

No new defect was discovered in this pass.

---

## 2. Gap Analysis (Design vs Implementation)

Legend: ✅ Match · ⚠️ Missing/stale in design (implemented but undocumented, or design text no longer describes the code) · ❌ Not implemented / diverged

### 2.1 Data Model — dataclasses (design §3.1)

| # | Design item | Implementation evidence | Status | Notes |
|---|-------------|------------------------|--------|-------|
| 1 | `TagConfig` frozen dataclass, 12 fields | `config.py:7-20` | ✅ | Field names, order, and PEP 604 unions match the design listing exactly |
| 2 | `sampling_interval_ms: int  # default 5000` | `config.py:19` | ❌ | Still declared as a required field with **no** default. The 5000 value exists only in the cloud-side config source, so the design annotation describes something the dataclass does not provide |
| 3 | `RawReading` frozen dataclass, 8 fields | `opc_client.py:17-26` | ✅ | `timestamp_utc: float` (unix epoch seconds) matches, including the inline comment |
| 4 | `RawReading.tag_name` as a name distinct from `tag_id` | `opc_client.py:116` — `tag_name=tag.tag_id` | ❌ | `TagConfig` still carries no `tag_name` field, so there is no source for a human-readable name. The cloud spec example shows `"tag_name": "Bearing Temperature"` (`cloud_api.design.md:240`), which this pipeline cannot produce |
| 5 | `AlarmEvent` frozen dataclass, 10 fields | `alarm_engine.py:16-27` | ✅ | Including `ack_status` and `config_version` |
| 6 | `BufferedItem` frozen dataclass, 4 fields | `buffer.py:33-38` | ✅ | `Kind` alias (`buffer.py:10`) narrows `kind` to the same two literals |
| 7 | `AlarmState` enum (NORMAL / HIGH / LOW) | `alarm_engine.py:10-13` | ⚠️ | The hysteresis FSM's state type is still absent from §3.1 even though §5 #5 and §2.2 both depend on the HIGH/LOW transition semantics |
| 8 | `TransmitOutcome` dataclass (`inserted`, `duplicates`, `acked_ids`) | `transmitter.py:28-32` | ⚠️ | The transmit result value object is still absent from §3.1; it is the return type of the transmit path described in §2.2 |

### 2.2 Local SQLite Buffer Schema & Sequence Semantics (design §3.2, §3.3)

| # | Design item | Implementation evidence | Status | Notes |
|---|-------------|------------------------|--------|-------|
| 9 | `seq_counter (id INTEGER PK CHECK (id = 1), value INTEGER NOT NULL)` | `buffer.py:13-16` | ✅ | `IF NOT EXISTS` added so `open()` is idempotent — a benign superset of the design DDL |
| 10 | Seed row for `seq_counter` | `buffer.py:17` — `INSERT OR IGNORE INTO seq_counter (id, value) VALUES (1, 0)` | ⚠️ | The design DDL shows no seed row, yet `_next_seq` (`buffer.py:78-84`) does `UPDATE ... WHERE id = 1` and raises if no row comes back — it is load-bearing, not incidental |
| 11 | `buffer_items (id, kind CHECK IN ('reading','alarm'), seq, payload_json, created_at)` | `buffer.py:19-25` | ✅ | Column names, types, `AUTOINCREMENT`, and the `kind` CHECK all match the design DDL exactly |
| 12 | `CREATE INDEX idx_buffer_items_kind_id ON buffer_items (kind, id)` | `buffer.py:29` | ✅ | Same name, same columns, same order |
| 13 | `journal_mode=WAL` so unclean power loss cannot corrupt buffered data | `buffer.py:59` | ✅ | Applied on every `open()`, before the schema script |
| 14 | Drain is a **single** statement — `ORDER BY (kind = 'alarm') DESC, id ASC` | `buffer.py:100-107` | ✅ | Byte-identical to the design's expression, so alarm priority cannot race reading drain |
| 15 | "`seq` is a single collector-local **monotonic** counter shared by readings and alarms, assigned at enqueue time" (§3.3) | `buffer.py:73-84` | ✅ | **Fixed — see detail below.** The increment and the read are now one statement, so concurrent `enqueue` calls cannot be handed the same value |
| 16 | `seq` merged into the transmitted payload | `buffer.py:89` — `payload = {**payload, "seq": seq}` | ⚠️ | §3.3 describes `seq` only as a buffer column and a dedup-key component. That the buffer also injects it into the caller's payload dict — which is what satisfies the cloud's required `seq` body field (`cloud_api.design.md:242`) — remains undocumented |

**#15 detail (🔴 → ✅, closed this iteration).** The read-modify-write is now a single
SQL statement:

```python
cur = await db.execute(
    "UPDATE seq_counter SET value = value + 1 WHERE id = 1 RETURNING value"
)   # buffer.py:78-80
```

This is the correct shape of the fix, not a workaround:

- **One round trip, so one suspension point.** The previous code suspended between the
  `UPDATE` and the `SELECT`, and `Collector._on_reading` (`main.py:36`) is scheduled once
  per OPC datachange notification via `asyncio.run_coroutine_threadsafe`
  (`opc_client.py:121`) — with up to 100 tags on a shared 5 s cadence, many `_on_reading`
  coroutines are concurrent by construction. There is now no point at which another
  coroutine can observe the counter between its increment and its read.
- **`RETURNING` returns the post-update value**, so the value handed back is exactly the
  one this statement wrote — not a re-read that a competing writer may have advanced.
- **The bare `assert` is gone.** `buffer.py:82-83` now raises an explicit
  `RuntimeError("seq_counter row missing — buffer schema not initialized")`. The old
  `assert` was stripped under `python -O`, which would have converted a clear failure
  into a downstream `TypeError`; that 🟢 smell from the v0.2 edition is closed as part of
  the same change.
- **The regression test is real, not vacuous.** `test_buffer.py:66-75` runs
  `asyncio.gather` over 50 concurrent `enqueue()` calls and asserts both
  `len(set(results)) == 50` **and** `sorted(results) == list(range(1, 51))`. The second
  assertion is the stronger one: it rules out gaps as well as collisions, which is
  precisely the continuity property design §3.3 and §12 lean on. Against the old
  two-statement implementation this test fails.

Adjacent behaviour was checked for regression and is intact: `enqueue`
(`buffer.py:86-95`) still stamps `seq` into the payload before the `INSERT`, still
commits once, and still returns the assigned `seq` to `Collector._on_reading`, which
forwards it into `build_alarm_id` (`main.py:57` → `alarm_engine.py:72`). The five
pre-existing buffer tests (monotonicity, priority drain, ack, unacked survival, capacity)
all still pass unmodified, so the fix changed the concurrency behaviour without changing
the sequential contract.

One residual, recorded as an observation rather than a gap: `buffer_items.seq` still
carries no `UNIQUE` constraint, so the invariant now holds by construction in
`_next_seq` rather than being enforced by the schema. Design §3.2's DDL does not specify
one either, so there is no design–implementation divergence — but a `UNIQUE` index would
turn a future regression into an immediate `IntegrityError` instead of silent cloud-side
dedup. See §10.3.

### 2.3 Outbound HTTP Interface (design §4.1)

| # | Design item | Implementation evidence | Status | Notes |
|---|-------------|------------------------|--------|-------|
| 17 | `GET /config/v1/tags?since_version=N` | `tag_config_sync.py:53-58` | ✅ | Query param name and the `_since_version` cursor match |
| 18 | Config pull failure → log + keep stale config, retry next poll | `tag_config_sync.py:44-47` | ✅ | `logger.exception("tag_config poll failed; keeping stale config")`; the loop continues |
| 19 | `POST /ingest/v1/readings` for batch readings | `transmitter.py:86-89` | ✅ | Body shape `{"readings": [...]}` matches `cloud_api.design.md:239` |
| 20 | `POST /ingest/v1/alarms`, sent **before** readings | `transmitter.py:75-76` precedes `:86-89` | ✅ | Ordering is structural, not incidental |
| 21 | Up to 5 attempts, exponential backoff, leave rows buffered on exhaustion | `transmitter.py:54` (`max_retries=5`), `:115` (loop), `:137`/`:151` (`base × 2**attempt`), `:161-162` (log + `return None`) | ✅ | Returning `None` means the ids are never added to `acked_ids`, so the rows survive |
| 22 | `POST /status/v1/collector` for `gateway_down`/`gateway_recovered`; log on failure, not buffered | `transmitter.py:164-183` | ✅ | Posts directly with no buffer interaction |
| 23 | Each ingest POST carries a per-batch `idempotency-key` UUID header | `transmitter.py:112` (`uuid.uuid4()`), `:120` (header) | ✅ | One UUID per `_send_with_retry` call, stable across that call's retries — correct batch identity semantics |
| 24 | "Any `2xx` with a parseable body means committed" — §4.1 and §6 recognise **no** permanent-rejection case | `transmitter.py:21,25,126-136,77-79,90-91` | ⚠️ | Fixed in code (a 4xx outside `{408, 429}` is classified `PERMANENT_REJECTION`, logged `CRITICAL`, and acked so it cannot occupy the head of the alarm channel), but the design document still describes no such case, and in particular does not record that the collector may **delete undelivered rows** — a policy in tension with design §1.1 "Lose no data" |

### 2.4 OPC UA Interface (design §4.2)

| # | Design item | Implementation evidence | Status | Notes |
|---|-------------|------------------------|--------|-------|
| 25 | One subscription per PLC at `create_subscription(min(sampling_interval_ms))` | `opc_client.py:184-185` | ✅ | Per-PLC loop; empty tag lists skipped (`:178-179`) |
| 26 | `subscribe_data_change` over that PLC's nodes | `opc_client.py:186-187` | ✅ | Nodes resolved from the `node_to_tag` keys |
| 27 | Read-only: no write-back to any node | No write call anywhere in `src/collector/` (full-module scan) | ✅ | `set_writable`/`write_value` appear only in the in-process test server (`test_integration_opc.py:59,123,128`) — never in shipped collector code |
| 28 | Liveness probe on `Server_ServerStatus_CurrentTime` at `heartbeat_timeout_s / 3` (min 1 s) | `opc_client.py:201,204,208` | ✅ | The probe interval doubles as the read timeout — deliberate and documented in the docstring |
| 29 | Probes are **counted**: after `round(timeout / interval)` consecutive failures, mark down and raise `ConnectionError` | `opc_client.py:203,210-216` | ✅ | Counter resets on any success (`:209`) |
| 30 | On any session failure: mark all PLCs down, sleep `reconnect_backoff_s`, re-establish and re-create every monitored item | `opc_client.py:159-165`; the loop re-enters `_connect_and_subscribe`, rebuilding all subscriptions | ✅ | Correctly reflects that OPC UA subscriptions do not survive a session drop |
| 31 | Quality normalization StatusCode → Good / Uncertain / Bad | `opc_client.py:33-40`, applied at `:104` | ✅ | Matches the §2.2 data-flow ordering (normalize, then gate) |
| 32 | Deadband gate scoped to the tag identity (§4.2, §5 #4) | `opc_client.py:58-81` — `dict[tuple[str, str], float]` at `:73`, `key = (plc_id, tag_id)` at `:76` | ✅ | Uses the same `(plc_id, tag_id)` identity pair as `alarm_engine.py:61` and `tag_config_sync.py:37` |
| 33 | The `since_version` pull keeps the field current without a manual restart (§4.2 + §5 #6) | `main.py:117-127` builds `tags_by_plc` **once**, before `OpcCollectorClient` is constructed; `tag_config_sync.py:62-64` thereafter mutates only `TagConfigStore._tags` | ❌ | **Still open.** Threshold and severity changes *do* reach the running collector, because `main.py:53` re-reads the tag from the store on every reading. But the **monitored-item set is frozen at process start**: a newly added tag, a changed `opc_node_id`, a changed `sampling_interval_ms`, or a changed `deadband` never reaches the subscription. `OpcCollectorClient` exposes no method to update `_tags_by_plc`, and the handler's `node_to_tag` map (`opc_client.py:180`) plus the `TagConfig` references it captures are snapshotted at subscribe time |
| 34 | `SourceTimestamp` → `timestamp_utc` as unix epoch seconds, UTC | `opc_client.py:43-55` (`_source_timestamp_to_epoch`), called at `:119` | ✅ | The naive datetime asyncua returns is explicitly stamped `timezone.utc` before `.timestamp()`; `test_source_timestamp.py` asserts against a fixed epoch constant, so it cannot pass vacuously on a UTC host |

### 2.5 Environment Variables (design §4.3)

| # | Variable | Design (required / default) | Implementation evidence | Status |
|---|----------|----------------------------|------------------------|--------|
| 35 | `OPC_ENDPOINT_URL` | yes / — | `config.py:41` — `os.environ[...]`, raises `KeyError` at startup if absent | ✅ |
| 36 | `CLOUD_API_BASE_URL` | yes / — | `config.py:42` — same fail-fast form | ✅ |
| 37 | `COLLECTOR_PLC_IDS` | yes (effectively) / `""` | `config.py:39,44` — `.get(..., "")` then comma-split with empty-token filter | ✅ |
| 38 | `COLLECTOR_SQLITE_PATH` | no / `./collector_buffer.db` | `config.py:43` | ✅ |
| 39 | `GATEWAY_HEARTBEAT_TIMEOUT_S` | no / `15.0` | `config.py:45-47` | ✅ |
| 40 | `TAG_CONFIG_POLL_INTERVAL_S` | no / `60.0` | `config.py:48-50` | ✅ |
| 41 | `TRANSMIT_INTERVAL_S` | no / **`7.0`** | `config.py:34,51` — **`2.0`** | ⚠️ |
| 42 | `TRANSMIT_BATCH_MAX` | no / `500` | `config.py:52` | ✅ |

**#41 detail.** The implementation deliberately deviates from the design table, and the
implementation is the one that is right: `config.py:31-34` carries an in-code rationale
("2 s keeps the collector→TSDB P95 inside the 5 s budget (design §12)"). The deviation is
mirrored consistently in `deploy/.env.example` and `deploy/docker-compose.edge.yml`, so
the deployment surface is coherent. What is stale is the **design document**: §4.3 still
lists a `7.0` default and §12 still names "5 s sampling + 7 s drain interval" as the
mechanism. Counted as a design-document gap, not a code defect.

No undocumented environment variable is read anywhere in `src/collector/` — `os.environ`
is referenced only in `config.py`. `reconnect_backoff_s` (`opc_client.py:142`, default
`5.0`) remains a constructor parameter that `main.py:121-127` never passes, consistent
with §4.3, which does not list it as an environment variable.

### 2.6 Key Design Decisions (design §5)

| # | Decision | Implementation evidence | Status |
|---|----------|------------------------|--------|
| 43 | #1 Deterministic `alarm_id = plc_id:tag_id:triggered_at_utc:seq`, never a random UUID | `alarm_engine.py:30-39`; asserted by `test_alarm_engine.py` against an exact literal | ✅ |
| 44 | #2 Buffer drains alarms before readings | `buffer.py:100-107`; asserted by `test_buffer.py:28-39` (alarm enqueued *last* still dequeues first) | ✅ |
| 45 | #3 Gateway-down via explicit heartbeat probe, not inferred from notification silence | `opc_client.py:191-217`, an independent loop reading the server's own CurrentTime node | ✅ |
| 46 | #4 Deadband filtering client-side in `_DeadbandGate` | `opc_client.py:58-81` — client-side as designed, no reliance on server `DataChangeFilter` | ✅ |
| 47 | #5 Hysteresis `clear_margin` separates trigger from clear | `alarm_engine.py:101-111`; asserted by `test_alarm_engine.py` on both the HIGH and LOW sides | ✅ |
| 48 | #6 tag_config via periodic `since_version` pull | `tag_config_sync.py:42-74` — pull-based, outbound-only, incremental cursor | ✅ (the *scope limit* is tracked separately as #33) |
| 49 | #7 Commit = delete-on-ack, not an offset cursor | `buffer.py:114-120` (`DELETE ... WHERE id IN (...)`); called from `transmitter.py:98-99` | ✅ |
| 50 | #8 Alarms evaluated at the edge | `main.py:57` — `AlarmEngine.evaluate` on the notification path, no cloud round trip | ✅ |
| 51 | #9 Gateway status posted outside the buffer | `transmitter.py:164-183` (direct POST, no buffer reference); invoked from `main.py:76-81` | ✅ |
| 52 | #10 Alarm evaluation skipped unless `quality == "Good"` | `main.py:53-55` — early return when `tag is None or raw.quality != "Good"`, after the reading is already buffered | ✅ |

All ten recorded decisions are faithfully implemented. Decision #1 in particular is
*strengthened* by the #15 fix: `build_alarm_id` embeds `seq`, so a colliding `seq` would
previously have let two alarms triggered in the same instant on different tags collide on
the server's UNIQUE constraint. That failure mode is now structurally impossible.

### 2.7 Error Handling (design §6)

| # | Condition | Design response | Implementation evidence | Status |
|---|-----------|-----------------|------------------------|--------|
| 53 | OPC session drop / gateway restart | Mark all PLCs down, backoff, reconnect + re-subscribe from scratch | `opc_client.py:159-165` | ✅ |
| 54 | Gateway alive-but-unresponsive | N consecutive probe failures → `gateway_down`, raise to reconnect loop | `opc_client.py:210-216` | ✅ |
| 55 | Cloud API unreachable (`httpx.HTTPError`) | `base × 2^attempt`, 5 attempts, then leave rows buffered | `transmitter.py:147-159`; `test_transmitter.py` (503 × 5, nothing acked) | ✅ |
| 56 | Malformed cloud response | Treat as a transport failure — retry, do not ack | `transmitter.py:147` — caught tuple is `(httpx.HTTPError, KeyError, json.JSONDecodeError)` | ✅ |
| 57 | Partial batch success | Per-kind ack, so a readings failure never blocks committing a successful alarm send | `transmitter.py:75-96` — alarms and readings extend `acked_ids` independently | ✅ |
| 58 | tag_config poll failure | Log and keep last known config; never drop tags | `tag_config_sync.py:44-47` | ✅ |
| 59 | Buffer approaching disk capacity | `is_near_capacity(512 MiB)` each transmit cycle → `CRITICAL` log | `buffer.py:134-136`; `main.py:18,97-102` | ✅ |
| 60 | Unknown tag in a notification | Silently ignore | `opc_client.py:100-102` — `node_to_tag.get(...)` miss returns without logging | ✅ |

8/8. One error-handling path improved incidentally this iteration: an uninitialised
`seq_counter` now surfaces as a named `RuntimeError` from `buffer.py:83` rather than as an
`AssertionError` that `python -O` would have elided entirely — consistent with design §1.2
"Fail soft, never silently."

### 2.8 Security (design §7)

| # | Design item | Implementation evidence | Status |
|---|-------------|------------------------|--------|
| 61 | Outbound-only: no listening socket in the collector process; edge compose exposes no ports | No `Server`, `bind`, or `listen` in `src/collector/`; `httpx.AsyncClient` (`main.py:108`) and `asyncua.Client` (`opc_client.py:174`) are both clients; no `ports:` key anywhere in `deploy/docker-compose.edge.yml` | ✅ |
| 62 | Read-only OPC UA: no write path to any PLC node exists in the codebase | Confirmed by full-module scan — see item #27 | ✅ |
| 63 | Container hardening: non-root uid 10001; buffer on a `/data` volume surviving image replacement | `Dockerfile` — `useradd --uid 10001 collector`, `chown` on `/data`, `VOLUME ["/data"]`, `USER collector` | ✅ |
| 64 | No credentials compiled in; all endpoints from the environment; registry creds in the edge `.env`, not the image | `config.py:37-53` reads everything from `os.environ`; compose sources `REPO_USER`/`REPO_PASS` from `.env`; the Dockerfile contains no credential | ✅ |
| 65 | TLS: HTTPS cloud transport; OPC UA Sign & Encrypt and cert rotation deferred to `TODOS.md` #2 | `.env.example` uses an `https://` cloud base URL; `TODOS.md` #2 covers OPC UA client certs plus edge–cloud TLS mutual auth and rotation | ✅ |

Security is implemented as designed with no gaps. The one residual privilege —
Watchtower's read-write `/var/run/docker.sock` mount — is explicitly acknowledged and
justified in `deploy/README.md` as a local-only, never-network-exposed grant. No TLS
verification is disabled anywhere; httpx defaults are retained throughout. The #15 fix
introduced no new SQL string interpolation: the `RETURNING` statement is a fixed literal
with no parameters.

### 2.9 Test Plan (design §8)

| # | Design case | Implementation evidence | Status |
|---|-------------|------------------------|--------|
| 66 | Trigger at exactly `max_alarm`; no re-trigger while held above | `test_alarm_engine.py` (uses exactly `80.0`) | ✅ |
| 67 | Same `(plc_id, tag_id, triggered_at_utc, seq)` yields a byte-identical `alarm_id` | `test_alarm_engine.py` | ✅ |
| 68 | `seq` monotonicity | `test_buffer.py:20-25` (sequential) **plus** `:66-75` (50-way concurrent, distinct and gap-free) | ✅ **Strengthened** — the property design §3.3 actually needs is now asserted under concurrency, not only in a serialized path |
| 69 | Alarms enqueued after readings still dequeue first | `test_buffer.py:28-39` | ✅ |
| 70 | Ack semantics | `test_buffer.py:42-51` | ✅ |
| 71 | Deadband suppresses sub-threshold changes and passes the first sample of a tag | `test_deadband.py` — 7 cases, incl. two cross-PLC regressions | ✅ |
| 72 | Integration: real in-process OPC UA server → collector → **buffer** round trip | `test_integration_opc.py:78-136` | ❌ |
| 73 | Gateway stop is detected and surfaced as `gateway_down` | `test_integration_opc.py:139-189`, with a deliberately abrupt `_force_stop` (`:26-48`) simulating an ungraceful gateway death | ✅ |

**#72 detail (still open).** The integration test exercises a genuine OPC UA wire session
but still stops at the `on_reading` callback, which merely appends to a list
(`test_integration_opc.py:97-100`). The `Collector` composition root (`main.py`) is
instantiated nowhere in the suite, so `Collector._on_reading` — the
enqueue → evaluate → enqueue-alarm sequence that design §2.2 presents as the core data
flow — has no coverage. The buffer↔transmitter leg *is* covered, by `test_transmitter.py`
driving a real `SqliteBuffer` against a mocked HTTP transport; only the OPC→buffer leg
remains open.

**Suite inventory (executed 2026-08-04): 6 test modules, 33 test functions / 34 collected
node ids (one `parametrize` over `[408, 429]`), 34 passed, 0 failed, 6.9 s, no warnings.**

| Module | Collected | Subject |
|--------|:---------:|---------|
| `test_alarm_engine.py` | 7 | Hysteresis both sides, clear margin, deterministic `alarm_id` |
| `test_buffer.py` | 6 | Seq monotonicity, **concurrent seq distinctness (new)**, priority drain, ack, unacked survival, capacity |
| `test_deadband.py` | 7 | Deadband gate incl. 2 cross-PLC regressions |
| `test_source_timestamp.py` | 4 | Naive/aware/None SourceTimestamp → epoch |
| `test_transmitter.py` | 8 | 422 drop, poison-row unblocking, 408/429 + 503 retry, unparseable 2xx, missing keys, happy path |
| `test_integration_opc.py` | 2 | Real OPC UA session: deadband over the wire, gateway-down detection |

No coverage tooling is configured in `pyproject.toml` (no `pytest-cov`, no
`[tool.coverage]`), so no coverage percentage can be reported. Design §8 sets no numeric
target either, so the two are consistent — but both remain weak on this point.

### 2.10 Conventions (design §10) — detailed in §7 below

| # | Convention | Status |
|---|-----------|--------|
| 74 | Python 3.12 | ✅ (`pyproject.toml:5` — `requires-python = ">=3.12"`) |
| 75 | `from __future__ import annotations`, PEP 604 unions | ✅ |
| 76 | PascalCase classes; module-private helpers `_`-prefixed | ✅ |
| 77 | snake_case functions/vars; instance state `_`-prefixed | ✅ |
| 78 | Constants UPPER_SNAKE_CASE | ✅ |
| 79 | Immutable value objects `@dataclass(frozen=True)` | ✅ |
| 80 | `asyncio` throughout; no blocking I/O in the event loop | ✅ |
| 81 | Module-level `logging.getLogger(__name__)`; `logger.exception` for caught failures | ✅ |
| 82 | Env var prefixes | ✅ |
| 83 | Timestamps: timezone-aware UTC, ISO 8601 on the wire | ✅ |

### 2.11 Module Layout & Dependency Rules (design §9) — detailed in §6 below

| # | Item | Status |
|---|------|--------|
| 84-90 | Seven modules present with the stated responsibilities | ✅ (7/7) |
| 91 | Dependency rule: domain modules import no I/O; I/O modules do not import each other except `transmitter → buffer`; only `main` knows all | ✅ (0 violations) |

### 2.12 Deployment (design §11) — detailed in §8 below

| # | Item | Status |
|---|------|--------|
| 92-98 | Two-stage image, edge-only compose, Watchtower poll/label-gating, pull-based update, `/data` volume, compose `:?` guards | ✅ (7/7) |

### 2.13 Non-functional Requirements (design §12)

| # | Requirement | Target | Mechanism present? | Status |
|---|-------------|--------|--------------------|--------|
| 99 | Collection completeness ≥ 99.5%, denominator excludes gateway-down time | `seq` continuity + `gateway_down` events bounding the denominator | `buffer.py:73-84` (now atomic, so the continuity signal is trustworthy); `transmitter.py:170` | ✅ |
| 100 | Collect→TSDB latency ≤ 5 s at P95 | Sampling + drain interval; alarms jump the queue | `config.py:34` — `transmit_interval_s: float = 2.0`, mirrored in `.env.example` and `docker-compose.edge.yml` | ✅ |
| 101 | Alarm detection ≤ 3 s from threshold breach | Edge-local evaluation on the notification path | `main.py:57`, synchronous on the reading path | ✅ |
| 102 | Outage survival ≥ 72 h of buffered data | SQLite WAL buffer, ~512 MiB capacity budget | `buffer.py:59` (WAL); `main.py:18` (`BUFFER_CAPACITY_BYTES`) | ✅ |
| 103 | Recovery ordering: alarms before backlogged readings, readings in order | Priority drain + `ORDER BY id` | `buffer.py:100-107` | ✅ |
| 104 | Reconnect automatic, no manual intervention | Reconnect loop with full re-subscription | `opc_client.py:157-165` | ✅ |

**#99 caveat withdrawn.** The v0.2 edition qualified this row (✅ ⚠) because the
continuity metric it names as evidence rested on a `seq` that could duplicate or skip. With
`_next_seq` atomic, a gap in `seq` now means what design §12 says it means — a genuinely
lost reading — so the completeness figure is measurable rather than merely computable. The
qualification is removed.

### 2.14 Match Rate Summary

```
┌──────────────────────────────────────────────────┐
│  Overall Match Rate: 90.4%  (94 / 104 items)     │
├──────────────────────────────────────────────────┤
│  ✅ Match:               94 items (90.4%)         │
│  ⚠️ Doc gap / stale:      6 items ( 5.8%)         │
│  ❌ Not impl / diverged:  4 items ( 3.8%)         │
└──────────────────────────────────────────────────┘

  2026-08-03: 86.4% (89/103)
  2026-08-04 v0.2: 89.4% (93/104)
  2026-08-04 v0.3: 90.4% (94/104)   ▲ +1.0 pt  ✅ gate cleared
```

Per-section derivation of the 104 items:

| Section | Design ref | Items | ✅ | ⚠️ | ❌ |
|---------|-----------|:-----:|:--:|:--:|:--:|
| 2.1 Data model — dataclasses | §3.1 | 8 | 4 | 2 | 2 |
| 2.2 Local buffer schema & seq semantics | §3.2–3.3 | 8 | 6 | 2 | 0 |
| 2.3 Outbound HTTP interface | §4.1 | 8 | 7 | 1 | 0 |
| 2.4 OPC UA interface | §4.2 | 10 | 9 | 0 | 1 |
| 2.5 Environment variables | §4.3 | 8 | 7 | 1 | 0 |
| 2.6 Key design decisions | §5 | 10 | 10 | 0 | 0 |
| 2.7 Error handling | §6 | 8 | 8 | 0 | 0 |
| 2.8 Security | §7 | 5 | 5 | 0 | 0 |
| 2.9 Test plan | §8 | 8 | 7 | 0 | 1 |
| 2.10 Conventions | §10 | 10 | 10 | 0 | 0 |
| 2.11 Module layout & dependency rules | §9 | 8 | 8 | 0 | 0 |
| 2.12 Deployment | §11 | 7 | 7 | 0 | 0 |
| 2.13 Non-functional requirements | §12 | 6 | 6 | 0 | 0 |
| **Total** | | **104** | **94** | **6** | **4** |

The item set is unchanged from v0.2 (no item added, retired, or re-scoped), so the
comparison is like-for-like: exactly one item — #15 — moved from ❌ to ✅.

Match Rate = 94 / 104 = **90.4%**, which **clears the 90% PDCA Check gate**. A completion
report may now be written.

Eight of thirteen sections are at 100%: local buffer schema is not among them (two
documentation gaps remain), but §2.2 now carries **zero code defects** — as do §5, §6, §7,
§9, §10, §11, and §12.

---

## 3. Code Quality Analysis

### 3.1 Complexity

| File | Function | Assessment |
|------|----------|------------|
| `main.py` | `Collector._on_reading` (L36-74) | Two inline payload dict literals; readable, but the wire shapes duplicate the §3.1 dataclasses in untyped form |
| `transmitter.py` | `transmit_once` (L63-103) | Near-duplicate alarms/readings blocks (L75-96); the `PERMANENT_REJECTION` branch doubles the duplication. Modest extraction opportunity, low risk |
| `transmitter.py` | `_send_with_retry` (L105-162) | Two exception arms with near-identical backoff/log bodies; the classification logic itself is clear and well-commented |
| `opc_client.py` | `_heartbeat_loop` (L191-217) | Clear; probe interval doubling as read timeout is intentional and documented |
| `alarm_engine.py` | `_next_state` (L90-113) | Flat, well-factored FSM; pure and static, hence trivially testable |
| `buffer.py` | all methods | Each is one or two statements plus a commit; `_next_seq` (L73-84) is now **shorter** than before the fix — one statement instead of two, plus an explicit guard |

No function exceeds roughly 45 lines. Overall complexity remains low, consistent with the
"single-responsibility modules" principle in design §1.2.

### 3.2 Code Smells

| Type | File | Location | Description | Severity |
|------|------|----------|-------------|----------|
| Frozen monitored-item set | `main.py` / `opc_client.py` | `main.py:117-127`; no updater on `OpcCollectorClient` | New tags, node-id, sampling-interval, and deadband changes never reach the subscription — gap #33 | 🟡 |
| Fire-and-forget coroutine | `opc_client.py` | L121 | `asyncio.run_coroutine_threadsafe(...)` result is never retrieved, so any exception raised inside `Collector._on_reading` (e.g. a buffer write failure) is discarded with **no log at all**. Directly at odds with design §1.2 "Fail soft, never silently". This is now the highest-severity open code smell | 🟡 |
| Batch-granularity data drop | `transmitter.py` | L77-79, L90-91 | `PERMANENT_REJECTION` acks the **entire** batch. One malformed row in a 500-row drain discards up to 499 valid rows, with only a `CRITICAL` log. Correct as a channel-unblocking measure; too coarse as a data-retention policy | 🟡 |
| Deadband updated on bad-quality samples | `opc_client.py` | L104-108 | Quality is computed before the gate but not consulted by it, so an `Uncertain`/`Bad` value becomes the reference the next `Good` value is compared against — a garbage sample can suppress a real one | 🟡 |
| Fragile deserialization | `tag_config_sync.py` | L63 | `TagConfig(**raw)` raises `TypeError` on any field the cloud adds; the poll loop then keeps stale config indefinitely with only a log line, so a benign server-side schema addition degrades into a silent config freeze | 🟡 |
| Sleep after the final attempt | `transmitter.py` | L146, L159 | The backoff sleep runs even on the last iteration, so an exhausted retry chain idles ~16 s before returning `None` | 🟢 |
| Untyped payload duplication | `main.py` | L39-49, L61-72 | Wire payloads built as inline dicts instead of serializing the §3.1 dataclasses, so a field rename cannot be caught by a type checker | 🟢 |
| `seq` uniqueness unenforced by schema | `buffer.py` | L19-25 | `buffer_items.seq` has no `UNIQUE` index; the invariant now holds by construction in `_next_seq` but nothing would catch a future regression at the storage layer | 🟢 |
| Undeclared SQLite floor | `buffer.py` / `pyproject.toml` | L78-80 / — | `UPDATE ... RETURNING` requires SQLite ≥ 3.35. Satisfied everywhere the service actually runs (`python:3.12-slim` bundles a much newer SQLite), and neither design §9 nor §11 states a SQLite version, so this is not a design divergence — but the floor is now load-bearing and undeclared | 🟢 |
| Blocking call in the event loop | `buffer.py` | L128-132 | `os.path.getsize` is a synchronous stat, called from the transmit loop each cycle; negligible in practice but at odds with §10 "no blocking I/O in the event loop" | 🟢 |
| Broad exception swallow | `main.py` | L94-95 | `except Exception` around the whole transmit cycle is deliberate per §1.2 and no longer masks any known defect | 🟢 |

**Closed since v0.2:** the 🔴 non-atomic counter (`buffer.py` `_next_seq`) and the 🟢 bare
`assert` for a runtime invariant — both retired by the same change. **No 🔴 code smell
remains in the collector.**

### 3.3 Security Issues

| Severity | File | Location | Issue | Recommendation |
|----------|------|----------|-------|----------------|
| 🟢 Info | `src/collector/` | — | No hardcoded secret, no listening socket, no OPC write path, non-root container | None — design §7 is fully honoured |
| 🟢 Info | `.env.example` | — | HTTPS cloud base URL; TLS verification is disabled nowhere (httpx defaults retained) | None |
| 🟡 Warning | `deploy/docker-compose.edge.yml` | Watchtower service | Watchtower holds a read-write `/var/run/docker.sock` mount | Accepted and documented in `deploy/README.md`; local-only, never network-exposed |
| 🟢 Info | `transmitter.py` | L129-135 | The `CRITICAL` log on a permanent rejection prints the status code and item count but not the payload | Correct — no PLC data is leaked into logs |
| 🟢 Info | `buffer.py` | L78-80 | The new `RETURNING` statement is a fixed literal with no interpolation or parameters | No injection surface introduced by the fix |
| 🟢 Info | — | — | OPC UA Sign & Encrypt not yet enabled | Already tracked as `TODOS.md` #2 and disclosed in design §7 |

No new security finding. Security remains 5/5 against design §7.

---

## 4. Performance / Non-functional Assessment

No load test or latency measurement exists in the repository, so design §12 targets are
assessed by mechanism rather than measurement (see §2.13).

| Location | Concern | Impact |
|----------|---------|--------|
| `buffer.py:78-80` | `_next_seq` is now **one** round trip instead of two | The fix removed a statement from the hottest write path in the service: enqueue is now two statements plus a commit rather than three plus a commit, a ~33% reduction in per-reading SQL round trips. Correctness and throughput moved in the same direction |
| `config.py:34` + `transmitter.py:64` | `dequeue_batch(500)` once per **2 s** cycle gives a ~250 rows/s drain ceiling against a 20 rows/s steady state | Headroom ≈ 12.5×. A full 72 h backlog (~1.4 M rows) drains in ~1.6 h — a recovery-time property design §12 does not yet state |
| `buffer.py:86-95` | Each `enqueue` still commits (fsync) per reading | At 20 rows/s this is comfortable, but per-reading fsync remains the dominant cost and is worth knowing before raising tag counts |
| `buffer.py:114-119` | Ack builds an `IN (...)` clause with up to 500 placeholders | Well within SQLite's limit; fine at this scale |
| `opc_client.py:121` | `asyncio.run_coroutine_threadsafe` per notification, from the asyncua callback thread | Correct thread-boundary pattern. It is what made many `_on_reading` coroutines concurrent — the precondition that gap #15 depended on, and which the new `test_buffer.py:66-75` now simulates directly |

---

## 5. Test Coverage

### 5.1 Coverage Status

| Area | Evidence | Status |
|------|----------|--------|
| `buffer.py` | 6 direct tests — now including a 50-way concurrent enqueue — plus indirect exercise by all 8 transmitter tests | ✅ Strong (concurrent path now covered) |
| `alarm_engine.py` | 7 tests: both trigger sides, hysteresis, clear-margin, id determinism | ✅ Strong |
| `opc_client.py` `_DeadbandGate` | 7 tests, incl. 2 cross-PLC regressions | ✅ Strong |
| `opc_client.py` `_source_timestamp_to_epoch` | 4 tests, host-TZ-independent by construction | ✅ Strong |
| `opc_client.py` session + heartbeat | 2 integration tests over a real wire session | ✅ Adequate |
| `transmitter.py` | 8 tests: permanent rejection, poison-row unblocking, retryable 4xx, 5xx, unparseable body, missing keys, happy path | ✅ Strong |
| `tag_config_sync.py` | none | ❌ Uncovered |
| `main.py` (`Collector`, `_on_reading`, `_transmit_loop`) | none | ❌ Uncovered |
| `config.py` `CollectorSettings.from_env` | none | ❌ Uncovered |

The v0.2 edition's headline coverage complaint — "concurrency is untested everywhere" — is
now partially answered: the buffer's concurrent path is covered, and it is covered by the
exact test that edition specified (`asyncio.gather` of 50 enqueues asserting distinctness).
No other module has a concurrency test, but no other module has a shared mutable counter.

### 5.2 Uncovered Areas of Note

- `Collector._on_reading` (`main.py:36-74`) — the enqueue → evaluate → enqueue-alarm
  composition, the core flow of design §2.2 and the missing leg of gap #72. This remains
  the single largest untested path in the system.
- `tag_config_sync.refresh_once` — the `TagConfig(**raw)` fragility noted in §3.2 is
  untested, as is the `since_version` cursor advance.
- `CollectorSettings.from_env` — the eight values verified in §2.5 are confirmed by
  reading only, not by assertion; in particular nothing pins the `2.0`
  `transmit_interval_s` default that the ≤ 5 s P95 target depends on.
- No `UNIQUE`-constraint-level guard on `seq`, so the new concurrency test is the *only*
  thing standing between a future refactor of `_next_seq` and a silent return of gap #15.
  That test is well-targeted, but it is a single point of defence.

---

## 6. Python Module Layering Compliance

> Replaces the bkit template's frontend Clean Architecture section.
> Reference: design §9 (Module Layout) and its stated dependency rule.

### 6.1 Module Presence and Responsibility

| Design module | Responsibility (§9) | Actual location | Status |
|---------------|--------------------|-----------------|--------|
| `config.py` | `TagConfig`, `CollectorSettings` (env parsing) | `src/collector/config.py` | ✅ |
| `opc_client.py` | OPC UA session/subscription, reconnect, deadband, heartbeat | `src/collector/opc_client.py` | ✅ |
| `buffer.py` | Durable buffer, seq counter, priority drain, ack, capacity check | `src/collector/buffer.py` | ✅ |
| `alarm_engine.py` | Hysteresis FSM, `build_alarm_id` | `src/collector/alarm_engine.py` | ✅ |
| `tag_config_sync.py` | `since_version` pull, `current_version` exposure | `src/collector/tag_config_sync.py:29-31,53` | ✅ |
| `transmitter.py` | Batch POST, retry/backoff, gateway-status channel | `src/collector/transmitter.py` | ✅ |
| `main.py` | Composition root, three concurrent loops, buffer-capacity alarm | `src/collector/main.py:106-133` (`asyncio.gather` of exactly three loops at `:129-133`), `:18,97-102` | ✅ |

All seven modules exist with the designed responsibility and nothing extra;
`src/collector/__init__.py` is empty, as a pure package marker should be. `main.py` is
verifiably the composition root: it is the only module importing more than one sibling.

### 6.2 Dependency Direction Verification

Design rule (§9): the domain modules (`config`, `alarm_engine`) import nothing from the
I/O modules; the I/O modules (`opc_client`, `buffer`, `tag_config_sync`, `transmitter`)
may import domain types but not each other, **except** `transmitter → buffer`, which is
its drain source. Only `main.py` knows about every module.

| Module | Design-permitted internal imports | Actual imports (verified) | Status |
|--------|----------------------------------|---------------------------|--------|
| `config.py` | stdlib only | `os`, `dataclasses` — `config.py:3-4` | ✅ |
| `alarm_engine.py` | `config` | `dataclasses`, `datetime`, `enum`, `collector.config` — `alarm_engine.py:3-7` | ✅ |
| `opc_client.py` | `asyncua`, `config` | `asyncio`, `logging`, `time`, `collections.abc`, `dataclasses`, `datetime`, `asyncua`, `collector.config` — `opc_client.py:3-12` | ✅ |
| `buffer.py` | `aiosqlite` | `json`, `os`, `dataclasses`, `typing`, `aiosqlite` — `buffer.py:3-8` | ✅ |
| `tag_config_sync.py` | `httpx`, `config` | `asyncio`, `logging`, `httpx`, `collector.config` — `tag_config_sync.py:3-8` | ✅ |
| `transmitter.py` | `httpx`, `buffer` | `asyncio`, `json`, `logging`, `uuid`, `dataclasses`, `datetime`, `typing`, `httpx`, `collector.buffer` — `transmitter.py:3-13` | ✅ |
| `main.py` | all | all six siblings — `main.py:9-14` | ✅ |

**Zero dependency violations.** Specifically verified this pass:

- The #15 fix added **no import** to `buffer.py` — the import block (`buffer.py:3-8`) is
  byte-identical to the v0.2 edition's. In particular the fix did not reach for
  `asyncio.Lock`, which would have been the other viable remedy and would have added an
  `asyncio` import to a module design §9 lists as depending on `aiosqlite` alone. Pushing
  the atomicity down into SQL kept the module's dependency footprint exactly as designed.
- No I/O module imports another I/O module except the sanctioned `transmitter → buffer`
  edge (`transmitter.py:13`).
- `alarm_engine.py` and `config.py` are entirely free of `asyncua`, `httpx`, and
  `aiosqlite` — which is what makes the pure unit tests in §5.1 possible with no I/O
  fixture at all.
- Nothing imports `main`.

### 6.3 Layering Score

```
┌──────────────────────────────────────────────────┐
│  Module Layering Compliance: 100%                 │
├──────────────────────────────────────────────────┤
│  ✅ Modules present & correctly scoped:   7/7     │
│  ✅ Dependency-rule violations:           0       │
│  ✅ Domain modules free of I/O deps:      2/2     │
│  ✅ Sanctioned I/O→I/O edges only:        1/1     │
└──────────────────────────────────────────────────┘
```

---

## 7. Convention Compliance

> Replaces the bkit template's npm/TSX convention checks.
> Reference: design §10 (Conventions).

### 7.1 Convention Checks

| # | Convention (§10) | Verified against | Compliance | Notes |
|---|------------------|------------------|:----------:|-------|
| 74 | Language: Python **3.12** | `pyproject.toml:5` — `requires-python = ">=3.12"`; `Dockerfile` — `python:3.12-slim` both stages | ✅ | Metadata, image, and design statement agree |
| 75 | `from __future__ import annotations`, PEP 604 unions | Line 1 of all seven modules; PEP 604 at `config.py:12,14-15`, `opc_client.py:24`, `buffer.py:55`, `alarm_engine.py:25`, `transmitter.py:111` | ✅ | 7/7 present |
| 76 | Classes PascalCase; module-private helpers `_`-prefixed | `_DeadbandGate`, `_SubscriptionHandler`, `OpcCollectorClient`, `SqliteBuffer`, `AlarmEngine`, `AlarmState`, `TagConfigStore`, `Transmitter`, `Collector` | ✅ | Exactly the two `_`-prefixed helpers §10 names by example, and no others |
| 77 | Functions/vars snake_case; instance state `_`-prefixed | All methods snake_case; all instance attributes `_`-prefixed. Module-private functions `_quality_from_statuscode`, `_source_timestamp_to_epoch`, and the private method `_next_seq` (`buffer.py:73`) | ✅ | No violation found |
| 78 | Constants UPPER_SNAKE_CASE | `BUFFER_CAPACITY_BYTES` (`main.py:18`), `_SCHEMA` (`buffer.py:12`), `PERMANENT_REJECTION` (`transmitter.py:21`), `_RETRYABLE_4XX` (`:25`) | ✅ | |
| 79 | Immutable value objects `@dataclass(frozen=True)` | `TagConfig`, `CollectorSettings`, `RawReading`, `AlarmEvent`, `BufferedItem`, `TransmitOutcome` | ✅ | 6/6 value objects frozen; no unfrozen dataclass exists |
| 80 | `asyncio` throughout; no blocking I/O in the event loop | `aiosqlite`, `httpx.AsyncClient`, `asyncua` async API, `asyncio.sleep`/`wait_for`; thread boundary handled at `opc_client.py:121` | ✅ | Only `os.path.getsize` (`buffer.py:129`) is synchronous — a single stat, recorded as 🟢 in §3.2 rather than a convention breach |
| 81 | Module-level `logging.getLogger(__name__)`; `logger.exception` for caught failures | Loggers: `opc_client.py:14`, `tag_config_sync.py:10`, `transmitter.py:15`, `main.py:16`; `logger.exception` at `opc_client.py:163`, `tag_config_sync.py:47`, `transmitter.py:183`, `main.py:95` | ✅ | `config.py`, `buffer.py`, `alarm_engine.py` declare no logger and emit no logs — appropriate for pure/domain modules. The `RuntimeError` added at `buffer.py:83` raises rather than logs, which is the right choice for an unrecoverable initialization invariant in a logger-free module |
| 82 | Env vars: `COLLECTOR_*` plus `OPC_ENDPOINT_URL`, `CLOUD_API_BASE_URL`, `GATEWAY_*`, `TAG_CONFIG_*`, `TRANSMIT_*` | `config.py:39-52` | ✅ | All eight names conform; no stray or off-convention name anywhere |
| 83 | Timestamps: timezone-aware UTC, ISO 8601 on the wire | tz-aware construction: `main.py:37,80`, `alarm_engine.py:38`, `opc_client.py:54`. ISO 8601 on the wire: `main.py:44,68`, `transmitter.py:177` | ✅ | Pinned by `test_source_timestamp.py` |

### 7.2 Project Structure

| Expected | Exists | Notes |
|----------|:------:|-------|
| `collector/src/collector/` (src layout) | ✅ | Matches `pyproject.toml:34-35` — `[tool.setuptools.packages.find] where = ["src"]` |
| `collector/tests/` | ✅ | 6 test modules plus `__init__.py`; `testpaths = ["tests"]`, `asyncio_mode = "auto"`, `integration` marker registered |
| `collector/Dockerfile` | ✅ | |
| `collector/pyproject.toml` | ✅ | |
| `deploy/` — edge-only compose + env template + README | ✅ | All three present |
| Module file names snake_case | ✅ | 7/7 |

### 7.3 Convention Score

```
┌──────────────────────────────────────────────────┐
│  Convention Compliance: 100%  (10/10 checks)      │
├──────────────────────────────────────────────────┤
│  Naming (classes/functions/constants):   100%     │
│  Typing & immutability:                  100%     │
│  Async / logging:                        100%     │
│  Env var naming:                         100%     │
│  Timestamps (tz-aware + wire format):    100%     │
│  Language version pinning:               100%     │
└──────────────────────────────────────────────────┘
```

---

## 8. Deployment Verification (design §11)

| # | Design item | Implementation evidence | Status |
|---|-------------|------------------------|--------|
| 92 | Two-stage Docker image: builder installs into `/opt/venv`, runtime copies the venv, drops to uid 10001, declares `VOLUME ["/data"]`, entrypoint `python -m collector.main` | `Dockerfile` — builder stage with `python -m venv /opt/venv`, `COPY --from=builder /opt/venv /opt/venv`, `useradd --uid 10001`, `VOLUME ["/data"]`, `USER collector`, `CMD ["python", "-m", "collector.main"]` | ✅ (expressed as `CMD` rather than `ENTRYPOINT` — functionally equivalent here) |
| 93 | Field devices run **only** `deploy/docker-compose.edge.yml`; the root `docker-compose.yml` is the cloud stack and must not reach the edge | Compose header states exactly this; `name: sdc-edge`; the root `docker-compose.yml` is a separate cloud-side file | ✅ |
| 94 | Watchtower polls the registry every 5 min (`WATCHTOWER_POLL_INTERVAL`) and recreates the container | `containrrr/watchtower` with `WATCHTOWER_POLL_INTERVAL: ${WATCHTOWER_POLL_INTERVAL:-300}` | ✅ |
| 95 | Label-gated (`WATCHTOWER_LABEL_ENABLE=true` + `com.centurylinklabs.watchtower.enable` on the collector) so it never touches unrelated containers | Label on the collector service; `--label-enable`; `WATCHTOWER_LABEL_ENABLE: "true"` | ✅ |
| 96 | The update path is a **pull**, preserving outbound-only — no inbound port, no push agent | No `ports:` key for any service; rationale stated in the compose header and argued in `deploy/README.md` | ✅ |
| 97 | The SQLite buffer must live on the named `collector_buffer` volume at `/data` | `COLLECTOR_SQLITE_PATH: /data/collector_buffer.db`, `collector_buffer:/data` mount, named volume declared; the Dockerfile pins the same default inside the image | ✅ |
| 98 | Required env vars use compose's `:?` form so a misconfigured site fails at `up` | All three required vars use `:?` with an actionable message | ✅ |

Deployment remains the most faithfully implemented section: **7/7**. The #15 fix required
no deployment change — it is confined to a single SQL statement inside the image — and the
`RETURNING` syntax it relies on is comfortably supported by the SQLite bundled with
`python:3.12-slim`.

---

## 9. Overall Score

```
┌──────────────────────────────────────────────────┐
│  Overall Score: 93/100                            │
├──────────────────────────────────────────────────┤
│  Design Match:        90 points (94/104 items)    │
│  Module Layering:    100 points (0 violations)    │
│  Deployment:         100 points (7/7)             │
│  Security:            95 points (5/5, 1 accepted) │
│  Convention:         100 points (10/10)           │
│  Code Quality:        90 points (no red smells)   │
│  Testing:             88 points (34 tests, 6 mods)│
└──────────────────────────────────────────────────┘

  2026-08-03 overall 87/100
  2026-08-04 v0.2    90/100
  2026-08-04 v0.3    93/100
```

Match Rate **90.4% ≥ 90%** → the PDCA Check gate is **met**. Proceed to the completion
report; the remaining ten items are documentation work and hardening, none of them a
correctness defect.

---

## 10. Recommended Actions

### 10.1 Immediate (within 24 hours)

| Priority | Item | Location | Gap |
|----------|------|----------|-----|
| 🟡 1 | Retrieve the future returned by `asyncio.run_coroutine_threadsafe`, or attach a done-callback that logs, so a failure inside `Collector._on_reading` is not discarded silently. Now the highest-severity code issue in the service | `opc_client.py:121` | §3.2 |

No 🔴 item remains.

### 10.2 Short-term (within 1 week)

| Priority | Item | Location | Expected impact | Gap |
|----------|------|----------|-----------------|-----|
| 🟡 1 | Apply the eight design-document updates in §11 — six of them close a counted gap outright and would lift the Match Rate to roughly 96% | design doc | Design baseline stops lagging the code | #2,#4,#7,#8,#10,#16,#24,#41 |
| 🟡 2 | Decide and implement: either re-subscribe when `tag_config` changes the monitored-item set, or explicitly document the restart requirement in design §4.2/§5 #6 | `main.py:117-127`; `opc_client.py` (needs an update entry point) | New tags and node/interval/deadband changes reach the field without a site action | #33 |
| 🟡 3 | Narrow the permanent-rejection drop from batch granularity to row granularity — bisect the batch, or quarantine rejected rows instead of deleting them | `transmitter.py:77-79, 90-91` | One poison row stops discarding up to 499 valid rows | §3.2 |
| 🟡 4 | Consult `quality` in the deadband gate so an `Uncertain`/`Bad` sample cannot become the reference value for the next `Good` one | `opc_client.py:104-108` | Removes a path where a garbage sample suppresses a real reading | §3.2 |
| 🟡 5 | Extend the integration test through `SqliteBuffer` — ideally through `Collector._on_reading` — to cover the designed OPC → collector → buffer round trip | `test_integration_opc.py:78-136` | Closes the largest untested path in the system | #72 |
| 🟢 6 | Add unit tests for `tag_config_sync.refresh_once` and `CollectorSettings.from_env` (all eight defaults, including the `2.0` the P95 target depends on) | new test modules | Two of the three remaining bare modules | §5.1 |
| 🟢 7 | Harden `TagConfig(**raw)` against unknown fields so a cloud-side schema addition cannot silently freeze edge config | `tag_config_sync.py:63` | Removes a silent-failure mode | §3.2 |

### 10.3 Long-term (backlog)

| Item | Location | Notes |
|------|----------|-------|
| Add a `UNIQUE` index on `buffer_items.seq` | `buffer.py:19-29` | Turns a future `_next_seq` regression into an immediate `IntegrityError` instead of silent cloud-side dedup. Belt-and-braces behind the new concurrency test |
| Record the SQLite ≥ 3.35 floor (`UPDATE ... RETURNING`) in design §9 or §11 | design doc | The floor is now load-bearing and undeclared; satisfied by `python:3.12-slim` today |
| Serialize wire payloads from the §3.1 dataclasses instead of inline dicts | `main.py:39-49,61-72` | Removes duplication and makes field renames type-checkable |
| Add `pytest-cov` plus a coverage floor | `pyproject.toml` | §8 sets no numeric target today, so there is nothing to regress against |
| Skip the backoff sleep on the final retry attempt | `transmitter.py:146,159` | Removes ~16 s of dead time per exhausted chain |
| Extract the duplicated alarms/readings blocks in `transmit_once` | `transmitter.py:75-96` | Duplication grew when the `PERMANENT_REJECTION` branch was added |
| Document the backlog drain time (~1.6 h for a 72 h backlog at 2 s cadence) | design §12 | Recovery-time expectation is still unstated |
| OPC UA Sign & Encrypt plus certificate issuance/rotation | — | Already tracked as `TODOS.md` #2 and correctly disclosed as deferred in design §7 |

---

## 11. Design Document Updates Needed

All ten open items are now either documentation-only (six ⚠️ plus two ❌ that are design
annotations) or hardening work. Applying the list below would move the Match Rate from
90.4% to roughly 96%.

- [ ] §3.1 — document `AlarmState` (NORMAL/HIGH/LOW) and `TransmitOutcome{inserted, duplicates, acked_ids}` (gaps #7, #8).
- [ ] §3.1 — resolve `RawReading.tag_name`: either add a `tag_name` field to `TagConfig` so a real name can flow, or state that it deliberately mirrors `tag_id` and correct the `cloud_api.design.md:240` example (gap #4).
- [ ] §3.1 — remove or relocate the "default 5000" annotation on `sampling_interval_ms`; the default lives cloud-side, not in the dataclass (gap #2).
- [ ] §3.2 — show the `seq_counter` seed row (`INSERT OR IGNORE ... VALUES (1, 0)`) in the DDL, since `_next_seq` depends on it (gap #10).
- [ ] §3.3 — state that `seq` is merged into the transmitted JSON payload, not merely stored as a buffer column (gap #16). While editing §3.3, replace "monotonic" with an explicit **uniqueness** guarantee and note that it is enforced by a single-statement `UPDATE ... RETURNING` — the wording that was too weak to be testable is exactly what let gap #15 hide.
- [ ] §4.1 / §6 — add a permanent-rejection row: 4xx outside `{408, 429}` is not retried, the batch is dropped, and a `CRITICAL` is logged. Record the deliberate tension with §1.1 "lose no data" (gap #24).
- [ ] §4.3 / §12 — change the `TRANSMIT_INTERVAL_S` default from `7.0` to `2.0`, and restate the §12 mechanism sentence as "5 s sampling + 2 s drain interval" (gap #41).
- [ ] §4.2 / §5 #6 — record that the monitored-item set is fixed at process start and that only thresholds hot-reload, or change the code to match the stated intent (gap #33).

---

## 12. Next Steps

- [x] Fix the 🔴 correctness defect (#15, non-atomic `seq` assignment) and add the concurrency test that catches it — **done this iteration**, verified atomic and verified by a test that fails against the old code.
- [ ] Write the completion report (`docs/04-report/features/collector.report.md`) — the ≥ 90% gate is met.
- [ ] Apply the eight design-document updates in §11, ideally before the report so the report cites a current baseline.
- [ ] Decide #33 (re-subscribe on tag-set change vs. document the restart requirement).
- [ ] Address the fire-and-forget coroutine at `opc_client.py:121`, the last 🟡 that contradicts a stated design principle.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-08-03 | Initial gap analysis against the reverse-derived design baseline; 103 items; 86.4% match (89 ✅ / 4 ⚠️ / 10 ❌) | bkit gap-detector |
| 0.2 | 2026-08-04 | Full independent re-analysis after the first Act iteration. Verified #24, #32, #34, #56, #74, #100 closed; #33 and #72 still open; discovered #15 (non-atomic `seq` assignment, 🔴). 33 passed / 0 failed. 104 items; 89.4% (93 ✅ / 6 ⚠️ / 5 ❌) | bkit gap-detector |
| 0.3 | 2026-08-04 | Full independent re-analysis after the #15 fix. Verified `_next_seq` now atomic via single-statement `UPDATE ... RETURNING`, bare `assert` replaced by an explicit `RuntimeError`, and a 50-way concurrent regression test added; confirmed no adjacent regression and no new defect. All ten other items re-verified as unchanged. 34 passed / 0 failed. 104 items; **90.4% (94 ✅ / 6 ⚠️ / 4 ❌) — Check gate met** | bkit gap-detector |
