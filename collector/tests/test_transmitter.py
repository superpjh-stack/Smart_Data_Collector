"""Regression tests for the transmitter's retry classification.

Two failure modes are covered, both previously silent:

* A **permanent** rejection (a 4xx that retrying cannot fix, e.g. 422 on a
  malformed row) used to be treated as transient. Because `dequeue_batch` puts
  alarms at the head of the queue, one poison alarm row stayed at the head
  forever and blocked every later alarm from ever being delivered.
* A **2xx with an unparseable body** raised `json.JSONDecodeError`, which was not
  in the caught tuple, so it escaped the backoff loop and aborted the whole drain
  cycle instead of retrying as design §6 requires.
"""

from __future__ import annotations

import httpx
import pytest

from collector.buffer import SqliteBuffer
from collector.transmitter import Transmitter


@pytest.fixture
async def buffer(tmp_path):
    buf = SqliteBuffer(str(tmp_path / "buffer.db"))
    await buf.open()
    yield buf
    await buf.close()


def make_transmitter(buffer: SqliteBuffer) -> Transmitter:
    # backoff_base_s=0 keeps the retry path instant; the arithmetic is unchanged.
    return Transmitter(
        buffer, "https://cloud.example.com", max_retries=5, backoff_base_s=0.0
    )


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_non_retryable_4xx_drops_the_batch_instead_of_looping(buffer):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/alarms"):
            return httpx.Response(422, json={"detail": "invalid severity"})
        return httpx.Response(200, json={"inserted": 1, "duplicates": 0})

    await buffer.enqueue("alarm", {"alarm_id": "poison"}, now=1.0)
    await buffer.enqueue("reading", {"tag_id": "TEMP_01"}, now=2.0)

    async with client_for(handler) as client:
        outcome = await make_transmitter(buffer).transmit_once(client)

    alarm_calls = [c for c in calls if c.endswith("/alarms")]
    assert len(alarm_calls) == 1, "a 422 must not be retried"

    # The poison row is gone (dropped, not delivered) and the reading was sent,
    # so nothing is left occupying the head of the queue.
    assert await buffer.pending_count() == 0
    assert len(outcome.acked_ids) == 2
    # Only the reading actually landed server-side.
    assert outcome.inserted == 1


async def test_poison_alarm_does_not_block_later_alarms(buffer):
    delivered: list[str] = []
    state = {"reject": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["reject"]:
            return httpx.Response(422, json={"detail": "invalid severity"})
        delivered.append(request.content.decode())
        return httpx.Response(200, json={"inserted": 1, "duplicates": 0})

    transmitter = make_transmitter(buffer)

    await buffer.enqueue("alarm", {"alarm_id": "poison"}, now=1.0)
    async with client_for(handler) as client:
        await transmitter.transmit_once(client)
    assert await buffer.pending_count() == 0

    # A subsequent, well-formed alarm now gets through.
    state["reject"] = False
    await buffer.enqueue("alarm", {"alarm_id": "healthy"}, now=3.0)
    async with client_for(handler) as client:
        outcome = await transmitter.transmit_once(client)

    assert outcome.inserted == 1
    assert delivered, "the healthy alarm must reach the server"
    assert await buffer.pending_count() == 0


@pytest.mark.parametrize("status_code", [408, 429])
async def test_retryable_4xx_keeps_retrying_and_leaves_rows_buffered(buffer, status_code):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(status_code)
        return httpx.Response(status_code, json={"detail": "try later"})

    await buffer.enqueue("alarm", {"alarm_id": "a1"}, now=1.0)

    async with client_for(handler) as client:
        outcome = await make_transmitter(buffer).transmit_once(client)

    assert len(calls) == 5, f"{status_code} must stay retryable"
    assert outcome.acked_ids == []
    assert await buffer.pending_count() == 1


async def test_5xx_stays_retryable(buffer):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(503)
        return httpx.Response(503, text="upstream unavailable")

    await buffer.enqueue("reading", {"tag_id": "TEMP_01"}, now=1.0)

    async with client_for(handler) as client:
        outcome = await make_transmitter(buffer).transmit_once(client)

    assert len(calls) == 5
    assert outcome.acked_ids == []
    assert await buffer.pending_count() == 1


async def test_unparseable_2xx_body_retries_inside_the_backoff_loop(buffer):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text="<html>proxy interstitial</html>")

    await buffer.enqueue("reading", {"tag_id": "TEMP_01"}, now=1.0)

    async with client_for(handler) as client:
        # Must not raise: json.JSONDecodeError has to be handled in the loop.
        outcome = await make_transmitter(buffer).transmit_once(client)

    assert len(calls) == 5, "an unparseable 2xx body must be retried, not escape"
    assert outcome.acked_ids == []
    assert await buffer.pending_count() == 1, "nothing may be acked"


async def test_missing_count_keys_are_retried(buffer):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"unexpected": "shape"})

    await buffer.enqueue("reading", {"tag_id": "TEMP_01"}, now=1.0)

    async with client_for(handler) as client:
        outcome = await make_transmitter(buffer).transmit_once(client)

    assert len(calls) == 5
    assert outcome.acked_ids == []
    assert await buffer.pending_count() == 1


async def test_successful_batch_is_acked_and_removed(buffer):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"inserted": 1, "duplicates": 0})

    await buffer.enqueue("alarm", {"alarm_id": "a1"}, now=1.0)
    await buffer.enqueue("reading", {"tag_id": "TEMP_01"}, now=2.0)

    async with client_for(handler) as client:
        outcome = await make_transmitter(buffer).transmit_once(client)

    assert outcome.inserted == 2
    assert len(outcome.acked_ids) == 2
    assert await buffer.pending_count() == 0
