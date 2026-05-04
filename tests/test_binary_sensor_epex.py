"""Tests for the EPEX negative-price binary sensor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from custom_components.engie_be.binary_sensor import (
    EPEX_NEGATIVE_SENSOR_DESCRIPTION,
    EngieBeAuthSensor,
    EngieBeEpexNegativeSensor,
    async_setup_entry,
)
from custom_components.engie_be.const import (
    EPEX_TZ,
    SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
)
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


def _make_subentry(
    subentry_id: str = "sub_test",
    title: str = "Test Account",
) -> MagicMock:
    """Build a MagicMock ConfigSubentry with the given id and title."""
    subentry = MagicMock()
    subentry.subentry_id = subentry_id
    subentry.subentry_type = SUBENTRY_TYPE_CUSTOMER_ACCOUNT
    subentry.title = title
    return subentry


def _make_epex_coordinator(payload: EpexPayload | None) -> MagicMock:
    """Build a MagicMock EngieBeEpexCoordinator stub with the given payload."""
    coordinator = MagicMock()
    coordinator.data = payload
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


def test_unique_id_is_subentry_scoped() -> None:
    """Unique IDs must be entry+subentry scoped to survive multi-account installs."""
    coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry(subentry_id="sub_xyz")
    sensor = EngieBeEpexNegativeSensor(coordinator, subentry)
    assert sensor.unique_id == "test_entry_id_sub_xyz_epex_negative"


# ---------------------------------------------------------------------------
# Availability gating
# ---------------------------------------------------------------------------


def test_unavailable_when_payload_missing() -> None:
    """First-poll 404 leaves ``epex=None`` -> unavailable."""
    coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry()
    sensor = EngieBeEpexNegativeSensor(coordinator, subentry)
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
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexNegativeSensor(coordinator, subentry)
    with _patched_now():
        assert sensor.available is True
        assert sensor.is_on is None


def test_available_when_slot_covers_now() -> None:
    """Happy path: slot covering now -> available."""
    payload = _build_payload([(15, 0.025)])
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexNegativeSensor(coordinator, subentry)
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
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexNegativeSensor(coordinator, subentry)
    with _patched_now():
        assert sensor.is_on is True


def test_is_on_false_when_current_slot_zero() -> None:
    """
    A wholesale price of exactly 0.0 EUR/kWh is NOT negative.

    Edge case: zero is not negative.  Belgian EPEX has cleared at
    exactly 0.0 historically; flagging this as ``on`` would be wrong.
    """
    payload = _build_payload([(15, 0.0)])
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexNegativeSensor(coordinator, subentry)
    with _patched_now():
        assert sensor.is_on is False


def test_is_on_false_when_current_slot_positive() -> None:
    """Typical case: positive wholesale price -> sensor off."""
    payload = _build_payload([(15, 0.025)])
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexNegativeSensor(coordinator, subentry)
    with _patched_now():
        assert sensor.is_on is False


def test_is_on_none_when_unavailable() -> None:
    """No payload -> ``is_on`` is None (HA renders unavailable)."""
    coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry()
    sensor = EngieBeEpexNegativeSensor(coordinator, subentry)
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
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexNegativeSensor(coordinator, subentry)
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
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexNegativeSensor(coordinator, subentry)

    # Anchor exactly on the 15:00 boundary; should pick the 15:00 slot.
    boundary = datetime(2026, 5, 4, 15, 0, 0, tzinfo=_BRUSSELS).astimezone(UTC)
    with patch(
        "custom_components.engie_be.binary_sensor.dt_util.utcnow",
        return_value=boundary,
    ):
        assert sensor.is_on is False


# ---------------------------------------------------------------------------
# Setup-entry gating: the EPEX entity is only created for dynamic subentries.
# ---------------------------------------------------------------------------


def _make_sub_data(*, is_dynamic: bool) -> MagicMock:
    """Build a per-subentry runtime-data stub with a coordinator stub."""
    sub_data = MagicMock()
    sub_data.coordinator = MagicMock()
    sub_data.coordinator.is_dynamic = is_dynamic
    return sub_data


def _make_entry(
    epex_coordinator: MagicMock,
    *,
    subentries: dict[str, MagicMock],
    sub_runtime: dict[str, MagicMock],
) -> MagicMock:
    """Build a MagicMock parent ConfigEntry exposing the v3 runtime layout."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.subentries = subentries
    entry.runtime_data = MagicMock()
    entry.runtime_data.epex_coordinator = epex_coordinator
    entry.runtime_data.subentry_data = sub_runtime
    entry.runtime_data.authenticated = True
    return entry


async def test_setup_entry_omits_negative_sensor_for_non_dynamic_account() -> None:
    """
    Fixed-tariff accounts must NOT get the EPEX negative-price entity.

    A permanently unavailable entity is UI noise; gating at setup keeps
    the device card clean for the (majority) fixed-tariff users.
    """
    epex_coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry(subentry_id="sub_fixed")
    entry = _make_entry(
        epex_coordinator,
        subentries={"sub_fixed": subentry},
        sub_runtime={"sub_fixed": _make_sub_data(is_dynamic=False)},
    )

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    # Only the per-entry auth sensor; no EPEX negative entity.
    assert len(added) == 1
    assert isinstance(added[0], EngieBeAuthSensor)


async def test_setup_entry_adds_negative_sensor_for_dynamic_account() -> None:
    """Dynamic accounts get both the auth sensor and the EPEX indicator."""
    epex_coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry(subentry_id="sub_dynamic")
    entry = _make_entry(
        epex_coordinator,
        subentries={"sub_dynamic": subentry},
        sub_runtime={"sub_dynamic": _make_sub_data(is_dynamic=True)},
    )

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    assert len(added) == 2
    assert any(isinstance(e, EngieBeAuthSensor) for e in added)
    assert any(isinstance(e, EngieBeEpexNegativeSensor) for e in added)


async def test_setup_entry_skips_subentry_when_runtime_missing() -> None:
    """
    A subentry without runtime data is skipped (not crashed on).

    The auth sensor still gets created from the EPEX coordinator
    fallback, since it does not depend on subentry runtime data.
    """
    epex_coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry(subentry_id="sub_orphan")
    entry = _make_entry(
        epex_coordinator,
        subentries={"sub_orphan": subentry},
        sub_runtime={},  # no runtime data -> warn-and-skip
    )

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    assert len(added) == 1
    assert isinstance(added[0], EngieBeAuthSensor)


async def test_setup_entry_only_adds_negative_sensor_for_dynamic_subentries() -> None:
    """
    Mixed install: one dynamic + one fixed -> exactly one EPEX entity.

    Pins the per-subentry granularity: a single login can mix tariff
    types across customer accounts.
    """
    epex_coordinator = _make_epex_coordinator(None)
    sub_dyn = _make_subentry(subentry_id="sub_dyn", title="Dynamic")
    sub_fix = _make_subentry(subentry_id="sub_fix", title="Fixed")
    entry = _make_entry(
        epex_coordinator,
        subentries={"sub_dyn": sub_dyn, "sub_fix": sub_fix},
        sub_runtime={
            "sub_dyn": _make_sub_data(is_dynamic=True),
            "sub_fix": _make_sub_data(is_dynamic=False),
        },
    )

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    epex_entities = [e for e in added if isinstance(e, EngieBeEpexNegativeSensor)]
    assert len(epex_entities) == 1
    # And the one created sensor is bound to the dynamic subentry.
    assert epex_entities[0].unique_id == "test_entry_id_sub_dyn_epex_negative"
