"""Collector status endpoint tests against a real TimescaleDB (see conftest.py)."""

from datetime import datetime, timezone


async def test_report_gateway_down_lands_in_db(client, db_conn):
    event = {
        "plc_id": "PLC-01",
        "status": "gateway_down",
        "occurred_at": datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
    }
    resp = await client.post("/status/v1/collector", json=event)
    assert resp.status_code == 201
    assert resp.json() == {"status": "recorded"}

    row = await db_conn.fetchrow(
        "SELECT plc_id, status FROM collector_status_events WHERE plc_id = $1", "PLC-01"
    )
    assert row is not None
    assert row["status"] == "gateway_down"


async def test_report_gateway_recovered_after_down(client, db_conn):
    base_ts = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    await client.post(
        "/status/v1/collector",
        json={"plc_id": "PLC-01", "status": "gateway_down", "occurred_at": base_ts.isoformat()},
    )
    resp = await client.post(
        "/status/v1/collector",
        json={
            "plc_id": "PLC-01",
            "status": "gateway_recovered",
            "occurred_at": base_ts.replace(minute=5).isoformat(),
        },
    )
    assert resp.status_code == 201

    count = await db_conn.fetchval(
        "SELECT count(*) FROM collector_status_events WHERE plc_id = $1", "PLC-01"
    )
    assert count == 2


async def test_report_invalid_status_rejected(client):
    resp = await client.post(
        "/status/v1/collector",
        json={
            "plc_id": "PLC-01",
            "status": "not_a_real_status",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert resp.status_code == 422


async def test_gateways_returns_latest_status_per_plc(client):
    base_ts = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    await client.post(
        "/status/v1/collector",
        json={"plc_id": "PLC-01", "status": "gateway_down", "occurred_at": base_ts.isoformat()},
    )
    await client.post(
        "/status/v1/collector",
        json={
            "plc_id": "PLC-01",
            "status": "gateway_recovered",
            "occurred_at": base_ts.replace(minute=5).isoformat(),
        },
    )
    await client.post(
        "/status/v1/collector",
        json={"plc_id": "PLC-02", "status": "gateway_down", "occurred_at": base_ts.isoformat()},
    )

    resp = await client.get("/status/v1/gateways")
    assert resp.status_code == 200
    by_plc = {g["plc_id"]: g["status"] for g in resp.json()["gateways"]}

    assert by_plc == {"PLC-01": "gateway_recovered", "PLC-02": "gateway_down"}


async def test_gateways_empty_when_no_events(client):
    resp = await client.get("/status/v1/gateways")
    assert resp.status_code == 200
    assert resp.json() == {"gateways": []}


async def test_gateway_history_returns_full_event_log_most_recent_first(client):
    base_ts = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    await client.post(
        "/status/v1/collector",
        json={"plc_id": "PLC-01", "status": "gateway_down", "occurred_at": base_ts.isoformat()},
    )
    await client.post(
        "/status/v1/collector",
        json={
            "plc_id": "PLC-01",
            "status": "gateway_recovered",
            "occurred_at": base_ts.replace(minute=5).isoformat(),
        },
    )

    resp = await client.get("/status/v1/gateways/history", params={"plc_id": "PLC-01"})
    assert resp.status_code == 200
    statuses = [g["status"] for g in resp.json()["gateways"]]
    assert statuses == ["gateway_recovered", "gateway_down"]


async def test_gateway_history_respects_limit(client):
    base_ts = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(5):
        status = "gateway_down" if i % 2 == 0 else "gateway_recovered"
        await client.post(
            "/status/v1/collector",
            json={"plc_id": "PLC-01", "status": status, "occurred_at": base_ts.replace(minute=i).isoformat()},
        )

    resp = await client.get("/status/v1/gateways/history", params={"limit": 2})
    assert len(resp.json()["gateways"]) == 2
