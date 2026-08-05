"""Fixtures for tests that run against a real TimescaleDB instance.

Requires the `sdc-timescaledb` dev container to be running (see progress.md
"How to spin up the dev stack again" for how to start it).

The suite runs against its own database (`smart_data_collector_test` by
default), never the dev database — the `clean_tables` fixture below TRUNCATEs
everything before each test, which would otherwise wipe the dev `tag_config`
seed row. The test database is created and migrated automatically by the
`test_database` session fixture, so no manual setup is needed.

Override the target with `TEST_DATABASE_URL` if you want to point the suite at
a different server; otherwise it is derived from `DATABASE_URL` by appending
`_test` to the database name.
"""

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

DEV_DATABASE_URL = "postgresql://sdc:sdc_dev_pw@127.0.0.1:5442/smart_data_collector"
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION_FILES = sorted(MIGRATIONS_DIR.glob("*.sql"))


def _derive_test_dsn() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        dsn = explicit
    else:
        parts = urlsplit(os.environ.get("DATABASE_URL", DEV_DATABASE_URL))
        dsn = urlunsplit(parts._replace(path=parts.path.rstrip("/") + "_test"))

    if urlsplit(dsn).path in ("", "/", "/postgres"):
        raise RuntimeError(
            f"Refusing to run tests against {dsn!r}: no dedicated test database in the DSN."
        )
    return dsn


TEST_DSN = _derive_test_dsn()
os.environ["DATABASE_URL"] = TEST_DSN

from cloud_api import raw_data_storage  # noqa: E402
from cloud_api.db import close_pool, init_pool  # noqa: E402
from cloud_api.main import app  # noqa: E402

TRUNCATE_TABLES = ["readings", "alarms", "tag_config", "collector_status_events", "raw_data_sources"]


def _migration_statements() -> list[str]:
    # asyncpg wraps a multi-statement execute() in a single implicit
    # transaction, which TimescaleDB rejects for create_hypertable() and
    # continuous aggregates — so run each migration file statement by
    # statement, in filename order (001_, 002_, ...).
    statements: list[str] = []
    for path in MIGRATION_FILES:
        sql = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
        statements.extend(s.strip() for s in sql.split(";") if s.strip())
    return statements


async def _provision() -> None:
    parts = urlsplit(TEST_DSN)
    db_name = parts.path.lstrip("/")
    admin_dsn = urlunsplit(parts._replace(path="/postgres"))

    admin = await asyncpg.connect(dsn=admin_dsn)
    try:
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_name
        )
        if not exists:
            await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()

    conn = await asyncpg.connect(dsn=TEST_DSN)
    try:
        for statement in _migration_statements():
            await conn.execute(statement)
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def test_database():
    asyncio.run(_provision())


@pytest_asyncio.fixture
async def db_conn(test_database):
    conn = await asyncpg.connect(dsn=TEST_DSN)
    yield conn
    await conn.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(db_conn):
    """Reset all tables and the config version sequence before each test.

    Cleans up-front (not in teardown) so a prior interrupted run never
    leaves the DB dirty for the next test.
    """
    for table in TRUNCATE_TABLES:
        await db_conn.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
    await db_conn.execute("ALTER SEQUENCE config_version_seq RESTART")
    yield


@pytest.fixture(autouse=True)
def raw_data_storage_root(tmp_path, monkeypatch):
    """Redirect raw_data_storage's module-level storage root to a per-test
    tmp_path so uploaded test files never land in (or leak from) the real
    RAW_DATA_STORAGE_ROOT — same isolation principle as the test DB."""
    monkeypatch.setattr(raw_data_storage, "RAW_DATA_STORAGE_ROOT", str(tmp_path))


@pytest_asyncio.fixture
async def client():
    # Bypass the app's lifespan (httpx's ASGITransport doesn't drive the
    # ASGI lifespan protocol) and manage the pool directly instead — the
    # routers only ever call cloud_api.db.get_pool(), so this is equivalent.
    await init_pool()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        await close_pool()
