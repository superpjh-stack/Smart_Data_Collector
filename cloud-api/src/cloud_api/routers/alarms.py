from typing import Literal

from fastapi import APIRouter, Query

from cloud_api.db import get_pool
from cloud_api.schemas import Alarm, AlarmsListResponse

router = APIRouter(prefix="/alarms/v1", tags=["alarms"])


@router.get("", response_model=AlarmsListResponse)
async def list_alarms(
    ack_status: Literal["UNACKED", "ACKED", "AUTO_CLEARED"] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> AlarmsListResponse:
    """Most-recent-first alarm feed for the dashboard's alarm panel.

    `ack_status` lets the UI show only what still needs attention without a
    client-side filter over a potentially large page.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        if ack_status is None:
            rows = await conn.fetch(
                """
                SELECT alarm_id, plc_id, tag_id, severity, condition, triggered_value,
                       triggered_at_utc, cleared_at_utc, ack_status, seq, config_version
                FROM alarms
                ORDER BY triggered_at_utc DESC
                LIMIT $1
                """,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT alarm_id, plc_id, tag_id, severity, condition, triggered_value,
                       triggered_at_utc, cleared_at_utc, ack_status, seq, config_version
                FROM alarms
                WHERE ack_status = $1
                ORDER BY triggered_at_utc DESC
                LIMIT $2
                """,
                ack_status,
                limit,
            )
    return AlarmsListResponse(alarms=[Alarm(**dict(row)) for row in rows])
