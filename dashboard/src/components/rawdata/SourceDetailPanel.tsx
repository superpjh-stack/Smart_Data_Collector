import { rawDataDownloadUrl } from "../../api";
import type { RawDataSource } from "../../types";
import { SourceTypeBadge } from "./SourceTypeBadge";
import { SOURCE_TYPE_LABEL } from "./sourceTypeMeta";
import { SourceStatusPill } from "./SourceStatusPill";

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "–";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface Props {
  source: RawDataSource;
  onClose: () => void;
  onDelete: (id: number) => void;
}

export function SourceDetailPanel({ source, onClose, onDelete }: Props) {
  const hasFile = !!source.file_name;

  return (
    <div className="rd-modal-backdrop" onClick={onClose}>
      <div
        className="rd-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`${source.name} 상세`}
      >
        <div className="rd-modal-head">
          <SourceTypeBadge type={source.source_type} />
          <h3>{source.name}</h3>
          <button type="button" className="rd-modal-close" onClick={onClose} aria-label="닫기">
            ✕
          </button>
        </div>

        <dl className="rd-detail-grid">
          <dt>소스 타입</dt>
          <dd>{SOURCE_TYPE_LABEL[source.source_type]}</dd>
          <dt>상태</dt>
          <dd>
            <SourceStatusPill status={source.source_type === "db_sql" ? "registered" : "synced"} />
          </dd>
          <dt>설비/라인 태그</dt>
          <dd>{source.equipment_tag ?? "–"}</dd>
          <dt>등록자</dt>
          <dd>{source.registered_by}</dd>
          <dt>등록일시</dt>
          <dd className="num">{formatDate(source.created_at)}</dd>
          <dt>설명/비고</dt>
          <dd>{source.description ?? "–"}</dd>

          {hasFile && (
            <>
              <dt>파일명</dt>
              <dd>{source.file_name}</dd>
              <dt>파일 크기</dt>
              <dd className="num">{formatBytes(source.file_size_bytes)}</dd>
            </>
          )}

          {source.source_type === "equipment_layout" && (
            <>
              <dt>라인명</dt>
              <dd>{source.layout_line_name ?? "–"}</dd>
              <dt>설비명</dt>
              <dd>{source.layout_equipment_name ?? "–"}</dd>
              <dt>위치/구역</dt>
              <dd>{source.layout_location_desc ?? "–"}</dd>
            </>
          )}

          {source.source_type === "db_sql" && (
            <>
              <dt>DB 종류</dt>
              <dd>{source.db_kind ?? "–"}</dd>
              <dt>호스트:포트</dt>
              <dd className="num">
                {source.db_host ?? "–"}
                {source.db_port ? `:${source.db_port}` : ""}
              </dd>
              <dt>쿼리 텍스트</dt>
              <dd className="rd-mono">{source.db_query_text ?? "–"}</dd>
            </>
          )}
        </dl>

        {source.source_type === "db_sql" && (
          <p className="rd-db-note">이 정보는 메타데이터로만 저장되며, 실제 DB에 접속하지 않습니다.</p>
        )}

        <div className="rd-modal-actions">
          {hasFile && (
            <a className="btn-ghost" href={rawDataDownloadUrl(source.id)}>
              ⬇ 다운로드
            </a>
          )}
          <button type="button" className="btn-danger" onClick={() => onDelete(source.id)}>
            삭제
          </button>
        </div>
      </div>
    </div>
  );
}
