"""Tests for the Happy Hour active binary sensor."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from custom_components.engie_be.binary_sensor import (
    HAPPY_HOUR_ACTIVE_SENSOR_DESCRIPTION,
    EngieBeHappyHourActiveSensor,
)
from custom_components.engie_be.const import SUBENTRY_TYPE_BUSINESS_AGREEMENT

# Anchor inside the canonical 12:00-15:00 +02:00 happy-hour window.
_NOW_INSIDE = datetime(2026, 5, 23, 11, 30, tzinfo=UTC)
# Anchor before the window.
_NOW_BEFORE = datetime(2026, 5, 23, 7, 0, tzinfo=UTC)

_SCHEDULED = {
    "tomorrow": {
        "startTime": "2026-05-23T12:00:00+02:00",
        "endTime": "2026-05-23T15:00:00+02:00",
    },
}


def _make_subentry(subentry_id: str = "sub_test") -> MagicMock:
    """Build a MagicMock ``ConfigSubentry`` stub."""
    subentry = MagicMock()
    subentry.subentry_id = subentry_id
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {}
    return subentry


def _make_coordinator(data: object) -> MagicMock:
    """Build a MagicMock per-subentry coordinator stub."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = True
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


def _wrap(payload: object) -> dict:
    """Wrap a payload in the coordinator's happy-hour storage shape."""
    return {"happy_hour": {"data": payload}}


def _patched_now(when: datetime):  # noqa: ANN202
    """Patch ``dt_util.utcnow`` inside the binary_sensor module."""
    return patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=when,
    )


# ---------------------------------------------------------------------------
# Entity description metadata
# ---------------------------------------------------------------------------


def test_description_metadata() -> None:
    """Translation key and key are stable; no device class is required."""
    desc = HAPPY_HOUR_ACTIVE_SENSOR_DESCRIPTION
    assert desc.key == "happy_hour_active"
    assert desc.translation_key == "happy_hour_active"


def test_unique_id_is_subentry_scoped() -> None:
    """Unique IDs follow ``{entry_id}_{subentry_id}_happy_hour_active``."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    subentry = _make_subentry(subentry_id="sub_xyz")
    sensor = EngieBeHappyHourActiveSensor(coordinator, subentry)
    assert sensor.unique_id == "test_entry_id_sub_xyz_happy_hour_active"


def test_entity_id_uses_ban_when_present() -> None:
    """A subentry with a BAN gets the stable ``engie_belgium_<ban>_...`` slug."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    subentry = _make_subentry()
    subentry.data = {"business_agreement_number": "002208796420"}
    sensor = EngieBeHappyHourActiveSensor(coordinator, subentry)
    assert sensor.entity_id == (
        "binary_sensor.engie_belgium_002208796420_happy_hour_active"
    )


# ---------------------------------------------------------------------------
# is_on
# ---------------------------------------------------------------------------


def test_is_on_true_inside_window() -> None:
    """Now inside the scheduled window -> ``on``."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    with _patched_now(_NOW_INSIDE):
        assert sensor.is_on is True


def test_is_on_false_outside_window() -> None:
    """Now before the scheduled window -> ``off``."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    with _patched_now(_NOW_BEFORE):
        assert sensor.is_on is False


def test_is_on_false_when_no_event_scheduled() -> None:
    """Empty ``{}`` payload -> always ``off`` regardless of ``now``."""
    coordinator = _make_coordinator(_wrap({}))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    with _patched_now(_NOW_INSIDE):
        assert sensor.is_on is False


def test_is_on_false_when_no_data_yet() -> None:
    """No coordinator data yet -> ``off`` (sensor is always available)."""
    coordinator = _make_coordinator(None)
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    with _patched_now(_NOW_INSIDE):
        assert sensor.is_on is False


def test_is_on_returns_bool_not_none() -> None:
    """``is_on`` must always be a concrete bool (no ``unknown`` state)."""
    coordinator = _make_coordinator(None)
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    with _patched_now(_NOW_INSIDE):
        result = sensor.is_on
    assert isinstance(result, bool)
