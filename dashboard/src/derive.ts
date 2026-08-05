import type { Alarm, GatewayStatus, LatestReading, PlcHealth, PlcSummary } from "./types";

const SEVERITY_RANK: Record<Alarm["severity"], number> = {
  LOW: 1,
  MEDIUM: 2,
  HIGH: 3,
  CRITICAL: 4,
};

/**
 * Builds one summary row per PLC from three independently-polled feeds.
 *
 * Gateway state wins over alarm state: an offline PLC's readings are stale
 * by definition, so "equipment is fine" and "we haven't heard from it" must
 * never be conflated (this is the project's Issue 3 distinction, carried
 * into the UI).
 */
export function derivePlcSummaries(
  readings: LatestReading[],
  alarms: Alarm[],
  gateways: GatewayStatus[],
): PlcSummary[] {
  const gatewayByPlc = new Map(gateways.map((g) => [g.plc_id, g]));
  const readingsByPlc = new Map<string, LatestReading[]>();
  for (const r of readings) {
    const list = readingsByPlc.get(r.plc_id) ?? [];
    list.push(r);
    readingsByPlc.set(r.plc_id, list);
  }
  const unackedByPlc = new Map<string, Alarm[]>();
  for (const a of alarms) {
    if (a.ack_status !== "UNACKED") continue;
    const list = unackedByPlc.get(a.plc_id) ?? [];
    list.push(a);
    unackedByPlc.set(a.plc_id, list);
  }

  const plcIds = new Set<string>([...readingsByPlc.keys(), ...gatewayByPlc.keys()]);

  return Array.from(plcIds)
    .sort()
    .map((plcId) => {
      const tags = (readingsByPlc.get(plcId) ?? []).slice().sort((a, b) => a.tag_id.localeCompare(b.tag_id));
      const gw = gatewayByPlc.get(plcId);
      const plcAlarms = unackedByPlc.get(plcId) ?? [];

      const gatewayStatus = gw?.status ?? "unknown";
      const health: PlcHealth =
        gatewayStatus === "gateway_down"
          ? "offline"
          : plcAlarms.some((a) => SEVERITY_RANK[a.severity] >= SEVERITY_RANK.HIGH)
            ? "crit"
            : plcAlarms.length > 0
              ? "warn"
              : "ok";

      const primaryTag =
        plcAlarms.length > 0
          ? (tags.find((t) => t.tag_id === plcAlarms[0].tag_id) ?? tags[0] ?? null)
          : (tags[0] ?? null);

      return {
        plcId,
        health,
        tags,
        primaryTag,
        activeAlarmCount: plcAlarms.length,
        gatewayStatus,
        gatewayChangedAt: gw?.occurred_at ?? null,
      };
    });
}
