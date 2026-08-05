import type { RawDataSourceType } from "../../types";

const BADGE_TEXT: Record<RawDataSourceType, string> = {
  excel: "XLS",
  word: "DOC",
  scanned_pdf: "SCN",
  equipment_layout: "LAY",
  db_sql: "SQL",
};

/** 소스 "종류" 시각 언어 — 상태색(ok/warn/crit)과 충돌하지 않는 별도
 * 배지 네임스페이스(--src-*)를 쓰는 고정폭 코드 라벨. ui-design.md §1.3. */
export function SourceTypeBadge({ type }: { type: RawDataSourceType }) {
  return <span className={`src-badge src-${type}`}>{BADGE_TEXT[type]}</span>;
}
