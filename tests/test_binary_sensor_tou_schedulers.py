"""
Tests for the TOU is-optimal binary sensor boundary scheduler.

Validates that ``EngieBeTouIsOptimalSensor`` rearms via the shared
``_BoundaryScheduleMixin`` so its on/off state flips at the exact
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
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.engie_be.binary_sensor import (
    TOU_INJECTION_IS_OPTIMAL_DESCRIPTION,
    TOU_OFFTAKE_IS_OPTIMAL_DESCRIPTION,
    EngieBeTouIsOptimalSensor,
)
from custom_components.engie_be.const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    EPEX_TZ,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

    AddSensor = Callable[[HomeAssistant, object], Awaitable[None]]

pytestmark = pytest.mark.tou

_FIXTURES = Path(__file__).parent / "fixtures"
_TOU_BIHORAIRE = _FIXTURES / "tou_schedules_bihoraire.json"

_BRUSSELS = ZoneInfo(EPEX_TZ)
_EAN = "541448820070000000"


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


def _offtake_sensor(coordinator: MagicMock) -> EngieBeTouIsOptimalSensor:
    return EngieBeTouIsOptimalSensor(
        coordinator=coordinator,
        subentry=_make_subentry(),
        entity_description=TOU_OFFTAKE_IS_OPTIMAL_DESCRIPTION,
        ean=_EAN,
        direction="offtake",
    )


# Monday 2026-07-07 05:30 Brussels (UTC+02:00 CEST) = 03:30 UTC
# Monday 2026-07-07 06:00 Brussels = 04:00 UTC — slot flips OFFPEAK -> PEAK.
_MONDAY_05_30_UTC = datetime(2026, 7, 7, 3, 30, tzinfo=UTC)
_MONDAY_06_00_UTC = datetime(2026, 7, 7, 4, 0, tzinfo=UTC)


async def test_offtake_is_optimal_flips_at_boundary(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """
    is_on flips True -> False at 06:00 Brussels.

    Offtake slot changes from OFFPEAK (optimal) to PEAK (not optimal).
    """
    coordinator = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    sensor = _offtake_sensor(coordinator)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=_MONDAY_05_30_UTC,
    ):
        await add_sensor(hass, sensor)
        # Before 06:00: slot is OFFPEAK == optimalTimeslotCode (OFFPEAK) -> on.
        assert sensor.is_on is True
        assert sensor._unsub_boundary is not None
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=_MONDAY_06_00_UTC,
    ):
        async_fire_time_changed(hass, _MONDAY_06_00_UTC)
        await hass.async_block_till_done()
        # After 06:00: slot is PEAK != optimalTimeslotCode (OFFPEAK) -> off.
        assert sensor.is_on is False
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


async def test_remove_cancels_pending_timer(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Removing the entity cancels its pending boundary timer."""
    coordinator = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    sensor = _offtake_sensor(coordinator)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=_MONDAY_05_30_UTC,
    ):
        await add_sensor(hass, sensor)
        assert sensor._unsub_boundary is not None
        sensor._call_on_remove_callbacks()
        assert sensor._unsub_boundary is None


async def test_injection_is_optimal_uses_injection_schedule(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """
    The injection variant reads the injection sub-block.

    Injection optimalTimeslotCode in the bihoraire fixture is PEAK.
    At 05:30 Brussels, the slot is OFFPEAK, so is_on is False.
    """
    coordinator = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    sensor = EngieBeTouIsOptimalSensor(
        coordinator=coordinator,
        subentry=_make_subentry(),
        entity_description=TOU_INJECTION_IS_OPTIMAL_DESCRIPTION,
        ean=_EAN,
        direction="injection",
    )
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=_MONDAY_05_30_UTC,
    ):
        await add_sensor(hass, sensor)
        # injection optimalTimeslotCode is PEAK; at 05:30 slot is OFFPEAK -> off.
        assert sensor.is_on is False
        assert sensor._unsub_boundary is not None
        sensor._call_on_remove_callbacks()


async def test_injection_is_optimal_on_during_peak(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """
    Injection sensor is_on flips True at 06:00 Brussels when PEAK begins.

    PEAK is the injection optimalTimeslotCode, so is_on is True during PEAK.
    """
    coordinator = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    sensor = EngieBeTouIsOptimalSensor(
        coordinator=coordinator,
        subentry=_make_subentry(),
        entity_description=TOU_INJECTION_IS_OPTIMAL_DESCRIPTION,
        ean=_EAN,
        direction="injection",
    )
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=_MONDAY_06_00_UTC,
    ):
        await add_sensor(hass, sensor)
        # At 06:00: slot is PEAK == optimalTimeslotCode (PEAK) -> on.
        assert sensor.is_on is True
        sensor._call_on_remove_callbacks()
