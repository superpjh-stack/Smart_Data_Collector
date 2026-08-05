import { rawDataDownloadUrl, type CreateRawDataSourceInput } from "../../api";
import type { RawDataSource, RawDataSourceType } from "../../types";
import { SourceStatusPill } from "./SourceStatusPill";
import { SourceTypeBadge } from "./SourceTypeBadge";

export interface OptimisticRow {
  clientId: string;
  sourceType: RawDataSourceType;
  name: string;
  fileLabel: string | null;
  equipmentTag: string | null;
  registeredBy: string;
  status: "uploading" | "error";
  errorMessage?: string;
  /** Original submission — replayed on "재시도". */
  retryInput?: CreateRawDataSourceInput;
}

interface Props {
  sources: RawDataSource[];
  optimisticRows: OptimisticRow[];
  onSelect: (source: RawDataSource) => void;
  onRetry: (clientId: string) => void;
  onDismissError: (clientId: string) => void;
}

const HAS_DOWNLOAD_TYPES = new Set<RawDataSourceType>(["excel", "word", "scanned_pdf"]);

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

/** 목록 테이블 — 기존 `.gw-list`/`.gw-row` grid 관용구를 확장 적용
 * (ui-design.md §2.5). db_sql은 실제 상태색이 아닌 중립 `registered` pill로,
 * 나머지 4종은 등록 성공 시 `synced`(등록완료) pill로 표시한다. */
export function SourceList({ sources, optimisticRows, onSelect, onRetry, onDismissError }: Props) {
  return (
    <div className="rd-list">
      <div className="rd-list-head">
        <span>타입</span>
        <span>소스명 / 파일명</span>
        <span>설비·라인 태그</span>
        <span>등록자</span>
        <span>등록일시</span>
        <span>상태</span>
        <span>액션</span>
      </div>

      {optimisticRows.map((row) => (
        <div className="rd-list-row optimistic" key={row.clientId}>
          <SourceTypeBadge type={row.sourceType} />
          <span className="rd-list-name">
            {row.name}
            {row.fileLabel && <span className="rd-list-file"> · {row.fileLabel}</span>}
            {row.status === "error" && row.errorMessage && (
              <span className="rd-inline-error">⚠ 업로드 실패: {row.errorMessage}</span>
            )}
          </span>
          <span className="rd-list-tag">{row.equipmentTag ?? "–"}</span>
          <span>{row.registeredBy}</span>
          <span className="num">–</span>
          <SourceStatusPill status={row.status} />
          {row.status === "error" ? (
            <div className="rd-row-actions">
              <button type="button" className="rd-link-btn" onClick={() => onRetry(row.clientId)}>
                재시도
              </button>
              <button type="button" className="rd-link-btn" onClick={() => onDismissError(row.clientId)}>
                닫기
              </button>
            </div>
          ) : (
            <span className="rd-list-progress">업로드 중…</span>
          )}
        </div>
      ))}

      {sources.map((source) => {
        const hasDownload =
          HAS_DOWNLOAD_TYPES.has(source.source_type) ||
          (source.source_type === "equipment_layout" && !!source.file_name);
        return (
          <div
            className="rd-list-row"
            key={source.id}
            onClick={() => onSelect(source)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter") onSelect(source);
            }}
          >
            <SourceTypeBadge type={source.source_type} />
            <span className="rd-list-name">
              {source.name}
              {source.file_name && <span className="rd-list-file"> · {source.file_name}</span>}
              {source.file_size_bytes != null && (
                <span className="rd-list-size num"> {formatBytes(source.file_size_bytes)}</span>
              )}
            </span>
            <span className="rd-list-tag">{source.equipment_tag ?? "–"}</span>
            <span>{source.registered_by}</span>
            <span className="num">{formatDate(source.created_at)}</span>
            <SourceStatusPill status={source.source_type === "db_sql" ? "registered" : "synced"} />
            {hasDownload ? (
              <a className="rd-link-btn" href={rawDataDownloadUrl(source.id)} onClick={(e) => e.stopPropagation()}>
                ⬇ 다운로드
              </a>
            ) : (
              <button
                type="button"
                className="rd-link-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(source);
                }}
              >
                상세보기
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
