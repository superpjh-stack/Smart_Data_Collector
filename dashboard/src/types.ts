export type Quality = "Good" | "Uncertain" | "Bad";
export type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type AckStatus = "UNACKED" | "ACKED" | "AUTO_CLEARED";
export type GatewayEventStatus = "gateway_down" | "gateway_recovered";

export interface LatestReading {
  plc_id: string;
  tag_id: string;
  tag_name: string;
  value: number;
  unit: string | null;
  data_type: string;
  quality: Quality;
  timestamp_utc: string;
}

export interface ReadingPoint {
  bucket: string;
  avg_value: number;
  min_value: number;
  max_value: number;
  sample_count: number;
}

export interface Alarm {
  alarm_id: string;
  plc_id: string;
  tag_id: string;
  severity: Severity;
  condition: string;
  triggered_value: number;
  triggered_at_utc: string;
  cleared_at_utc: string | null;
  ack_status: AckStatus;
  seq: number;
  config_version: number;
}

export interface GatewayStatus {
  plc_id: string;
  status: GatewayEventStatus;
  occurred_at: string;
}

export interface ThroughputPoint {
  bucket: string;
  reading_count: number;
}

// ── Raw data collection ("원본 데이터 수집") ────────────────────────────

export type RawDataSourceType = "excel" | "word" | "scanned_pdf" | "equipment_layout" | "db_sql";

/** Server-side status is always "registered". "uploading"/"synced"/"error"
 * are client-local lifecycle states for an in-flight or just-completed
 * registration (see raw_data.ui-design.md §1.4) — they never come from the
 * API and are never persisted. */
export type RawDataLifecycleStatus = "uploading" | "synced" | "error" | "registered";

export interface RawDataSource {
  id: number;
  source_type: RawDataSourceType;
  name: string;
  description: string | null;
  equipment_tag: string | null;
  registered_by: string;
  status: "registered";
  file_name: string | null;
  file_size_bytes: number | null;
  content_type: string | null;
  layout_line_name: string | null;
  layout_equipment_name: string | null;
  layout_location_desc: string | null;
  db_kind: string | null;
  db_host: string | null;
  db_port: number | null;
  db_query_text: string | null;
  created_at: string;
}

export interface RawDataSourceSummary {
  excel: number;
  word: number;
  scanned_pdf: number;
  equipment_layout: number;
  db_sql: number;
  total: number;
}

/** Derived, client-side: not a wire type. One card's worth of state. */
export type PlcHealth = "ok" | "warn" | "crit" | "offline";

export interface PlcSummary {
  plcId: string;
  health: PlcHealth;
  tags: LatestReading[];
  primaryTag: LatestReading | null;
  activeAlarmCount: number;
  gatewayStatus: GatewayEventStatus | "unknown";
  gatewayChangedAt: string | null;
}
