import type { GatewayStatus } from "../types";

function formatTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "방금 전";
  if (mins < 60) return `${mins}분 전`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}시간 전`;
  return new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function GatewayHistory({ events }: { events: GatewayStatus[] }) {
  if (events.length === 0) {
    return <p className="empty-note">기록된 게이트웨이 이벤트가 없습니다.</p>;
  }

  return (
    <div className="gw-list">
      {events.map((g, idx) => {
        const isDown = g.status === "gateway_down";
        return (
          <div className="gw-row" key={`${g.plc_id}-${g.occurred_at}-${idx}`}>
            <span className="plc">{g.plc_id}</span>
            <span className={`status-pill ${isDown ? "crit" : "ok"}`}>{isDown ? "DOWN" : "복구"}</span>
            <span className="gw-event">{isDown ? "heartbeat 유실" : "정상 복구"}</span>
            <span className="gw-time num">{formatTime(g.occurred_at)}</span>
          </div>
        );
      })}
    </div>
  );
}
