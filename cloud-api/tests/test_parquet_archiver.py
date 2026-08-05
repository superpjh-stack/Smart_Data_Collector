"""Parquet archival tests against a real TimescaleDB (see conftest.py)."""

from datetime import date, datetime, timezone

import pyarrow.parquet as pq
import pytest

from parquet_archiver import (
    archive_partition,
    find_eligible_partitions,
    parquet_path_for,
    retention_cutoff,
    run_archival_job,
    verify_partition,
)

OLD_DAY = date(2024, 3, 15)
OTHER_OLD_DAY = date(2024, 3, 16)


@pytest.fixture(autouse=True)
def archive_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PARQUET_ARCHIVE_ROOT", str(tmp_path / "archive"))
    return tmp_path / "archive"


async def _insert_readings(db_conn, day: date, count: int, seq_start: int = 1) -> None:
    await db_conn.executemany(
        """
        INSERT INTO readings
            (plc_id, tag_id, tag_name, timestamp_utc, value, data_type, unit, quality, seq)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        [
            (
                "PLC-01",
                "TEMP_01",
                "Temp01",
                datetime(day.year, day.month, day.day, 0, i, 0, tzinfo=timezone.utc),
                20.0 + i,
                "float",
                "C",
                "Good",
                seq_start + i,
            )
            for i in range(count)
        ],
    )


async def _count_on_day(db_conn, day: date) -> int:
    return await db_conn.fetchval(
        """
        SELECT count(*) FROM readings
        WHERE timestamp_utc >= $1 AND timestamp_utc < $1 + INTERVAL '1 day'
        """,
        datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
    )


async def test_only_partitions_older_than_cutoff_are_eligible(db_conn):
    cutoff = retention_cutoff()
    recent_day = cutoff.date()
    await _insert_readings(db_conn, OLD_DAY, 3)
    await _insert_readings(db_conn, recent_day, 3, seq_start=100)

    assert await find_eligible_partitions(db_conn) == [OLD_DAY]


async def test_eligible_partitions_are_oldest_first(db_conn):
    await _insert_readings(db_conn, OTHER_OLD_DAY, 2, seq_start=50)
    await _insert_readings(db_conn, OLD_DAY, 2)

    assert await find_eligible_partitions(db_conn) == [OLD_DAY, OTHER_OLD_DAY]


async def test_partition_exports_verifies_and_deletes(db_conn, archive_root):
    await _insert_readings(db_conn, OLD_DAY, 5)

    result = await archive_partition(db_conn, OLD_DAY)

    assert result.verified is True
    assert result.deleted is True
    assert result.row_count == 5

    expected = archive_root / "2024" / "03" / "15.parquet"
    assert result.parquet_path == expected
    assert expected.exists()

    table = pq.read_table(expected)
    assert table.num_rows == 5
    assert set(table.column("plc_id").to_pylist()) == {"PLC-01"}

    assert await _count_on_day(db_conn, OLD_DAY) == 0


async def test_verify_failure_skips_delete_and_keeps_rows(db_conn, monkeypatch):
    """Regression guard: no partition may be deleted without a verified match.

    A row landing between export and verify (late-arriving ingestion) makes the
    hot store count exceed the Parquet count — that row is not in the archive,
    so deleting the partition would lose it permanently.
    """
    await _insert_readings(db_conn, OLD_DAY, 4)

    import parquet_archiver

    real_export = parquet_archiver.export_partition_to_parquet

    async def export_then_inject(conn, partition_key):
        path = await real_export(conn, partition_key)
        await _insert_readings(conn, partition_key, 1, seq_start=999)
        return path

    monkeypatch.setattr(parquet_archiver, "export_partition_to_parquet", export_then_inject)

    result = await archive_partition(db_conn, OLD_DAY)

    assert result.verified is False
    assert result.deleted is False
    assert result.error is not None
    assert await _count_on_day(db_conn, OLD_DAY) == 5


async def test_verify_returns_false_when_parquet_missing(db_conn, archive_root):
    await _insert_readings(db_conn, OLD_DAY, 2)

    assert await verify_partition(db_conn, OLD_DAY, parquet_path_for(OLD_DAY)) is False


async def test_verify_returns_false_on_count_mismatch(db_conn):
    await _insert_readings(db_conn, OLD_DAY, 3)
    from parquet_archiver import export_partition_to_parquet

    path = await export_partition_to_parquet(db_conn, OLD_DAY)
    await _insert_readings(db_conn, OLD_DAY, 1, seq_start=500)

    assert await verify_partition(db_conn, OLD_DAY, path) is False


async def test_job_continues_past_a_failing_partition(db_conn, monkeypatch):
    await _insert_readings(db_conn, OLD_DAY, 3)
    await _insert_readings(db_conn, OTHER_OLD_DAY, 2, seq_start=50)

    import parquet_archiver

    real_verify = parquet_archiver.verify_partition

    async def fail_first_day(conn, partition_key, parquet_path):
        if partition_key == OLD_DAY:
            return False
        return await real_verify(conn, partition_key, parquet_path)

    monkeypatch.setattr(parquet_archiver, "verify_partition", fail_first_day)

    results = await run_archival_job(db_conn)

    by_day = {r.partition_key: r for r in results}
    assert by_day[OLD_DAY].deleted is False
    assert by_day[OTHER_OLD_DAY].deleted is True
    assert await _count_on_day(db_conn, OLD_DAY) == 3
    assert await _count_on_day(db_conn, OTHER_OLD_DAY) == 0


async def test_job_leaves_recent_partitions_alone(db_conn):
    recent_day = retention_cutoff().date()
    await _insert_readings(db_conn, recent_day, 3)

    assert await run_archival_job(db_conn) == []
    assert await _count_on_day(db_conn, recent_day) == 3
