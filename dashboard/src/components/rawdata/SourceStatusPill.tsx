import type { RawDataLifecycleStatus } from "../../types";

const LABEL: Record<RawDataLifecycleStatus, string> = {
  uploading: "업로드 중",
  synced: "등록 완료",
  error: "업로드 실패",
  registered: "등록됨",
};

/** 등록 상태 pill — 설비 건강 상태(ok/warn/crit/offline)와 무관한 업로드/
 * 등록 라이프사이클. `registered`는 DB/SQL 전용 고정값으로, "연결 성공"
 * 뉘앙스를 주지 않도록 의도적으로 중립색을 쓴다 (ui-design.md §1.4). */
export function SourceStatusPill({ status }: { status: RawDataLifecycleStatus }) {
  return <span className={`rd-status-pill ${status}`}>{LABEL[status]}</span>;
}
