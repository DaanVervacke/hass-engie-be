"""Tests for the solar-surplus forecast sensor entity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from custom_components.engie_be.const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.sensor import (
    EngieBeSolarSurplusCurrentSensor,
    EngieBeSolarSurplusNextHourSensor,
    EngieBeSolarSurplusSensor,
    EngieBeSolarSurplusTodayPeakSensor,
    EngieBeSolarSurplusTodayTotalSensor,
    _build_solar_surplus_sensors,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory

pytestmark = pytest.mark.solar_surplus

_FIXTURES = Path(__file__).parent / "fixtures"
_SOLAR_HIGH = _FIXTURES / "solar_surplus_high.json"
_SOLAR_NO_DATA = _FIXTURES / "solar_surplus_no_data.json"

_EAN = "541448820070414088"


def _load(path: Path) -> dict:
    """Return a fresh copy of a JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def _wrap(per_ean: dict[str, Any]) -> dict:
    """Wrap a per-EAN forecasts dict in the coordinator storage shape."""
    return {
        "solar_surplus": {
            "data": per_ean,
            "fetched_at": "2026-07-08T10:00:00+00:00",
        }
    }


def _make_subentry() -> MagicMock:
    """Build a MagicMock ConfigSubentry."""
    subentry = MagicMock()
    subentry.subentry_id = "sub_abc"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {CONF_BUSINESS_AGREEMENT_NUMBER: "000000000000"}
    return subentry


def _make_coordinator(data: object) -> MagicMock:
    """Build a MagicMock coordinator with the given ``.data``."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry"
    return coordinator


def _sensor(data: object, ean: str = _EAN) -> EngieBeSolarSurplusSensor:
    """Build the sensor under test with the given coordinator data."""
    return EngieBeSolarSurplusSensor(_make_coordinator(data), _make_subentry(), ean)


# ---------------------------------------------------------------------------
# _build_solar_surplus_sensors
# ---------------------------------------------------------------------------


def test_build_creates_full_sensor_set_per_electricity_ean() -> None:
    """One EAN yields the level sensor plus four numeric sensors."""
    coord = _make_coordinator(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    subentry = _make_subentry()
    sensors = _build_solar_surplus_sensors(
        coord,
        subentry,
        {_EAN: "ELECTRICITY", "54ZZ": "GAS"},
    )
    unique_ids = {s.unique_id for s in sensors}
    assert unique_ids == {
        f"test_entry_sub_abc_{_EAN}_solar_surplus",
        f"test_entry_sub_abc_{_EAN}_solar_surplus_current",
        f"test_entry_sub_abc_{_EAN}_solar_surplus_next_hour",
        f"test_entry_sub_abc_{_EAN}_solar_surplus_today_total",
        f"test_entry_sub_abc_{_EAN}_solar_surplus_today_peak",
    }


def test_build_returns_empty_for_gas_only() -> None:
    """No electricity service points means no sensors are built."""
    coord = _make_coordinator(_wrap({}))
    sensors = _build_solar_surplus_sensors(
        coord,
        _make_subentry(),
        {"54ZZ": "GAS"},
    )
    assert sensors == []


# ---------------------------------------------------------------------------
# native_value
# ---------------------------------------------------------------------------


def test_native_value_matches_todays_forecast_date(
    freezer: FrozenDateTimeFactory,
) -> None:
    """When today's ``forecastDate`` is present the entry's level is returned."""
    freezer.move_to("2026-07-08T12:00:00+02:00")
    sensor = _sensor(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    assert sensor.native_value == "high_surplus"


def test_native_value_falls_back_to_first_day_when_today_missing(
    freezer: FrozenDateTimeFactory,
) -> None:
    """A date the payload does not carry falls back to the first entry."""
    freezer.move_to("2027-01-01T12:00:00+01:00")
    sensor = _sensor(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    assert sensor.native_value == "high_surplus"


def test_native_value_no_data_level_is_returned() -> None:
    """``NO_DATA`` is a valid enum value and surfaces as ``no_data``."""
    sensor = _sensor(_wrap({_EAN: _load(_SOLAR_NO_DATA)["forecasts"]}))
    assert sensor.native_value == "no_data"


def test_native_value_none_when_no_wrapper() -> None:
    """A coordinator without a ``solar_surplus`` key yields None."""
    sensor = _sensor({})
    assert sensor.native_value is None


def test_native_value_none_when_ean_absent_from_payload() -> None:
    """Data for a different EAN does not leak into this sensor."""
    sensor = _sensor(_wrap({"other": _load(_SOLAR_HIGH)["forecasts"]}))
    assert sensor.native_value is None


def test_native_value_none_when_forecasts_empty() -> None:
    """An empty forecasts list yields None."""
    sensor = _sensor(_wrap({_EAN: []}))
    assert sensor.native_value is None


def test_native_value_none_for_unknown_level() -> None:
    """Levels not in the enum are dropped so unknown data stays unknown."""
    payload = [
        {
            "forecastDate": "2026-07-08",
            "level": "MYSTERY",
            "details": [],
        }
    ]
    sensor = _sensor(_wrap({_EAN: payload}))
    assert sensor.native_value is None


def test_native_value_ignores_non_dict_days() -> None:
    """Malformed day entries do not raise and fall back to a valid one."""
    payload = [
        "not a dict",
        {"forecastDate": "2026-07-08", "level": "LOW_SURPLUS", "details": []},
    ]
    sensor = _sensor(_wrap({_EAN: payload}))
    assert sensor.native_value == "low_surplus"


# ---------------------------------------------------------------------------
# extra_state_attributes
# ---------------------------------------------------------------------------


def test_extra_state_attributes_flattens_all_days() -> None:
    """The attribute flattens every slot across the returned days."""
    sensor = _sensor(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    attrs = sensor.extra_state_attributes
    assert attrs["ean"] == _EAN
    assert len(attrs["forecast"]) == 4  # 3 from day 1 + 1 from day 2
    first = attrs["forecast"][0]
    assert first == {
        "start": "2026-07-08T06:00:00+02:00",
        "value": 0.2,
        "level": "low_surplus",
    }
    assert attrs["forecast"][-1]["level"] == "low_surplus"


def test_extra_state_attributes_empty_when_no_forecast() -> None:
    """No forecasts → empty attribute dict (no ``ean``/``forecast`` keys)."""
    sensor = _sensor({})
    assert sensor.extra_state_attributes == {}


def test_extra_state_attributes_skips_malformed_slots() -> None:
    """Slots with non-string levels are silently skipped."""
    payload = [
        {
            "forecastDate": "2026-07-08",
            "level": "LOW_SURPLUS",
            "details": [
                {"startTime": "2026-07-08T06:00:00+02:00", "value": 0.1, "level": None},
                "not a dict",
                {
                    "startTime": "2026-07-08T07:00:00+02:00",
                    "value": 0.2,
                    "level": "HIGH_SURPLUS",
                },
            ],
        }
    ]
    sensor = _sensor(_wrap({_EAN: payload}))
    attrs = sensor.extra_state_attributes
    assert len(attrs["forecast"]) == 1
    assert attrs["forecast"][0]["level"] == "high_surplus"


def test_entity_id_carries_ban_and_ean() -> None:
    """The entity_id includes the BAN and EAN for a stable slug."""
    sensor = _sensor(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    assert sensor.entity_id == (
        f"sensor.engie_belgium_000000000000_{_EAN}_solar_surplus_forecast"
    )


# ---------------------------------------------------------------------------
# Numeric sensors: current / next / today total / today peak
# ---------------------------------------------------------------------------


def _numeric_forecast() -> list[dict[str, Any]]:
    """Return a small hand-authored 2-day hourly forecast for numeric tests."""
    return [
        {
            "forecastDate": "2026-07-08",
            "level": "HIGH_SURPLUS",
            "details": [
                {
                    "startTime": "2026-07-08T10:00:00+02:00",
                    "value": 1.5,
                    "level": "LOW_SURPLUS",
                },
                {
                    "startTime": "2026-07-08T11:00:00+02:00",
                    "value": 3.2,
                    "level": "HIGH_SURPLUS",
                },
                {
                    "startTime": "2026-07-08T12:00:00+02:00",
                    "value": 0.4,
                    "level": "MINIMAL_SURPLUS",
                },
            ],
        },
        {
            "forecastDate": "2026-07-09",
            "level": "LOW_SURPLUS",
            "details": [
                {
                    "startTime": "2026-07-09T10:00:00+02:00",
                    "value": 2.0,
                    "level": "LOW_SURPLUS",
                }
            ],
        },
    ]


def test_current_sensor_returns_covering_slot_value(
    freezer: FrozenDateTimeFactory,
) -> None:
    """Current-hour sensor returns the value of the slot covering now."""
    freezer.move_to("2026-07-08T10:30:00+02:00")
    coord = _make_coordinator(_wrap({_EAN: _numeric_forecast()}))
    sensor = EngieBeSolarSurplusCurrentSensor(coord, _make_subentry(), _EAN)
    assert sensor.native_value == 1.5


def test_current_sensor_none_when_no_slot_covers_now(
    freezer: FrozenDateTimeFactory,
) -> None:
    """Outside any slot (e.g. 03:00) the current sensor is unknown."""
    freezer.move_to("2026-07-08T03:00:00+02:00")
    coord = _make_coordinator(_wrap({_EAN: _numeric_forecast()}))
    sensor = EngieBeSolarSurplusCurrentSensor(coord, _make_subentry(), _EAN)
    assert sensor.native_value is None


def test_current_sensor_none_without_wrapper() -> None:
    """No solar_surplus key on coordinator.data means unknown."""
    coord = _make_coordinator({})
    sensor = EngieBeSolarSurplusCurrentSensor(coord, _make_subentry(), _EAN)
    assert sensor.native_value is None


def test_next_hour_sensor_returns_following_slot(
    freezer: FrozenDateTimeFactory,
) -> None:
    """Next-hour sensor returns the value one hour after now."""
    freezer.move_to("2026-07-08T10:30:00+02:00")
    coord = _make_coordinator(_wrap({_EAN: _numeric_forecast()}))
    sensor = EngieBeSolarSurplusNextHourSensor(coord, _make_subentry(), _EAN)
    assert sensor.native_value == 3.2


def test_next_hour_sensor_none_when_target_past_final_slot(
    freezer: FrozenDateTimeFactory,
) -> None:
    """When ``now+1h`` sits past the last slot the sensor is unknown."""
    freezer.move_to("2026-07-08T13:30:00+02:00")
    coord = _make_coordinator(_wrap({_EAN: _numeric_forecast()}))
    sensor = EngieBeSolarSurplusNextHourSensor(coord, _make_subentry(), _EAN)
    assert sensor.native_value is None


def test_today_total_sums_todays_slots(freezer: FrozenDateTimeFactory) -> None:
    """Today-total sums every slot whose Brussels-local date is today."""
    freezer.move_to("2026-07-08T09:00:00+02:00")
    coord = _make_coordinator(_wrap({_EAN: _numeric_forecast()}))
    sensor = EngieBeSolarSurplusTodayTotalSensor(coord, _make_subentry(), _EAN)
    # 1.5 + 3.2 + 0.4 = 5.1
    assert sensor.native_value == 5.1


def test_today_total_none_when_no_todays_slots(freezer: FrozenDateTimeFactory) -> None:
    """Days without data return unknown rather than zero."""
    freezer.move_to("2026-07-10T09:00:00+02:00")
    coord = _make_coordinator(_wrap({_EAN: _numeric_forecast()}))
    sensor = EngieBeSolarSurplusTodayTotalSensor(coord, _make_subentry(), _EAN)
    assert sensor.native_value is None


def test_today_peak_returns_max_and_peak_start(freezer: FrozenDateTimeFactory) -> None:
    """Today-peak returns the max value and the start time attribute."""
    freezer.move_to("2026-07-08T09:00:00+02:00")
    coord = _make_coordinator(_wrap({_EAN: _numeric_forecast()}))
    sensor = EngieBeSolarSurplusTodayPeakSensor(coord, _make_subentry(), _EAN)
    assert sensor.native_value == 3.2
    assert sensor.extra_state_attributes == {
        "peak_start": "2026-07-08T11:00:00+02:00",
    }


def test_today_peak_empty_when_no_todays_slots(freezer: FrozenDateTimeFactory) -> None:
    """No slots today → unknown state and empty attributes."""
    freezer.move_to("2026-07-10T09:00:00+02:00")
    coord = _make_coordinator(_wrap({_EAN: _numeric_forecast()}))
    sensor = EngieBeSolarSurplusTodayPeakSensor(coord, _make_subentry(), _EAN)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_numeric_sensors_skip_non_numeric_values(
    freezer: FrozenDateTimeFactory,
) -> None:
    """Non-numeric ``value`` fields don't break aggregation."""
    freezer.move_to("2026-07-08T10:30:00+02:00")
    payload = [
        {
            "forecastDate": "2026-07-08",
            "level": "LOW_SURPLUS",
            "details": [
                {"startTime": "2026-07-08T10:00:00+02:00", "value": "oops"},
                {"startTime": "2026-07-08T11:00:00+02:00", "value": 1.2},
            ],
        }
    ]
    coord = _make_coordinator(_wrap({_EAN: payload}))
    total = EngieBeSolarSurplusTodayTotalSensor(coord, _make_subentry(), _EAN)
    assert total.native_value == 1.2
    peak = EngieBeSolarSurplusTodayPeakSensor(coord, _make_subentry(), _EAN)
    assert peak.native_value == 1.2


# ---------------------------------------------------------------------------
# _cached_flat_slots memoization
# ---------------------------------------------------------------------------


def test_cached_flat_slots_memoizes_within_refresh_cycle() -> None:
    """Repeated property reads return the same flat-slot list object."""
    coord = _make_coordinator(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    sensor = EngieBeSolarSurplusCurrentSensor(coord, _make_subentry(), _EAN)
    a = sensor._cached_flat_slots()
    b = sensor._cached_flat_slots()
    assert a is b


def test_cached_flat_slots_invalidates_when_data_swapped() -> None:
    """A new coordinator.data dict yields a fresh flat-slot list."""
    coord = _make_coordinator(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    sensor = EngieBeSolarSurplusCurrentSensor(coord, _make_subentry(), _EAN)
    first = sensor._cached_flat_slots()
    coord.data = _wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]})
    second = sensor._cached_flat_slots()
    assert first is not second


# ---------------------------------------------------------------------------
# forecast_creation_date and inference_key attributes
# ---------------------------------------------------------------------------


def test_extra_state_attributes_include_forecast_creation_date_when_today_matches(
    freezer: FrozenDateTimeFactory,
) -> None:
    """The creation date attribute mirrors today's day-entry from the fixture."""
    freezer.move_to("2026-07-08T12:00:00+02:00")
    sensor = _sensor(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    attrs = sensor.extra_state_attributes
    assert attrs["forecast_creation_date"] == "2026-07-07T22:00:00+02:00"
    assert attrs["inference_key"] == "actuals"


def test_extra_state_attributes_surface_no_data_sentinel_creation_date(
    freezer: FrozenDateTimeFactory,
) -> None:
    """The 1970-01-01 sentinel from ENGIE for no-data days is exposed verbatim."""
    freezer.move_to("2026-07-08T12:00:00+02:00")
    sensor = _sensor(_wrap({_EAN: _load(_SOLAR_NO_DATA)["forecasts"]}))
    attrs = sensor.extra_state_attributes
    assert attrs["forecast_creation_date"] == "1970-01-01T01:00:00+01:00"
    assert attrs["inference_key"] == "no_data"


def test_extra_state_attributes_metadata_none_when_today_absent(
    freezer: FrozenDateTimeFactory,
) -> None:
    """When today's date is not in the payload, metadata falls back to first day."""
    freezer.move_to("2027-01-01T12:00:00+01:00")
    sensor = _sensor(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    attrs = sensor.extra_state_attributes
    # Falls back to first day in the fixture, which also has this metadata.
    assert attrs["forecast_creation_date"] == "2026-07-07T22:00:00+02:00"
    assert attrs["inference_key"] == "actuals"
