"""
Tests for the solar-surplus current + next-hour sensor boundary scheduler.

Validates that ``EngieBeSolarSurplusCurrentSensor`` and
``EngieBeSolarSurplusNextHourSensor`` both rearm via the shared
``_BoundaryScheduleMixin`` so their kWh values roll over at the exact
second ENGIE moves between hourly forecast slots.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.engie_be.const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    EPEX_TZ,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.sensor import (
    EngieBeSolarSurplusCurrentSensor,
    EngieBeSolarSurplusNextHourSensor,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

    AddSensor = Callable[[HomeAssistant, object], Awaitable[None]]

_BRUSSELS = ZoneInfo(EPEX_TZ)
_EAN = "541448820070414088"


def _make_forecast(slots: tuple[tuple[int, float], ...]) -> list[dict]:
    """Build a single-day ENGIE forecasts list from ``(hour, kwh)`` tuples."""
    return [
        {
            "forecastDate": "2026-07-08",
            "level": "HIGH_SURPLUS",
            "details": [
                {
                    "startTime": datetime(
                        2026, 7, 8, hour, 0, 0, tzinfo=_BRUSSELS
                    ).isoformat(),
                    "value": value,
                    "level": "HIGH_SURPLUS",
                }
                for hour, value in slots
            ],
        }
    ]


def _wrap(forecasts: list[dict]) -> dict:
    """Wrap a forecasts list in the coordinator storage shape."""
    return {
        "solar_surplus": {
            "data": {_EAN: forecasts},
            "fetched_at": "2026-07-08T10:00:00+00:00",
        }
    }


def _make_subentry() -> MagicMock:
    """Build a MagicMock ConfigSubentry stub."""
    subentry = MagicMock()
    subentry.subentry_id = "sub_test"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {CONF_BUSINESS_AGREEMENT_NUMBER: "000000000000"}
    return subentry


def _make_coordinator(data: object) -> MagicMock:
    """Build a MagicMock EngieBeDataUpdateCoordinator stub."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = True
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


# Three adjacent slots so next-hour has a value to read at 11:30 Brussels.
_FORECAST = _make_forecast(((10, 0.8), (11, 2.5), (12, 3.4)))
_SLOT_10_START_UTC = datetime(2026, 7, 8, 8, 0, tzinfo=UTC)  # 10:00 Brussels (+02:00)
_BOUNDARY_UTC = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)  # 11:00 Brussels


def _current_sensor(coordinator: MagicMock) -> EngieBeSolarSurplusCurrentSensor:
    """Build a current-hour surplus sensor."""
    return EngieBeSolarSurplusCurrentSensor(coordinator, _make_subentry(), _EAN)


def _next_hour_sensor(coordinator: MagicMock) -> EngieBeSolarSurplusNextHourSensor:
    """Build a next-hour surplus sensor."""
    return EngieBeSolarSurplusNextHourSensor(coordinator, _make_subentry(), _EAN)


async def test_current_sensor_flips_at_slot_boundary(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Current-hour surplus sensor rolls to the next slot at the boundary."""
    coordinator = _make_coordinator(_wrap(_FORECAST))
    sensor = _current_sensor(coordinator)
    inside = _SLOT_10_START_UTC + timedelta(minutes=30)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=inside,
    ):
        await add_sensor(hass, sensor)
        assert sensor.native_value == pytest.approx(0.8)
        assert sensor._unsub_boundary is not None
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_BOUNDARY_UTC,
    ):
        async_fire_time_changed(hass, _BOUNDARY_UTC)
        await hass.async_block_till_done()
        assert sensor.native_value == pytest.approx(2.5)
        assert sensor._unsub_boundary is not None


async def test_next_hour_sensor_flips_at_slot_boundary(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Next-hour surplus sensor advances one slot forward at the boundary."""
    coordinator = _make_coordinator(_wrap(_FORECAST))
    sensor = _next_hour_sensor(coordinator)
    inside = _SLOT_10_START_UTC + timedelta(minutes=30)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=inside,
    ):
        await add_sensor(hass, sensor)
        assert sensor.native_value == pytest.approx(2.5)
        assert sensor._unsub_boundary is not None
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_BOUNDARY_UTC,
    ):
        async_fire_time_changed(hass, _BOUNDARY_UTC)
        await hass.async_block_till_done()
        assert sensor.native_value == pytest.approx(3.4)
        assert sensor._unsub_boundary is not None


async def test_scheduler_does_not_arm_when_wrapper_missing(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """No solar_surplus wrapper on the coordinator: nothing to schedule."""
    coordinator = _make_coordinator({})
    sensor = _current_sensor(coordinator)
    await add_sensor(hass, sensor)
    assert sensor._unsub_boundary is None


async def test_scheduler_does_not_arm_when_all_slots_in_past(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Cached forecasts whose slots are all in the past: no timer armed."""
    coordinator = _make_coordinator(_wrap(_FORECAST))
    sensor = _current_sensor(coordinator)
    future = _BOUNDARY_UTC + timedelta(days=1)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=future,
    ):
        await add_sensor(hass, sensor)
        assert sensor._unsub_boundary is None


async def test_remove_cancels_pending_timer(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Removing the entity cancels its pending boundary timer."""
    coordinator = _make_coordinator(_wrap(_FORECAST))
    sensor = _next_hour_sensor(coordinator)
    inside = _SLOT_10_START_UTC + timedelta(minutes=30)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=inside,
    ):
        await add_sensor(hass, sensor)
        assert sensor._unsub_boundary is not None
        sensor._call_on_remove_callbacks()
        assert sensor._unsub_boundary is None
