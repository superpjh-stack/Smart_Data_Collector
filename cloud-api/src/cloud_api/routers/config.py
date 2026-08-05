from fastapi import APIRouter, Query

from cloud_api.db import get_pool
from cloud_api.schemas import TagConfig, TagConfigResponse

router = APIRouter(prefix="/config/v1", tags=["config"])


@router.get("/tags", response_model=TagConfigResponse)
async def get_tag_config(since_version: int = Query(default=0, ge=0)) -> TagConfigResponse:
    """Collector-side periodic pull sync (Issue 6).

    Returns only rows changed since `since_version` so the poll stays cheap
    even with a large tag_config table; `current_version` lets the collector
    know what it's now caught up to (and expose it for the dashboard's
    "did the threshold change land?" check).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT plc_id, tag_id, opc_node_id, unit, data_type,
                   min_alarm, max_alarm, clear_margin, deadband, severity,
                   sampling_interval_ms, updated_version
            FROM tag_config
            WHERE updated_version > $1
            ORDER BY updated_version
            """,
            since_version,
        )
        current_version = await conn.fetchval(
            "SELECT last_value FROM config_version_seq"
        )

    tags = [TagConfig(**dict(row)) for row in rows]
    return TagConfigResponse(tags=tags, current_version=current_version or 0)
