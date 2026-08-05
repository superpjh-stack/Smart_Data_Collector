from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from collector.config import TagConfig


class AlarmState(Enum):
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True)
class AlarmEvent:
    alarm_id: str
    plc_id: str
    tag_id: str
    severity: str
    condition: str
    triggered_value: float
    triggered_at_utc: datetime
    cleared_at_utc: datetime | None
    ack_status: str
    config_version: int


def build_alarm_id(plc_id: str, tag_id: str, triggered_at_utc: datetime, seq: int) -> str:
    """Deterministic dedup key (Issue 1) — never a random UUID.

    A crash between local persistence and cloud ACK must regenerate the same
    id on re-evaluation so the server's UNIQUE constraint on
    (plc_id, tag_id, triggered_at_utc, seq) rejects the retransmit instead of
    creating a duplicate-looking alarm on the dashboard.
    """
    ts = triggered_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"
    return f"{plc_id}:{tag_id}:{ts}:{seq}"


class AlarmEngine:
    """Per-tag hysteresis state machine (Issue 5).

    Trigger at max_alarm/min_alarm, clear only after crossing back past
    clear_margin — without this gap a value oscillating right at the
    threshold re-triggers on every sample, flooding the alarm store and
    training operators to ignore alarms (alarm fatigue).
    """

    def __init__(self) -> None:
        self._state: dict[tuple[str, str], AlarmState] = {}

    def evaluate(
        self,
        tag: TagConfig,
        value: float,
        now: datetime,
        seq: int,
    ) -> AlarmEvent | None:
        key = (tag.plc_id, tag.tag_id)
        state = self._state.get(key, AlarmState.NORMAL)

        new_state, condition, triggered = self._next_state(tag, value, state)
        if new_state == state:
            return None

        self._state[key] = new_state

        if triggered:
            return AlarmEvent(
                alarm_id=build_alarm_id(tag.plc_id, tag.tag_id, now, seq),
                plc_id=tag.plc_id,
                tag_id=tag.tag_id,
                severity=tag.severity,
                condition=condition,
                triggered_value=value,
                triggered_at_utc=now,
                cleared_at_utc=None,
                ack_status="UNACKED",
                config_version=tag.updated_version,
            )

        # Transition back to NORMAL is a clear, not a new alarm row in this
        # engine's model — the caller updates the existing alarm's
        # cleared_at_utc rather than emitting a fresh event. Signaled by
        # returning None with new_state == NORMAL having already been stored.
        return None

    @staticmethod
    def _next_state(
        tag: TagConfig, value: float, state: AlarmState
    ) -> tuple[AlarmState, str, bool]:
        if state == AlarmState.NORMAL:
            if tag.max_alarm is not None and value >= tag.max_alarm:
                return AlarmState.HIGH, f"value >= {tag.max_alarm}", True
            if tag.min_alarm is not None and value <= tag.min_alarm:
                return AlarmState.LOW, f"value <= {tag.min_alarm}", True
            return AlarmState.NORMAL, "", False

        if state == AlarmState.HIGH:
            clear_at = (tag.max_alarm or 0) - tag.clear_margin
            if value <= clear_at:
                return AlarmState.NORMAL, f"value <= {clear_at} (cleared)", False
            return AlarmState.HIGH, "", False

        if state == AlarmState.LOW:
            clear_at = (tag.min_alarm or 0) + tag.clear_margin
            if value >= clear_at:
                return AlarmState.NORMAL, f"value >= {clear_at} (cleared)", False
            return AlarmState.LOW, "", False

        return AlarmState.NORMAL, "", False

    def current_state(self, plc_id: str, tag_id: str) -> AlarmState:
        return self._state.get((plc_id, tag_id), AlarmState.NORMAL)
