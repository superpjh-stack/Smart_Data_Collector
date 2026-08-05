import { useState, type FormEvent } from "react";
import type { CreateRawDataSourceInput } from "../../api";
import type { RawDataSourceType } from "../../types";
import { FileDropzone } from "./FileDropzone";
import { SourceTypeBadge } from "./SourceTypeBadge";
import { SOURCE_TYPE_LABEL } from "./sourceTypeMeta";

const TYPES: RawDataSourceType[] = ["excel", "word", "scanned_pdf", "equipment_layout", "db_sql"];

const ACCEPT_BY_TYPE: Record<RawDataSourceType, string> = {
  excel: ".xlsx,.xls",
  word: ".docx,.doc",
  scanned_pdf: ".pdf,.png,.jpg,.jpeg",
  equipment_layout: ".pdf,.png,.jpg,.jpeg,.xlsx,.dwg,.dxf",
  db_sql: "",
};

const FILE_REQUIRED_TYPES = new Set<RawDataSourceType>(["excel", "word", "scanned_pdf"]);
const DB_KIND_OPTIONS = ["MSSQL", "Oracle", "MySQL", "PostgreSQL", "기타"];

interface Props {
  submitting: boolean;
  formError: string | null;
  onCancel: () => void;
  onSubmit: (input: CreateRawDataSourceInput) => void;
}

/** 등록 패널: 소스 타입 세그먼트 + 타입별 폼 전환. DB/SQL 폼에는 "연결
 * 테스트" 버튼이 물리적으로 존재하지 않는다 — 저장 버튼 하나뿐
 * (ui-design.md §2.3). */
export function SourceRegisterPanel({ submitting, formError, onCancel, onSubmit }: Props) {
  const [sourceType, setSourceType] = useState<RawDataSourceType>("excel");
  const [name, setName] = useState("");
  const [equipmentTag, setEquipmentTag] = useState("");
  const [description, setDescription] = useState("");
  const [registeredBy, setRegisteredBy] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [layoutLineName, setLayoutLineName] = useState("");
  const [layoutEquipmentName, setLayoutEquipmentName] = useState("");
  const [layoutLocationDesc, setLayoutLocationDesc] = useState("");
  const [dbKind, setDbKind] = useState(DB_KIND_OPTIONS[0]);
  const [dbHost, setDbHost] = useState("");
  const [dbPort, setDbPort] = useState("");
  const [dbQueryText, setDbQueryText] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  function switchType(t: RawDataSourceType) {
    setSourceType(t);
    setFiles([]);
    setLocalError(null);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLocalError(null);

    if (!name.trim()) {
      setLocalError("소스명을 입력하세요.");
      return;
    }
    if (!registeredBy.trim()) {
      setLocalError("등록자를 입력하세요.");
      return;
    }
    if (FILE_REQUIRED_TYPES.has(sourceType) && files.length === 0) {
      setLocalError("파일을 최소 1개 이상 선택하세요.");
      return;
    }
    if (sourceType === "equipment_layout") {
      if (!layoutLineName.trim() || !layoutEquipmentName.trim()) {
        setLocalError("라인명과 설비명을 입력하세요.");
        return;
      }
      if (files.length > 1) {
        setLocalError("도면 파일은 1개만 첨부할 수 있습니다.");
        return;
      }
    }
    if (sourceType === "db_sql") {
      if (!dbKind.trim() || !dbHost.trim() || !dbQueryText.trim()) {
        setLocalError("DB 종류, 호스트, 쿼리 텍스트를 입력하세요.");
        return;
      }
    }

    onSubmit({
      source_type: sourceType,
      name: name.trim(),
      registered_by: registeredBy.trim(),
      description: description.trim() || undefined,
      equipment_tag: equipmentTag.trim() || undefined,
      layout_line_name: sourceType === "equipment_layout" ? layoutLineName.trim() : undefined,
      layout_equipment_name: sourceType === "equipment_layout" ? layoutEquipmentName.trim() : undefined,
      layout_location_desc:
        sourceType === "equipment_layout" ? layoutLocationDesc.trim() || undefined : undefined,
      db_kind: sourceType === "db_sql" ? dbKind : undefined,
      db_host: sourceType === "db_sql" ? dbHost.trim() : undefined,
      db_port: sourceType === "db_sql" ? dbPort.trim() || undefined : undefined,
      db_query_text: sourceType === "db_sql" ? dbQueryText.trim() : undefined,
      files: FILE_REQUIRED_TYPES.has(sourceType) || sourceType === "equipment_layout" ? files : undefined,
    });
  }

  const isFileType = FILE_REQUIRED_TYPES.has(sourceType);
  const submitLabel = submitting ? "업로드 중…" : isFileType ? "업로드" : "등록";

  return (
    <div className="rd-panel">
      <div className="rd-type-seg" role="radiogroup" aria-label="소스 타입 선택">
        {TYPES.map((t) => (
          <button
            key={t}
            type="button"
            role="radio"
            aria-checked={sourceType === t}
            className={`rd-type-seg-btn ${sourceType === t ? "active" : ""}`}
            onClick={() => switchType(t)}
          >
            <SourceTypeBadge type={t} />
            <span>{SOURCE_TYPE_LABEL[t]}</span>
          </button>
        ))}
      </div>

      <form className="rd-form" onSubmit={handleSubmit}>
        <div className="rd-form-grid">
          <label className="rd-field">
            <span>소스명 *</span>
            <input value={name} onChange={(e) => setName(e.target.value)} required />
          </label>
          <label className="rd-field">
            <span>설비/라인 태그</span>
            <input value={equipmentTag} onChange={(e) => setEquipmentTag(e.target.value)} placeholder="예: LINE-A" />
          </label>
          <label className="rd-field">
            <span>등록자 *</span>
            <input value={registeredBy} onChange={(e) => setRegisteredBy(e.target.value)} required />
          </label>
          <label className="rd-field rd-field-wide">
            <span>설명/비고</span>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
          </label>
        </div>

        {isFileType && (
          <FileDropzone accept={ACCEPT_BY_TYPE[sourceType]} multiple files={files} onChange={setFiles} />
        )}

        {sourceType === "equipment_layout" && (
          <div className="rd-form-grid">
            <label className="rd-field">
              <span>라인명 *</span>
              <input value={layoutLineName} onChange={(e) => setLayoutLineName(e.target.value)} required />
            </label>
            <label className="rd-field">
              <span>설비명 *</span>
              <input value={layoutEquipmentName} onChange={(e) => setLayoutEquipmentName(e.target.value)} required />
            </label>
            <label className="rd-field rd-field-wide">
              <span>위치/구역 설명</span>
              <input value={layoutLocationDesc} onChange={(e) => setLayoutLocationDesc(e.target.value)} />
            </label>
            <div className="rd-field rd-field-wide">
              <span>도면 파일 (선택)</span>
              <FileDropzone
                accept={ACCEPT_BY_TYPE.equipment_layout}
                multiple={false}
                files={files}
                onChange={setFiles}
                label="파일을 드래그하거나 클릭하여 선택 (선택)"
              />
            </div>
          </div>
        )}

        {sourceType === "db_sql" && (
          <div className="rd-form-grid">
            <label className="rd-field">
              <span>DB 종류 *</span>
              <select value={dbKind} onChange={(e) => setDbKind(e.target.value)}>
                {DB_KIND_OPTIONS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </label>
            <label className="rd-field">
              <span>호스트 *</span>
              <input value={dbHost} onChange={(e) => setDbHost(e.target.value)} placeholder="예: 10.20.30.40" />
            </label>
            <label className="rd-field">
              <span>포트</span>
              <input
                value={dbPort}
                onChange={(e) => setDbPort(e.target.value.replace(/[^0-9]/g, ""))}
                inputMode="numeric"
                placeholder="예: 1433"
              />
            </label>
            <label className="rd-field rd-field-wide">
              <span>쿼리 텍스트 *</span>
              <textarea
                className="rd-mono"
                value={dbQueryText}
                onChange={(e) => setDbQueryText(e.target.value)}
                rows={3}
                placeholder="SELECT * FROM ..."
              />
            </label>
            <p className="rd-db-note">이 정보는 메타데이터로만 저장되며, 실제 DB에 접속하지 않습니다.</p>
          </div>
        )}

        {(localError || formError) && <div className="conn-error rd-form-error">{localError ?? formError}</div>}

        <div className="rd-panel-actions">
          <button type="button" className="btn-ghost" onClick={onCancel} disabled={submitting}>
            취소
          </button>
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitLabel}
          </button>
        </div>
      </form>
    </div>
  );
}
