"""
Tests for the EPEX current-price + next-hour sensor boundary scheduler.

Validates that ``EngieBeEpexCurrentSensor`` and
``EngieBeEpexNextHourSensor`` both rearm via the shared
``_BoundaryScheduleMixin`` so their values shift at the exact second
the market moves between hourly slots.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.engie_be import sensor as sensor_module
from custom_components.engie_be.const import EPEX_TZ, SUBENTRY_TYPE_BUSINESS_AGREEMENT
from custom_components.engie_be.data import EpexPayload, EpexSlot
from custom_components.engie_be.sensor import (
    EngieBeEpexCurrentSensor,
    EngieBeEpexNextHourSensor,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

    AddSensor = Callable[[HomeAssistant, object], Awaitable[None]]

_EPEX_CURRENT_DESC = sensor_module._EPEX_CURRENT
_EPEX_NEXT_HOUR_DESC = sensor_module._EPEX_NEXT_HOUR

_BRUSSELS = ZoneInfo(EPEX_TZ)


def _make_slot(*, hour: int, value_eur_per_kwh: float) -> EpexSlot:
    """Build a 1-hour EpexSlot at 2026-05-04 {hour}:00 Brussels-local."""
    start = datetime(2026, 5, 4, hour, 0, 0, tzinfo=_BRUSSELS)
    return EpexSlot(
        start=start,
        end=start + timedelta(hours=1),
        value_eur_per_kwh=value_eur_per_kwh,
        duration_minutes=60,
    )


def _build_payload(slots: tuple[tuple[int, float], ...]) -> EpexPayload:
    """Build an EpexPayload from ``(hour, eur_per_kwh)`` tuples."""
    return EpexPayload(
        slots=tuple(_make_slot(hour=h, value_eur_per_kwh=v) for h, v in slots),
        publication_time=None,
        market_date="2026-05-04",
    )


def _make_subentry() -> MagicMock:
    """Build a MagicMock ConfigSubentry stub."""
    subentry = MagicMock()
    subentry.subentry_id = "sub_test"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {}
    return subentry


def _make_coordinator(payload: EpexPayload | None) -> MagicMock:
    """Build a MagicMock EngieBeEpexCoordinator stub."""
    coordinator = MagicMock()
    coordinator.data = payload
    coordinator.last_update_success = True
    coordinator.last_update_success_time = None
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


# Three adjacent slots so next-hour has a value to read at 14:30.
_PAYLOAD = _build_payload(((14, 0.071), (15, 0.124), (16, 0.098)))
_SLOT_14_START_UTC = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)  # 14:00 Brussels
_BOUNDARY_UTC = datetime(2026, 5, 4, 13, 0, tzinfo=UTC)  # 15:00 Brussels


def _current_sensor(coordinator: MagicMock) -> EngieBeEpexCurrentSensor:
    """Build a current-price sensor with the live descriptor."""
    return EngieBeEpexCurrentSensor(coordinator, _make_subentry(), _EPEX_CURRENT_DESC)


def _next_hour_sensor(coordinator: MagicMock) -> EngieBeEpexNextHourSensor:
    """Build a next-hour sensor with the live descriptor."""
    return EngieBeEpexNextHourSensor(
        coordinator, _make_subentry(), _EPEX_NEXT_HOUR_DESC
    )


async def test_current_sensor_flips_at_slot_boundary(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Current-price sensor rolls to the next slot at the boundary."""
    coordinator = _make_coordinator(_PAYLOAD)
    sensor = _current_sensor(coordinator)
    inside = _SLOT_14_START_UTC + timedelta(minutes=30)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=inside,
    ):
        await add_sensor(hass, sensor)
        assert sensor.native_value == pytest.approx(0.071)
        assert sensor._unsub_boundary is not None
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_BOUNDARY_UTC,
    ):
        async_fire_time_changed(hass, _BOUNDARY_UTC)
        await hass.async_block_till_done()
        assert sensor.native_value == pytest.approx(0.124)
        assert sensor._unsub_boundary is not None


async def test_next_hour_sensor_flips_at_slot_boundary(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Next-hour sensor advances one slot forward at the boundary."""
    coordinator = _make_coordinator(_PAYLOAD)
    sensor = _next_hour_sensor(coordinator)
    inside = _SLOT_14_START_UTC + timedelta(minutes=30)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=inside,
    ):
        await add_sensor(hass, sensor)
        assert sensor.native_value == pytest.approx(0.124)
        assert sensor._unsub_boundary is not None
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_BOUNDARY_UTC,
    ):
        async_fire_time_changed(hass, _BOUNDARY_UTC)
        await hass.async_block_till_done()
        assert sensor.native_value == pytest.approx(0.098)
        assert sensor._unsub_boundary is not None


async def test_scheduler_does_not_arm_when_payload_missing(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """No cached payload: nothing to schedule against."""
    coordinator = _make_coordinator(None)
    sensor = _current_sensor(coordinator)
    await add_sensor(hass, sensor)
    assert sensor._unsub_boundary is None


async def test_scheduler_does_not_arm_when_payload_in_past(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Cached payload whose slots are all in the past: no timer armed."""
    coordinator = _make_coordinator(_PAYLOAD)
    sensor = _current_sensor(coordinator)
    future = _BOUNDARY_UTC + timedelta(hours=10)
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
    coordinator = _make_coordinator(_PAYLOAD)
    sensor = _next_hour_sensor(coordinator)
    inside = _SLOT_14_START_UTC + timedelta(minutes=30)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=inside,
    ):
        await add_sensor(hass, sensor)
        assert sensor._unsub_boundary is not None
        sensor._call_on_remove_callbacks()
        assert sensor._unsub_boundary is None
