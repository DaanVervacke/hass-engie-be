"""Tests for the Happy Hour active binary sensor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.engie_be.binary_sensor import (
    HAPPY_HOUR_ACTIVE_SENSOR_DESCRIPTION,
    EngieBeHappyHourActiveSensor,
)
from custom_components.engie_be.const import SUBENTRY_TYPE_BUSINESS_AGREEMENT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

    AddSensor = Callable[[HomeAssistant, object], Awaitable[None]]

# Anchor inside the canonical 12:00-15:00 +02:00 happy-hour window.
_NOW_INSIDE = datetime(2026, 5, 23, 11, 30, tzinfo=UTC)
# Anchor before the window.
_NOW_BEFORE = datetime(2026, 5, 23, 7, 0, tzinfo=UTC)

_SCHEDULED = {
    "tomorrow": {
        "startTime": "2026-05-23T12:00:00+02:00",
        "endTime": "2026-05-23T15:00:00+02:00",
    },
}

# The same window re-published under the ``today`` key once midnight passes.
_TODAY_SCHEDULED = {
    "today": {
        "startTime": "2026-05-23T12:00:00+02:00",
        "endTime": "2026-05-23T15:00:00+02:00",
    },
}


def _make_subentry(subentry_id: str = "sub_test") -> MagicMock:
    """Build a MagicMock ``ConfigSubentry`` stub."""
    subentry = MagicMock()
    subentry.subentry_id = subentry_id
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {}
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


# ---------------------------------------------------------------------------
# Entity description metadata
# ---------------------------------------------------------------------------


def test_description_metadata() -> None:
    """Translation key and key are stable; no device class is required."""
    desc = HAPPY_HOUR_ACTIVE_SENSOR_DESCRIPTION
    assert desc.key == "happy_hour_active"
    assert desc.translation_key == "happy_hour_active"


def test_unique_id_is_subentry_scoped() -> None:
    """Unique IDs follow ``{entry_id}_{subentry_id}_happy_hour_active``."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    subentry = _make_subentry(subentry_id="sub_xyz")
    sensor = EngieBeHappyHourActiveSensor(coordinator, subentry)
    assert sensor.unique_id == "test_entry_id_sub_xyz_happy_hour_active"


def test_entity_id_uses_ban_when_present() -> None:
    """A subentry with a BAN gets the stable ``engie_belgium_<ban>_...`` slug."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    subentry = _make_subentry()
    subentry.data = {"business_agreement_number": "002208796420"}
    sensor = EngieBeHappyHourActiveSensor(coordinator, subentry)
    assert sensor.entity_id == (
        "binary_sensor.engie_belgium_002208796420_happy_hour_active"
    )


# ---------------------------------------------------------------------------
# is_on
# ---------------------------------------------------------------------------


def test_is_on_true_inside_window() -> None:
    """Now inside the scheduled window -> ``on``."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    with _patched_now(_NOW_INSIDE):
        assert sensor.is_on is True


def test_is_on_false_outside_window() -> None:
    """Now before the scheduled window -> ``off``."""
    coordinator = _make_coordinator(_wrap(_SCHEDULED))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    with _patched_now(_NOW_BEFORE):
        assert sensor.is_on is False


def test_is_on_true_inside_today_key_window() -> None:
    """A ``today``-key live window reports ``on`` (post-midnight regression)."""
    coordinator = _make_coordinator(_wrap(_TODAY_SCHEDULED))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    with _patched_now(_NOW_INSIDE):
        assert sensor.is_on is True


def test_is_on_false_when_no_event_scheduled() -> None:
    """Empty ``{}`` payload -> always ``off`` regardless of ``now``."""
    coordinator = _make_coordinator(_wrap({}))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    with _patched_now(_NOW_INSIDE):
        assert sensor.is_on is False


def test_is_on_false_when_no_data_yet() -> None:
    """No coordinator data yet -> ``off`` (sensor is always available)."""
    coordinator = _make_coordinator(None)
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    with _patched_now(_NOW_INSIDE):
        assert sensor.is_on is False


def test_is_on_returns_bool_not_none() -> None:
    """``is_on`` must always be a concrete bool (no ``unknown`` state)."""
    coordinator = _make_coordinator(None)
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    with _patched_now(_NOW_INSIDE):
        result = sensor.is_on
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Instant-flip scheduler
#
# These tests exercise the point-in-time scheduler that makes the sensor
# flip on/off at the exact second the Happy Hour window starts or ends,
# instead of waiting up to a full coordinator refresh interval.
# ---------------------------------------------------------------------------

# Canonical window used by the scheduler tests, anchored well into the
# future so freeze-time math is unambiguous regardless of when the suite
# runs. Both timestamps carry an explicit +02:00 offset (matching the
# real ENGIE payload shape) so ``happy_hour_window`` returns tz-aware
# datetimes.
_FUTURE_PAYLOAD = {
    "tomorrow": {
        "startTime": "2099-06-15T12:00:00+02:00",
        "endTime": "2099-06-15T15:00:00+02:00",
    },
}
# Same window as UTC datetimes for assertions / time travel.
_FUTURE_START_UTC = datetime(2099, 6, 15, 10, 0, tzinfo=UTC)
_FUTURE_END_UTC = datetime(2099, 6, 15, 13, 0, tzinfo=UTC)
# The same future window re-published under the ``today`` key.
_TODAY_FUTURE_PAYLOAD = {
    "today": {
        "startTime": "2099-06-15T12:00:00+02:00",
        "endTime": "2099-06-15T15:00:00+02:00",
    },
}
# An alternative window that starts earlier; used for the reschedule test.
_ALT_PAYLOAD = {
    "tomorrow": {
        "startTime": "2099-06-15T11:00:00+02:00",
        "endTime": "2099-06-15T14:00:00+02:00",
    },
}
_ALT_START_UTC = datetime(2099, 6, 15, 9, 0, tzinfo=UTC)


async def test_scheduler_arms_at_start_when_now_before_window(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Before the window: timer must be armed; firing it flips to ``on``."""
    coordinator = _make_coordinator(_wrap(_FUTURE_PAYLOAD))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    before = _FUTURE_START_UTC - timedelta(minutes=5)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=before,
    ):
        await add_sensor(hass, sensor)
        assert sensor.is_on is False
        assert sensor._unsub_boundary is not None
    # Fire the start boundary; sensor should now be on and a new
    # timer should be armed for the end boundary.
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=_FUTURE_START_UTC,
    ):
        async_fire_time_changed(hass, _FUTURE_START_UTC)
        await hass.async_block_till_done()
        assert sensor.is_on is True
        assert sensor._unsub_boundary is not None
    # The re-armed end-boundary timer targets 2099; cancel it so it does
    # not linger on the event loop past teardown (verify_cleanup fails on
    # lingering timers as of pytest-homeassistant-custom-component 0.13.337).
    sensor._call_on_remove_callbacks()
    assert sensor._unsub_boundary is None


async def test_scheduler_arms_for_today_key_window(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """A ``today``-key future window arms the timer and flips on like ``tomorrow``."""
    coordinator = _make_coordinator(_wrap(_TODAY_FUTURE_PAYLOAD))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    before = _FUTURE_START_UTC - timedelta(minutes=5)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=before,
    ):
        await add_sensor(hass, sensor)
        assert sensor.is_on is False
        assert sensor._unsub_boundary is not None
    # Firing the start boundary flips the sensor on, proving the scheduler
    # honours ``today``-key windows identically to ``tomorrow``-key ones.
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=_FUTURE_START_UTC,
    ):
        async_fire_time_changed(hass, _FUTURE_START_UTC)
        await hass.async_block_till_done()
        assert sensor.is_on is True
        assert sensor._unsub_boundary is not None
    # Cancel the re-armed end-boundary timer (targets 2099) to keep the
    # event loop clean for verify_cleanup at teardown.
    sensor._call_on_remove_callbacks()
    assert sensor._unsub_boundary is None


async def test_scheduler_arms_at_end_when_now_inside_window(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Inside the window: timer arms at end; firing it flips to ``off``."""
    coordinator = _make_coordinator(_wrap(_FUTURE_PAYLOAD))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    inside = _FUTURE_START_UTC + timedelta(minutes=30)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=inside,
    ):
        await add_sensor(hass, sensor)
        assert sensor.is_on is True
        assert sensor._unsub_boundary is not None
    # Fire the end boundary; sensor should now be off and no further
    # timer should be armed (window is exhausted).
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=_FUTURE_END_UTC,
    ):
        async_fire_time_changed(hass, _FUTURE_END_UTC)
        await hass.async_block_till_done()
        assert sensor.is_on is False
        assert sensor._unsub_boundary is None


async def test_scheduler_does_not_arm_when_window_already_passed(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """A payload whose window is entirely in the past arms no timer."""
    coordinator = _make_coordinator(_wrap(_FUTURE_PAYLOAD))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    past = _FUTURE_END_UTC + timedelta(hours=1)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=past,
    ):
        await add_sensor(hass, sensor)
        assert sensor.is_on is False
        assert sensor._unsub_boundary is None


async def test_scheduler_does_not_arm_when_payload_empty(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """No scheduled window: no timer armed; sensor is off."""
    coordinator = _make_coordinator(_wrap({}))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    await add_sensor(hass, sensor)
    assert sensor.is_on is False
    assert sensor._unsub_boundary is None


async def test_coordinator_update_reschedules_to_new_window(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """A payload swap must cancel the existing timer and arm a fresh one."""
    coordinator = _make_coordinator(_wrap(_FUTURE_PAYLOAD))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    before = _ALT_START_UTC - timedelta(minutes=5)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=before,
    ):
        await add_sensor(hass, sensor)
        old_unsub = sensor._unsub_boundary
        assert old_unsub is not None
        # Swap to an earlier-starting window and notify the entity as a
        # CoordinatorEntity would on the next refresh.
        coordinator.data = _wrap(_ALT_PAYLOAD)
        sensor._handle_coordinator_update()
        new_unsub = sensor._unsub_boundary
        assert new_unsub is not None
        # Re-arming must replace the unsub handle, not stack a second one.
        assert new_unsub is not old_unsub
    # The freshly armed timer targets 2099; cancel it so it does not linger
    # on the event loop past teardown (verify_cleanup fails on lingering
    # timers as of pytest-homeassistant-custom-component 0.13.337).
    sensor._call_on_remove_callbacks()
    assert sensor._unsub_boundary is None


async def test_remove_cancels_pending_timer(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Removing the entity cancels its pending boundary timer."""
    coordinator = _make_coordinator(_wrap(_FUTURE_PAYLOAD))
    sensor = EngieBeHappyHourActiveSensor(coordinator, _make_subentry())
    before = _FUTURE_START_UTC - timedelta(minutes=5)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=before,
    ):
        await add_sensor(hass, sensor)
        assert sensor._unsub_boundary is not None
        # The cancel is registered via ``self.async_on_remove`` and runs
        # in ``_call_on_remove_callbacks``; firing those directly avoids
        # the full ``async_remove`` path which requires the entity to be
        # registered in ``entity_sources``.
        sensor._call_on_remove_callbacks()
        assert sensor._unsub_boundary is None
