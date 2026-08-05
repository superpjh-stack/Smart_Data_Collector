"""Parquet archival of aged-out `readings` rows (copy-then-verify-then-delete).

Implements the retention procedure from docs/smart-data-collector-plan.md
("Storage & Retention"): rows older than 1 year move from the Hot store
(TimescaleDB) to Cold storage as Parquet. The write -> verify -> delete order
is mandatory: deleting before a row-count match is confirmed can permanently
lose data on a partial write.

Partition granularity is one UTC calendar day. The retention boundary is
coarse (1 year), so day partitions give small enough files to re-verify
cheaply while aligning with TimescaleDB's default day/week chunk boundaries —
a day partition never spans more chunks than necessary. Month partitions
would make a single verification failure block ~30x more data from aging out.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import asyncpg
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

RETENTION = timedelta(days=365)

# Column order used for both the SELECT and the Parquet schema.
COLUMNS = (
    "plc_id",
    "tag_id",
    "tag_name",
    "timestamp_utc",
    "value",
    "data_type",
    "unit",
    "quality",
    "seq",
    "ingested_at",
)

PARQUET_SCHEMA = pa.schema(
    [
        ("plc_id", pa.string()),
        ("tag_id", pa.string()),
        ("tag_name", pa.string()),
        ("timestamp_utc", pa.timestamp("us", tz="UTC")),
        ("value", pa.float64()),
        ("data_type", pa.string()),
        ("unit", pa.string()),
        ("quality", pa.string()),
        ("seq", pa.int64()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)


class VerificationFailed(Exception):
    """Parquet row count did not match the Hot store count for a partition."""


@dataclass(frozen=True)
class PartitionResult:
    partition_key: date
    row_count: int
    parquet_path: Path | None
    verified: bool
    deleted: bool
    error: str | None = None


def archive_root() -> Path:
    # Local filesystem stands in for the Object Storage tier in the plan doc.
    # Swapping to S3/Blob is a matter of changing the write/read target here
    # (e.g. pyarrow.fs.S3FileSystem passed to pq.write_table/read_metadata);
    # the export -> verify -> delete control flow is storage-agnostic.
    return Path(os.environ.get("PARQUET_ARCHIVE_ROOT", "./parquet-archive"))


def retention_cutoff(now: datetime | None = None) -> datetime:
    """Start of the oldest UTC day that must be *kept*.

    Truncated to a day boundary so a partition is either entirely eligible or
    entirely retained — never half-exported.
    """
    now = now or datetime.now(timezone.utc)
    return datetime.combine((now - RETENTION).date(), time.min, tzinfo=timezone.utc)


def partition_bounds(partition_key: date) -> tuple[datetime, datetime]:
    start = datetime.combine(partition_key, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def parquet_path_for(partition_key: date) -> Path:
    return (
        archive_root()
        / f"{partition_key.year:04d}"
        / f"{partition_key.month:02d}"
        / f"{partition_key.day:02d}.parquet"
    )


async def find_eligible_partitions(
    conn: asyncpg.Connection, now: datetime | None = None
) -> list[date]:
    """UTC days strictly older than the retention cutoff that still hold rows."""
    cutoff = retention_cutoff(now)
    rows = await conn.fetch(
        """
        SELECT DISTINCT date_trunc('day', timestamp_utc) AS day
        FROM readings
        WHERE timestamp_utc < $1
        ORDER BY day
        """,
        cutoff,
    )
    return [row["day"].astimezone(timezone.utc).date() for row in rows]


async def export_partition_to_parquet(
    conn: asyncpg.Connection, partition_key: date
) -> Path:
    start, end = partition_bounds(partition_key)
    rows = await conn.fetch(
        f"""
        SELECT {", ".join(COLUMNS)}
        FROM readings
        WHERE timestamp_utc >= $1 AND timestamp_utc < $2
        ORDER BY plc_id, tag_id, timestamp_utc, seq
        """,
        start,
        end,
    )

    table = pa.Table.from_pydict(
        {col: [row[col] for row in rows] for col in COLUMNS},
        schema=PARQUET_SCHEMA,
    )
    path = parquet_path_for(partition_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    logger.info("exported %d rows for %s to %s", len(rows), partition_key, path)
    return path


async def count_partition_rows(conn: asyncpg.Connection, partition_key: date) -> int:
    start, end = partition_bounds(partition_key)
    return await conn.fetchval(
        "SELECT count(*) FROM readings WHERE timestamp_utc >= $1 AND timestamp_utc < $2",
        start,
        end,
    )


async def verify_partition(
    conn: asyncpg.Connection, partition_key: date, parquet_path: Path
) -> bool:
    """Exact row-count match between the Hot store and the written Parquet file.

    Reads the count back out of the file rather than trusting the export's own
    tally or the file merely existing — a truncated or partially flushed write
    must be caught here, since this is the only thing standing between an
    incomplete copy and an irreversible DELETE.
    """
    if not parquet_path.exists():
        logger.error("parquet file missing for %s: %s", partition_key, parquet_path)
        return False

    db_count = await count_partition_rows(conn, partition_key)
    try:
        parquet_count = pq.read_metadata(parquet_path).num_rows
    except Exception:
        logger.exception("parquet file unreadable for %s: %s", partition_key, parquet_path)
        return False

    if db_count != parquet_count:
        logger.error(
            "verification failed for %s: hot store has %d rows, parquet has %d",
            partition_key,
            db_count,
            parquet_count,
        )
        return False

    logger.info("verified %s: %d rows match", partition_key, db_count)
    return True


async def _delete_partition(conn: asyncpg.Connection, partition_key: date) -> int:
    """Private: only ever reachable via archive_partition() after verification."""
    start, end = partition_bounds(partition_key)
    status = await conn.execute(
        "DELETE FROM readings WHERE timestamp_utc >= $1 AND timestamp_utc < $2",
        start,
        end,
    )
    return int(status.split()[-1])


async def archive_partition(
    conn: asyncpg.Connection, partition_key: date
) -> PartitionResult:
    """The only public path that can delete rows: export -> verify -> delete."""
    path = await export_partition_to_parquet(conn, partition_key)
    row_count = await count_partition_rows(conn, partition_key)

    if not await verify_partition(conn, partition_key, path):
        return PartitionResult(
            partition_key=partition_key,
            row_count=row_count,
            parquet_path=path,
            verified=False,
            deleted=False,
            error="row count mismatch between hot store and parquet",
        )

    deleted = await _delete_partition(conn, partition_key)
    return PartitionResult(
        partition_key=partition_key,
        row_count=deleted,
        parquet_path=path,
        verified=True,
        deleted=True,
    )


async def run_archival_job(
    conn: asyncpg.Connection, now: datetime | None = None
) -> list[PartitionResult]:
    """Archive every eligible partition oldest-first.

    One partition's failure must not abort the rest: a verification failure is
    a retryable condition for that day only, and blocking newer days behind it
    would let the hot store grow unbounded.
    """
    results: list[PartitionResult] = []
    for partition_key in await find_eligible_partitions(conn, now):
        try:
            result = await archive_partition(conn, partition_key)
        except Exception as exc:
            logger.exception("archival failed for partition %s", partition_key)
            result = PartitionResult(
                partition_key=partition_key,
                row_count=0,
                parquet_path=None,
                verified=False,
                deleted=False,
                error=repr(exc),
            )
        results.append(result)

    archived = [r for r in results if r.deleted]
    skipped = [r for r in results if not r.deleted]
    logger.info(
        "archival job done: %d partitions archived (%d rows), %d skipped: %s",
        len(archived),
        sum(r.row_count for r in archived),
        len(skipped),
        [str(r.partition_key) for r in skipped],
    )
    return results


async def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    conn = await asyncpg.connect(dsn=os.environ["DATABASE_URL"])
    try:
        results = await run_archival_job(conn)
    finally:
        await conn.close()
    # Non-zero exit so the scheduler surfaces skipped partitions as an alarm.
    return 1 if any(not r.deleted for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
