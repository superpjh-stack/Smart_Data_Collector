from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from collector.alarm_engine import AlarmEngine
from collector.buffer import SqliteBuffer
from collector.config import CollectorSettings
from collector.opc_client import OpcCollectorClient, RawReading
from collector.tag_config_sync import TagConfigStore
from collector.transmitter import Transmitter

logger = logging.getLogger(__name__)

BUFFER_CAPACITY_BYTES = 512 * 1024 * 1024  # ~72h at this project's data volume


class Collector:
    def __init__(self, settings: CollectorSettings) -> None:
        self._settings = settings
        self._buffer = SqliteBuffer(settings.sqlite_path)
        self._tag_config = TagConfigStore(
            settings.cloud_api_base_url, settings.tag_config_poll_interval_s
        )
        self._alarm_engine = AlarmEngine()
        self._transmitter = Transmitter(
            self._buffer,
            settings.cloud_api_base_url,
            batch_max=settings.transmit_batch_max,
        )
        self._http_client: httpx.AsyncClient | None = None

    async def _on_reading(self, raw: RawReading) -> None:
        now = datetime.fromtimestamp(raw.timestamp_utc, tz=timezone.utc)
        seq = await self._buffer.enqueue(
            "reading",
            {
                "plc_id": raw.plc_id,
                "tag_id": raw.tag_id,
                "tag_name": raw.tag_name,
                "timestamp_utc": now.isoformat(),
                "value": raw.value,
                "data_type": raw.data_type,
                "unit": raw.unit,
                "quality": raw.quality,
            },
            raw.timestamp_utc,
        )

        tag = self._tag_config.get(raw.plc_id, raw.tag_id)
        if tag is None or raw.quality != "Good":
            return

        event = self._alarm_engine.evaluate(tag, raw.value, now, seq)
        if event is not None:
            await self._buffer.enqueue(
                "alarm",
                {
                    "alarm_id": event.alarm_id,
                    "plc_id": event.plc_id,
                    "tag_id": event.tag_id,
                    "severity": event.severity,
                    "condition": event.condition,
                    "triggered_value": event.triggered_value,
                    "triggered_at_utc": event.triggered_at_utc.isoformat(),
                    "cleared_at_utc": None,
                    "ack_status": event.ack_status,
                    "config_version": event.config_version,
                },
                raw.timestamp_utc,
            )

    async def _on_gateway_status(self, plc_id: str, is_up: bool) -> None:
        logger.warning("gateway status for %s: %s", plc_id, "up" if is_up else "DOWN")
        if self._http_client is not None:
            await self._transmitter.send_gateway_status(
                self._http_client, plc_id, is_up, datetime.now(timezone.utc)
            )

    async def _transmit_loop(self) -> None:
        assert self._http_client is not None
        while True:
            try:
                outcome = await self._transmitter.transmit_once(self._http_client)
                if outcome.acked_ids:
                    logger.info(
                        "transmitted batch: inserted=%d duplicates=%d",
                        outcome.inserted,
                        outcome.duplicates,
                    )
            except Exception:
                logger.exception("transmit cycle failed")

            if self._buffer.is_near_capacity(BUFFER_CAPACITY_BYTES):
                logger.critical(
                    "local buffer near capacity (%d bytes) — cloud connectivity "
                    "may be down for an extended period",
                    self._buffer.disk_usage_bytes(),
                )

            await asyncio.sleep(self._settings.transmit_interval_s)

    async def run(self) -> None:
        await self._buffer.open()
        async with httpx.AsyncClient() as client:
            self._http_client = client

            # Fetch tag_config once synchronously before subscribing — the
            # OPC client needs the full tag list up front to build its
            # monitored items; the recurring poll loop below only applies
            # incremental changes after that.
            await self._tag_config.refresh_once(client)

            tags_by_plc: dict[str, list] = {plc_id: [] for plc_id in self._settings.plc_ids}
            for tag in self._tag_config.all_tags():
                tags_by_plc.setdefault(tag.plc_id, []).append(tag)

            opc_client = OpcCollectorClient(
                endpoint_url=self._settings.opc_endpoint_url,
                tags_by_plc=tags_by_plc,
                on_reading=self._on_reading,
                on_gateway_status=self._on_gateway_status,
                heartbeat_timeout_s=self._settings.gateway_heartbeat_timeout_s,
            )

            await asyncio.gather(
                self._tag_config.run(client),
                opc_client.run(),
                self._transmit_loop(),
            )


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = CollectorSettings.from_env()
    collector = Collector(settings)
    await collector.run()


if __name__ == "__main__":
    asyncio.run(_main())
