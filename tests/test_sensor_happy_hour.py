"""Tests for the Happy Hour timestamp sensors."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.engie_be.const import SUBENTRY_TYPE_BUSINESS_AGREEMENT
from custom_components.engie_be.sensor import (
    EngieBeHappyHourTimestampSensor,
    _build_happy_hour_sensors,
)

_SCHEDULED = {
    "tomorrow": {
        "startTime": "2026-05-23T12:00:00+02:00",
        "endTime": "2026-05-23T15:00:00+02:00",
    },
}

# The same window re-published under the ``today`` key once midnight passes.
_TODAY_SCHEDULED = {
    "today": {
        "startTime": "2026-06-07T11:00:00+02:00",
        "endTime": "2026-06-07T17:00:00+02:00",
    },
}


def _make_subentry(subentry_id: str = "sub_test") -> MagicMock:
    """Build a MagicMock ``ConfigSubentry`` stub."""
    subentry = MagicMock()
    subentry.subentry_id = subentry_id
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    # Default ``.data`` to an empty dict so the BAN lookup yields no
    # entity_id override (mirrors how the integration handles a
    # subentry that hasn't been backfilled yet).
    subentry.data = {}
    return subentry


def _make_coordinator(data: object) -> MagicMock:
    """Build a MagicMock per-subentry coordinator with the given ``.data``."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


def _wrap(payload: object) -> dict:
    """Wrap a payload in the coordinator's happy-hour storage shape."""
    return {"happy_hour": {"data": payload}}


# ---------------------------------------------------------------------------
# _build_happy_hour_sensors
# ---------------------------------------------------------------------------


def test_build_creates_two_timestamp_sensors() -> None:
    """The factory always returns the start + end timestamp pair."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    subentry = _make_subentry(subentry_id="sub_abc")
    sensors = _build_happy_hour_sensors(coordinator, subentry)

    keys = {s.entity_description.key for s in sensors}
    assert keys == {"happy_hour_next_start", "happy_hour_next_end"}
    # Subentry-scoped unique IDs follow the standard convention.
    for sensor in sensors:
        assert sensor.unique_id == (
            f"test_entry_id_sub_abc_{sensor.entity_description.key}"
        )
        assert sensor.device_class == SensorDeviceClass.TIMESTAMP


def test_build_runs_without_happy_hour_payload() -> None:
    """Sensors are built even when the coordinator has no happy-hour data yet."""
    coordinator = _make_coordinator({"items": []})
    subentry = _make_subentry()
    sensors = _build_happy_hour_sensors(coordinator, subentry)
    assert len(sensors) == 2


# ---------------------------------------------------------------------------
# native_value
# ---------------------------------------------------------------------------


def test_start_sensor_returns_window_start() -> None:
    """The ``start`` field returns the parsed window start datetime."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    subentry = _make_subentry()
    sensors = _build_happy_hour_sensors(coordinator, subentry)
    start_sensor = next(
        s for s in sensors if s.entity_description.key == "happy_hour_next_start"
    )
    value = start_sensor.native_value
    assert isinstance(value, datetime)
    assert value == datetime.fromisoformat("2026-05-23T12:00:00+02:00")


def test_end_sensor_returns_window_end() -> None:
    """The ``end`` field returns the parsed window end datetime."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    subentry = _make_subentry()
    sensors = _build_happy_hour_sensors(coordinator, subentry)
    end_sensor = next(
        s for s in sensors if s.entity_description.key == "happy_hour_next_end"
    )
    value = end_sensor.native_value
    assert isinstance(value, datetime)
    assert value == datetime.fromisoformat("2026-05-23T15:00:00+02:00")


def test_native_value_uses_today_key_after_midnight() -> None:
    """A ``today`` key payload populates both timestamp sensors (regression)."""
    coordinator = _make_coordinator(_wrap(_TODAY_SCHEDULED))
    subentry = _make_subentry()
    sensors = _build_happy_hour_sensors(coordinator, subentry)
    values = {s.entity_description.key: s.native_value for s in sensors}
    assert values["happy_hour_next_start"] == datetime.fromisoformat(
        "2026-06-07T11:00:00+02:00"
    )
    assert values["happy_hour_next_end"] == datetime.fromisoformat(
        "2026-06-07T17:00:00+02:00"
    )


def test_native_value_is_none_when_no_event_scheduled() -> None:
    """Empty ``{}`` payload -> both sensors report ``None`` (unknown)."""
    coordinator = _make_coordinator(_wrap({}))
    subentry = _make_subentry()
    sensors = _build_happy_hour_sensors(coordinator, subentry)
    for sensor in sensors:
        assert sensor.native_value is None


def test_native_value_is_none_when_no_payload_yet() -> None:
    """Before the first poll, both sensors report ``None``."""
    coordinator = _make_coordinator(None)
    subentry = _make_subentry()
    sensors = _build_happy_hour_sensors(coordinator, subentry)
    for sensor in sensors:
        assert sensor.native_value is None


def test_native_value_is_none_when_timestamps_malformed() -> None:
    """Unparseable timestamps -> ``None`` (no exception leaks)."""
    bad = {"tomorrow": {"startTime": "garbage", "endTime": "garbage"}}
    coordinator = _make_coordinator(_wrap(bad))
    subentry = _make_subentry()
    sensors = _build_happy_hour_sensors(coordinator, subentry)
    for sensor in sensors:
        assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Entity ID slug
# ---------------------------------------------------------------------------


def test_entity_id_uses_ban_when_present() -> None:
    """When the subentry carries a BAN, the entity_id slug uses it."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    subentry = _make_subentry()
    subentry.data = {"business_agreement_number": "002208796420"}
    sensor = EngieBeHappyHourTimestampSensor(
        coordinator,
        subentry,
        next(
            s
            for s in _build_happy_hour_sensors(coordinator, subentry)
            if s.entity_description.key == "happy_hour_next_start"
        ).entity_description,
        field="start",
    )
    assert sensor.entity_id == (
        "sensor.engie_belgium_002208796420_happy_hour_next_start"
    )
