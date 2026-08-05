"""Regression tests for OPC SourceTimestamp → unix epoch conversion.

asyncua hands back a *naive* datetime holding UTC wall-clock. `.timestamp()` on a
naive datetime is interpreted in the host's local timezone, so on any edge device
not set to UTC every reading's `timestamp_utc` was shifted by the device's UTC
offset — and that value is part of the cloud dedup key
`(plc_id, tag_id, seq, timestamp_utc)`.

These assertions are host-TZ-independent by construction: the expected value is a
fixed epoch constant, and the conversion is compared against the explicitly
UTC-aware equivalent of the same wall-clock. If the implementation ever falls back
to local-time interpretation, these fail on every non-UTC host.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from collector.opc_client import _source_timestamp_to_epoch

# 2026-07-13T09:00:10Z — the same instant used by the alarm_id fixtures.
NAIVE_UTC = datetime(2026, 7, 13, 9, 0, 10)
EXPECTED_EPOCH = 1783933210.0


def test_naive_source_timestamp_is_read_as_utc():
    assert _source_timestamp_to_epoch(NAIVE_UTC) == EXPECTED_EPOCH


def test_naive_matches_the_utc_aware_equivalent():
    aware = NAIVE_UTC.replace(tzinfo=timezone.utc)
    assert _source_timestamp_to_epoch(NAIVE_UTC) == aware.timestamp()


def test_already_aware_timestamp_is_not_shifted_again():
    # A non-UTC aware timestamp keeps its own offset; it must not be overwritten.
    kst = timezone(timedelta(hours=9))
    aware = datetime(2026, 7, 13, 18, 0, 10, tzinfo=kst)  # same instant as above
    assert _source_timestamp_to_epoch(aware) == EXPECTED_EPOCH


def test_missing_source_timestamp_falls_back_to_now():
    before = time.time()
    result = _source_timestamp_to_epoch(None)
    after = time.time()
    assert before <= result <= after
