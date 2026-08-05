import { useEffect, useMemo, useState } from "react";
import {
  fetchAlarms,
  fetchGatewayHistory,
  fetchGatewaySnapshot,
  fetchLatestReadings,
  fetchRawDataSummary,
  fetchReadingHistory,
  fetchThroughput,
} from "./api";
import { derivePlcSummaries } from "./derive";
import { usePolling } from "./hooks/usePolling";
import { PlcGrid } from "./components/PlcGrid";
import { AlarmList } from "./components/AlarmList";
import { GatewayHistory } from "./components/GatewayHistory";
import { RawDataTab } from "./components/rawdata/RawDataTab";
import { ThroughputChart } from "./components/ThroughputChart";
import type { ReadingPoint } from "./types";

type Tab = "overview" | "alarms" | "gateways" | "rawdata";

export default function App() {
  const [tab, setTab] = useState<Tab>("overview");
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const readings = usePolling(fetchLatestReadings, 5000);
  const alarms = usePolling(() => fetchAlarms(), 5000);
  const gateways = usePolling(fetchGatewaySnapshot, 5000);
  const gatewayHistory = usePolling(() => fetchGatewayHistory(50), 15000);
  const throughput = usePolling(() => fetchThroughput(60), 10000);
  const rawDataSummary = usePolling(fetchRawDataSummary, 15000);

  const plcs = useMemo(
    () => derivePlcSummaries(readings.data ?? [], alarms.data ?? [], gateways.data ?? []),
    [readings.data, alarms.data, gateways.data],
  );

  const [history, setHistory] = useState<Map<string, ReadingPoint[]>>(new Map());
  const trackedTagKeys = plcs
    .filter((p) => p.health !== "offline" && p.primaryTag)
    .map((p) => `${p.plcId}:${p.primaryTag!.tag_id}`)
    .join(",");

  useEffect(() => {
    if (!trackedTagKeys) return;
    let cancelled = false;

    (async () => {
      const entries = await Promise.all(
        trackedTagKeys.split(",").map(async (key) => {
          const [plcId, tagId] = key.split(":");
          try {
            const points = await fetchReadingHistory(plcId, tagId, 60);
            return [key, points] as const;
          } catch {
            return [key, [] as ReadingPoint[]] as const;
          }
        }),
      );
      if (!cancelled) setHistory(new Map(entries));
    })();

    return () => {
      cancelled = true;
    };
  }, [trackedTagKeys]);

  const unackedCount = (alarms.data ?? []).filter((a) => a.ack_status === "UNACKED").length;
  const gatewaysUp = plcs.filter((p) => p.health !== "offline").length;
  const totalGateways = plcs.length;
  const currentRate =
    throughput.data && throughput.data.length > 0 ? throughput.data[throughput.data.length - 1].reading_count : 0;

  const anyError = readings.error ?? alarms.error ?? gateways.error;

  return (
    <div className="app">
      <div className="topbar">
        <div className="brand">
          <span className={`dot ${anyError ? "crit" : "ok"}`} />
          <h1>Smart Data Collector · 운영 현황판</h1>
          <span className="live-badge">{anyError ? "연결 끊김" : "실시간 연결됨"}</span>
        </div>
        <div className="kpis">
          <Kpi
            value={`${gatewaysUp} / ${totalGateways || "–"}`}
            label="게이트웨이 가동"
            tone={totalGateways > 0 && gatewaysUp === totalGateways ? "ok" : "warn"}
          />
          <Kpi value={String(unackedCount)} label="미확인 알람" tone={unackedCount > 0 ? "crit" : "ok"} />
          <Kpi value={`~${currentRate} pts/min`} label="최근 1분 수집량" />
          <Kpi value={readings.loading ? "동기화 중…" : "정상"} label="수신 상태" />
        </div>
        <button className="theme-toggle" type="button" onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}>
          ☾ 다크 / ☀ 라이트
        </button>
      </div>

      <div className="tabs" role="tablist">
        <TabButton active={tab === "overview"} onClick={() => setTab("overview")} label="개요" pill={String(totalGateways)} />
        <TabButton
          active={tab === "alarms"}
          onClick={() => setTab("alarms")}
          label="알람"
          pill={String(alarms.data?.length ?? 0)}
          pillTone="crit"
        />
        <TabButton
          active={tab === "gateways"}
          onClick={() => setTab("gateways")}
          label="게이트웨이 이력"
          pill={String(gatewayHistory.data?.length ?? 0)}
        />
        <TabButton
          active={tab === "rawdata"}
          onClick={() => setTab("rawdata")}
          label="원본 데이터 수집"
          title="Raw Data Collection"
          pill={String(rawDataSummary.data?.total ?? 0)}
        />
      </div>

      <main>
        {anyError && (
          <div className="conn-error">
            cloud_api에 연결할 수 없습니다 ({anyError.message}). API 서버가 떠 있는지, VITE_API_BASE_URL 설정을 확인하세요.
          </div>
        )}

        {tab === "overview" && <PlcGrid plcs={plcs} history={history} />}
        {tab === "alarms" && <AlarmList alarms={alarms.data ?? []} />}
        {tab === "gateways" && <GatewayHistory events={gatewayHistory.data ?? []} />}
        {tab === "rawdata" && <RawDataTab />}

        {tab !== "rawdata" && (
          <div className="throughput">
            <div className="throughput-head">
              <h2>수집 처리량 — 최근 60분</h2>
              <span className="cur num">
                현재 <b>{currentRate}</b> pts/min
              </span>
            </div>
            <ThroughputChart points={throughput.data ?? []} color={theme === "dark" ? "#33b7e8" : "#0f7fa8"} />
          </div>
        )}
      </main>

      <footer className="page-foot">
        DATA SOURCE: cloud_api (readings / alarms / collector_status_events) · dashboard 프로젝트 · Smart Data Collector
      </footer>
    </div>
  );
}

function Kpi({ value, label, tone }: { value: string; label: string; tone?: "ok" | "warn" | "crit" }) {
  return (
    <div className="kpi">
      <span className={`v num ${tone ?? ""}`}>{value}</span>
      <span className="l">{label}</span>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  label,
  pill,
  pillTone,
  title,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  pill: string;
  pillTone?: "crit";
  title?: string;
}) {
  return (
    <button className="tab" role="tab" aria-selected={active} onClick={onClick} type="button" title={title}>
      {label} <span className={`pill ${pillTone ?? ""}`}>{pill}</span>
    </button>
  );
}
