"""Tests for the TOU slot sensor entity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.engie_be.const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.sensor import (
    _TOU_INJECTION_SLOT,
    _TOU_OFFTAKE_SLOT,
    EngieBeTouSlotSensor,
    _build_tou_sensors,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory

_FIXTURES = Path(__file__).parent / "fixtures"
_TOU_BIHORAIRE = _FIXTURES / "tou_schedules_bihoraire.json"
_TOU_FLAT = _FIXTURES / "tou_schedules_flat_all_offpeak.json"

_EAN = "541448820070000000"
_BRUSSELS = ZoneInfo("Europe/Brussels")

pytestmark = pytest.mark.tou


def _load(path: Path) -> dict:
    """Return a fresh copy of a JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def _wrap(payload: dict[str, Any]) -> dict:
    """Wrap a raw TOU payload in the coordinator storage shape."""
    return {
        "tou_schedules": {
            "data": payload,
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


def _offtake_sensor(data: object, ean: str = _EAN) -> EngieBeTouSlotSensor:
    """Build the offtake slot sensor under test."""
    return EngieBeTouSlotSensor(
        _make_coordinator(data),
        _make_subentry(),
        _TOU_OFFTAKE_SLOT,
        ean=ean,
        direction="offtake",
    )


def _injection_sensor(data: object, ean: str = _EAN) -> EngieBeTouSlotSensor:
    """Build the injection slot sensor under test."""
    return EngieBeTouSlotSensor(
        _make_coordinator(data),
        _make_subentry(),
        _TOU_INJECTION_SLOT,
        ean=ean,
        direction="injection",
    )


# ---------------------------------------------------------------------------
# _build_tou_sensors
# ---------------------------------------------------------------------------


def test_build_creates_offtake_and_injection_per_electricity_ean() -> None:
    """One electricity EAN yields exactly two TOU sensors (offtake + injection)."""
    coord = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    subentry = _make_subentry()
    sensors = _build_tou_sensors(
        coord,
        subentry,
        {_EAN: "ELECTRICITY", "54ZZ": "GAS"},
    )
    unique_ids = {s.unique_id for s in sensors}
    assert unique_ids == {
        f"test_entry_sub_abc_{_EAN}_offtake_slot",
        f"test_entry_sub_abc_{_EAN}_injection_slot",
    }


def test_build_returns_empty_for_gas_only() -> None:
    """No electricity service points means no TOU sensors are built."""
    coord = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    sensors = _build_tou_sensors(coord, _make_subentry(), {"54ZZ": "GAS"})
    assert sensors == []


# ---------------------------------------------------------------------------
# native_value at various Brussels-local times
# ---------------------------------------------------------------------------


def test_native_value_early_monday_is_offpeak(
    freezer: FrozenDateTimeFactory,
) -> None:
    """04:00 Brussels Monday is in the OFFPEAK slot (00:00-06:00)."""
    freezer.move_to("2026-07-06T02:00:00Z")  # 04:00 Brussels (UTC+2 summer)
    sensor = _offtake_sensor(_wrap(_load(_TOU_BIHORAIRE)))
    assert sensor.native_value == "offpeak"


def test_native_value_midday_wednesday_is_peak(
    freezer: FrozenDateTimeFactory,
) -> None:
    """10:00 Brussels Wednesday is in the PEAK slot (06:00-21:00)."""
    freezer.move_to("2026-07-08T08:00:00Z")  # 10:00 Brussels (UTC+2 summer)
    sensor = _offtake_sensor(_wrap(_load(_TOU_BIHORAIRE)))
    assert sensor.native_value == "peak"


def test_native_value_evening_friday_is_offpeak(
    freezer: FrozenDateTimeFactory,
) -> None:
    """22:00 Brussels Friday is in the OFFPEAK slot (21:00-00:00)."""
    freezer.move_to("2026-07-10T20:00:00Z")  # 22:00 Brussels (UTC+2 summer)
    sensor = _offtake_sensor(_wrap(_load(_TOU_BIHORAIRE)))
    assert sensor.native_value == "offpeak"


def test_native_value_saturday_all_day_is_offpeak(
    freezer: FrozenDateTimeFactory,
) -> None:
    """Any time on Saturday is OFFPEAK (single all-day slot)."""
    freezer.move_to("2026-07-11T14:00:00Z")  # 16:00 Brussels Saturday
    sensor = _offtake_sensor(_wrap(_load(_TOU_BIHORAIRE)))
    assert sensor.native_value == "offpeak"


def test_native_value_returns_none_when_wrapper_absent() -> None:
    """No coordinator data returns None."""
    sensor = _offtake_sensor(None)
    assert sensor.native_value is None


def test_native_value_returns_none_for_malformed_slot_times() -> None:
    """A slot with a non-string startTime is skipped and returns None."""
    bad_payload = {
        "items": [
            {
                "eanWithSuffix": f"{_EAN}_ID1",
                "supplierSchedule": {
                    "activeConfigurationId": "X",
                    "offtake": {
                        "optimalTimeslotCode": "OFFPEAK",
                        "monday": [
                            {
                                "startTime": None,
                                "endTime": "06:00",
                                "slotCode": "OFFPEAK",
                            }
                        ],
                        "tuesday": [],
                        "wednesday": [],
                        "thursday": [],
                        "friday": [],
                        "saturday": [],
                        "sunday": [],
                    },
                    "injection": {
                        "optimalTimeslotCode": "OFFPEAK",
                        "monday": [],
                        "tuesday": [],
                        "wednesday": [],
                        "thursday": [],
                        "friday": [],
                        "saturday": [],
                        "sunday": [],
                    },
                },
                "dgoTgoSchedule": {
                    "activeConfigurationId": "X",
                    "offtake": {
                        "optimalTimeslotCode": "OFFPEAK",
                        "monday": [],
                        "tuesday": [],
                        "wednesday": [],
                        "thursday": [],
                        "friday": [],
                        "saturday": [],
                        "sunday": [],
                    },
                    "injection": {
                        "optimalTimeslotCode": "OFFPEAK",
                        "monday": [],
                        "tuesday": [],
                        "wednesday": [],
                        "thursday": [],
                        "friday": [],
                        "saturday": [],
                        "sunday": [],
                    },
                },
            }
        ]
    }
    sensor = _offtake_sensor(_wrap(bad_payload))
    # No slot covers the current instant because the only slot has malformed times.
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------


def test_extra_state_attributes_present(
    freezer: FrozenDateTimeFactory,
) -> None:
    """All expected attribute keys are present for a normal bihoraire schedule."""
    freezer.move_to("2026-07-06T08:00:00Z")  # 10:00 Brussels Monday (PEAK)
    sensor = _offtake_sensor(_wrap(_load(_TOU_BIHORAIRE)))
    attrs = sensor.extra_state_attributes
    assert "optimal_slot" in attrs
    assert "next_transition" in attrs
    assert "weekday_slots" in attrs
    assert "dgo_tgo_slot" in attrs
    assert attrs["optimal_slot"] == "offpeak"
    # At 10:00 we're in PEAK; next transition is 21:00 Brussels
    assert attrs["next_transition"] is not None
    assert "monday" in attrs["weekday_slots"]


def test_extra_state_attributes_injection_optimal_is_peak() -> None:
    """For injection, optimal_slot is 'peak' per the bihoraire fixture."""
    sensor = _injection_sensor(_wrap(_load(_TOU_BIHORAIRE)))
    attrs = sensor.extra_state_attributes
    assert attrs["optimal_slot"] == "peak"


def test_extra_state_attributes_empty_when_no_wrapper() -> None:
    """No data returns an empty attributes dict."""
    sensor = _offtake_sensor(None)
    assert sensor.extra_state_attributes == {}


# ---------------------------------------------------------------------------
# unique_id shape
# ---------------------------------------------------------------------------


def test_unique_id_shape_offtake() -> None:
    """unique_id follows the {entry_id}_{subentry_id}_{ean}_{key} schema."""
    sensor = _offtake_sensor(_wrap(_load(_TOU_BIHORAIRE)))
    assert sensor.unique_id == f"test_entry_sub_abc_{_EAN}_offtake_slot"


def test_unique_id_shape_injection() -> None:
    """unique_id for injection uses the injection_slot key."""
    sensor = _injection_sensor(_wrap(_load(_TOU_BIHORAIRE)))
    assert sensor.unique_id == f"test_entry_sub_abc_{_EAN}_injection_slot"
