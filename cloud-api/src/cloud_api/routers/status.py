from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from cloud_api.db import get_pool
from cloud_api.schemas import GatewayStatus, GatewaysStatusResponse

router = APIRouter(prefix="/status/v1", tags=["status"])


class CollectorStatusEvent(BaseModel):
    plc_id: str
    status: Literal["gateway_down", "gateway_recovered"]
    occurred_at: datetime


@router.post("/collector", status_code=201)
async def report_collector_status(event: CollectorStatusEvent) -> dict[str, str]:
    """Issue 3: lets the dashboard show 'data unavailable' distinctly from
    'equipment reading is normal' instead of silently displaying stale values
    during a gateway outage."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO collector_status_events (plc_id, status, occurred_at)
            VALUES ($1, $2, $3)
            """,
            event.plc_id,
            event.status,
            event.occurred_at,
        )
    return {"status": "recorded"}


@router.get("/gateways", response_model=GatewaysStatusResponse)
async def get_gateway_statuses() -> GatewaysStatusResponse:
    """Latest status per plc_id — the current up/down state each PLC card's
    status pill is driven from, distinct from the full event history."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (plc_id) plc_id, status, occurred_at
            FROM collector_status_events
            ORDER BY plc_id, occurred_at DESC
            """
        )
    return GatewaysStatusResponse(gateways=[GatewayStatus(**dict(row)) for row in rows])


@router.get("/gateways/history", response_model=GatewaysStatusResponse)
async def get_gateway_history(
    plc_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> GatewaysStatusResponse:
    """Full down/recovered event log — backs the dashboard's gateway history tab."""
    pool = get_pool()
    async with pool.acquire() as conn:
        if plc_id is None:
            rows = await conn.fetch(
                """
                SELECT plc_id, status, occurred_at FROM collector_status_events
                ORDER BY occurred_at DESC LIMIT $1
                """,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT plc_id, status, occurred_at FROM collector_status_events
                WHERE plc_id = $1
                ORDER BY occurred_at DESC LIMIT $2
                """,
                plc_id,
                limit,
            )
    return GatewaysStatusResponse(gateways=[GatewayStatus(**dict(row)) for row in rows])
