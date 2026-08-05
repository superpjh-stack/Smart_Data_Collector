"""Tag-config sync endpoint tests against a real TimescaleDB (see conftest.py)."""


async def _seed_tag(db_conn, plc_id: str, tag_id: str) -> int:
    version = await db_conn.fetchval("SELECT nextval('config_version_seq')")
    await db_conn.execute(
        """
        INSERT INTO tag_config
            (plc_id, tag_id, opc_node_id, unit, data_type, min_alarm, max_alarm,
             clear_margin, deadband, severity, sampling_interval_ms, updated_version)
        VALUES ($1, $2, $3, 'C', 'float', NULL, 80.0, 2.0, 1.0, 'MEDIUM', 5000, $4)
        """,
        plc_id,
        tag_id,
        f"ns=2;s=Node_{tag_id}",
        version,
    )
    return version


async def test_get_all_tags_since_zero(client, db_conn):
    v1 = await _seed_tag(db_conn, "PLC-01", "TEMP_01")
    v2 = await _seed_tag(db_conn, "PLC-01", "TEMP_02")

    resp = await client.get("/config/v1/tags", params={"since_version": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_version"] == v2
    tag_ids = {t["tag_id"] for t in body["tags"]}
    assert tag_ids == {"TEMP_01", "TEMP_02"}
    assert v1 < v2


async def test_get_tags_only_returns_rows_newer_than_since_version(client, db_conn):
    v1 = await _seed_tag(db_conn, "PLC-01", "TEMP_01")
    v2 = await _seed_tag(db_conn, "PLC-01", "TEMP_02")

    resp = await client.get("/config/v1/tags", params={"since_version": v1})
    body = resp.json()
    assert body["current_version"] == v2
    assert [t["tag_id"] for t in body["tags"]] == ["TEMP_02"]


async def test_get_tags_empty_when_nothing_seeded(client):
    # A freshly-RESTARTed sequence reports last_value=1 even before any
    # nextval() call (is_called=false), which is what the endpoint reads —
    # so current_version is 1, not 0, in a clean/never-configured DB.
    resp = await client.get("/config/v1/tags", params={"since_version": 0})
    assert resp.status_code == 200
    assert resp.json() == {"tags": [], "current_version": 1}
