import type { RawDataSourceSummary, RawDataSourceType } from "../../types";
import { SourceTypeBadge } from "./SourceTypeBadge";
import { SOURCE_TYPE_LABEL } from "./sourceTypeMeta";

const TYPES: RawDataSourceType[] = ["excel", "word", "scanned_pdf", "equipment_layout", "db_sql"];

interface Props {
  summary: RawDataSourceSummary | null;
  activeFilter: RawDataSourceType | "all";
  onSelect: (type: RawDataSourceType) => void;
}

/** 5개 요약 카운트 카드. 0건이어도 카드는 숨기지 않는다 — "이 타입은 아직
 * 하나도 없다"는 정보 자체가 유의미하기 때문 (ui-design.md §3.1). */
export function SourceSummaryCards({ summary, activeFilter, onSelect }: Props) {
  return (
    <div className="rd-summary-grid">
      {TYPES.map((type) => (
        <button
          key={type}
          type="button"
          className={`rd-summary-card ${activeFilter === type ? "active" : ""}`}
          onClick={() => onSelect(type)}
        >
          <SourceTypeBadge type={type} />
          <span className="rd-summary-num num">{summary ? summary[type] : 0}</span>
          <span className="rd-summary-label">건 등록됨</span>
          <span className="rd-summary-type">{SOURCE_TYPE_LABEL[type]}</span>
        </button>
      ))}
    </div>
  );
}
