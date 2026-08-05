from collector.opc_client import _DeadbandGate


def test_first_value_always_forwarded():
    gate = _DeadbandGate()
    assert gate.should_forward("PLC-01", "TEMP_01", 50.0, deadband=1.0) is True


def test_small_change_within_deadband_is_suppressed():
    gate = _DeadbandGate()
    gate.should_forward("PLC-01", "TEMP_01", 50.0, deadband=1.0)

    assert gate.should_forward("PLC-01", "TEMP_01", 50.5, deadband=1.0) is False


def test_change_at_or_beyond_deadband_is_forwarded():
    gate = _DeadbandGate()
    gate.should_forward("PLC-01", "TEMP_01", 50.0, deadband=1.0)

    assert gate.should_forward("PLC-01", "TEMP_01", 51.0, deadband=1.0) is True


def test_zero_deadband_forwards_every_value():
    gate = _DeadbandGate()
    gate.should_forward("PLC-01", "TEMP_01", 50.0, deadband=0)

    assert gate.should_forward("PLC-01", "TEMP_01", 50.0001, deadband=0) is True


def test_tags_are_tracked_independently():
    gate = _DeadbandGate()
    gate.should_forward("PLC-01", "TEMP_01", 50.0, deadband=1.0)

    # A different tag's first value must not be suppressed by TEMP_01's state.
    assert gate.should_forward("PLC-01", "TEMP_02", 50.05, deadband=1.0) is True


def test_same_tag_id_on_different_plcs_does_not_cross_suppress():
    """Regression for the cross-PLC deadband collision.

    Every PLC in the fleet carries the same tag_id set (TEMP_01, ...), and one
    _DeadbandGate instance is shared by every PLC's subscription handler. Keying
    on tag_id alone made PLC-02's first reading look like a sub-deadband change
    of PLC-01's value, silently dropping it.
    """
    gate = _DeadbandGate()
    assert gate.should_forward("PLC-01", "TEMP_01", 50.0, deadband=1.0) is True

    # PLC-02 has never been seen: its first value must be forwarded even though
    # it is within the deadband of PLC-01's last value.
    assert gate.should_forward("PLC-02", "TEMP_01", 50.05, deadband=1.0) is True


def test_deadband_still_applies_per_plc_after_cross_plc_traffic():
    gate = _DeadbandGate()
    gate.should_forward("PLC-01", "TEMP_01", 50.0, deadband=1.0)
    gate.should_forward("PLC-02", "TEMP_01", 80.0, deadband=1.0)

    # Each PLC is compared against its own last value, not the other's.
    assert gate.should_forward("PLC-01", "TEMP_01", 50.5, deadband=1.0) is False
    assert gate.should_forward("PLC-02", "TEMP_01", 80.5, deadband=1.0) is False
    assert gate.should_forward("PLC-01", "TEMP_01", 51.0, deadband=1.0) is True
    assert gate.should_forward("PLC-02", "TEMP_01", 81.0, deadband=1.0) is True
