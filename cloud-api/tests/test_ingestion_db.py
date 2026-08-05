"""Ingestion endpoint tests against a real TimescaleDB (see conftest.py)."""

from datetime import datetime, timezone


def _reading(seq: int, value: float = 42.0, timestamp: datetime | None = None) -> dict:
    return {
        "plc_id": "PLC-01",
        "tag_id": "TEMP_01",
        "tag_name": "Temp01",
        "timestamp_utc": (timestamp or datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)).isoformat(),
        "value": value,
        "data_type": "float",
        "unit": "C",
        "quality": "Good",
        "seq": seq,
    }


def _alarm(seq: int, alarm_id: str, triggered_at: datetime | None = None) -> dict:
    return {
        "alarm_id": alarm_id,
        "plc_id": "PLC-01",
        "tag_id": "TEMP_01",
        "severity": "HIGH",
        "condition": "max_alarm",
        "triggered_value": 95.0,
        "triggered_at_utc": (triggered_at or datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)).isoformat(),
        "ack_status": "UNACKED",
        "seq": seq,
        "config_version": 1,
    }


async def test_empty_readings_batch_is_noop(client):
    resp = await client.post("/ingest/v1/readings", json={"readings": []})
    assert resp.status_code == 200
    assert resp.json() == {"inserted": 0, "duplicates": 0}


async def test_insert_readings_lands_in_db(client, db_conn):
    batch = {"readings": [_reading(seq=1), _reading(seq=2)]}
    resp = await client.post("/ingest/v1/readings", json=batch)
    assert resp.status_code == 200
    assert resp.json() == {"inserted": 2, "duplicates": 0}

    count = await db_conn.fetchval("SELECT count(*) FROM readings WHERE plc_id = 'PLC-01'")
    assert count == 2


async def test_retransmit_same_readings_dedups(client):
    batch = {"readings": [_reading(seq=1), _reading(seq=2)]}
    first = await client.post("/ingest/v1/readings", json=batch)
    assert first.json() == {"inserted": 2, "duplicates": 0}

    # Same (plc_id, tag_id, seq, timestamp_utc) -> ON CONFLICT DO NOTHING.
    # Regression guard for the hypertable PK bug fixed 2026-07-14.
    retransmit = await client.post("/ingest/v1/readings", json=batch)
    assert retransmit.status_code == 200
    assert retransmit.json() == {"inserted": 0, "duplicates": 2}


async def test_partial_overlap_readings_batch_splits_correctly(client):
    await client.post("/ingest/v1/readings", json={"readings": [_reading(seq=1)]})

    mixed = {"readings": [_reading(seq=1), _reading(seq=2), _reading(seq=3)]}
    resp = await client.post("/ingest/v1/readings", json=mixed)
    assert resp.json() == {"inserted": 2, "duplicates": 1}


async def test_empty_alarms_batch_is_noop(client):
    resp = await client.post("/ingest/v1/alarms", json={"alarms": []})
    assert resp.status_code == 200
    assert resp.json() == {"inserted": 0, "duplicates": 0}


async def test_insert_alarms_lands_in_db(client, db_conn):
    batch = {"alarms": [_alarm(seq=1, alarm_id="PLC-01:TEMP_01:2026-07-14T12:00:00Z:1")]}
    resp = await client.post("/ingest/v1/alarms", json=batch)
    assert resp.status_code == 200
    assert resp.json() == {"inserted": 1, "duplicates": 0}

    row = await db_conn.fetchrow("SELECT * FROM alarms WHERE alarm_id = $1", batch["alarms"][0]["alarm_id"])
    assert row is not None
    assert row["severity"] == "HIGH"


async def test_retransmit_same_alarm_id_dedups(client):
    alarm_id = "PLC-01:TEMP_01:2026-07-14T12:00:00Z:1"
    batch = {"alarms": [_alarm(seq=1, alarm_id=alarm_id)]}

    first = await client.post("/ingest/v1/alarms", json=batch)
    assert first.json() == {"inserted": 1, "duplicates": 0}

    retransmit = await client.post("/ingest/v1/alarms", json=batch)
    assert retransmit.json() == {"inserted": 0, "duplicates": 1}
