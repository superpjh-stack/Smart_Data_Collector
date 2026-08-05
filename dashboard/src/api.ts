import type {
  Alarm,
  AckStatus,
  GatewayStatus,
  LatestReading,
  RawDataSource,
  RawDataSourceSummary,
  RawDataSourceType,
  ReadingPoint,
  ThroughputPoint,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function getJson<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(path, BASE_URL);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      url.searchParams.set(key, String(value));
    }
  }
  const resp = await fetch(url, { headers: { Accept: "application/json" } });
  if (!resp.ok) {
    throw new Error(`${path} -> HTTP ${resp.status}`);
  }
  return (await resp.json()) as T;
}

export async function fetchLatestReadings(): Promise<LatestReading[]> {
  const body = await getJson<{ readings: LatestReading[] }>("/readings/v1/latest");
  return body.readings;
}

export async function fetchReadingHistory(
  plcId: string,
  tagId: string,
  minutes = 60,
): Promise<ReadingPoint[]> {
  const body = await getJson<{ points: ReadingPoint[] }>("/readings/v1/history", {
    plc_id: plcId,
    tag_id: tagId,
    minutes,
  });
  return body.points;
}

export async function fetchThroughput(minutes = 60): Promise<ThroughputPoint[]> {
  const body = await getJson<{ points: ThroughputPoint[] }>("/readings/v1/throughput", { minutes });
  return body.points;
}

export async function fetchAlarms(ackStatus?: AckStatus, limit = 100): Promise<Alarm[]> {
  const params: Record<string, string | number> = { limit };
  if (ackStatus) params.ack_status = ackStatus;
  const body = await getJson<{ alarms: Alarm[] }>("/alarms/v1", params);
  return body.alarms;
}

export async function fetchGatewaySnapshot(): Promise<GatewayStatus[]> {
  const body = await getJson<{ gateways: GatewayStatus[] }>("/status/v1/gateways");
  return body.gateways;
}

export async function fetchGatewayHistory(limit = 50): Promise<GatewayStatus[]> {
  const body = await getJson<{ gateways: GatewayStatus[] }>("/status/v1/gateways/history", { limit });
  return body.gateways;
}

// ── Raw data collection ("원본 데이터 수집") ────────────────────────────

export async function fetchRawDataSummary(): Promise<RawDataSourceSummary> {
  return getJson<RawDataSourceSummary>("/raw-data/v1/sources/summary");
}

export async function fetchRawDataSources(params?: {
  sourceType?: RawDataSourceType;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<{ sources: RawDataSource[]; total: number }> {
  const query: Record<string, string | number> = {};
  if (params?.sourceType) query.source_type = params.sourceType;
  if (params?.search) query.search = params.search;
  if (params?.limit) query.limit = params.limit;
  if (params?.offset) query.offset = params.offset;
  return getJson<{ sources: RawDataSource[]; total: number }>("/raw-data/v1/sources", query);
}

export async function fetchRawDataSource(id: number): Promise<RawDataSource> {
  return getJson<RawDataSource>(`/raw-data/v1/sources/${id}`);
}

export interface CreateRawDataSourceInput {
  source_type: RawDataSourceType;
  name: string;
  registered_by: string;
  description?: string;
  equipment_tag?: string;
  layout_line_name?: string;
  layout_equipment_name?: string;
  layout_location_desc?: string;
  db_kind?: string;
  db_host?: string;
  db_port?: string;
  db_query_text?: string;
  files?: File[];
}

async function readErrorDetail(resp: Response): Promise<string | null> {
  try {
    const body = (await resp.json()) as { detail?: unknown };
    return typeof body?.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

/** Multipart create — 1 source's metadata + 0..N files. Not routed through
 * `getJson` since this is the one write/multipart request in the app. */
export async function createRawDataSources(input: CreateRawDataSourceInput): Promise<RawDataSource[]> {
  const form = new FormData();
  form.set("source_type", input.source_type);
  form.set("name", input.name);
  form.set("registered_by", input.registered_by);
  if (input.description) form.set("description", input.description);
  if (input.equipment_tag) form.set("equipment_tag", input.equipment_tag);
  if (input.layout_line_name) form.set("layout_line_name", input.layout_line_name);
  if (input.layout_equipment_name) form.set("layout_equipment_name", input.layout_equipment_name);
  if (input.layout_location_desc) form.set("layout_location_desc", input.layout_location_desc);
  if (input.db_kind) form.set("db_kind", input.db_kind);
  if (input.db_host) form.set("db_host", input.db_host);
  if (input.db_port) form.set("db_port", input.db_port);
  if (input.db_query_text) form.set("db_query_text", input.db_query_text);
  for (const file of input.files ?? []) {
    form.append("files", file);
  }

  const url = new URL("/raw-data/v1/sources", BASE_URL);
  const resp = await fetch(url, { method: "POST", body: form });
  if (!resp.ok) {
    const detail = await readErrorDetail(resp);
    throw new Error(detail ?? `POST /raw-data/v1/sources -> HTTP ${resp.status}`);
  }
  const body = (await resp.json()) as { sources: RawDataSource[] };
  return body.sources;
}

export async function deleteRawDataSource(id: number): Promise<void> {
  const url = new URL(`/raw-data/v1/sources/${id}`, BASE_URL);
  const resp = await fetch(url, { method: "DELETE" });
  if (!resp.ok) {
    const detail = await readErrorDetail(resp);
    throw new Error(detail ?? `DELETE /raw-data/v1/sources/${id} -> HTTP ${resp.status}`);
  }
}

export function rawDataDownloadUrl(id: number): string {
  return new URL(`/raw-data/v1/sources/${id}/download`, BASE_URL).toString();
}
