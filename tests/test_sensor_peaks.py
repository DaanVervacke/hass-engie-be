"""Tests for capacity-tariff (captar) peak sensor entities."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.engie_be.sensor import (
    EngieBeMonthlyPeakTimestampSensor,
    EngieBeMonthlyPeakValueSensor,
    _build_peak_sensors,
)

_PEAKS_FIXTURE = Path(__file__).parent / "fixtures" / "peaks_2026_04.json"


def _peaks() -> dict:
    """Return the parsed peaks fixture as a fresh dict."""
    return json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))


def _wrap(
    peaks: dict,
    *,
    year: int = 2026,
    month: int = 4,
    is_fallback: bool = False,
) -> dict:
    """Return the coordinator wrapper dict expected by sensor helpers."""
    return {
        "data": peaks,
        "year": year,
        "month": month,
        "is_fallback": is_fallback,
    }


def _make_coordinator(data: dict | None) -> MagicMock:
    """Build a MagicMock coordinator stub with the given ``.data``."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


# ---------------------------------------------------------------------------
# _build_peak_sensors
# ---------------------------------------------------------------------------


def test_build_peak_sensors_creates_four_entities() -> None:
    """``_build_peak_sensors`` always returns the four monthly captar sensors."""
    coordinator = _make_coordinator({"items": [], "peaks": _wrap(_peaks())})
    sensors = _build_peak_sensors(coordinator)

    keys = {s.entity_description.key for s in sensors}
    assert keys == {
        "captar_monthly_peak_power",
        "captar_monthly_peak_energy",
        "captar_monthly_peak_start",
        "captar_monthly_peak_end",
    }
    # Every sensor must carry a unique_id namespaced under the entry.
    for sensor in sensors:
        assert sensor.unique_id == (f"test_entry_id_{sensor.entity_description.key}")


def test_build_peak_sensors_runs_without_peaks_payload() -> None:
    """Sensors are built even before the first peaks fetch arrives."""
    coordinator = _make_coordinator({"items": []})
    sensors = _build_peak_sensors(coordinator)
    assert len(sensors) == 4


# ---------------------------------------------------------------------------
# Monthly value sensor
# ---------------------------------------------------------------------------


def test_monthly_peak_power_native_value() -> None:
    """``peakKW`` from ``peakOfTheMonth`` is returned as a float."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        _build_peak_sensors(coordinator)[0].entity_description,
        field="peakKW",
    )
    assert sensor.native_value == 3.5


def test_monthly_peak_energy_native_value() -> None:
    """``peakKWh`` from ``peakOfTheMonth`` is returned as a float."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        _build_peak_sensors(coordinator)[1].entity_description,
        field="peakKWh",
    )
    assert sensor.native_value == 0.875


def test_monthly_peak_value_returns_none_when_peaks_missing() -> None:
    """Missing peaks payload yields ``None`` rather than raising."""
    coordinator = _make_coordinator({"items": []})
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        _build_peak_sensors(coordinator)[0].entity_description,
        field="peakKW",
    )
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Monthly timestamp sensor
# ---------------------------------------------------------------------------


def test_monthly_peak_timestamp_parses_iso8601_with_offset() -> None:
    """ISO 8601 timestamps with timezone offsets are parsed to ``datetime``."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    sensor = EngieBeMonthlyPeakTimestampSensor(
        coordinator,
        _build_peak_sensors(coordinator)[2].entity_description,
        field="start",
    )
    value = sensor.native_value
    assert isinstance(value, datetime)
    assert value.tzinfo is not None
    assert value == datetime.fromisoformat("2026-04-15T18:00:00+02:00")
    # Sanity: device class is timestamp.
    assert sensor.device_class == SensorDeviceClass.TIMESTAMP


def test_monthly_peak_timestamp_returns_none_when_field_missing() -> None:
    """A missing timestamp field returns ``None``."""
    coordinator = _make_coordinator(
        {"peaks": _wrap({"peakOfTheMonth": {"peakKW": "1.0"}})},
    )
    sensor = EngieBeMonthlyPeakTimestampSensor(
        coordinator,
        _build_peak_sensors(coordinator)[2].entity_description,
        field="start",
    )
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Extra state attributes
# ---------------------------------------------------------------------------


def test_extra_state_attributes_includes_last_fetched_when_set() -> None:
    """``last_fetched`` is included when the coordinator has a fetch timestamp."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    coordinator.last_successful_fetch = datetime.fromisoformat(
        "2026-04-15T19:00:00+00:00",
    ) - timedelta(hours=1)
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        _build_peak_sensors(coordinator)[0].entity_description,
        field="peakKW",
    )
    attrs = sensor.extra_state_attributes
    assert "last_fetched" in attrs


def test_extra_state_attributes_omits_last_fetched_when_unset() -> None:
    """Without a fetch timestamp ``last_fetched`` is omitted but peak meta stays."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        _build_peak_sensors(coordinator)[0].entity_description,
        field="peakKW",
    )
    attrs = sensor.extra_state_attributes
    assert "last_fetched" not in attrs
    # peak metadata is still present from the wrapper
    assert attrs.get("peak_month") == "2026-04"
    assert attrs.get("peak_is_fallback") is False


def test_extra_state_attributes_includes_peak_month_metadata() -> None:
    """Peak month + fallback flag are surfaced from the coordinator wrapper."""
    coordinator = _make_coordinator(
        {"peaks": _wrap(_peaks(), year=2026, month=4, is_fallback=True)},
    )
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        _build_peak_sensors(coordinator)[0].entity_description,
        field="peakKW",
    )
    attrs = sensor.extra_state_attributes
    assert attrs.get("peak_month") == "2026-04"
    assert attrs.get("peak_is_fallback") is True


def test_extra_state_attributes_omits_peak_meta_when_no_wrapper() -> None:
    """Without a peaks wrapper the peak metadata attributes are omitted."""
    coordinator = _make_coordinator({"items": []})
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        _build_peak_sensors(coordinator)[0].entity_description,
        field="peakKW",
    )
    attrs = sensor.extra_state_attributes
    assert "peak_month" not in attrs
    assert "peak_is_fallback" not in attrs
