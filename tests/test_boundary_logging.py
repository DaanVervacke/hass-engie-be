"""
Tests for ``_BoundaryScheduleMixin`` debug logging.

The mixin is intentionally silent at the HTTP layer but now emits
DEBUG lines when it arms a boundary timer, when that timer fires, and
when no future boundary exists. These lines are the only in-log
evidence that a time-windowed entity (Happy Hours active, EPEX
negative, EPEX price sensors) flipped at the exact window boundary --
the ``async_write_ha_state`` path itself writes nothing to the log, so
without these lines a shared debug bundle cannot prove an on-the-second
flip. Account numbers are masked in the log name.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.engie_be.binary_sensor import EngieBeHappyHourActiveSensor
from custom_components.engie_be.const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

    AddSensor = Callable[[HomeAssistant, object], Awaitable[None]]

_LOGGER_NAME = "custom_components.engie_be"

# Canonical 12:00-15:00 +02:00 window (= 10:00-13:00 UTC).
_WINDOW_START_UTC = datetime(2026, 5, 23, 10, 0, tzinfo=UTC)
_WINDOW_END_UTC = datetime(2026, 5, 23, 13, 0, tzinfo=UTC)
_BEFORE_WINDOW_UTC = _WINDOW_START_UTC - timedelta(hours=2)

_SCHEDULED = {
    "today": {
        "startTime": "2026-05-23T12:00:00+02:00",
        "endTime": "2026-05-23T15:00:00+02:00",
    },
}

# A real-format ENGIE BAN: purely numeric, 12 digits (matches the
# fixtures and the existing happy-hour slug tests). The masked form
# keeps only the last four digits (``***6420``).
_BAN = "002208796420"
_BAN_LAST4 = "6420"


def _make_subentry(*, ban: str | None = None) -> MagicMock:
    """Build a MagicMock ``ConfigSubentry`` stub, optionally carrying a BAN."""
    subentry = MagicMock()
    subentry.subentry_id = "sub_test"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {CONF_BUSINESS_AGREEMENT_NUMBER: ban} if ban else {}
    return subentry


def _make_coordinator(data: object) -> MagicMock:
    """Build a MagicMock per-subentry coordinator stub."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = True
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


def _wrap(payload: object) -> dict:
    """Wrap a payload in the coordinator's happy-hour storage shape."""
    return {"happy_hour": {"data": payload}}


def _patched_now(when: datetime):  # noqa: ANN202
    """Patch ``dt_util.utcnow`` inside the binary_sensor module."""
    return patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=when,
    )


async def test_arm_log_includes_masked_ban_and_target(
    hass: HomeAssistant,
    add_sensor: AddSensor,
    caplog,  # noqa: ANN001
) -> None:
    """Arming a timer logs the masked BAN name and the boundary target."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry(ban=_BAN))
    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME),
        _patched_now(_BEFORE_WINDOW_UTC),
    ):
        await add_sensor(hass, sensor)

    assert sensor._unsub_boundary is not None
    arm_lines = [
        r.message
        for r in caplog.records
        if r.name == _LOGGER_NAME and "armed boundary timer" in r.message
    ]
    assert arm_lines, "expected an 'armed boundary timer' debug line"
    # Descriptive, BAN-free key plus the masked account number.
    assert f"happy_hours_active[***{_BAN_LAST4}]" in arm_lines[-1]
    # The raw BAN must never reach the log.
    assert _BAN not in arm_lines[-1]
    # The armed target is the window start. The boundary helper returns
    # it as a Brussels-local aware datetime (``+02:00``), which is the
    # same instant as ``_WINDOW_START_UTC`` rendered in UTC; assert on
    # the form actually emitted.
    assert "2026-05-23T12:00:00+02:00" in arm_lines[-1]


async def test_fire_log_records_flip_to_on_then_off(
    hass: HomeAssistant,
    add_sensor: AddSensor,
    caplog,  # noqa: ANN001
) -> None:
    """Each boundary fire logs 'boundary fired' and the freshly-written value."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry(ban=_BAN))

    # Start before the window: sensor off, timer armed for the start.
    with _patched_now(_BEFORE_WINDOW_UTC):
        await add_sensor(hass, sensor)
        assert sensor.is_on is False

    # Fire the start boundary -> flips on.
    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME),
        _patched_now(_WINDOW_START_UTC),
    ):
        async_fire_time_changed(hass, _WINDOW_START_UTC)
        await hass.async_block_till_done()
        assert sensor.is_on is True

    fired = [
        r.message
        for r in caplog.records
        if r.name == _LOGGER_NAME and "boundary fired" in r.message
    ]
    wrote = [
        r.message
        for r in caplog.records
        if r.name == _LOGGER_NAME and "wrote new value" in r.message
    ]
    assert fired, "expected a 'boundary fired' debug line"
    assert any("wrote new value True" in m for m in wrote)
    assert all(_BAN not in m for m in fired + wrote)

    caplog.clear()

    # Fire the end boundary -> flips off.
    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME),
        _patched_now(_WINDOW_END_UTC),
    ):
        async_fire_time_changed(hass, _WINDOW_END_UTC)
        await hass.async_block_till_done()
        assert sensor.is_on is False

    wrote_off = [
        r.message
        for r in caplog.records
        if r.name == _LOGGER_NAME and "wrote new value" in r.message
    ]
    assert any("wrote new value False" in m for m in wrote_off)


async def test_no_boundary_log_when_window_in_past(
    hass: HomeAssistant,
    add_sensor: AddSensor,
    caplog,  # noqa: ANN001
) -> None:
    """When both endpoints are in the past, the 'no future boundary' line logs."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry(ban=_BAN))
    after_window = _WINDOW_END_UTC + timedelta(hours=2)
    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME),
        _patched_now(after_window),
    ):
        await add_sensor(hass, sensor)

    assert sensor._unsub_boundary is None
    no_boundary = [
        r.message
        for r in caplog.records
        if r.name == _LOGGER_NAME and "no future boundary to arm" in r.message
    ]
    assert no_boundary, "expected a 'no future boundary to arm' debug line"
    assert "next_boundary=None" in no_boundary[-1]


def test_log_name_falls_back_without_ban() -> None:
    """Without a BAN slug, the log name is the bare description key."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    # No BAN -> entity_id not pre-set to the engie_belgium_<BAN> slug,
    # so the masked-BAN branch is skipped and the key stands alone.
    assert sensor._boundary_log_name() == "happy_hours_active"
