"""Seed/refresh the dev `tag_config` row(s) for the manual dev-stack functional test.

Safe to re-run: upserts on (plc_id, tag_id) and always bumps `updated_version` off
`config_version_seq`, so a collector polling with any `since_version` (including a
stale cursor from a previous run) will pick up the change on its next poll.

The dev seed is durable as of 2026-08-03 -- the pytest suite runs against its own
`smart_data_collector_test` database, so it no longer wipes this row. Run this only
to establish the row in a fresh dev database (or to change the tag definition below).

Usage:
    cd cloud-api && source .venv/Scripts/activate
    export DATABASE_URL="postgresql://sdc:sdc_dev_pw@127.0.0.1:5442/smart_data_collector"
    python scripts/seed_dev_tag_config.py
"""

import asyncio
import os

import asyncpg

DEV_DSN = "postgresql://sdc:sdc_dev_pw@127.0.0.1:5442/smart_data_collector"

TAGS = [
    dict(
        plc_id="PLC-01",
        tag_id="TEMP_01",
        opc_node_id="ns=2;s=Temp01",
        unit="C",
        data_type="float",
        min_alarm=None,
        max_alarm=80.0,
        clear_margin=2.0,
        deadband=1.0,
        severity="HIGH",
        sampling_interval_ms=1000,
    ),
]


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL", DEV_DSN)
    conn = await asyncpg.connect(dsn=dsn)
    try:
        for tag in TAGS:
            version = await conn.fetchval("SELECT nextval('config_version_seq')")
            await conn.execute(
                """
                INSERT INTO tag_config
                    (plc_id, tag_id, opc_node_id, unit, data_type, min_alarm, max_alarm,
                     clear_margin, deadband, severity, sampling_interval_ms, updated_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (plc_id, tag_id) DO UPDATE SET
                    opc_node_id = EXCLUDED.opc_node_id,
                    unit = EXCLUDED.unit,
                    data_type = EXCLUDED.data_type,
                    min_alarm = EXCLUDED.min_alarm,
                    max_alarm = EXCLUDED.max_alarm,
                    clear_margin = EXCLUDED.clear_margin,
                    deadband = EXCLUDED.deadband,
                    severity = EXCLUDED.severity,
                    sampling_interval_ms = EXCLUDED.sampling_interval_ms,
                    updated_version = EXCLUDED.updated_version,
                    updated_at = now()
                """,
                tag["plc_id"],
                tag["tag_id"],
                tag["opc_node_id"],
                tag["unit"],
                tag["data_type"],
                tag["min_alarm"],
                tag["max_alarm"],
                tag["clear_margin"],
                tag["deadband"],
                tag["severity"],
                tag["sampling_interval_ms"],
                version,
            )
            print(f"seeded {tag['plc_id']}/{tag['tag_id']} at updated_version={version}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
