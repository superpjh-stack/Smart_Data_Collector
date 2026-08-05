import type { PlcSummary, ReadingPoint } from "../types";
import { Sparkline } from "./Sparkline";

const HEALTH_LABEL: Record<PlcSummary["health"], string> = {
  ok: "정상",
  warn: "경고",
  crit: "알람",
  offline: "오프라인",
};

interface Props {
  plcs: PlcSummary[];
  history: Map<string, ReadingPoint[]>;
}

export function PlcGrid({ plcs, history }: Props) {
  if (plcs.length === 0) {
    return <p className="empty-note">아직 수신된 PLC 데이터가 없습니다 — collector가 연결되면 여기 표시됩니다.</p>;
  }

  return (
    <div className="grid">
      {plcs.map((plc) => (
        <PlcCard key={plc.plcId} plc={plc} history={history.get(historyKey(plc)) ?? []} />
      ))}
    </div>
  );
}

function historyKey(plc: PlcSummary): string {
  return plc.primaryTag ? `${plc.plcId}:${plc.primaryTag.tag_id}` : "";
}

function PlcCard({ plc, history }: { plc: PlcSummary; history: ReadingPoint[] }) {
  const isOffline = plc.health === "offline";

  return (
    <div className={`card ${plc.health}`}>
      <div className="card-head">
        <span className="plc">{plc.plcId}</span>
        <span className={`status-pill ${plc.health}`}>{HEALTH_LABEL[plc.health]}</span>
      </div>

      {isOffline ? (
        <div className="offline-note">신호 없음 — heartbeat 유실</div>
      ) : plc.primaryTag ? (
        <>
          <div className="card-tag">{plc.primaryTag.tag_id}</div>
          <div className="card-value">
            <span className="n num">{plc.primaryTag.value.toFixed(1)}</span>
            <span className="u">{plc.primaryTag.unit ?? ""}</span>
          </div>
          <Sparkline points={history} colorToken={plc.health === "ok" ? "ok" : plc.health} />
        </>
      ) : (
        <div className="offline-note">아직 데이터 없음</div>
      )}

      <div className="card-foot">
        <span>{isOffline ? "게이트웨이" : `${plc.tags.length} tags 구독 중`}</span>
        {plc.activeAlarmCount > 0 ? (
          <span className="alarm-badge">● 알람 {plc.activeAlarmCount}건</span>
        ) : (
          <span>이상 없음</span>
        )}
      </div>
    </div>
  );
}
