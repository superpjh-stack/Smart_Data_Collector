"""raw_data ("원본 데이터 수집") integration tests against a real TimescaleDB
(see conftest.py) and a real, isolated on-disk storage root (tmp_path — see
conftest.py's `raw_data_storage_root` autouse fixture).

QA-authored, following the existing test_*_db.py pattern. Not wired into any
CI config change by this file alone.
"""

import io
from urllib.parse import quote

from cloud_api import raw_data_storage as storage


def _xlsx_bytes() -> bytes:
    return b"PK\x03\x04fake-xlsx-content-for-testing"


async def test_create_excel_source_saves_file_and_row(client, db_conn):
    resp = await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "excel", "name": "3월 점검표", "registered_by": "홍길동"},
        files={"files": ("3월_점검표.xlsx", _xlsx_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["sources"]) == 1
    row = body["sources"][0]
    assert row["source_type"] == "excel"
    assert row["file_name"] == "3월_점검표.xlsx"
    assert row["status"] == "registered"
    assert "file_path" not in row  # never on the wire (design §3.3)

    db_row = await db_conn.fetchrow(
        "SELECT file_path, file_size_bytes FROM raw_data_sources WHERE id = $1", row["id"]
    )
    assert db_row is not None
    absolute = storage.absolute_path_for(db_row["file_path"])
    assert absolute.is_file()
    assert absolute.read_bytes() == _xlsx_bytes()
    assert db_row["file_size_bytes"] == len(_xlsx_bytes())


async def test_multi_file_excel_upload_creates_one_row_per_file(client):
    resp = await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "excel", "name": "다중 업로드", "registered_by": "홍길동"},
        files=[
            ("files", ("a.xlsx", _xlsx_bytes(), "application/octet-stream")),
            ("files", ("b.xlsx", _xlsx_bytes(), "application/octet-stream")),
        ],
    )
    assert resp.status_code == 201
    sources = resp.json()["sources"]
    assert len(sources) == 2
    assert {s["file_name"] for s in sources} == {"a.xlsx", "b.xlsx"}
    assert all(s["name"] == "다중 업로드" for s in sources)


async def test_wrong_extension_for_type_rejected_400(client):
    resp = await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "excel", "name": "잘못된 확장자", "registered_by": "홍길동"},
        files={"files": ("bad.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400
    assert "지원하지 않는 파일 형식" in resp.json()["detail"]


async def test_zero_files_for_file_required_type_rejected_400(client):
    resp = await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "excel", "name": "파일 없음", "registered_by": "홍길동"},
    )
    assert resp.status_code == 400


async def test_db_sql_with_file_rejected_400(client):
    resp = await client.post(
        "/raw-data/v1/sources",
        data={
            "source_type": "db_sql",
            "name": "잘못된 DB 등록",
            "registered_by": "김철수",
            "db_kind": "MSSQL",
            "db_host": "10.0.0.1",
            "db_query_text": "SELECT 1",
        },
        files={"files": ("oops.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert resp.status_code == 400


async def test_db_sql_registration_metadata_only_no_file(client, db_conn):
    resp = await client.post(
        "/raw-data/v1/sources",
        data={
            "source_type": "db_sql",
            "name": "레거시 MES 생산실적",
            "registered_by": "김철수",
            "db_kind": "MSSQL",
            "db_host": "10.20.30.40",
            "db_port": "1433",
            "db_query_text": "SELECT * FROM PROD_RESULT",
        },
    )
    assert resp.status_code == 201
    row = resp.json()["sources"][0]
    assert row["status"] == "registered"
    assert row["file_name"] is None
    assert row["db_host"] == "10.20.30.40"

    db_row = await db_conn.fetchrow(
        "SELECT file_path FROM raw_data_sources WHERE id = $1", row["id"]
    )
    assert db_row["file_path"] is None


async def test_equipment_layout_without_file(client):
    resp = await client.post(
        "/raw-data/v1/sources",
        data={
            "source_type": "equipment_layout",
            "name": "라인A 배치",
            "registered_by": "박대리",
            "layout_line_name": "LINE-A",
            "layout_equipment_name": "CNC-01",
        },
    )
    assert resp.status_code == 201
    row = resp.json()["sources"][0]
    assert row["layout_line_name"] == "LINE-A"
    assert row["file_name"] is None


async def test_equipment_layout_missing_required_fields_rejected_400(client):
    resp = await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "equipment_layout", "name": "불완전 배치", "registered_by": "박대리"},
    )
    assert resp.status_code == 400


async def test_summary_counts_by_type(client):
    await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "excel", "name": "s1", "registered_by": "u"},
        files={"files": ("s1.xlsx", _xlsx_bytes(), "application/octet-stream")},
    )
    await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "excel", "name": "s2", "registered_by": "u"},
        files={"files": ("s2.xlsx", _xlsx_bytes(), "application/octet-stream")},
    )
    await client.post(
        "/raw-data/v1/sources",
        data={
            "source_type": "db_sql",
            "name": "s3",
            "registered_by": "u",
            "db_kind": "MSSQL",
            "db_host": "h",
            "db_query_text": "q",
        },
    )

    resp = await client.get("/raw-data/v1/sources/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["excel"] == 2
    assert body["db_sql"] == 1
    assert body["word"] == 0
    assert body["total"] == 3


async def test_list_filter_by_type_and_search(client):
    await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "excel", "name": "라인A 점검표", "registered_by": "u", "equipment_tag": "LINE-A"},
        files={"files": ("a.xlsx", _xlsx_bytes(), "application/octet-stream")},
    )
    await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "word", "name": "매뉴얼", "registered_by": "u"},
        files={"files": ("m.docx", b"word-bytes", "application/octet-stream")},
    )

    resp = await client.get("/raw-data/v1/sources", params={"source_type": "excel"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["sources"][0]["name"] == "라인A 점검표"

    resp2 = await client.get("/raw-data/v1/sources", params={"search": "라인A"})
    assert resp2.json()["total"] == 1

    resp3 = await client.get("/raw-data/v1/sources", params={"search": "존재하지않음"})
    assert resp3.json()["total"] == 0


async def test_download_returns_saved_bytes_with_display_filename(client):
    create = await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "excel", "name": "다운로드 테스트", "registered_by": "u"},
        files={"files": ("원본파일명.xlsx", _xlsx_bytes(), "application/octet-stream")},
    )
    source_id = create.json()["sources"][0]["id"]

    resp = await client.get(f"/raw-data/v1/sources/{source_id}/download")
    assert resp.status_code == 200
    assert resp.content == _xlsx_bytes()
    # Non-ASCII filenames come back RFC 5987-encoded (filename*=utf-8''...)
    # rather than a plain filename="..." param — that's Starlette's FileResponse
    # doing the standards-correct thing for a non-latin1 name, not a bug.
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=utf-8''" in disposition
    assert quote("원본파일명.xlsx") in disposition


async def test_scanned_pdf_upload_saves_and_downloads(client, db_conn):
    create = await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "scanned_pdf", "name": "점검대장 스캔", "registered_by": "u"},
        files={"files": ("scan01.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert create.status_code == 201, create.text
    source_id = create.json()["sources"][0]["id"]

    db_row = await db_conn.fetchrow(
        "SELECT file_path FROM raw_data_sources WHERE id = $1", source_id
    )
    assert storage.absolute_path_for(db_row["file_path"]).is_file()

    resp = await client.get(f"/raw-data/v1/sources/{source_id}/download")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 fake"


async def test_download_of_db_sql_source_404(client):
    create = await client.post(
        "/raw-data/v1/sources",
        data={
            "source_type": "db_sql",
            "name": "다운로드 불가",
            "registered_by": "u",
            "db_kind": "MSSQL",
            "db_host": "h",
            "db_query_text": "q",
        },
    )
    source_id = create.json()["sources"][0]["id"]
    resp = await client.get(f"/raw-data/v1/sources/{source_id}/download")
    assert resp.status_code == 404


async def test_download_unknown_id_404(client):
    resp = await client.get("/raw-data/v1/sources/999999/download")
    assert resp.status_code == 404


async def test_detail_unknown_id_404(client):
    resp = await client.get("/raw-data/v1/sources/999999")
    assert resp.status_code == 404


async def test_delete_removes_row_and_file(client, db_conn):
    create = await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "excel", "name": "삭제 테스트", "registered_by": "u"},
        files={"files": ("del.xlsx", _xlsx_bytes(), "application/octet-stream")},
    )
    source_id = create.json()["sources"][0]["id"]
    db_row = await db_conn.fetchrow(
        "SELECT file_path FROM raw_data_sources WHERE id = $1", source_id
    )
    absolute = storage.absolute_path_for(db_row["file_path"])
    assert absolute.is_file()

    resp = await client.delete(f"/raw-data/v1/sources/{source_id}")
    assert resp.status_code == 204

    gone = await db_conn.fetchrow("SELECT 1 FROM raw_data_sources WHERE id = $1", source_id)
    assert gone is None
    assert not absolute.is_file()


async def test_delete_unknown_id_404(client):
    resp = await client.delete("/raw-data/v1/sources/999999")
    assert resp.status_code == 404


async def test_oversized_file_rejected_and_no_partial_file_left(client, monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "RAW_DATA_MAX_FILE_SIZE_BYTES", 10)
    resp = await client.post(
        "/raw-data/v1/sources",
        data={"source_type": "excel", "name": "너무 큰 파일", "registered_by": "u"},
        files={"files": ("big.xlsx", b"x" * 1000, "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "최대 허용치" in resp.json()["detail"]
    # No partial file left anywhere under the isolated root.
    leftovers = list(tmp_path.rglob("*big.xlsx"))
    assert leftovers == []


def test_no_db_driver_dependency_added():
    """Structural guard for design §5 decision 6 / §8: db_sql registration
    must never gain real connectivity via a driver dependency."""
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = " ".join(data["project"]["dependencies"]).lower()
    forbidden = ["pyodbc", "pymssql", "cx_oracle", "mysqlclient", "pymysql", "psycopg"]
    hits = [d for d in forbidden if d in deps]
    assert hits == [], f"Found forbidden DB driver dependency: {hits}"
