from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx

from collector.buffer import BufferedItem, SqliteBuffer

logger = logging.getLogger(__name__)

# Sentinel result from _send_with_retry: the server permanently rejected this
# batch (a 4xx that retrying cannot fix). The rows must be dropped rather than
# left buffered — because dequeue_batch puts alarms at the head of the queue, a
# single poison alarm row left in place would block the alarm channel forever.
PERMANENT_REJECTION: Literal["permanent_rejection"] = "permanent_rejection"

# 408 Request Timeout and 429 Too Many Requests are the two 4xx codes that are
# about *this attempt*, not about the payload, so they stay retryable.
_RETRYABLE_4XX = frozenset({408, 429})


@dataclass(frozen=True)
class TransmitOutcome:
    inserted: int
    duplicates: int
    acked_ids: list[int]


class Transmitter:
    """Drains the local buffer to the cloud API.

    Alarms and readings are sent to separate endpoints per the transmission
    format, but `SqliteBuffer.dequeue_batch` already orders alarms first
    (Issue 2) — this class just needs to send each kind's slice and ack only
    what actually landed, so a readings failure never blocks a successful
    alarm send from being committed.

    Duplicate delivery is not an error (Issue 7): the cloud API always
    responds 200 with an inserted/duplicates count, so success here means
    "the HTTP call didn't fail," not "every row was new."
    """

    def __init__(
        self,
        buffer: SqliteBuffer,
        base_url: str,
        batch_max: int = 500,
        max_retries: int = 5,
        backoff_base_s: float = 1.0,
    ) -> None:
        self._buffer = buffer
        self._base_url = base_url.rstrip("/")
        self._batch_max = batch_max
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s

    async def transmit_once(self, client: httpx.AsyncClient) -> TransmitOutcome:
        items = await self._buffer.dequeue_batch(self._batch_max)
        if not items:
            return TransmitOutcome(inserted=0, duplicates=0, acked_ids=[])

        alarms = [i for i in items if i.kind == "alarm"]
        readings = [i for i in items if i.kind == "reading"]

        total_inserted = 0
        total_duplicates = 0
        acked_ids: list[int] = []

        if alarms:
            result = await self._send_with_retry("/ingest/v1/alarms", "alarms", alarms, client)
            if result == PERMANENT_REJECTION:
                # Dropped, not delivered: ack to unblock the channel.
                acked_ids.extend(i.id for i in alarms)
            elif result is not None:
                inserted, duplicates = result
                total_inserted += inserted
                total_duplicates += duplicates
                acked_ids.extend(i.id for i in alarms)

        if readings:
            result = await self._send_with_retry(
                "/ingest/v1/readings", "readings", readings, client
            )
            if result == PERMANENT_REJECTION:
                acked_ids.extend(i.id for i in readings)
            elif result is not None:
                inserted, duplicates = result
                total_inserted += inserted
                total_duplicates += duplicates
                acked_ids.extend(i.id for i in readings)

        if acked_ids:
            await self._buffer.ack(acked_ids)

        return TransmitOutcome(
            inserted=total_inserted, duplicates=total_duplicates, acked_ids=acked_ids
        )

    async def _send_with_retry(
        self,
        path: str,
        field_name: str,
        items: list[BufferedItem],
        client: httpx.AsyncClient,
    ) -> tuple[int, int] | Literal["permanent_rejection"] | None:
        idempotency_key = str(uuid.uuid4())
        payload = {field_name: [i.payload for i in items]}

        for attempt in range(self._max_retries):
            try:
                resp = await client.post(
                    f"{self._base_url}{path}",
                    json=payload,
                    headers={"idempotency-key": idempotency_key},
                    timeout=15.0,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["inserted"], data["duplicates"]
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if 400 <= status_code < 500 and status_code not in _RETRYABLE_4XX:
                    logger.critical(
                        "transmit %s permanently rejected with HTTP %d; dropping %d "
                        "item(s) to unblock the channel",
                        path,
                        status_code,
                        len(items),
                    )
                    return PERMANENT_REJECTION
                backoff = self._backoff_base_s * (2**attempt)
                logger.warning(
                    "transmit %s failed with HTTP %d (attempt %d/%d), retrying in %.1fs",
                    path,
                    status_code,
                    attempt + 1,
                    self._max_retries,
                    backoff,
                )
                await asyncio.sleep(backoff)
            except (httpx.HTTPError, KeyError, json.JSONDecodeError):
                # json.JSONDecodeError covers a 2xx whose body will not parse:
                # per design §6 an unreadable body is a transport failure, so it
                # must retry inside this loop rather than escape the drain cycle.
                backoff = self._backoff_base_s * (2**attempt)
                logger.warning(
                    "transmit %s failed (attempt %d/%d), retrying in %.1fs",
                    path,
                    attempt + 1,
                    self._max_retries,
                    backoff,
                )
                await asyncio.sleep(backoff)

        logger.error("transmit %s exhausted retries; leaving %d items buffered", path, len(items))
        return None

    async def send_gateway_status(
        self, client: httpx.AsyncClient, plc_id: str, is_up: bool, occurred_at: datetime
    ) -> None:
        """Highest-priority channel for the Issue 3 self-diagnostic event —
        sent directly, not through the readings/alarms buffer, so a gateway
        outage is never itself delayed by the buffer it's reporting on."""
        status = "gateway_recovered" if is_up else "gateway_down"
        try:
            resp = await client.post(
                f"{self._base_url}/status/v1/collector",
                json={
                    "plc_id": plc_id,
                    "status": status,
                    "occurred_at": occurred_at.isoformat(),
                },
                timeout=10.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.exception("failed to report %s for %s", status, plc_id)
