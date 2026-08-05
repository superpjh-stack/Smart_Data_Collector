from __future__ import annotations

import asyncio
import logging

import httpx

from collector.config import TagConfig

logger = logging.getLogger(__name__)


class TagConfigStore:
    """Periodic pull-sync of tag_config from the cloud API (Issue 6).

    Push/pull-on-restart-only were rejected in review because threshold
    changes need to reach the field without a manual collector restart.
    `current_version` is exposed so the collector's own status output can show
    whether a configuration change has actually landed.
    """

    def __init__(self, base_url: str, poll_interval_s: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._poll_interval_s = poll_interval_s
        self._tags: dict[tuple[str, str], TagConfig] = {}
        self._since_version = 0
        self._stopped = asyncio.Event()

    @property
    def current_version(self) -> int:
        return self._since_version

    def all_tags(self) -> list[TagConfig]:
        return list(self._tags.values())

    def get(self, plc_id: str, tag_id: str) -> TagConfig | None:
        return self._tags.get((plc_id, tag_id))

    async def stop(self) -> None:
        self._stopped.set()

    async def run(self, client: httpx.AsyncClient) -> None:
        while not self._stopped.is_set():
            try:
                await self.refresh_once(client)
            except Exception:
                logger.exception("tag_config poll failed; keeping stale config")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self._poll_interval_s)
            except asyncio.TimeoutError:
                pass

    async def refresh_once(self, client: httpx.AsyncClient) -> None:
        resp = await client.get(
            f"{self._base_url}/config/v1/tags",
            params={"since_version": self._since_version},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        for raw in data["tags"]:
            tag = TagConfig(**raw)
            self._tags[(tag.plc_id, tag.tag_id)] = tag

        new_version = data["current_version"]
        if new_version != self._since_version:
            logger.info(
                "tag_config updated: %s -> %s (%d tags changed)",
                self._since_version,
                new_version,
                len(data["tags"]),
            )
        self._since_version = new_version
