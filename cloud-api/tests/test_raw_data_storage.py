"""Unit tests for raw_data_storage's pure helpers — no DB, no event loop.

Design doc §8/§9 called for this file alongside test_raw_data_db.py; QA's
review (raw_data.analysis.md §5 P2) flagged it as missing.
"""

from pathlib import Path

import pytest

from cloud_api.raw_data_storage import (
    RawDataValidationError,
    build_relative_path,
    resolve_safe_content_type,
    sanitize_filename,
    validate_extension,
    validate_file_count,
)


def test_validate_file_count_rejects_below_minimum():
    with pytest.raises(RawDataValidationError):
        validate_file_count("excel", 0)


def test_validate_file_count_rejects_above_maximum():
    with pytest.raises(RawDataValidationError):
        validate_file_count("equipment_layout", 2)


def test_validate_file_count_db_sql_requires_exactly_zero():
    validate_file_count("db_sql", 0)
    with pytest.raises(RawDataValidationError):
        validate_file_count("db_sql", 1)


def test_validate_file_count_accepts_within_range():
    validate_file_count("excel", 1)
    validate_file_count("excel", 5)
    validate_file_count("equipment_layout", 0)
    validate_file_count("equipment_layout", 1)


def test_validate_extension_accepts_allowed():
    assert validate_extension("excel", "report.xlsx") == ".xlsx"
    assert validate_extension("scanned_pdf", "scan.PDF") == ".pdf"  # case-insensitive


def test_validate_extension_rejects_disallowed():
    with pytest.raises(RawDataValidationError):
        validate_extension("excel", "report.pdf")


def test_validate_extension_rejects_missing_extension():
    with pytest.raises(RawDataValidationError):
        validate_extension("excel", "report")


def test_validate_extension_db_sql_has_no_allowed_extensions():
    with pytest.raises(RawDataValidationError):
        validate_extension("db_sql", "anything.txt")


def test_sanitize_filename_strips_unsafe_characters():
    # Path(...).stem takes only the last path component first (so any
    # separator in the client-supplied name is dropped, not just replaced),
    # then remaining disallowed characters become underscores.
    assert sanitize_filename("a b*c.xlsx", ".xlsx") == "a_b_c.xlsx"


def test_sanitize_filename_preserves_korean_via_underscore_fallback_not_needed():
    # Korean characters fall outside [A-Za-z0-9._-] and are replaced —
    # the sanitized on-disk name is ASCII-safe; the original is kept
    # separately as the DB display name (see raw_data.design.md §5 decision).
    result = sanitize_filename("원본파일명.xlsx", ".xlsx")
    assert result.endswith(".xlsx")
    assert "원" not in result


def test_sanitize_filename_empty_stem_falls_back_to_file():
    # A genuinely empty stem (not just one that becomes short after
    # sanitizing) is the only way to hit the "file" fallback.
    assert sanitize_filename("", ".xlsx") == "file.xlsx"


def test_sanitize_filename_caps_length():
    long_stem = "a" * 300
    result = sanitize_filename(f"{long_stem}.xlsx", ".xlsx")
    assert len(result) <= 150 + len(".xlsx")


def test_build_relative_path_layout():
    rel = build_relative_path("excel", "report.xlsx")
    parts = rel.parts
    assert parts[0] == "excel"
    assert len(parts[1]) == 4 and parts[1].isdigit()  # yyyy
    assert len(parts[2]) == 2 and parts[2].isdigit()  # mm
    assert parts[3].endswith("__report.xlsx")


def test_build_relative_path_is_unique_per_call():
    a = build_relative_path("excel", "same_name.xlsx")
    b = build_relative_path("excel", "same_name.xlsx")
    assert a != b  # uuid4 prefix guarantees no collision even for identical names


def test_resolve_safe_content_type_matches_canonical():
    assert (
        resolve_safe_content_type(
            "report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_resolve_safe_content_type_falls_back_on_mismatch():
    # Client claimed a content_type that doesn't match the extension's
    # canonical type — never trust it, serve octet-stream instead.
    assert resolve_safe_content_type("report.xlsx", "text/plain") == "application/octet-stream"


def test_resolve_safe_content_type_falls_back_when_none_stored():
    assert resolve_safe_content_type("report.xlsx", None) == "application/octet-stream"


def test_resolve_safe_content_type_unknown_extension_is_octet_stream():
    assert resolve_safe_content_type("report.dwg", "application/octet-stream") == "application/octet-stream"
