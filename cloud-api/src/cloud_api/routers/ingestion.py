from fastapi import APIRouter, Header

from cloud_api.db import get_pool
from cloud_api.schemas import AlarmsBatch, IngestResult, ReadingsBatch

router = APIRouter(prefix="/ingest/v1", tags=["ingestion"])


@router.post("/readings", response_model=IngestResult)
async def ingest_readings(
    batch: ReadingsBatch,
    idempotency_key: str | None = Header(default=None),
) -> IngestResult:
    """Batch-insert readings.

    Duplicates (retransmits after a collector crash/reconnect, at-least-once
    delivery) are NOT an error — see Issue 7. We always return 200 with an
    inserted/duplicates count so the collector's retry loop has no error
    branch to get wrong; ON CONFLICT DO NOTHING + RETURNING tells us exactly
    how many rows actually landed.
    """
    if not batch.readings:
        return IngestResult(inserted=0, duplicates=0)

    pool = get_pool()
    rows = [
        (
            r.plc_id,
            r.tag_id,
            r.tag_name,
            r.timestamp_utc,
            r.value,
            r.data_type,
            r.unit,
            r.quality,
            r.seq,
        )
        for r in batch.readings
    ]

    async with pool.acquire() as conn:
        inserted_rows = await conn.fetch(
            """
            INSERT INTO readings
                (plc_id, tag_id, tag_name, timestamp_utc, value, data_type, unit, quality, seq)
            SELECT * FROM unnest(
                $1::text[], $2::text[], $3::text[], $4::timestamptz[],
                $5::double precision[], $6::text[], $7::text[], $8::text[], $9::bigint[]
            )
            ON CONFLICT (plc_id, tag_id, seq, timestamp_utc) DO NOTHING
            RETURNING plc_id
            """,
            *zip(*rows),
        )

    inserted = len(inserted_rows)
    return IngestResult(inserted=inserted, duplicates=len(rows) - inserted)


@router.post("/alarms", response_model=IngestResult)
async def ingest_alarms(
    batch: AlarmsBatch,
    idempotency_key: str | None = Header(default=None),
) -> IngestResult:
    if not batch.alarms:
        return IngestResult(inserted=0, duplicates=0)

    pool = get_pool()
    rows = [
        (
            a.alarm_id,
            a.plc_id,
            a.tag_id,
            a.severity,
            a.condition,
            a.triggered_value,
            a.triggered_at_utc,
            a.cleared_at_utc,
            a.ack_status,
            a.seq,
            a.config_version,
        )
        for a in batch.alarms
    ]

    async with pool.acquire() as conn:
        inserted_rows = await conn.fetch(
            """
            INSERT INTO alarms
                (alarm_id, plc_id, tag_id, severity, condition, triggered_value,
                 triggered_at_utc, cleared_at_utc, ack_status, seq, config_version)
            SELECT * FROM unnest(
                $1::text[], $2::text[], $3::text[], $4::text[], $5::text[],
                $6::double precision[], $7::timestamptz[], $8::timestamptz[],
                $9::text[], $10::bigint[], $11::bigint[]
            )
            ON CONFLICT (alarm_id) DO NOTHING
            RETURNING alarm_id
            """,
            *zip(*rows),
        )

    inserted = len(inserted_rows)
    return IngestResult(inserted=inserted, duplicates=len(rows) - inserted)
