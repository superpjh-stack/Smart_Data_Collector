"""Local-disk storage for the "원본 데이터 수집" (raw data collection) feature.

Stdlib + env config only — this module has no dependency on `db`, `schemas`,
or any other part of `cloud_api`, so it stays trivially unit-testable and,
per design doc §5 decision 6 / §7, adds no DB-driver or networking capability
of any kind. It only ever touches the local filesystem.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile

# ── Config ────────────────────────────────────────────────────────────────

RAW_DATA_STORAGE_ROOT = os.environ.get("RAW_DATA_STORAGE_ROOT", "./data/raw-uploads")
RAW_DATA_MAX_FILE_SIZE_BYTES = int(os.environ.get("RAW_DATA_MAX_FILE_SIZE_BYTES", str(50 * 1024 * 1024)))

_UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB read chunks while streaming to disk

# Per-type extension allowlist (case-insensitive). `db_sql` maps to an empty
# set: presence of ANY file for that type is a validation error, never an
# allowlist lookup.
ALLOWED_EXTENSIONS_BY_TYPE: dict[str, frozenset[str]] = {
    "excel": frozenset({".xlsx", ".xls"}),
    "word": frozenset({".docx", ".doc"}),
    "scanned_pdf": frozenset({".pdf", ".png", ".jpg", ".jpeg"}),
    "equipment_layout": frozenset({".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".dwg", ".dxf"}),
    "db_sql": frozenset(),
}

# Minimum/maximum file count per source type: (min, max) where max=None means
# unbounded. Used by the router to produce the 400s described in design §4.3.
FILE_COUNT_RANGE_BY_TYPE: dict[str, tuple[int, int | None]] = {
    "excel": (1, None),
    "word": (1, None),
    "scanned_pdf": (1, None),
    "equipment_layout": (0, 1),
    "db_sql": (0, 0),
}

# Content-Type served on download, keyed off the (already-validated) file
# extension — never the client-supplied Content-Type at upload time. If the
# stored (client-reported) content_type doesn't match this canonical value,
# the download falls back to application/octet-stream. See design §7.
SAFE_CONTENT_TYPE_BY_EXTENSION: dict[str, str] = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".dwg": "application/octet-stream",
    ".dxf": "application/octet-stream",
}

_SAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_MAX_SANITIZED_STEM_LEN = 150


class RawDataValidationError(Exception):
    """A client-input problem the router should surface as HTTP 400."""


def validate_file_count(source_type: str, file_count: int) -> None:
    min_count, max_count = FILE_COUNT_RANGE_BY_TYPE[source_type]
    if file_count < min_count:
        raise RawDataValidationError(
            f"'{source_type}' 소스는 파일을 최소 {min_count}개 업로드해야 합니다."
        )
    if max_count is not None and file_count > max_count:
        raise RawDataValidationError(
            f"'{source_type}' 소스는 파일을 최대 {max_count}개까지만 업로드할 수 있습니다."
        )


def validate_extension(source_type: str, filename: str) -> str:
    """Returns the validated, lowercased extension (with leading dot)."""
    ext = Path(filename).suffix.lower()
    allowed = ALLOWED_EXTENSIONS_BY_TYPE.get(source_type, frozenset())
    if not ext or ext not in allowed:
        allowed_list = ", ".join(sorted(allowed)) or "(허용된 확장자 없음)"
        raise RawDataValidationError(
            f"지원하지 않는 파일 형식입니다: '{filename}'. "
            f"'{source_type}' 소스에 허용된 확장자: {allowed_list}"
        )
    return ext


def sanitize_filename(original_name: str, ext: str) -> str:
    """Strip path separators and any character outside [A-Za-z0-9._-],
    cap the stem length, and always keep the (already-validated) extension.
    """
    stem = Path(original_name).stem
    safe_stem = _SAFE_NAME_CHARS.sub("_", stem)[:_MAX_SANITIZED_STEM_LEN]
    if not safe_stem:
        safe_stem = "file"
    return f"{safe_stem}{ext}"


def build_relative_path(source_type: str, sanitized_name: str) -> Path:
    """{source_type}/{yyyy}/{mm}/{uuid4}__{sanitized_filename}, relative to
    RAW_DATA_STORAGE_ROOT."""
    now = datetime.now(timezone.utc)
    return Path(source_type) / f"{now:%Y}" / f"{now:%m}" / f"{uuid.uuid4()}__{sanitized_name}"


def storage_root() -> Path:
    return Path(RAW_DATA_STORAGE_ROOT)


async def save_upload(upload: UploadFile, source_type: str) -> tuple[str, int]:
    """Validate extension, then stream `upload` to disk under the configured
    storage root, enforcing RAW_DATA_MAX_FILE_SIZE_BYTES by counting actual
    bytes read (never trusting Content-Length — see design §5 decision 8).

    Returns (relative_path_str, size_bytes). Raises RawDataValidationError on
    a disallowed extension or an oversized file (partial file is removed).
    """
    ext = validate_extension(source_type, upload.filename or "")
    sanitized = sanitize_filename(upload.filename or "file", ext)
    relative_path = build_relative_path(source_type, sanitized)
    absolute_path = storage_root() / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)

    size = 0
    try:
        with open(absolute_path, "wb") as out:
            while True:
                chunk = await upload.read(_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > RAW_DATA_MAX_FILE_SIZE_BYTES:
                    out.close()
                    _delete_on_disk(relative_path)
                    max_mb = RAW_DATA_MAX_FILE_SIZE_BYTES / (1024 * 1024)
                    raise RawDataValidationError(
                        f"파일 크기가 최대 허용치({max_mb:.0f}MB)를 초과했습니다: '{upload.filename}'"
                    )
                out.write(chunk)
    finally:
        await upload.close()

    return str(relative_path).replace("\\", "/"), size


def resolve_safe_content_type(file_name: str, stored_content_type: str | None) -> str:
    ext = Path(file_name).suffix.lower()
    canonical = SAFE_CONTENT_TYPE_BY_EXTENSION.get(ext, "application/octet-stream")
    if stored_content_type and stored_content_type.split(";")[0].strip().lower() == canonical.lower():
        return canonical
    return "application/octet-stream"


def absolute_path_for(relative_path: str) -> Path:
    return storage_root() / relative_path


def _delete_on_disk(relative_path: str | Path) -> None:
    try:
        os.remove(storage_root() / relative_path)
    except FileNotFoundError:
        pass


def delete_on_disk(relative_path: str | None) -> None:
    """Best-effort delete; a missing file is not an error (design §4.2)."""
    if not relative_path:
        return
    try:
        os.remove(storage_root() / relative_path)
    except FileNotFoundError:
        pass
    except OSError:
        # Any other OS error is swallowed here too: the catalog row is the
        # source of truth (design §4.2) — an orphaned file is recoverable,
        # a stuck row is not. The caller still returns 204.
        pass
