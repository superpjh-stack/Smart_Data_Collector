from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from asyncua import Client, ua

from collector.config import TagConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawReading:
    plc_id: str
    tag_id: str
    tag_name: str
    value: float
    data_type: str
    unit: str | None
    quality: str
    timestamp_utc: float  # unix epoch seconds, UTC


OnReading = Callable[[RawReading], Awaitable[None]]
OnGatewayStatus = Callable[[str, bool], Awaitable[None]]  # (plc_id, is_up)


def _quality_from_statuscode(code: ua.StatusCode) -> str:
    # OPC UA StatusCode is 3-tier (Good/Uncertain/Bad) per the severity bits —
    # normalize to our own 3-value enum rather than exposing the raw code.
    if code.is_good():
        return "Good"
    if code.is_uncertain():
        return "Uncertain"
    return "Bad"


def _source_timestamp_to_epoch(source_ts: datetime | None) -> float:
    """Convert an OPC UA SourceTimestamp to unix epoch seconds, UTC.

    asyncua hands back a *naive* datetime that carries UTC wall-clock. Calling
    `.timestamp()` on it directly would make Python interpret it in the host's
    local timezone, shifting every reading by the edge device's UTC offset — and
    that value is part of the cloud dedup key. Attach UTC explicitly first.
    """
    if source_ts is None:
        return time.time()
    if source_ts.tzinfo is None:
        source_ts = source_ts.replace(tzinfo=timezone.utc)
    return source_ts.timestamp()


class _DeadbandGate:
    """Client-side deadband filter.

    We filter here instead of relying on server-side DataChangeFilter because
    not every OPC UA server implements deadband filtering, and testing our own
    bandwidth-reduction logic is much easier when it doesn't depend on server
    behavior we can't control from the collector.

    Last-value state is keyed on `(plc_id, tag_id)`, not `tag_id` alone: the same
    tag_id (e.g. TEMP_01) exists on every PLC, so a tag_id-only key would let one
    PLC's value suppress another PLC's reading. `(plc_id, tag_id)` is the identity
    pair used everywhere else in the collector.
    """

    def __init__(self) -> None:
        self._last_value: dict[tuple[str, str], float] = {}

    def should_forward(self, plc_id: str, tag_id: str, value: float, deadband: float) -> bool:
        key = (plc_id, tag_id)
        last = self._last_value.get(key)
        if last is None or deadband <= 0 or abs(value - last) >= deadband:
            self._last_value[key] = value
            return True
        return False


class _SubscriptionHandler:
    def __init__(
        self,
        plc_id: str,
        node_to_tag: dict[str, TagConfig],
        deadband_gate: _DeadbandGate,
        on_reading: OnReading,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._plc_id = plc_id
        self._node_to_tag = node_to_tag
        self._deadband_gate = deadband_gate
        self._on_reading = on_reading
        self._loop = loop

    def datachange_notification(self, node, val, data) -> None:
        tag = self._node_to_tag.get(node.nodeid.to_string())
        if tag is None:
            return

        quality = _quality_from_statuscode(data.monitored_item.Value.StatusCode)
        if not self._deadband_gate.should_forward(
            self._plc_id, tag.tag_id, float(val), tag.deadband
        ):
            return

        source_ts = data.monitored_item.Value.SourceTimestamp
        reading = RawReading(
            plc_id=self._plc_id,
            tag_id=tag.tag_id,
            tag_name=tag.tag_id,
            value=float(val),
            data_type=tag.data_type,
            unit=tag.unit,
            quality=quality,
            timestamp_utc=_source_timestamp_to_epoch(source_ts),
        )
        asyncio.run_coroutine_threadsafe(self._on_reading(reading), self._loop)


class OpcCollectorClient:
    """OPC UA subscription client for one gateway endpoint covering multiple PLCs.

    Runs a reconnect loop: on disconnect it starts a heartbeat timer: if the
    connection isn't restored within `heartbeat_timeout_s`, it fires
    on_gateway_status(plc_id, is_up=False) — this is what lets the dashboard
    distinguish "gateway/collector down" from "equipment reading is normal"
    (Issue 3). Reconnect re-subscribes every monitored item from scratch —
    OPC UA subscriptions do not survive a session drop.
    """

    def __init__(
        self,
        endpoint_url: str,
        tags_by_plc: dict[str, list[TagConfig]],
        on_reading: OnReading,
        on_gateway_status: OnGatewayStatus,
        heartbeat_timeout_s: float = 15.0,
        reconnect_backoff_s: float = 5.0,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._tags_by_plc = tags_by_plc
        self._on_reading = on_reading
        self._on_gateway_status = on_gateway_status
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._reconnect_backoff_s = reconnect_backoff_s
        self._deadband_gate = _DeadbandGate()
        self._stopped = asyncio.Event()
        self._gateway_up: dict[str, bool] = {plc_id: True for plc_id in tags_by_plc}

    async def stop(self) -> None:
        self._stopped.set()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stopped.is_set():
            try:
                await self._connect_and_subscribe(loop)
            except Exception:
                logger.exception("OPC UA session failed for %s", self._endpoint_url)
                await self._mark_all_gateways(False)
                await asyncio.sleep(self._reconnect_backoff_s)

    async def _mark_all_gateways(self, is_up: bool) -> None:
        for plc_id in self._tags_by_plc:
            if self._gateway_up.get(plc_id) != is_up:
                self._gateway_up[plc_id] = is_up
                await self._on_gateway_status(plc_id, is_up)

    async def _connect_and_subscribe(self, loop: asyncio.AbstractEventLoop) -> None:
        async with Client(url=self._endpoint_url) as client:
            await self._mark_all_gateways(True)

            for plc_id, tags in self._tags_by_plc.items():
                if not tags:
                    continue
                node_to_tag = {t.opc_node_id: t for t in tags}
                handler = _SubscriptionHandler(
                    plc_id, node_to_tag, self._deadband_gate, self._on_reading, loop
                )
                period_ms = min(t.sampling_interval_ms for t in tags)
                sub = await client.create_subscription(period_ms, handler)
                nodes = [client.get_node(nid) for nid in node_to_tag]
                await sub.subscribe_data_change(nodes)

            await self._heartbeat_loop(client)

    async def _heartbeat_loop(self, client: Client) -> None:
        """Explicit liveness probe, independent of subscription notifications.

        A tag that legitimately never changes would otherwise never produce a
        datachange_notification, so we can't infer "gateway down" purely from
        notification silence. Instead we poll the server's own CurrentTime
        node — if that read fails or times out repeatedly for
        heartbeat_timeout_s, we treat the gateway as down, mark it, and let the
        exception propagate up to run()'s reconnect loop.
        """
        probe_interval_s = max(1.0, self._heartbeat_timeout_s / 3)
        consecutive_failures = 0
        max_failures = max(1, round(self._heartbeat_timeout_s / probe_interval_s))
        current_time_node = client.get_node(ua.NodeId(ua.ObjectIds.Server_ServerStatus_CurrentTime, 0))

        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(current_time_node.read_value(), timeout=probe_interval_s)
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    await self._mark_all_gateways(False)
                    raise ConnectionError(
                        f"gateway heartbeat lost after {self._heartbeat_timeout_s}s"
                    )
            await asyncio.sleep(probe_interval_s)
