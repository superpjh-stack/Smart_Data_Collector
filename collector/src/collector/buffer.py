from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal

import aiosqlite

Kind = Literal["reading", "alarm"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seq_counter (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    value INTEGER NOT NULL
);
INSERT OR IGNORE INTO seq_counter (id, value) VALUES (1, 0);

CREATE TABLE IF NOT EXISTS buffer_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('reading', 'alarm')),
    seq INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

-- Priority drain (Issue 2): this index lets "alarms first" be a plain ORDER
-- BY on (kind, id) instead of two separate queries racing each other.
CREATE INDEX IF NOT EXISTS idx_buffer_items_kind_id ON buffer_items (kind, id);
"""


@dataclass(frozen=True)
class BufferedItem:
    id: int
    kind: Kind
    seq: int
    payload: dict[str, Any]


class SqliteBuffer:
    """Local store-and-forward buffer.

    Alarms are always drained ahead of readings (Issue 2) so a reconnect
    storm — up to 1,200 backlogged readings/minute at this project's scale —
    never delays an alarm behind routine data. Commit is implemented as
    delete-on-ack: once the cloud API has accepted a batch (200 response,
    duplicates included per Issue 7), the caller deletes those rows. There is
    no separate offset cursor to reconcile against a mixed-priority read
    order, so delete-on-ack is the simpler correct choice here.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("buffer not opened — call open() first")
        return self._db

    async def _next_seq(self) -> int:
        # Single-statement UPDATE...RETURNING: the increment and read must not
        # be split across two round trips, or two concurrent enqueue() calls
        # can interleave between them and be handed the same seq value.
        db = self._conn()
        cur = await db.execute(
            "UPDATE seq_counter SET value = value + 1 WHERE id = 1 RETURNING value"
        )
        row = await cur.fetchone()
        if row is None:
            raise RuntimeError("seq_counter row missing — buffer schema not initialized")
        return int(row[0])

    async def enqueue(self, kind: Kind, payload: dict[str, Any], now: float) -> int:
        db = self._conn()
        seq = await self._next_seq()
        payload = {**payload, "seq": seq}
        await db.execute(
            "INSERT INTO buffer_items (kind, seq, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (kind, seq, json.dumps(payload), now),
        )
        await db.commit()
        return seq

    async def dequeue_batch(self, max_items: int) -> list[BufferedItem]:
        """Return up to max_items, alarms first, then oldest readings."""
        db = self._conn()
        cur = await db.execute(
            """
            SELECT id, kind, seq, payload_json FROM buffer_items
            ORDER BY (kind = 'alarm') DESC, id ASC
            LIMIT ?
            """,
            (max_items,),
        )
        rows = await cur.fetchall()
        return [
            BufferedItem(id=row[0], kind=row[1], seq=row[2], payload=json.loads(row[3]))
            for row in rows
        ]

    async def ack(self, ids: list[int]) -> None:
        if not ids:
            return
        db = self._conn()
        placeholders = ",".join("?" for _ in ids)
        await db.execute(f"DELETE FROM buffer_items WHERE id IN ({placeholders})", ids)
        await db.commit()

    async def pending_count(self) -> int:
        db = self._conn()
        cur = await db.execute("SELECT COUNT(*) FROM buffer_items")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    def disk_usage_bytes(self) -> int:
        try:
            return os.path.getsize(self._path)
        except OSError:
            return 0

    def is_near_capacity(self, max_bytes: int) -> bool:
        """Self-monitoring check backing the buffer-capacity self-alarm."""
        return self.disk_usage_bytes() >= max_bytes
