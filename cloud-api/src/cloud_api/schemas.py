from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Reading(BaseModel):
    plc_id: str
    tag_id: str
    tag_name: str
    timestamp_utc: datetime
    value: float
    data_type: str
    unit: str | None = None
    quality: Literal["Good", "Uncertain", "Bad"]
    seq: int


class ReadingsBatch(BaseModel):
    readings: list[Reading]


class Alarm(BaseModel):
    alarm_id: str
    plc_id: str
    tag_id: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    condition: str
    triggered_value: float
    triggered_at_utc: datetime
    cleared_at_utc: datetime | None = None
    ack_status: Literal["UNACKED", "ACKED", "AUTO_CLEARED"] = "UNACKED"
    seq: int
    config_version: int


class AlarmsBatch(BaseModel):
    alarms: list[Alarm]


class IngestResult(BaseModel):
    inserted: int
    duplicates: int


class TagConfig(BaseModel):
    plc_id: str
    tag_id: str
    opc_node_id: str
    unit: str | None = None
    data_type: str
    min_alarm: float | None = None
    max_alarm: float | None = None
    clear_margin: float = 0
    deadband: float = 0
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    sampling_interval_ms: int = 5000
    updated_version: int


class TagConfigResponse(BaseModel):
    tags: list[TagConfig]
    current_version: int


# ── Dashboard read models ────────────────────────────────────────────────
# These back the operator dashboard's GET queries. They intentionally reuse
# the wire vocabulary of the ingestion models above rather than inventing a
# parallel set of field names.


class LatestReading(BaseModel):
    plc_id: str
    tag_id: str
    tag_name: str
    value: float
    unit: str | None = None
    data_type: str
    quality: Literal["Good", "Uncertain", "Bad"]
    timestamp_utc: datetime


class LatestReadingsResponse(BaseModel):
    readings: list[LatestReading]


class ReadingPoint(BaseModel):
    bucket: datetime
    avg_value: float
    min_value: float
    max_value: float
    sample_count: int


class ReadingsHistoryResponse(BaseModel):
    plc_id: str
    tag_id: str
    points: list[ReadingPoint]


class AlarmsListResponse(BaseModel):
    alarms: list[Alarm]


class GatewayStatus(BaseModel):
    plc_id: str
    status: Literal["gateway_down", "gateway_recovered"]
    occurred_at: datetime


class GatewaysStatusResponse(BaseModel):
    gateways: list[GatewayStatus]


class ThroughputPoint(BaseModel):
    bucket: datetime
    reading_count: int


class ThroughputResponse(BaseModel):
    points: list[ThroughputPoint]


# ── Raw data collection ("원본 데이터 수집") ─────────────────────────────
# Catalog of raw, pre-analysis data sources registered by project staff.
# See docs/02-design/features/raw_data.design.md §3.3 for the wire contract.

RawDataSourceType = Literal["excel", "word", "scanned_pdf", "equipment_layout", "db_sql"]


class RawDataSource(BaseModel):
    id: int
    source_type: RawDataSourceType
    name: str
    description: str | None = None
    equipment_tag: str | None = None
    registered_by: str
    status: Literal["registered"]
    file_name: str | None = None
    file_size_bytes: int | None = None
    content_type: str | None = None
    layout_line_name: str | None = None
    layout_equipment_name: str | None = None
    layout_location_desc: str | None = None
    db_kind: str | None = None
    db_host: str | None = None
    db_port: int | None = None
    db_query_text: str | None = None
    created_at: datetime
    # file_path is intentionally NOT exposed on the wire — it is a server-side
    # storage detail; the download endpoint is the only way to reach the bytes.


class RawDataSourceCreateResponse(BaseModel):
    sources: list[RawDataSource]


class RawDataSourceListResponse(BaseModel):
    sources: list[RawDataSource]
    total: int


class RawDataSourceSummary(BaseModel):
    excel: int
    word: int
    scanned_pdf: int
    equipment_layout: int
    db_sql: int
    total: int
