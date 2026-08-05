import asyncio
import tempfile
from pathlib import Path

import pytest

from collector.buffer import SqliteBuffer


@pytest.fixture
async def buffer():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "buffer.db")
        buf = SqliteBuffer(path)
        await buf.open()
        yield buf
        await buf.close()


async def test_seq_is_monotonic_across_kinds(buffer):
    seq1 = await buffer.enqueue("reading", {"v": 1}, now=1.0)
    seq2 = await buffer.enqueue("alarm", {"v": 2}, now=2.0)
    seq3 = await buffer.enqueue("reading", {"v": 3}, now=3.0)

    assert [seq1, seq2, seq3] == [1, 2, 3]


async def test_alarms_drain_before_readings_even_when_older(buffer):
    """Reconnect-storm scenario (Issue 2): 1,200 backlogged readings must not
    delay a newly-arrived alarm."""
    for i in range(5):
        await buffer.enqueue("reading", {"i": i}, now=float(i))
    await buffer.enqueue("alarm", {"i": "the-alarm"}, now=100.0)

    batch = await buffer.dequeue_batch(max_items=3)

    assert batch[0].kind == "alarm"
    assert batch[0].payload["i"] == "the-alarm"
    assert [item.kind for item in batch[1:]] == ["reading", "reading"]


async def test_ack_removes_only_specified_items(buffer):
    await buffer.enqueue("reading", {"i": 1}, now=1.0)
    await buffer.enqueue("reading", {"i": 2}, now=2.0)
    batch = await buffer.dequeue_batch(max_items=10)

    await buffer.ack([batch[0].id])

    assert await buffer.pending_count() == 1
    remaining = await buffer.dequeue_batch(max_items=10)
    assert remaining[0].payload["i"] == 2


async def test_unacked_items_remain_for_next_drain(buffer):
    await buffer.enqueue("reading", {"i": 1}, now=1.0)
    await buffer.dequeue_batch(max_items=10)  # dequeue without ack

    assert await buffer.pending_count() == 1


async def test_disk_capacity_check(buffer, tmp_path=None):
    assert buffer.is_near_capacity(max_bytes=0) is True
    assert buffer.is_near_capacity(max_bytes=10**12) is False


async def test_concurrent_enqueue_assigns_distinct_seqs(buffer):
    """Regression guard for gap #15: _next_seq's UPDATE+SELECT used to be two
    round trips, letting concurrent enqueue() calls interleave and collide on
    the same seq value."""
    results = await asyncio.gather(
        *(buffer.enqueue("reading", {"i": i}, now=float(i)) for i in range(50))
    )

    assert len(set(results)) == 50
    assert sorted(results) == list(range(1, 51))
