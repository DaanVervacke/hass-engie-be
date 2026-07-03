"""Tests for Happy Hours month-report sensor entities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CURRENCY_EURO, UnitOfEnergy

from custom_components.engie_be.const import SUBENTRY_TYPE_BUSINESS_AGREEMENT
from custom_components.engie_be.sensor import (
    _HAPPY_HOUR_MONTH_CONSUMPTION,
    EngieBeHappyHourMonthSensor,
    _build_happy_hour_month_report_sensors,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "happy_hour_month_report.json"


def _report() -> dict:
    """Return a fresh copy of the month-report fixture."""
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _wrap(payload: Any, *, is_fallback: bool = False) -> dict:
    """Wrap a payload in the coordinator's month-report storage shape."""
    return {
        "happy_hour_month_report": {
            "data": payload,
            "year": 2026,
            "month": 7,
            "is_fallback": is_fallback,
        }
    }


def _make_subentry(subentry_id: str = "sub_test") -> MagicMock:
    """Build a MagicMock ConfigSubentry stub."""
    subentry = MagicMock()
    subentry.subentry_id = subentry_id
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {}
    return subentry


def _make_coordinator(data: object) -> MagicMock:
    """Build a MagicMock per-subentry coordinator with the given ``.data``."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


# ---------------------------------------------------------------------------
# _build_happy_hour_month_report_sensors
# ---------------------------------------------------------------------------


def test_build_creates_three_sensors() -> None:
    """The factory always returns the three month-report sensors."""
    coordinator = _make_coordinator(_wrap(_report()))
    subentry = _make_subentry(subentry_id="sub_abc")
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)

    keys = {s.entity_description.key for s in sensors}
    assert keys == {
        "happy_hours_month_consumption",
        "happy_hours_month_eligible_hours",
        "happy_hours_month_reward",
    }
    for sensor in sensors:
        assert sensor.unique_id == (
            f"test_entry_id_sub_abc_{sensor.entity_description.key}"
        )


def test_build_runs_without_month_report_payload() -> None:
    """Sensors are built even when the coordinator has no month-report data yet."""
    coordinator = _make_coordinator({"items": []})
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    assert len(sensors) == 3


# ---------------------------------------------------------------------------
# native_value
# ---------------------------------------------------------------------------


def test_consumption_sensor_returns_kwh_value() -> None:
    """The consumption sensor exposes consumptionKWh from the fixture."""
    coordinator = _make_coordinator(_wrap(_report()))
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    sensor = next(
        s
        for s in sensors
        if s.entity_description.key == "happy_hours_month_consumption"
    )
    assert sensor.native_value == 12.345
    assert (
        sensor.entity_description.native_unit_of_measurement
        == UnitOfEnergy.KILO_WATT_HOUR
    )
    assert sensor.entity_description.device_class == SensorDeviceClass.ENERGY


def test_eligible_hours_sensor_returns_count() -> None:
    """The eligible-hours sensor exposes numberOfEligibleHappyHours."""
    coordinator = _make_coordinator(_wrap(_report()))
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    sensor = next(
        s
        for s in sensors
        if s.entity_description.key == "happy_hours_month_eligible_hours"
    )
    assert sensor.native_value == 3.0
    assert sensor.entity_description.native_unit_of_measurement is None


def test_reward_sensor_returns_euros() -> None:
    """The reward sensor exposes rewardEuros in EUR."""
    coordinator = _make_coordinator(_wrap(_report()))
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    sensor = next(
        s for s in sensors if s.entity_description.key == "happy_hours_month_reward"
    )
    assert sensor.native_value == 4.56
    assert sensor.entity_description.native_unit_of_measurement == CURRENCY_EURO
    assert sensor.entity_description.device_class == SensorDeviceClass.MONETARY


def test_native_value_is_none_when_no_data_yet() -> None:
    """All sensors return None before the first coordinator refresh."""
    coordinator = _make_coordinator(None)
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    for sensor in sensors:
        assert sensor.native_value is None


def test_native_value_is_none_when_wrapper_missing() -> None:
    """Sensors return None when the wrapper key is absent from coordinator data."""
    coordinator = _make_coordinator({"items": []})
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    for sensor in sensors:
        assert sensor.native_value is None


def test_native_value_is_none_when_inner_data_is_none() -> None:
    """Sensors return None when the wrapper data field is None."""
    coordinator = _make_coordinator(
        {"happy_hour_month_report": {"data": None, "year": 2026, "month": 7}}
    )
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    for sensor in sensors:
        assert sensor.native_value is None


def test_native_value_is_none_when_path_missing() -> None:
    """Sensors return None when the expected JSON path is absent."""
    coordinator = _make_coordinator(_wrap({"month": {}}))
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    for sensor in sensors:
        assert sensor.native_value is None


def test_native_value_is_none_for_nonnumeric_value() -> None:
    """Non-numeric leaf values do not raise — they return None."""
    bad = {
        "month": {
            "happyHour": {
                "consumptionKWh": "not-a-number",
                "numberOfEligibleHappyHours": None,
                "rewardEuros": {},
            }
        }
    }
    coordinator = _make_coordinator(_wrap(bad))
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    for sensor in sensors:
        assert sensor.native_value is None


# ---------------------------------------------------------------------------
# extra_state_attributes
# ---------------------------------------------------------------------------


def test_reward_sensor_exposes_is_calculation_ongoing_false() -> None:
    """The reward sensor exposes isCalculationOngoing as an attribute."""
    coordinator = _make_coordinator(_wrap(_report()))
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    sensor = next(
        s for s in sensors if s.entity_description.key == "happy_hours_month_reward"
    )
    attrs = sensor.extra_state_attributes
    assert "is_calculation_ongoing" in attrs
    assert attrs["is_calculation_ongoing"] is False


def test_reward_sensor_is_calculation_ongoing_true() -> None:
    """The reward sensor reports True when the calculation is still running."""
    payload = _report()
    payload["month"]["happyHour"]["isCalculationOngoing"] = True
    coordinator = _make_coordinator(_wrap(payload))
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    sensor = next(
        s for s in sensors if s.entity_description.key == "happy_hours_month_reward"
    )
    assert sensor.extra_state_attributes["is_calculation_ongoing"] is True


def test_consumption_sensor_has_no_is_calculation_ongoing_attr() -> None:
    """Non-reward sensors do not expose is_calculation_ongoing."""
    coordinator = _make_coordinator(_wrap(_report()))
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    sensor = next(
        s
        for s in sensors
        if s.entity_description.key == "happy_hours_month_consumption"
    )
    assert "is_calculation_ongoing" not in sensor.extra_state_attributes


def test_reward_sensor_attrs_empty_when_no_payload() -> None:
    """When no payload is available, the reward sensor emits no attributes."""
    coordinator = _make_coordinator(None)
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    sensor = next(
        s for s in sensors if s.entity_description.key == "happy_hours_month_reward"
    )
    assert sensor.extra_state_attributes == {}


def test_all_sensors_expose_report_month_and_is_fallback() -> None:
    """All three sensors expose report_month and report_is_fallback attributes."""
    coordinator = _make_coordinator(_wrap(_report(), is_fallback=False))
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    for sensor in sensors:
        attrs = sensor.extra_state_attributes
        assert attrs.get("report_month") == "2026-07"
        assert attrs.get("report_is_fallback") is False


def test_all_sensors_expose_is_fallback_true_when_stale() -> None:
    """When the wrapper is marked as fallback, sensors report it as True."""
    coordinator = _make_coordinator(_wrap(_report(), is_fallback=True))
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    for sensor in sensors:
        assert sensor.extra_state_attributes.get("report_is_fallback") is True


def test_sensors_omit_report_month_when_wrapper_absent() -> None:
    """Sensors emit no report_month/report_is_fallback when no wrapper is present."""
    coordinator = _make_coordinator({"items": []})
    subentry = _make_subentry()
    sensors = _build_happy_hour_month_report_sensors(coordinator, subentry)
    for sensor in sensors:
        attrs = sensor.extra_state_attributes
        assert "report_month" not in attrs
        assert "report_is_fallback" not in attrs


# ---------------------------------------------------------------------------
# entity_id slug
# ---------------------------------------------------------------------------


def test_entity_id_uses_ban_when_present() -> None:
    """When the subentry carries a BAN, the entity_id slug uses it."""
    coordinator = _make_coordinator(_wrap(_report()))
    subentry = _make_subentry()
    subentry.data = {"business_agreement_number": "002208796420"}
    sensor = EngieBeHappyHourMonthSensor(
        coordinator,
        subentry,
        _HAPPY_HOUR_MONTH_CONSUMPTION,
        path=("month", "happyHour", "consumptionKWh"),
    )
    assert sensor.entity_id == (
        "sensor.engie_belgium_002208796420_happy_hours_month_consumption"
    )
