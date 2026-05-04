"""Tests for the EPEX negative-price binary sensor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.engie_be.binary_sensor import (
    EPEX_NEGATIVE_SENSOR_DESCRIPTION,
    EngieBeAuthSensor,
    EngieBeEpexNegativeSensor,
    async_setup_entry,
)
from custom_components.engie_be.const import EPEX_TZ, KEY_EPEX, KEY_IS_DYNAMIC
from custom_components.engie_be.data import EpexPayload, EpexSlot

_BRUSSELS = ZoneInfo(EPEX_TZ)

# Same anchor as the sensor tests: 15:30 Brussels falls inside the 15:00 slot.
_NOW_BRUSSELS = datetime(2026, 5, 4, 15, 30, 0, tzinfo=_BRUSSELS)
_NOW_UTC = _NOW_BRUSSELS.astimezone(UTC)


def _make_slot(*, hour: int, value_eur_per_kwh: float, day: int = 4) -> EpexSlot:
    """Build a 1-hour EpexSlot at 2026-05-{day} {hour}:00 Brussels-local."""
    start = datetime(2026, 5, day, hour, 0, 0, tzinfo=_BRUSSELS)
    return EpexSlot(
        start=start,
        end=start + timedelta(hours=1),
        value_eur_per_kwh=value_eur_per_kwh,
        duration_minutes=60,
    )


def _build_payload(today: list[tuple[int, float]]) -> EpexPayload:
    """Build an EpexPayload from ``(hour, eur_per_kwh)`` tuples."""
    return EpexPayload(
        slots=tuple(_make_slot(hour=h, value_eur_per_kwh=v) for h, v in today),
        publication_time=datetime(2026, 5, 4, 13, 7, 21, tzinfo=_BRUSSELS),
        market_date="2026-05-05",
    )


def _make_coordinator(data: dict | None) -> MagicMock:
    """Build a MagicMock coordinator stub with the given ``.data``."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = True
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


def _patched_now():  # noqa: ANN202
    """Patch ``dt_util.utcnow`` inside the binary_sensor module."""
    return patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=_NOW_UTC,
    )


# ---------------------------------------------------------------------------
# Entity description metadata
# ---------------------------------------------------------------------------


def test_epex_negative_description_metadata() -> None:
    """
    Translation key, no device class, and stable unique-id naming.

    No device class is intentional: none of the built-in
    ``BinarySensorDeviceClass`` values describe a price-sign indicator,
    and picking a wrong one (e.g. ``POWER``) would mislead UI rendering.
    """
    desc = EPEX_NEGATIVE_SENSOR_DESCRIPTION
    assert desc.key == "epex_negative"
    assert desc.translation_key == "epex_negative"
    assert desc.device_class is None


def test_unique_id_namespaced_per_entry() -> None:
    """Unique IDs must be per-entry to survive multi-account installs."""
    coordinator = _make_coordinator({KEY_IS_DYNAMIC: True, KEY_EPEX: None})
    sensor = EngieBeEpexNegativeSensor(coordinator)
    assert sensor.unique_id == "test_entry_id_epex_negative"


# ---------------------------------------------------------------------------
# Availability gating
# ---------------------------------------------------------------------------


def test_unavailable_on_non_dynamic_account() -> None:
    """
    Defensive: even if wrongly instantiated, non-dynamic -> unavailable.

    ``async_setup_entry`` already gates creation on ``is_dynamic``; this
    test pins the second line of defence so a runtime contract flip
    (without a config-entry reload) cannot start publishing wholesale
    state on an account that doesn't pay wholesale.
    """
    payload = _build_payload([(15, -0.05)])
    coordinator = _make_coordinator(
        {KEY_IS_DYNAMIC: False, KEY_EPEX: payload},
    )
    sensor = EngieBeEpexNegativeSensor(coordinator)
    with _patched_now():
        assert sensor.available is False


def test_unavailable_when_payload_missing() -> None:
    """First-poll 404 leaves ``epex=None`` -> unavailable."""
    coordinator = _make_coordinator({KEY_IS_DYNAMIC: True, KEY_EPEX: None})
    sensor = EngieBeEpexNegativeSensor(coordinator)
    with _patched_now():
        assert sensor.available is False


def test_unavailable_when_coordinator_data_is_none() -> None:
    """A pre-first-poll coordinator (data=None) must not raise."""
    coordinator = _make_coordinator(None)
    sensor = EngieBeEpexNegativeSensor(coordinator)
    with _patched_now():
        assert sensor.available is False


def test_available_when_payload_present_even_without_covering_slot() -> None:
    """
    Stale payload (no slot covers ``now``) -> available, is_on=None (unknown).

    Per HA quality-scale guidance, a successful fetch with a missing
    specific datum should surface as ``unknown``, not ``unavailable``.
    The fetch succeeded (we have an EPEX payload); only the current
    slot is missing, so ``is_on`` is ``None`` while the entity stays
    available.
    """
    # Slots only at 18:00; 15:30 anchor falls outside.
    payload = _build_payload([(18, -0.05)])
    coordinator = _make_coordinator(
        {KEY_IS_DYNAMIC: True, KEY_EPEX: payload},
    )
    sensor = EngieBeEpexNegativeSensor(coordinator)
    with _patched_now():
        assert sensor.available is True
        assert sensor.is_on is None


def test_available_when_slot_covers_now() -> None:
    """Happy path: dynamic account + slot covering now -> available."""
    payload = _build_payload([(15, 0.025)])
    coordinator = _make_coordinator(
        {KEY_IS_DYNAMIC: True, KEY_EPEX: payload},
    )
    sensor = EngieBeEpexNegativeSensor(coordinator)
    with _patched_now():
        assert sensor.available is True


# ---------------------------------------------------------------------------
# is_on
# ---------------------------------------------------------------------------


def test_is_on_true_when_current_slot_negative() -> None:
    """Negative wholesale price in the active slot -> sensor on."""
    payload = _build_payload(
        [
            (14, 0.02),
            (15, -0.0123),
            (16, 0.05),
        ],
    )
    coordinator = _make_coordinator(
        {KEY_IS_DYNAMIC: True, KEY_EPEX: payload},
    )
    sensor = EngieBeEpexNegativeSensor(coordinator)
    with _patched_now():
        assert sensor.is_on is True


def test_is_on_false_when_current_slot_zero() -> None:
    """
    A wholesale price of exactly 0.0 EUR/kWh is NOT negative.

    Edge case: zero is not negative.  Belgian EPEX has cleared at
    exactly 0.0 historically; flagging this as ``on`` would be wrong.
    """
    payload = _build_payload([(15, 0.0)])
    coordinator = _make_coordinator(
        {KEY_IS_DYNAMIC: True, KEY_EPEX: payload},
    )
    sensor = EngieBeEpexNegativeSensor(coordinator)
    with _patched_now():
        assert sensor.is_on is False


def test_is_on_false_when_current_slot_positive() -> None:
    """Typical case: positive wholesale price -> sensor off."""
    payload = _build_payload([(15, 0.025)])
    coordinator = _make_coordinator(
        {KEY_IS_DYNAMIC: True, KEY_EPEX: payload},
    )
    sensor = EngieBeEpexNegativeSensor(coordinator)
    with _patched_now():
        assert sensor.is_on is False


def test_is_on_none_when_unavailable() -> None:
    """No payload -> ``is_on`` is None (HA renders unavailable)."""
    coordinator = _make_coordinator({KEY_IS_DYNAMIC: True, KEY_EPEX: None})
    sensor = EngieBeEpexNegativeSensor(coordinator)
    with _patched_now():
        assert sensor.is_on is None


def test_is_on_none_when_no_slot_covers_now() -> None:
    """
    Stale payload with no covering slot -> ``is_on`` is None.

    Crucial: the previous slot may have been negative but is no longer
    active.  Returning False would imply the price is currently
    non-negative, which we don't actually know.
    """
    payload = _build_payload([(18, -0.05)])
    coordinator = _make_coordinator(
        {KEY_IS_DYNAMIC: True, KEY_EPEX: payload},
    )
    sensor = EngieBeEpexNegativeSensor(coordinator)
    with _patched_now():
        assert sensor.is_on is None


def test_slot_boundary_is_half_open() -> None:
    """
    Slots are ``[start, end)``: 15:00 boundary belongs to the 15:00 slot.

    Ensures the same boundary semantics as the current-price sensor so
    the binary indicator can never disagree with the numeric one.
    """
    payload = _build_payload(
        [
            (14, -0.05),  # would be wrong if upper bound were inclusive
            (15, 0.02),
        ],
    )
    coordinator = _make_coordinator(
        {KEY_IS_DYNAMIC: True, KEY_EPEX: payload},
    )
    sensor = EngieBeEpexNegativeSensor(coordinator)

    # Anchor exactly on the 15:00 boundary; should pick the 15:00 slot.
    boundary = datetime(2026, 5, 4, 15, 0, 0, tzinfo=_BRUSSELS).astimezone(UTC)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=boundary,
    ):
        assert sensor.is_on is False


# ---------------------------------------------------------------------------
# Setup-entry gating: only create the EPEX entity for dynamic accounts.
# ---------------------------------------------------------------------------


def _make_entry(coordinator: MagicMock) -> MagicMock:
    """Build a MagicMock config entry whose runtime_data exposes ``coordinator``."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.runtime_data = MagicMock()
    entry.runtime_data.coordinator = coordinator
    entry.runtime_data.authenticated = True
    return entry


@pytest.mark.asyncio
async def test_setup_entry_omits_negative_sensor_for_non_dynamic_account() -> None:
    """
    Fixed-tariff accounts must NOT get the EPEX negative-price entity.

    A permanently unavailable entity is UI noise; gating at setup keeps
    the device card clean for the (majority) fixed-tariff users.
    """
    coordinator = _make_coordinator({KEY_IS_DYNAMIC: False, KEY_EPEX: None})
    entry = _make_entry(coordinator)
    added: list = []
    await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert isinstance(added[0], EngieBeAuthSensor)


@pytest.mark.asyncio
async def test_setup_entry_adds_negative_sensor_for_dynamic_account() -> None:
    """Dynamic accounts get both the auth sensor and the EPEX indicator."""
    coordinator = _make_coordinator({KEY_IS_DYNAMIC: True, KEY_EPEX: None})
    entry = _make_entry(coordinator)
    added: list = []
    await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))

    assert len(added) == 2
    assert any(isinstance(e, EngieBeAuthSensor) for e in added)
    assert any(isinstance(e, EngieBeEpexNegativeSensor) for e in added)


@pytest.mark.asyncio
async def test_setup_entry_omits_negative_sensor_when_data_missing() -> None:
    """
    Pre-first-poll (``coordinator.data is None``) -> no EPEX entity.

    The contract type isn't known yet, so adding the entity would
    require it to defensively gate itself forever.  The sensor platform
    early-returns in this case anyway, so binary_sensor follows suit.
    """
    coordinator = _make_coordinator(None)
    entry = _make_entry(coordinator)
    added: list = []
    await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert isinstance(added[0], EngieBeAuthSensor)
