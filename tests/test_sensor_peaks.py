"""Tests for capacity-tariff (captar) peak sensor entities."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.engie_be.const import SUBENTRY_TYPE_BUSINESS_AGREEMENT
from custom_components.engie_be.sensor import (
    EngieBeLatestDailyPeakSensor,
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


def _make_subentry(
    subentry_id: str = "sub_test",
    title: str = "Test Account",
) -> MagicMock:
    """Build a MagicMock ConfigSubentry stub."""
    subentry = MagicMock()
    subentry.subentry_id = subentry_id
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = title
    return subentry


def _make_coordinator(data: dict | None) -> MagicMock:
    """Build a MagicMock per-subentry coordinator stub with the given ``.data``."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


# ---------------------------------------------------------------------------
# _build_peak_sensors
# ---------------------------------------------------------------------------


def test_build_peak_sensors_creates_five_entities() -> None:
    """``_build_peak_sensors`` returns the four monthly + one daily captar sensors."""
    coordinator = _make_coordinator({"items": [], "peaks": _wrap(_peaks())})
    subentry = _make_subentry(subentry_id="sub_xyz")
    sensors = _build_peak_sensors(coordinator, subentry)

    keys = {s.entity_description.key for s in sensors}
    assert keys == {
        "captar_monthly_peak_power",
        "captar_monthly_peak_energy",
        "captar_monthly_peak_start",
        "captar_monthly_peak_end",
        "captar_latest_daily_peak",
    }
    # Every sensor must carry an entry+subentry-scoped unique_id.
    for sensor in sensors:
        assert sensor.unique_id == (
            f"test_entry_id_sub_xyz_{sensor.entity_description.key}"
        )


def test_build_peak_sensors_runs_without_peaks_payload() -> None:
    """Sensors are built even before the first peaks fetch arrives."""
    coordinator = _make_coordinator({"items": []})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    assert len(sensors) == 5


# ---------------------------------------------------------------------------
# Monthly value sensor
# ---------------------------------------------------------------------------


def test_monthly_peak_power_native_value() -> None:
    """``peakKW`` from ``peakOfTheMonth`` is returned as a float."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        subentry,
        sensors[0].entity_description,
        field="peakKW",
    )
    assert sensor.native_value == 3.5


def test_monthly_peak_energy_native_value() -> None:
    """``peakKWh`` from ``peakOfTheMonth`` is returned as a float."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        subentry,
        sensors[1].entity_description,
        field="peakKWh",
    )
    assert sensor.native_value == 0.875


def test_monthly_peak_value_returns_none_when_peaks_missing() -> None:
    """Missing peaks payload yields ``None`` rather than raising."""
    coordinator = _make_coordinator({"items": []})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        subentry,
        sensors[0].entity_description,
        field="peakKW",
    )
    assert sensor.native_value is None


def test_monthly_peak_value_returns_none_when_field_missing() -> None:
    """
    Missing or non-numeric peak fields yield ``None`` rather than raising.

    Exercises the three defensive branches that protect against ENGIE
    payload drift: the ``peakOfTheMonth`` dict is present but the
    requested field is absent, ``None``, or non-coercible (a list / a
    non-numeric string).
    """
    base = _wrap(_peaks())
    # 1. Field absent entirely -> None
    base["data"]["peakOfTheMonth"] = {"start": "2026-04-15T18:00:00+02:00"}
    coordinator = _make_coordinator({"peaks": base})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        subentry,
        sensors[0].entity_description,
        field="peakKW",
    )
    assert sensor.native_value is None

    # 2. Field present but non-coercible to float (TypeError on float([...]))
    base["data"]["peakOfTheMonth"] = {"peakKW": [1, 2, 3]}
    coordinator.data = {"peaks": base}
    assert sensor.native_value is None

    # 3. Field present but unparseable string (ValueError on float("abc"))
    base["data"]["peakOfTheMonth"] = {"peakKW": "abc"}
    coordinator.data = {"peaks": base}
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Monthly timestamp sensor
# ---------------------------------------------------------------------------


def test_monthly_peak_timestamp_parses_iso8601_with_offset() -> None:
    """ISO 8601 timestamps with timezone offsets are parsed to ``datetime``."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = EngieBeMonthlyPeakTimestampSensor(
        coordinator,
        subentry,
        sensors[2].entity_description,
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
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = EngieBeMonthlyPeakTimestampSensor(
        coordinator,
        subentry,
        sensors[2].entity_description,
        field="start",
    )
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Latest daily peak sensor
# ---------------------------------------------------------------------------


def test_latest_daily_peak_native_value() -> None:
    """``peakKW`` of the last ``dailyPeaks`` entry is returned as a float."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = next(s for s in sensors if isinstance(s, EngieBeLatestDailyPeakSensor))
    assert sensor.native_value == 1.8


def test_latest_daily_peak_native_value_returns_none_when_empty() -> None:
    """An empty ``dailyPeaks`` array yields ``None`` rather than raising."""
    base = _wrap(_peaks())
    base["data"]["dailyPeaks"] = []
    coordinator = _make_coordinator({"peaks": base})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = next(s for s in sensors if isinstance(s, EngieBeLatestDailyPeakSensor))
    assert sensor.native_value is None


def test_latest_daily_peak_native_value_returns_none_when_key_missing() -> None:
    """A missing ``dailyPeaks`` key yields ``None`` rather than raising."""
    base = _wrap(_peaks())
    del base["data"]["dailyPeaks"]
    coordinator = _make_coordinator({"peaks": base})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = next(s for s in sensors if isinstance(s, EngieBeLatestDailyPeakSensor))
    assert sensor.native_value is None


def test_latest_daily_peak_native_value_returns_none_for_non_numeric_peak_kw() -> None:
    """A non-coercible ``peakKW`` yields ``None`` rather than raising."""
    base = _wrap(_peaks())
    base["data"]["dailyPeaks"] = [{"peakKW": "not-a-number"}]
    coordinator = _make_coordinator({"peaks": base})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = next(s for s in sensors if isinstance(s, EngieBeLatestDailyPeakSensor))
    assert sensor.native_value is None


def test_latest_daily_peak_extra_state_attributes() -> None:
    """Extra attributes expose the latest daily peak's detail and full array."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = next(s for s in sensors if isinstance(s, EngieBeLatestDailyPeakSensor))
    attrs = sensor.extra_state_attributes
    assert attrs["peak_date"] == "2026-04-16"
    assert attrs["peak_kwh"] == "0.45000000"
    assert attrs["peak_start"] == "2026-04-16T07:15:00+02:00"
    assert attrs["peak_end"] == "2026-04-16T07:30:00+02:00"
    assert attrs["daily_peaks"] == _peaks()["dailyPeaks"]
    # Base peak-sensor attributes are still present.
    assert attrs["peak_month"] == "2026-04"


def test_latest_daily_peak_extra_state_attributes_without_daily_peaks() -> None:
    """Without daily peaks the daily-specific attributes are omitted."""
    coordinator = _make_coordinator({"items": []})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = next(s for s in sensors if isinstance(s, EngieBeLatestDailyPeakSensor))
    attrs = sensor.extra_state_attributes
    assert "peak_date" not in attrs
    assert "peak_kwh" not in attrs
    assert "peak_start" not in attrs
    assert "peak_end" not in attrs
    assert "daily_peaks" not in attrs


# ---------------------------------------------------------------------------
# Extra state attributes
# ---------------------------------------------------------------------------


def test_extra_state_attributes_omits_last_fetched_even_when_set() -> None:
    """``last_fetched`` is never exposed, even with a fetch timestamp set."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    coordinator.last_successful_fetch = datetime.fromisoformat(
        "2026-04-15T19:00:00+00:00",
    ) - timedelta(hours=1)
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        subentry,
        sensors[0].entity_description,
        field="peakKW",
    )
    attrs = sensor.extra_state_attributes
    assert "last_fetched" not in attrs


def test_extra_state_attributes_omits_last_fetched_when_unset() -> None:
    """Without a fetch timestamp ``last_fetched`` is still absent, peak meta stays."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        subentry,
        sensors[0].entity_description,
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
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        subentry,
        sensors[0].entity_description,
        field="peakKW",
    )
    attrs = sensor.extra_state_attributes
    assert attrs.get("peak_month") == "2026-04"
    assert attrs.get("peak_is_fallback") is True


def test_extra_state_attributes_omits_peak_meta_when_no_wrapper() -> None:
    """Without a peaks wrapper the peak metadata attributes are omitted."""
    coordinator = _make_coordinator({"items": []})
    subentry = _make_subentry()
    sensors = _build_peak_sensors(coordinator, subentry)
    sensor = EngieBeMonthlyPeakValueSensor(
        coordinator,
        subentry,
        sensors[0].entity_description,
        field="peakKW",
    )
    attrs = sensor.extra_state_attributes
    assert "peak_month" not in attrs
    assert "peak_is_fallback" not in attrs
