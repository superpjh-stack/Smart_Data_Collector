# collector Design Document

> **Summary**: Edge OPC UA collector that subscribes to PLC tags, evaluates threshold alarms with hysteresis locally, buffers everything in SQLite, and drains that buffer to the cloud ingestion API over outbound-only HTTPS.
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
| Phase 1 | Schema Definition | N/A — data model lives in this doc §3 and the plan doc "Data Model" |
| Phase 2 | Coding Conventions | N/A — no separate conventions doc; conventions captured in §10 |
| Phase 3 | Mockup | N/A — headless service, no UI |
| Phase 4 | API Spec | See [cloud_api.design.md](./cloud_api.design.md) §4 (this service is the client) |

> **Note**: This document was reverse-derived after implementation to give the PDCA
> Check phase a design baseline. It is intended to describe the *design as decided*
> (plan doc + review outcomes), not merely to transcribe the code.

---

## 1. Overview

### 1.1 Design Goals

- Collect 100 tag points (10 PLCs × 10 tags) from an existing OPC UA gateway at a 5 s cadence, read-only.
- Keep collector→cloud-TSDB latency under 5 s at P95 (dashboard rendering is explicitly out of scope).
- Detect threshold violations **at the edge** within 3 s, without a cloud round trip.
- Lose no data across network or cloud outages: buffer locally, retransmit on recovery.
- Distinguish "equipment is fine" from "we cannot see the equipment" (gateway outage) as a first-class signal.
- Deploy and update remotely without a site visit, over outbound-only connections.

### 1.2 Design Principles

- **Outbound-only trust boundary.** The collector opens connections; nothing opens connections to it. No inbound port is exposed, including for updates.
- **At-least-once delivery, server-side dedup.** The edge never has to reason about whether a retransmit is a duplicate; it re-sends and the cloud rejects duplicates via deterministic keys.
- **Alarms outrank readings everywhere.** Buffer drain order and transmit order both put alarm records ahead of routine readings.
- **Single-responsibility modules.** OPC transport, buffering, alarm evaluation, config sync, and transmission are separate units, each independently unit-testable without the others.
- **Fail soft, never silently.** Any loop failure logs and retries rather than terminating the process; stale config is preferred over no config.

---

## 2. Architecture

### 2.1 Component Diagram

```
                   ┌──────────────────────────────────────────────────┐
   OPC UA gateway  │                  Collector process               │
   (OT network)    │                                                  │
        │          │  ┌────────────────┐      ┌──────────────────┐    │
        └─────────▶│  │OpcCollectorClnt│─────▶│   AlarmEngine    │    │
        subscribe  │  │ + _DeadbandGate│      │ (hysteresity FSM)│    │
        + heartbeat│  └───────┬────────┘      └────────┬─────────┘    │
                   │          │  readings              │ alarm events │
                   │          ▼                        ▼              │
                   │       ┌──────────────────────────────┐           │
                   │       │  SqliteBuffer (WAL, on disk) │           │
                   │       │  alarms drained before reads │           │
                   │       └───────────────┬──────────────┘           │
                   │                       │                          │
                   │  ┌─────────────────┐  ▼    ┌──────────────────┐  │
                   │  │ TagConfigStore  │  └───▶│   Transmitter    │  │
                   │  │ (since_version) │       │ retry + backoff  │  │
                   │  └────────┬────────┘       └────────┬─────────┘  │
                   └───────────┼─────────────────────────┼────────────┘
                               │ GET /config/v1/tags     │ POST /ingest/v1/*
                               │                         │ POST /status/v1/collector
                               ▼                         ▼
                          ═══════ Cloud Ingestion API (outbound HTTPS) ═══════
```

`Collector` (`main.py`) is the composition root: it opens the buffer, fetches
tag_config once synchronously so the OPC client has its monitored-item list up
front, then runs three long-lived loops concurrently via `asyncio.gather`:
tag_config poll, OPC session/reconnect loop, transmit loop.

### 2.2 Data Flow

```
OPC datachange notification
  → quality normalization (StatusCode → Good/Uncertain/Bad)
  → deadband gate (drop if |Δvalue| < deadband)
  → RawReading
  → buffer.enqueue("reading")  [assigns monotonic seq]
  → AlarmEngine.evaluate(tag, value, now, seq)   [skipped if quality != Good or tag unknown]
      → on state transition into HIGH/LOW: buffer.enqueue("alarm")
  → transmit loop: dequeue_batch (alarms first) → POST alarms → POST readings → ack (delete rows)
```

Gateway liveness runs on a separate path: heartbeat probe failure →
`on_gateway_status(plc_id, False)` → `Transmitter.send_gateway_status()` posts
directly to `/status/v1/collector`, bypassing the buffer entirely so an outage
report is never queued behind the backlog it is reporting on.

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `Collector` (main) | all modules below | Composition root, loop supervision |
| `OpcCollectorClient` | `asyncua`, `TagConfig` | Subscription, reconnect, deadband, heartbeat |
| `AlarmEngine` | `TagConfig` | Hysteresis state machine, deterministic alarm_id |
| `SqliteBuffer` | `aiosqlite` | Durable store-and-forward, priority drain, seq counter |
| `TagConfigStore` | `httpx` | Periodic `since_version` pull sync |
| `Transmitter` | `httpx`, `SqliteBuffer` | Batch POST with retry/backoff, ack-on-success |
| `CollectorSettings` | `os.environ` | Env-driven configuration |

---

## 3. Data Model

### 3.1 Entity Definitions

```python
# Configuration for one monitored tag, pulled from the cloud (config.py)
@dataclass(frozen=True)
class TagConfig:
    plc_id: str
    tag_id: str
    opc_node_id: str            # e.g. "ns=2;s=Line1.PLC01.Temp"
    unit: str | None
    data_type: str
    min_alarm: float | None
    max_alarm: float | None
    clear_margin: float          # hysteresis gap
    deadband: float              # noise suppression
    severity: str                # LOW | MEDIUM | HIGH | CRITICAL
    sampling_interval_ms: int    # default 5000
    updated_version: int         # stamped as alarm config_version

# One value observed on the wire (opc_client.py)
@dataclass(frozen=True)
class RawReading:
    plc_id: str
    tag_id: str
    tag_name: str
    value: float
    data_type: str
    unit: str | None
    quality: str                 # Good | Uncertain | Bad
    timestamp_utc: float         # unix epoch seconds, UTC

# A threshold event (alarm_engine.py)
@dataclass(frozen=True)
class AlarmEvent:
    alarm_id: str                # plc_id:tag_id:triggered_at_utc:seq
    plc_id: str
    tag_id: str
    severity: str
    condition: str               # human-readable, e.g. "value >= 80.0"
    triggered_value: float
    triggered_at_utc: datetime
    cleared_at_utc: datetime | None
    ack_status: str              # UNACKED at creation
    config_version: int          # tag.updated_version in effect at trigger time

# One buffered row awaiting transmission (buffer.py)
@dataclass(frozen=True)
class BufferedItem:
    id: int
    kind: Literal["reading", "alarm"]
    seq: int
    payload: dict                # JSON body sent to the cloud verbatim
```

### 3.2 Local Buffer Schema (SQLite)

```sql
CREATE TABLE seq_counter (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    value INTEGER NOT NULL          -- monotonic collector-local sequence
);

CREATE TABLE buffer_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL CHECK (kind IN ('reading', 'alarm')),
    seq          INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   REAL NOT NULL
);

CREATE INDEX idx_buffer_items_kind_id ON buffer_items (kind, id);
```

`journal_mode=WAL` so an unclean power loss cannot corrupt buffered data.
Drain query is a single statement — `ORDER BY (kind = 'alarm') DESC, id ASC` —
rather than two queries, so alarm priority cannot race with reading drain.

### 3.3 Sequence Semantics

`seq` is a single collector-local monotonic counter shared by readings and
alarms, assigned at enqueue time. Together with `plc_id` and `tag_id` it forms
the cloud-side dedup key, and its continuity is what lets the ≥99.5% collection
success rate be verified after the fact.

---

## 4. Interfaces

### 4.1 Outbound HTTP calls (this service is a client)

| Method | Path | Purpose | Failure handling |
|--------|------|---------|------------------|
| GET | `/config/v1/tags?since_version=N` | Pull changed tag_config rows | Log + keep stale config, retry next poll |
| POST | `/ingest/v1/readings` | Batch readings | Up to 5 attempts, exponential backoff, leave buffered |
| POST | `/ingest/v1/alarms` | Batch alarms | Same, sent before readings |
| POST | `/status/v1/collector` | `gateway_down` / `gateway_recovered` | Log on failure, not buffered |

Each ingest POST carries an `idempotency-key` header (a per-batch UUID). The
server contract is always `200 OK` with `{"inserted": N, "duplicates": M}` —
never `409` — so a duplicate needs no error branch on the client. Any `2xx` with
a parseable body means the batch is committed and its buffer rows are deleted.

### 4.2 OPC UA interface

- One subscription per PLC, `create_subscription(min(sampling_interval_ms))`, then `subscribe_data_change` over that PLC's nodes.
- Read-only: no write-back to any node (explicitly out of scope).
- Liveness probe: read the standard `Server_ServerStatus_CurrentTime` node on a timer at `heartbeat_timeout_s / 3` (min 1 s). Probes are counted, not just timed — after `round(timeout / interval)` consecutive failures the gateway is marked down and a `ConnectionError` is raised to trigger the reconnect loop.
- On any session failure: mark all PLCs down, sleep `reconnect_backoff_s`, and re-establish the session, re-creating every monitored item (OPC UA subscriptions do not survive a session drop).

### 4.3 Configuration (environment variables)

| Variable | Required | Default | Purpose |
|----------|:--------:|---------|---------|
| `OPC_ENDPOINT_URL` | yes | — | Gateway endpoint |
| `CLOUD_API_BASE_URL` | yes | — | Cloud ingestion API base |
| `COLLECTOR_PLC_IDS` | yes (effectively) | `""` | Comma-separated PLC ids to cover |
| `COLLECTOR_SQLITE_PATH` | no | `./collector_buffer.db` | Buffer file location |
| `GATEWAY_HEARTBEAT_TIMEOUT_S` | no | `15.0` | Gateway-down declaration threshold |
| `TAG_CONFIG_POLL_INTERVAL_S` | no | `60.0` | tag_config pull cadence |
| `TRANSMIT_INTERVAL_S` | no | `7.0` | Buffer drain cadence |
| `TRANSMIT_BATCH_MAX` | no | `500` | Max rows per drain |

---

## 5. Key Design Decisions

| # | Decision | Rationale | Rejected alternative |
|---|----------|-----------|----------------------|
| 1 | Deterministic `alarm_id` = `plc_id:tag_id:triggered_at_utc:seq` | A crash between local persist and cloud ack must regenerate the *same* id so the server's UNIQUE constraint rejects it | Random UUID — would create duplicate-looking alarms on the dashboard |
| 2 | Buffer drains alarms before readings | After a reconnect, up to ~1,200 backlogged readings/min must not delay an alarm raised during the outage | Single FIFO |
| 3 | Gateway-down via explicit heartbeat probe | A tag that legitimately never changes produces no notifications, so notification silence cannot imply an outage | Inferring from subscription silence |
| 4 | Deadband filtering client-side (`_DeadbandGate`) | Not every OPC UA server implements server-side `DataChangeFilter`; client-side is deterministically testable | Server-side DataChangeFilter |
| 5 | Hysteresis (`clear_margin`) separates trigger from clear | A value oscillating at the threshold would re-trigger on every sample, causing alarm flapping and operator alarm-fatigue | Single threshold |
| 6 | tag_config via periodic `since_version` pull | Threshold changes must reach the field without a manual restart; pull keeps the site outbound-only | Cloud push, or restart-only reload |
| 7 | Commit = delete-on-ack, not an offset cursor | With a mixed-priority (non-monotonic) read order, an offset cursor has nothing coherent to point at | Offset/watermark cursor |
| 8 | Alarms evaluated at the edge | 3 s alarm SLA cannot absorb a cloud round trip | Cloud-side evaluation |
| 9 | Gateway status posted outside the buffer | An outage report queued behind the backlog it describes would arrive uselessly late | Enqueue as a third buffer kind |
| 10 | Alarm evaluation skipped unless `quality == "Good"` | An `Uncertain`/`Bad` sample is not evidence of a real threshold breach; alarming on it would produce false positives during gateway trouble | Evaluate all samples |

---

## 6. Error Handling

| Condition | Detection | Response |
|-----------|-----------|----------|
| OPC session drop / gateway restart | Exception in `_connect_and_subscribe` | Mark all PLCs down, backoff, reconnect + re-subscribe from scratch |
| Gateway alive-but-unresponsive | N consecutive heartbeat probe failures | `gateway_down` event, raise to reconnect loop |
| Cloud API unreachable | `httpx.HTTPError` on POST | Exponential backoff (`base × 2^attempt`), 5 attempts, then leave rows buffered for the next cycle |
| Malformed cloud response | `KeyError` on `inserted`/`duplicates` | Treated as a transport failure — retry, do not ack |
| Partial batch success | Per-kind ack | Ack only the kind that succeeded, so a readings failure never blocks committing a successful alarm send |
| tag_config poll failure | Exception in `refresh_once` | Log and keep the last known config; never drop tags |
| Buffer approaching disk capacity | `is_near_capacity(512 MiB)` each transmit cycle | `CRITICAL` log (self-monitoring signal for extended cloud outage) |
| Unknown tag in a notification | `node_to_tag` miss | Silently ignore the notification |

---

## 7. Security Considerations

- **Outbound-only.** No listening socket in the collector process; the edge compose file exposes no ports. Cloud upload, config pull, and image pull are all collector-initiated.
- **Read-only OPC UA.** No write path to any PLC node exists in the codebase.
- **Container hardening.** Runs as non-root uid 10001; buffer lives on a `/data` volume so it survives image replacement.
- **Secrets.** No credentials are compiled in; all endpoints come from the environment. Registry credentials for updates live in the edge `.env`, not the image.
- **TLS.** Cloud transport is HTTPS in production (`CLOUD_API_BASE_URL`). OPC UA Sign & Encrypt and certificate issuance/rotation are deferred — see `TODOS.md` #2.

---

## 8. Test Plan

| Type | Target | Tool |
|------|--------|------|
| Unit | Hysteresis boundaries, deterministic `alarm_id`, seq monotonicity, priority drain, ack semantics, deadband gate | pytest |
| Integration | Real in-process OPC UA server → collector → buffer round trip, gateway-down detection | pytest + `asyncua` server |

Key cases:
- Trigger at exactly `max_alarm`; no re-trigger while held above; clear only below `max_alarm - clear_margin`.
- Same `(plc_id, tag_id, triggered_at_utc, seq)` yields a byte-identical `alarm_id`.
- Alarms enqueued after readings still dequeue first.
- Deadband suppresses sub-threshold changes and passes the first sample of a tag.
- Gateway stop is detected and surfaced as `gateway_down`.

---

## 9. Module Layout

| Module | Responsibility | Depends on |
|--------|---------------|------------|
| `config.py` | `TagConfig`, `CollectorSettings` (env parsing) | stdlib only |
| `opc_client.py` | OPC UA session/subscription, reconnect, deadband, heartbeat | `asyncua`, `config` |
| `buffer.py` | Durable buffer, seq counter, priority drain, ack, capacity check | `aiosqlite` |
| `alarm_engine.py` | Hysteresis FSM, `build_alarm_id` | `config` |
| `tag_config_sync.py` | `since_version` pull, `current_version` exposure | `httpx`, `config` |
| `transmitter.py` | Batch POST, retry/backoff, gateway-status channel | `httpx`, `buffer` |
| `main.py` | Composition root, three concurrent loops, buffer-capacity alarm | all of the above |

Dependency rule: the domain modules (`config`, `alarm_engine`) import nothing
from the I/O modules; I/O modules (`opc_client`, `buffer`, `tag_config_sync`,
`transmitter`) may import domain types but not each other, except
`transmitter → buffer`, which is its drain source. Only `main.py` knows about
every module.

---

## 10. Conventions

| Item | Convention |
|------|-----------|
| Language | Python 3.12, `from __future__ import annotations`, PEP 604 unions |
| Classes | PascalCase; module-private helpers prefixed `_` (`_DeadbandGate`, `_SubscriptionHandler`) |
| Functions/vars | snake_case; instance state prefixed `_` |
| Constants | UPPER_SNAKE_CASE (`BUFFER_CAPACITY_BYTES`) |
| Immutable value objects | `@dataclass(frozen=True)` |
| Async | `asyncio` throughout; no blocking I/O in the event loop |
| Logging | Module-level `logging.getLogger(__name__)`; `logger.exception` for caught failures |
| Env vars | `COLLECTOR_*` for collector-specific, plus `OPC_ENDPOINT_URL`, `CLOUD_API_BASE_URL`, `GATEWAY_*`, `TAG_CONFIG_*`, `TRANSMIT_*` |
| Timestamps | Always timezone-aware UTC; ISO 8601 on the wire |

---

## 11. Deployment

- Packaged as a two-stage Docker image (`collector/Dockerfile`): builder installs into `/opt/venv`, runtime copies the venv, drops to uid 10001, declares `VOLUME ["/data"]`, entrypoint `python -m collector.main`.
- Field devices run **only** `deploy/docker-compose.edge.yml` (collector + Watchtower). The root `docker-compose.yml` is the cloud stack and must not be deployed to the edge.
- Remote update: Watchtower polls the registry every 5 min (`WATCHTOWER_POLL_INTERVAL`) for the tracked tag and recreates the container. It is label-gated (`WATCHTOWER_LABEL_ENABLE=true` + `com.centurylinklabs.watchtower.enable` on the collector), so it never touches unrelated containers on the host.
- The update path is a **pull**, preserving the outbound-only boundary — no inbound port, no push agent.
- The SQLite buffer must live on the named `collector_buffer` volume at `/data`, so an image swap mid-outage does not discard buffered data.
- Required env vars use compose's `:?` form so a misconfigured site fails at `up` rather than crash-looping after `from_env()`.

---

## 12. Non-functional Requirements

| Requirement | Target | Mechanism |
|-------------|--------|-----------|
| Collection completeness | ≥ 99.5%, denominator excludes gateway-down time | `seq` continuity; `gateway_down` events bound the denominator |
| Collect→TSDB latency | ≤ 5 s at P95 | 5 s sampling + 7 s drain interval; alarms jump the queue |
| Alarm detection latency | ≤ 3 s from threshold breach | Edge-local evaluation on the notification path |
| Outage survival | ≥ 72 h of buffered data | SQLite WAL buffer, ~512 MiB capacity budget |
| Recovery ordering | Alarms before backlogged readings, readings in order | Priority drain + `ORDER BY id` |
| Reconnect | Automatic, no manual intervention | Reconnect loop with full re-subscription |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-08-03 | Initial baseline, reverse-derived from plan doc + implementation | — |
