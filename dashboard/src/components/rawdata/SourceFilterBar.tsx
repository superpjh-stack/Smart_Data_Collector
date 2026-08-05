import type { RawDataSourceType } from "../../types";
import { SourceTypeBadge } from "./SourceTypeBadge";

const TYPES: RawDataSourceType[] = ["excel", "word", "scanned_pdf", "equipment_layout", "db_sql"];

interface Props {
  active: RawDataSourceType | "all";
  onChange: (v: RawDataSourceType | "all") => void;
  search: string;
  onSearchChange: (v: string) => void;
}

export function SourceFilterBar({ active, onChange, search, onSearchChange }: Props) {
  return (
    <div className="rd-filter-bar">
      <div className="rd-chip-group">
        <button
          type="button"
          className={`rd-chip ${active === "all" ? "active" : ""}`}
          onClick={() => onChange("all")}
        >
          전체
        </button>
        {TYPES.map((type) => (
          <button
            key={type}
            type="button"
            className={`rd-chip ${active === type ? "active" : ""}`}
            onClick={() => onChange(type)}
          >
            <SourceTypeBadge type={type} />
          </button>
        ))}
      </div>
      <input
        type="search"
        className="rd-search"
        placeholder="소스명·태그 검색"
        aria-label="소스명·태그 검색"
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
      />
    </div>
  );
}
