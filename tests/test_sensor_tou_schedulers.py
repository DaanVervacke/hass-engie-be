"""
Tests for the TOU slot sensor boundary scheduler.

Validates that ``EngieBeTouSlotSensor`` rearms via the shared
``_BoundaryScheduleMixin`` so its enum state flips at the exact
second ENGIE's schedule crosses a slot boundary (typically 06:00 or
21:00 Brussels-local for bi-hourly customers).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorEntityDescription
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.engie_be.const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    EPEX_TZ,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.sensor import EngieBeTouSlotSensor

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

    AddSensor = Callable[[HomeAssistant, object], Awaitable[None]]

pytestmark = pytest.mark.tou

_FIXTURES = Path(__file__).parent / "fixtures"
_TOU_BIHORAIRE = _FIXTURES / "tou_schedules_bihoraire.json"
_TOU_FLAT = _FIXTURES / "tou_schedules_flat_all_offpeak.json"

_BRUSSELS = ZoneInfo(EPEX_TZ)
_EAN = "541448820070000000"

# Build a minimal SensorEntityDescription for the offtake slot sensor.
# The private module-level descriptors (_TOU_OFFTAKE_SLOT, _TOU_INJECTION_SLOT)
# are not exported; re-create equivalent stubs for test purposes only.
_OFFTAKE_DESC = SensorEntityDescription(
    key="offtake_slot",
    translation_key="tou_offtake_slot",
    device_class=SensorDeviceClass.ENUM,
    options=["offpeak", "peak"],
)
_INJECTION_DESC = SensorEntityDescription(
    key="injection_slot",
    translation_key="tou_injection_slot",
    device_class=SensorDeviceClass.ENUM,
    options=["offpeak", "peak"],
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _wrap(payload: dict) -> dict:
    """Wrap a raw TOU fixture in the coordinator.data storage shape."""
    return {
        "tou_schedules": {
            "data": payload,
            "fetched_at": "2026-07-08T10:00:00+00:00",
        }
    }


def _make_subentry() -> MagicMock:
    subentry = MagicMock()
    subentry.subentry_id = "sub_test"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {CONF_BUSINESS_AGREEMENT_NUMBER: "000000000000"}
    return subentry


def _make_coordinator(data: object) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = True
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


def _offtake_sensor(coordinator: MagicMock) -> EngieBeTouSlotSensor:
    return EngieBeTouSlotSensor(
        coordinator,
        _make_subentry(),
        _OFFTAKE_DESC,
        _EAN,
        "offtake",
    )


# Monday 2026-07-07 05:30 Brussels (UTC+02:00 CEST) = 03:30 UTC
# Monday 2026-07-07 06:00 Brussels = 04:00 UTC — slot flips OFFPEAK -> PEAK.
_MONDAY_05_30_UTC = datetime(2026, 7, 7, 3, 30, tzinfo=UTC)
_MONDAY_06_00_UTC = datetime(2026, 7, 7, 4, 0, tzinfo=UTC)


async def test_offtake_slot_flips_at_boundary(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Current-slot sensor flips OFFPEAK -> PEAK at 06:00 Brussels."""
    coordinator = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    sensor = _offtake_sensor(coordinator)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_MONDAY_05_30_UTC,
    ):
        await add_sensor(hass, sensor)
        assert sensor.native_value == "offpeak"
        assert sensor._unsub_boundary is not None
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_MONDAY_06_00_UTC,
    ):
        async_fire_time_changed(hass, _MONDAY_06_00_UTC)
        await hass.async_block_till_done()
        assert sensor.native_value == "peak"
        assert sensor._unsub_boundary is not None
        sensor._call_on_remove_callbacks()


async def test_scheduler_does_not_arm_when_wrapper_missing(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """No tou_schedules wrapper on the coordinator: no boundary timer armed."""
    coordinator = _make_coordinator({})
    sensor = _offtake_sensor(coordinator)
    await add_sensor(hass, sensor)
    assert sensor._unsub_boundary is None


async def test_scheduler_arms_on_flat_schedule(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """
    Flat all-OFFPEAK schedule has end-of-day (00:00) boundaries.

    _next_boundary returns the next midnight transition and a timer IS armed.
    """
    coordinator = _make_coordinator(_wrap(_load(_TOU_FLAT)))
    sensor = _offtake_sensor(coordinator)
    monday_noon_utc = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)  # 12:00 Brussels
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=monday_noon_utc,
    ):
        await add_sensor(hass, sensor)
        # Flat schedule entries have endTime "00:00" which resolves to
        # next-day midnight; a timer is armed to that boundary.
        assert sensor._unsub_boundary is not None
        sensor._call_on_remove_callbacks()


async def test_remove_cancels_pending_timer(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Removing the entity cancels its pending boundary timer."""
    coordinator = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    sensor = _offtake_sensor(coordinator)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_MONDAY_05_30_UTC,
    ):
        await add_sensor(hass, sensor)
        assert sensor._unsub_boundary is not None
        sensor._call_on_remove_callbacks()
        assert sensor._unsub_boundary is None


async def test_injection_slot_uses_injection_schedule(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """The injection variant reads the injection sub-block from the schedule."""
    coordinator = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    sensor = EngieBeTouSlotSensor(
        coordinator,
        _make_subentry(),
        _INJECTION_DESC,
        _EAN,
        "injection",
    )
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_MONDAY_05_30_UTC,
    ):
        await add_sensor(hass, sensor)
        # Injection schedule mirrors offtake in the bihoraire fixture:
        # before 06:00 Brussels it is also OFFPEAK.
        assert sensor.native_value == "offpeak"
        assert sensor._unsub_boundary is not None
        sensor._call_on_remove_callbacks()
