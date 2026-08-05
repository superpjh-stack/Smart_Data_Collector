"""Dashboard read-endpoint tests (readings/v1) against a real TimescaleDB."""

from datetime import datetime, timezone


def _reading(plc_id: str, tag_id: str, seq: int, value: float, timestamp: datetime) -> dict:
    return {
        "plc_id": plc_id,
        "tag_id": tag_id,
        "tag_name": tag_id,
        "timestamp_utc": timestamp.isoformat(),
        "value": value,
        "data_type": "float",
        "unit": "C",
        "quality": "Good",
        "seq": seq,
    }


async def test_latest_returns_one_row_per_plc_and_tag(client):
    t0 = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
    batch = {
        "readings": [
            _reading("PLC-01", "TEMP_01", seq=1, value=60.0, timestamp=t0),
            _reading("PLC-01", "TEMP_01", seq=2, value=61.5, timestamp=t0.replace(minute=1)),
            _reading("PLC-02", "PRESS_01", seq=1, value=4.2, timestamp=t0),
        ]
    }
    resp = await client.post("/ingest/v1/readings", json=batch)
    assert resp.json()["inserted"] == 3

    latest = await client.get("/readings/v1/latest")
    assert latest.status_code == 200
    rows = {(r["plc_id"], r["tag_id"]): r for r in latest.json()["readings"]}

    assert len(rows) == 2
    assert rows[("PLC-01", "TEMP_01")]["value"] == 61.5
    assert rows[("PLC-02", "PRESS_01")]["value"] == 4.2


async def test_latest_empty_when_no_readings(client):
    resp = await client.get("/readings/v1/latest")
    assert resp.status_code == 200
    assert resp.json() == {"readings": []}


async def test_history_reads_from_continuous_aggregate(client, db_conn):
    # Real current time, not a fixed 2026 date: the endpoint filters on
    # `bucket > now() - minutes interval`, so a stale fixed timestamp would
    # fall outside the window depending on when the suite actually runs.
    t0 = datetime.now(timezone.utc)
    batch = {
        "readings": [
            _reading("PLC-01", "TEMP_01", seq=1, value=60.0, timestamp=t0),
            _reading("PLC-01", "TEMP_01", seq=2, value=64.0, timestamp=t0.replace(minute=1)),
        ]
    }
    await client.post("/ingest/v1/readings", json=batch)

    # Continuous aggregates refresh on a schedule in production; force a
    # synchronous refresh so the test doesn't depend on timing.
    await db_conn.execute(
        "CALL refresh_continuous_aggregate('readings_1min', now() - interval '1 day', now() + interval '1 day')"
    )

    resp = await client.get(
        "/readings/v1/history", params={"plc_id": "PLC-01", "tag_id": "TEMP_01", "minutes": 60}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plc_id"] == "PLC-01"
    assert len(body["points"]) >= 1
    assert sum(p["sample_count"] for p in body["points"]) == 2


async def test_history_empty_for_unknown_tag(client):
    resp = await client.get(
        "/readings/v1/history", params={"plc_id": "PLC-99", "tag_id": "NOPE", "minutes": 60}
    )
    assert resp.status_code == 200
    assert resp.json()["points"] == []


async def test_throughput_counts_readings_in_window(client):
    # Uses the real current time (not a fixed 2026 date like the other tests
    # in this file) because the endpoint filters on `now() - interval`.
    t0 = datetime.now(timezone.utc)
    batch = {
        "readings": [
            _reading("PLC-01", "TEMP_01", seq=1, value=60.0, timestamp=t0),
            _reading("PLC-01", "TEMP_01", seq=2, value=61.0, timestamp=t0),
            _reading("PLC-02", "PRESS_01", seq=1, value=4.0, timestamp=t0),
        ]
    }
    await client.post("/ingest/v1/readings", json=batch)

    resp = await client.get("/readings/v1/throughput", params={"minutes": 1440})
    assert resp.status_code == 200
    points = resp.json()["points"]
    assert sum(p["reading_count"] for p in points) == 3
