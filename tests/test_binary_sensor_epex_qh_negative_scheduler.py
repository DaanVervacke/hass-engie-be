"""
Tests for the QH EPEX-negative binary sensor's boundary scheduler.

Validates that ``EngieBeEpexQuarterHourNegativeSensor`` flips at the
exact second the market moves to the next 15-minute slot, instead of
waiting for the next coordinator refresh.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.engie_be.binary_sensor import (
    EngieBeEpexQuarterHourNegativeSensor,
)
from custom_components.engie_be.const import EPEX_TZ, SUBENTRY_TYPE_BUSINESS_AGREEMENT
from custom_components.engie_be.data import EpexPayload, EpexSlot

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

    AddSensor = Callable[[HomeAssistant, object], Awaitable[None]]

_BRUSSELS = ZoneInfo(EPEX_TZ)


def _make_slot(*, hour: int, minute: int, value_eur_per_kwh: float) -> EpexSlot:
    """Build a 15-minute EpexSlot at 2026-05-04 {hour}:{minute} Brussels-local."""
    start = datetime(2026, 5, 4, hour, minute, 0, tzinfo=_BRUSSELS)
    return EpexSlot(
        start=start,
        end=start + timedelta(minutes=15),
        value_eur_per_kwh=value_eur_per_kwh,
    )


def _build_payload(slots: tuple[tuple[int, int, float], ...]) -> EpexPayload:
    """Build an EpexPayload from ``(hour, minute, eur_per_kwh)`` tuples."""
    return EpexPayload(
        slots=tuple(
            _make_slot(hour=h, minute=m, value_eur_per_kwh=v) for h, m, v in slots
        ),
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
    """Build a MagicMock EngieBeEpexQuarterHourCoordinator stub."""
    coordinator = MagicMock()
    coordinator.data = payload
    coordinator.last_update_success = True
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


# Two adjacent 15-minute slots: 14:00 negative, 14:15 non-negative.
_PAYLOAD = _build_payload(((14, 0, -0.012), (14, 15, 0.087)))
# 14:00 Brussels (= 12:00 UTC) is the start of the first slot, 14:15
# Brussels (= 12:15 UTC) is the boundary at which the sign should flip.
_NEG_SLOT_START_UTC = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
_BOUNDARY_UTC = datetime(2026, 5, 4, 12, 15, tzinfo=UTC)


async def test_scheduler_arms_at_next_slot_boundary(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Inside the negative slot, timer arms at the slot end."""
    coordinator = _make_coordinator(_PAYLOAD)
    sensor = EngieBeEpexQuarterHourNegativeSensor(coordinator, _make_subentry())
    inside = _NEG_SLOT_START_UTC + timedelta(minutes=7)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=inside,
    ):
        await add_sensor(hass, sensor)
        assert sensor.is_on is True
        assert sensor._unsub_boundary is not None
    # Fire the boundary; sensor flips to off and a fresh timer arms.
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=_BOUNDARY_UTC,
    ):
        async_fire_time_changed(hass, _BOUNDARY_UTC)
        await hass.async_block_till_done()
        assert sensor.is_on is False
        assert sensor._unsub_boundary is not None


async def test_scheduler_does_not_arm_when_payload_missing(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """No payload cached: nothing to schedule against."""
    coordinator = _make_coordinator(None)
    sensor = EngieBeEpexQuarterHourNegativeSensor(coordinator, _make_subentry())
    await add_sensor(hass, sensor)
    assert sensor._unsub_boundary is None


async def test_scheduler_does_not_arm_when_payload_in_past(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Cached payload whose slots are all in the past: no timer armed."""
    coordinator = _make_coordinator(_PAYLOAD)
    sensor = EngieBeEpexQuarterHourNegativeSensor(coordinator, _make_subentry())
    future = _BOUNDARY_UTC + timedelta(hours=2)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=future,
    ):
        await add_sensor(hass, sensor)
        assert sensor._unsub_boundary is None


async def test_coordinator_update_rearms_on_new_payload(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """A coordinator refresh swaps the timer to the new slot boundary."""
    coordinator = _make_coordinator(_PAYLOAD)
    sensor = EngieBeEpexQuarterHourNegativeSensor(coordinator, _make_subentry())
    inside = _NEG_SLOT_START_UTC + timedelta(minutes=7)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=inside,
    ):
        await add_sensor(hass, sensor)
        old_unsub = sensor._unsub_boundary
        assert old_unsub is not None
        # Same payload, simulated CoordinatorEntity update: timer must
        # be replaced (not stacked) even when the boundary is unchanged.
        sensor._handle_coordinator_update()
        new_unsub = sensor._unsub_boundary
        assert new_unsub is not None
        assert new_unsub is not old_unsub


async def test_remove_cancels_pending_timer(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Removing the entity cancels its pending boundary timer."""
    coordinator = _make_coordinator(_PAYLOAD)
    sensor = EngieBeEpexQuarterHourNegativeSensor(coordinator, _make_subentry())
    inside = _NEG_SLOT_START_UTC + timedelta(minutes=7)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=inside,
    ):
        await add_sensor(hass, sensor)
        assert sensor._unsub_boundary is not None
        sensor._call_on_remove_callbacks()
        assert sensor._unsub_boundary is None
