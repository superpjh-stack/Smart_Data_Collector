import type { Alarm } from "../types";

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "방금 전";
  if (mins < 60) return `${mins}분 전`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}시간 전`;
  return new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const SEV_LABEL: Record<Alarm["severity"], string> = {
  LOW: "LOW",
  MEDIUM: "MEDIUM",
  HIGH: "HIGH",
  CRITICAL: "CRITICAL",
};

export function AlarmList({ alarms }: { alarms: Alarm[] }) {
  if (alarms.length === 0) {
    return <p className="empty-note">활성 알람이 없습니다.</p>;
  }

  return (
    <div className="alarm-list">
      {alarms.map((a) => {
        const isHigh = a.severity === "HIGH" || a.severity === "CRITICAL";
        return (
          <div className="alarm-row" key={a.alarm_id}>
            <div className={`sev-bar ${isHigh ? "high" : "low"}`} />
            <div className="alarm-main">
              <div className="t1">
                {a.plc_id} · {a.tag_id}
              </div>
              <div className="t2">
                {a.condition} — {a.triggered_value} ({a.cleared_at_utc ? "해제됨" : "진행 중"})
              </div>
            </div>
            <span className={`sev-tag ${isHigh ? "high" : "low"}`}>{SEV_LABEL[a.severity]}</span>
            <span className={`ack-tag ${a.ack_status === "UNACKED" ? "unack" : "ack"}`}>
              {a.ack_status === "UNACKED" ? "미확인" : a.ack_status === "ACKED" ? "확인됨" : "자동 해제"}
            </span>
            <span className="alarm-time num">{relativeTime(a.triggered_at_utc)}</span>
          </div>
        );
      })}
    </div>
  );
}
