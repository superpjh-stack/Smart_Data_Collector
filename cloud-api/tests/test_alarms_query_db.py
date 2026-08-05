"""Dashboard alarm-feed endpoint tests (GET /alarms/v1) against a real TimescaleDB."""

from datetime import datetime, timezone


def _alarm(alarm_id: str, seq: int, triggered_at: datetime, ack_status: str = "UNACKED") -> dict:
    return {
        "alarm_id": alarm_id,
        "plc_id": "PLC-01",
        "tag_id": "TEMP_01",
        "severity": "HIGH",
        "condition": "max_alarm",
        "triggered_value": 95.0,
        "triggered_at_utc": triggered_at.isoformat(),
        "ack_status": ack_status,
        "seq": seq,
        "config_version": 1,
    }


async def test_list_alarms_most_recent_first(client):
    t0 = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
    batch = {
        "alarms": [
            _alarm("PLC-01:TEMP_01:t0:1", seq=1, triggered_at=t0),
            _alarm("PLC-01:TEMP_01:t1:2", seq=2, triggered_at=t0.replace(minute=5)),
        ]
    }
    resp = await client.post("/ingest/v1/alarms", json=batch)
    assert resp.json()["inserted"] == 2

    listed = await client.get("/alarms/v1")
    assert listed.status_code == 200
    ids = [a["alarm_id"] for a in listed.json()["alarms"]]
    assert ids == ["PLC-01:TEMP_01:t1:2", "PLC-01:TEMP_01:t0:1"]


async def test_list_alarms_filters_by_ack_status(client):
    t0 = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
    batch = {
        "alarms": [
            _alarm("PLC-01:TEMP_01:a:1", seq=1, triggered_at=t0, ack_status="UNACKED"),
            _alarm("PLC-01:TEMP_01:b:2", seq=2, triggered_at=t0.replace(minute=1), ack_status="ACKED"),
        ]
    }
    await client.post("/ingest/v1/alarms", json=batch)

    unacked = await client.get("/alarms/v1", params={"ack_status": "UNACKED"})
    assert [a["alarm_id"] for a in unacked.json()["alarms"]] == ["PLC-01:TEMP_01:a:1"]

    acked = await client.get("/alarms/v1", params={"ack_status": "ACKED"})
    assert [a["alarm_id"] for a in acked.json()["alarms"]] == ["PLC-01:TEMP_01:b:2"]


async def test_list_alarms_respects_limit(client):
    t0 = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
    batch = {
        "alarms": [
            _alarm(f"PLC-01:TEMP_01:x:{i}", seq=i, triggered_at=t0.replace(minute=i))
            for i in range(1, 6)
        ]
    }
    await client.post("/ingest/v1/alarms", json=batch)

    resp = await client.get("/alarms/v1", params={"limit": 2})
    assert len(resp.json()["alarms"]) == 2


async def test_list_alarms_empty_when_none(client):
    resp = await client.get("/alarms/v1")
    assert resp.status_code == 200
    assert resp.json() == {"alarms": []}
