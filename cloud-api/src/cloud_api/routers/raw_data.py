from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from cloud_api import raw_data_storage as storage
from cloud_api.db import get_pool
from cloud_api.raw_data_storage import RawDataValidationError
from cloud_api.schemas import (
    RawDataSource,
    RawDataSourceCreateResponse,
    RawDataSourceListResponse,
    RawDataSourceSummary,
    RawDataSourceType,
)

router = APIRouter(prefix="/raw-data/v1", tags=["raw_data"])

_SELECT_COLUMNS = """
    id, source_type, name, description, equipment_tag, registered_by, status,
    file_name, file_size_bytes, content_type,
    layout_line_name, layout_equipment_name, layout_location_desc,
    db_kind, db_host, db_port, db_query_text, created_at
"""

_INSERT_SQL = f"""
    INSERT INTO raw_data_sources
        (source_type, name, description, equipment_tag, registered_by,
         file_name, file_size_bytes, file_path, content_type,
         layout_line_name, layout_equipment_name, layout_location_desc,
         db_kind, db_host, db_port, db_query_text)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
    RETURNING {_SELECT_COLUMNS}
"""


@router.post("/sources", response_model=RawDataSourceCreateResponse, status_code=201)
async def create_sources(
    source_type: RawDataSourceType = Form(...),
    name: str = Form(...),
    registered_by: str = Form(...),
    description: str | None = Form(default=None),
    equipment_tag: str | None = Form(default=None),
    layout_line_name: str | None = Form(default=None),
    layout_equipment_name: str | None = Form(default=None),
    layout_location_desc: str | None = Form(default=None),
    db_kind: str | None = Form(default=None),
    db_host: str | None = Form(default=None),
    db_port: int | None = Form(default=None),
    db_query_text: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
) -> RawDataSourceCreateResponse:
    """Register one raw data source's metadata plus 0..N files (multipart).

    File-backed types (excel/word/scanned_pdf) create one catalog row per
    saved file, sharing the submitted metadata (design §5 decision 3).
    `equipment_layout` creates exactly one row (0 or 1 file). `db_sql`
    creates exactly one row and must carry zero files — see design §2.3 /
    §5 decision 6: nothing here ever opens a DB connection.
    """
    uploaded_files = [f for f in files if f.filename]

    try:
        storage.validate_file_count(source_type, len(uploaded_files))
        if source_type == "equipment_layout":
            if not layout_line_name or not layout_equipment_name:
                raise RawDataValidationError(
                    "설비 배치 등록에는 라인명과 설비명이 필요합니다."
                )
        if source_type == "db_sql":
            if not db_kind or not db_host or not db_query_text:
                raise RawDataValidationError(
                    "DB/SQL 등록에는 DB 종류, 호스트, 쿼리 텍스트가 필요합니다."
                )
    except RawDataValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved: list[tuple[str, int, str, str | None]] = []
    try:
        for upload in uploaded_files:
            relative_path, size = await storage.save_upload(upload, source_type)
            saved.append((relative_path, size, upload.filename or "file", upload.content_type))
    except RawDataValidationError as exc:
        for relative_path, *_ in saved:
            storage.delete_on_disk(relative_path)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pool = get_pool()
    created_rows = []
    try:
        async with pool.acquire() as conn:
            created_rows = await _insert_rows(
                conn,
                source_type=source_type,
                name=name,
                description=description,
                equipment_tag=equipment_tag,
                registered_by=registered_by,
                saved=saved,
                layout_line_name=layout_line_name,
                layout_equipment_name=layout_equipment_name,
                layout_location_desc=layout_location_desc,
                db_kind=db_kind,
                db_host=db_host,
                db_port=db_port,
                db_query_text=db_query_text,
            )
    except Exception:
        # A DB-side failure after files were already staged to disk (step 2,
        # above) must not leave orphaned files with no catalog row — clean up
        # whatever this request itself just saved before the 500 propagates.
        for relative_path, *_ in saved:
            storage.delete_on_disk(relative_path)
        raise

    return RawDataSourceCreateResponse(sources=[RawDataSource(**dict(r)) for r in created_rows])


async def _insert_rows(
    conn,
    *,
    source_type: str,
    name: str,
    description: str | None,
    equipment_tag: str | None,
    registered_by: str,
    saved: list[tuple[str, int, str, str | None]],
    layout_line_name: str | None,
    layout_equipment_name: str | None,
    layout_location_desc: str | None,
    db_kind: str | None,
    db_host: str | None,
    db_port: int | None,
    db_query_text: str | None,
):
    created_rows = []
    if source_type in ("excel", "word", "scanned_pdf"):
        for relative_path, size, file_name, content_type in saved:
            row = await conn.fetchrow(
                _INSERT_SQL,
                source_type,
                name,
                description,
                equipment_tag,
                registered_by,
                file_name,
                size,
                relative_path,
                content_type,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
            created_rows.append(row)
    elif source_type == "equipment_layout":
        if saved:
            relative_path, size, file_name, content_type = saved[0]
        else:
            relative_path = size = file_name = content_type = None
        row = await conn.fetchrow(
            _INSERT_SQL,
            source_type,
            name,
            description,
            equipment_tag,
            registered_by,
            file_name,
            size,
            relative_path,
            content_type,
            layout_line_name,
            layout_equipment_name,
            layout_location_desc,
            None,
            None,
            None,
            None,
        )
        created_rows.append(row)
    else:  # db_sql
        row = await conn.fetchrow(
            _INSERT_SQL,
            source_type,
            name,
            description,
            equipment_tag,
            registered_by,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            db_kind,
            db_host,
            db_port,
            db_query_text,
        )
        created_rows.append(row)

    return created_rows


@router.get("/sources", response_model=RawDataSourceListResponse)
async def list_sources(
    source_type: RawDataSourceType | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> RawDataSourceListResponse:
    """Newest-first catalog listing, optionally filtered by type and/or a
    name/equipment_tag substring search — backs the tab's main table."""
    pool = get_pool()

    conditions: list[str] = []
    params: list[object] = []
    if source_type is not None:
        params.append(source_type)
        conditions.append(f"source_type = ${len(params)}")
    if search:
        params.append(f"%{search}%")
        conditions.append(f"(name ILIKE ${len(params)} OR equipment_tag ILIKE ${len(params)})")
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) FROM raw_data_sources {where_clause}", *params
        )
        rows = await conn.fetch(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM raw_data_sources
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params,
            limit,
            offset,
        )

    return RawDataSourceListResponse(
        sources=[RawDataSource(**dict(r)) for r in rows], total=total or 0
    )


@router.get("/sources/summary", response_model=RawDataSourceSummary)
async def get_sources_summary() -> RawDataSourceSummary:
    """Registered count per source_type, always zero-filled for all 5 keys
    so the dashboard's 5 summary cards render directly off this response."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT source_type, count(*) AS cnt FROM raw_data_sources GROUP BY source_type"
        )
    counts = {row["source_type"]: row["cnt"] for row in rows}
    excel = counts.get("excel", 0)
    word = counts.get("word", 0)
    scanned_pdf = counts.get("scanned_pdf", 0)
    equipment_layout = counts.get("equipment_layout", 0)
    db_sql = counts.get("db_sql", 0)
    return RawDataSourceSummary(
        excel=excel,
        word=word,
        scanned_pdf=scanned_pdf,
        equipment_layout=equipment_layout,
        db_sql=db_sql,
        total=excel + word + scanned_pdf + equipment_layout + db_sql,
    )


@router.get("/sources/{source_id}", response_model=RawDataSource)
async def get_source(source_id: int) -> RawDataSource:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_SELECT_COLUMNS} FROM raw_data_sources WHERE id = $1", source_id
        )
    if row is None:
        raise HTTPException(status_code=404, detail="원본 데이터 소스를 찾을 수 없습니다.")
    return RawDataSource(**dict(row))


@router.get("/sources/{source_id}/download")
async def download_source(source_id: int) -> FileResponse:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT file_path, file_name, content_type FROM raw_data_sources WHERE id = $1",
            source_id,
        )
    if row is None or row["file_path"] is None:
        raise HTTPException(status_code=404, detail="다운로드할 파일이 없습니다.")

    absolute_path = storage.absolute_path_for(row["file_path"])
    if not absolute_path.is_file():
        # Deleted out-of-band — a clean 404, never a 500 (design §4.2/§12).
        raise HTTPException(status_code=404, detail="파일이 저장소에 존재하지 않습니다.")

    media_type = storage.resolve_safe_content_type(row["file_name"], row["content_type"])
    return FileResponse(path=absolute_path, media_type=media_type, filename=row["file_name"])


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM raw_data_sources WHERE id = $1 RETURNING file_path", source_id
        )
    if row is None:
        raise HTTPException(status_code=404, detail="원본 데이터 소스를 찾을 수 없습니다.")
    storage.delete_on_disk(row["file_path"])
