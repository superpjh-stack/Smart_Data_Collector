from fastapi import APIRouter, Query

from cloud_api.db import get_pool
from cloud_api.schemas import (
    LatestReading,
    LatestReadingsResponse,
    ReadingPoint,
    ReadingsHistoryResponse,
    ThroughputPoint,
    ThroughputResponse,
)

router = APIRouter(prefix="/readings/v1", tags=["readings"])


@router.get("/latest", response_model=LatestReadingsResponse)
async def get_latest_readings() -> LatestReadingsResponse:
    """One row per (plc_id, tag_id): the most recent reading received.

    Backs the dashboard overview grid — each PLC card shows its tags' current
    values. `DISTINCT ON` + the (plc_id, tag_id, timestamp_utc DESC) index
    added in migration 002 keeps this a single index scan per tag rather than
    an aggregate over the full hypertable.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (plc_id, tag_id)
                plc_id, tag_id, tag_name, value, unit, data_type, quality, timestamp_utc
            FROM readings
            ORDER BY plc_id, tag_id, timestamp_utc DESC
            """
        )
    return LatestReadingsResponse(readings=[LatestReading(**dict(row)) for row in rows])


@router.get("/history", response_model=ReadingsHistoryResponse)
async def get_reading_history(
    plc_id: str,
    tag_id: str,
    minutes: int = Query(default=60, ge=1, le=1440),
) -> ReadingsHistoryResponse:
    """Sparkline / trend source: 1-minute buckets from the continuous
    aggregate (not raw `readings`) — see design's Issue 8 rationale for why
    long-range dashboard reads must never scan the raw hypertable directly.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT bucket, avg_value, min_value, max_value, sample_count
            FROM readings_1min
            WHERE plc_id = $1 AND tag_id = $2
              AND bucket > now() - ($3 || ' minutes')::interval
            ORDER BY bucket
            """,
            plc_id,
            tag_id,
            str(minutes),
        )
    return ReadingsHistoryResponse(
        plc_id=plc_id,
        tag_id=tag_id,
        points=[ReadingPoint(**dict(row)) for row in rows],
    )


@router.get("/throughput", response_model=ThroughputResponse)
async def get_throughput(
    minutes: int = Query(default=60, ge=1, le=1440),
) -> ThroughputResponse:
    """Ingest rate over time, bucketed per minute — backs the footer chart.

    Reads raw `readings` bucketed by `timestamp_utc` (the hypertable's
    partitioning column, always indexed) rather than `ingested_at`, so this
    stays a single index-range scan even without an extra index.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT time_bucket('1 minute', timestamp_utc) AS bucket, count(*) AS reading_count
            FROM readings
            WHERE timestamp_utc > now() - ($1 || ' minutes')::interval
            GROUP BY bucket
            ORDER BY bucket
            """,
            str(minutes),
        )
    return ThroughputResponse(points=[ThroughputPoint(**dict(row)) for row in rows])
