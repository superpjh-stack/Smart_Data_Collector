from datetime import datetime, timezone

from collector.alarm_engine import AlarmEngine, AlarmState, build_alarm_id
from collector.config import TagConfig


def make_tag(**overrides) -> TagConfig:
    base = dict(
        plc_id="PLC-01",
        tag_id="TEMP_01",
        opc_node_id="ns=2;s=Line1.PLC01.Temp",
        unit="celsius",
        data_type="float",
        min_alarm=None,
        max_alarm=80.0,
        clear_margin=2.0,
        deadband=0.1,
        severity="HIGH",
        sampling_interval_ms=5000,
        updated_version=1,
    )
    base.update(overrides)
    return TagConfig(**base)


def ts(seconds: int) -> datetime:
    return datetime(2026, 7, 13, 9, 0, seconds, tzinfo=timezone.utc)


def test_triggers_at_max_alarm():
    engine = AlarmEngine()
    tag = make_tag()

    event = engine.evaluate(tag, 80.0, ts(0), seq=1)

    assert event is not None
    assert event.severity == "HIGH"
    assert engine.current_state(tag.plc_id, tag.tag_id) == AlarmState.HIGH


def test_no_retrigger_while_still_above_max():
    engine = AlarmEngine()
    tag = make_tag()
    engine.evaluate(tag, 82.0, ts(0), seq=1)

    event = engine.evaluate(tag, 81.0, ts(1), seq=2)

    assert event is None
    assert engine.current_state(tag.plc_id, tag.tag_id) == AlarmState.HIGH


def test_hysteresis_prevents_flapping_between_threshold_and_clear_margin():
    """The bug this exists to prevent: without a clear_margin gap, a value
    oscillating at exactly max_alarm re-triggers on every sample."""
    engine = AlarmEngine()
    tag = make_tag(max_alarm=80.0, clear_margin=2.0)

    assert engine.evaluate(tag, 80.1, ts(0), seq=1) is not None  # trigger
    assert engine.evaluate(tag, 79.9, ts(1), seq=2) is None  # still HIGH, no re-trigger
    assert engine.current_state(tag.plc_id, tag.tag_id) == AlarmState.HIGH
    assert engine.evaluate(tag, 80.1, ts(2), seq=3) is None  # back above, still no re-trigger
    assert engine.current_state(tag.plc_id, tag.tag_id) == AlarmState.HIGH


def test_clears_only_after_crossing_clear_margin():
    engine = AlarmEngine()
    tag = make_tag(max_alarm=80.0, clear_margin=2.0)
    engine.evaluate(tag, 82.0, ts(0), seq=1)

    engine.evaluate(tag, 79.0, ts(1), seq=2)  # below max_alarm but not past clear_margin
    assert engine.current_state(tag.plc_id, tag.tag_id) == AlarmState.HIGH

    engine.evaluate(tag, 77.9, ts(2), seq=3)  # below 80 - 2 = 78
    assert engine.current_state(tag.plc_id, tag.tag_id) == AlarmState.NORMAL


def test_low_side_trigger_and_clear():
    engine = AlarmEngine()
    tag = make_tag(min_alarm=10.0, max_alarm=None, clear_margin=1.0)

    assert engine.evaluate(tag, 9.0, ts(0), seq=1) is not None
    assert engine.current_state(tag.plc_id, tag.tag_id) == AlarmState.LOW

    engine.evaluate(tag, 10.5, ts(1), seq=2)  # above min but not past clear_margin (11.0)
    assert engine.current_state(tag.plc_id, tag.tag_id) == AlarmState.LOW

    engine.evaluate(tag, 11.1, ts(2), seq=3)
    assert engine.current_state(tag.plc_id, tag.tag_id) == AlarmState.NORMAL


def test_alarm_id_is_deterministic_not_random():
    t = ts(10)
    id_a = build_alarm_id("PLC-01", "TEMP_01", t, seq=184213)
    id_b = build_alarm_id("PLC-01", "TEMP_01", t, seq=184213)

    assert id_a == id_b  # re-evaluating the same alarm after a crash must reproduce the same id
    assert id_a == "PLC-01:TEMP_01:20260713T090010.000Z:184213"


def test_alarm_id_differs_by_seq():
    t = ts(10)
    assert build_alarm_id("PLC-01", "TEMP_01", t, seq=1) != build_alarm_id(
        "PLC-01", "TEMP_01", t, seq=2
    )
