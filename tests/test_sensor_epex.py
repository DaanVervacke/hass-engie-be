"""Tests for the EPEX day-ahead price sensors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.engie_be.const import EPEX_TZ, SUBENTRY_TYPE_CUSTOMER_ACCOUNT
from custom_components.engie_be.data import EpexPayload, EpexSlot
from custom_components.engie_be.sensor import (
    _EPEX_CURRENT,
    _EPEX_HIGH_TODAY,
    _EPEX_LOW_TODAY,
    EngieBeEpexCurrentSensor,
    EngieBeEpexExtremaSensor,
    _build_epex_sensors,
)

_BRUSSELS = ZoneInfo(EPEX_TZ)

# Anchor "now" inside the slot at 15:00 Brussels.  Picked deliberately
# to coincide with a slot whose value (0.02565 EUR/kWh) sits between
# today's min and max -- so the current sensor and the extrema sensors
# all return distinct values, making assertions meaningful.
_NOW_BRUSSELS = datetime(2026, 5, 4, 15, 30, 0, tzinfo=_BRUSSELS)
_NOW_UTC = _NOW_BRUSSELS.astimezone(UTC)


def _make_slot(
    *,
    hour: int,
    value_eur_per_kwh: float,
    day: int = 4,
) -> EpexSlot:
    """Build a 1-hour EpexSlot at 2026-05-{day} {hour}:00 Brussels-local."""
    start = datetime(2026, 5, day, hour, 0, 0, tzinfo=_BRUSSELS)
    return EpexSlot(
        start=start,
        end=start + timedelta(hours=1),
        value_eur_per_kwh=value_eur_per_kwh,
        duration_minutes=60,
    )


def _build_payload(
    today: list[tuple[int, float]],
    tomorrow: list[tuple[int, float]] | None = None,
) -> EpexPayload:
    """Build an EpexPayload from ``(hour, eur_per_kwh)`` tuples."""
    slots: list[EpexSlot] = []
    for hour, value in today:
        slots.append(_make_slot(hour=hour, value_eur_per_kwh=value, day=4))
    if tomorrow:
        for hour, value in tomorrow:
            slots.append(_make_slot(hour=hour, value_eur_per_kwh=value, day=5))
    return EpexPayload(
        slots=tuple(slots),
        publication_time=datetime(2026, 5, 4, 13, 7, 21, tzinfo=_BRUSSELS),
        market_date="2026-05-05",
    )


def _make_subentry(
    subentry_id: str = "sub_test",
    title: str = "Test Account",
) -> MagicMock:
    """Build a MagicMock ConfigSubentry stub."""
    subentry = MagicMock()
    subentry.subentry_id = subentry_id
    subentry.subentry_type = SUBENTRY_TYPE_CUSTOMER_ACCOUNT
    subentry.title = title
    return subentry


def _make_epex_coordinator(
    payload: EpexPayload | None,
    *,
    last_fetch: datetime | None = None,
) -> MagicMock:
    """
    Build a MagicMock EPEX coordinator stub.

    ``    EngieBeEpexCoordinator.data`` is now an ``EpexPayload | None``
    (no longer a dict with KEY_EPEX/KEY_IS_DYNAMIC). The current sensor
    reads ``last_update_success_time`` for the ``last_fetched`` attr,
    which is a custom property on ``EngieBeEpexCoordinator`` set on
    every successful parse.
    """
    coordinator = MagicMock()
    coordinator.data = payload
    coordinator.last_update_success = True
    coordinator.last_update_success_time = last_fetch
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def test_build_epex_sensors_creates_three_entities() -> None:
    """
    The builder always returns the three documented EPEX sensors.

    The set + count is part of the public contract; a regression here
    would break user dashboards. Unique IDs are subentry-scoped because
    the EPEX descriptors repeat across every dynamic-tariff customer
    account on a single login.
    """
    payload = _build_payload([(15, 0.02565)])
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry(subentry_id="sub_xyz")

    sensors = _build_epex_sensors(coordinator, subentry)

    keys = {s.entity_description.key for s in sensors}
    assert keys == {"epex_current", "epex_low_today", "epex_high_today"}
    for sensor in sensors:
        assert sensor.unique_id == (
            f"test_entry_id_sub_xyz_{sensor.entity_description.key}"
        )


def test_build_epex_sensors_runs_without_payload() -> None:
    """
    Builder must not crash when called before the first EPEX fetch.

    Platform setup runs before the first refresh; the entities exist
    but report ``unavailable`` until data arrives.
    """
    coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry()
    assert len(_build_epex_sensors(coordinator, subentry)) == 3


# ---------------------------------------------------------------------------
# Availability gating
# ---------------------------------------------------------------------------
#
# NOTE: per-subentry ``is_dynamic`` gating now happens at platform-setup
# time (entities are not even constructed for fixed accounts), so the
# sensor itself only checks payload presence here. The setup-level gating
# is exercised in test_init.py / test_binary_sensor_epex.py.


def test_sensors_unavailable_when_payload_missing() -> None:
    """``data=None`` (e.g. first-poll 404) reports unavailable, not zero."""
    coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry()

    for sensor in _build_epex_sensors(coordinator, subentry):
        assert sensor.available is False


def test_sensors_available_with_payload() -> None:
    """Happy path: payload present -> available."""
    payload = _build_payload([(15, 0.02565)])
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()

    for sensor in _build_epex_sensors(coordinator, subentry):
        assert sensor.available is True


# ---------------------------------------------------------------------------
# Current sensor: native_value
# ---------------------------------------------------------------------------


def test_current_sensor_picks_slot_covering_now() -> None:
    """
    ``native_value`` must return the price of the slot containing ``utcnow``.

    Slots are half-open ``[start, end)``; the 15:30 anchor falls inside
    the 15:00 slot, so the expected value is 0.02565 EUR/kWh, not 0.080
    (16:00) or 0.025 (14:00).
    """
    payload = _build_payload(
        [
            (14, -0.0123),
            (15, 0.02565),
            (16, 0.08040),
        ],
    )
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexCurrentSensor(coordinator, subentry, _EPEX_CURRENT)

    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_NOW_UTC,
    ):
        assert sensor.native_value == pytest.approx(0.02565)


def test_current_sensor_returns_none_outside_any_slot() -> None:
    """
    If no slot covers ``now``, ``native_value`` is ``None`` (becomes unknown).

    Protects against silently displaying a stale value when the
    coordinator's last-known payload no longer covers the present
    instant (e.g. 25h+ outage).
    """
    # Slots only cover 18:00-19:00, so 15:30 anchor falls outside.
    payload = _build_payload([(18, 0.19840)])
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexCurrentSensor(coordinator, subentry, _EPEX_CURRENT)

    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_NOW_UTC,
    ):
        assert sensor.native_value is None


def test_current_sensor_native_value_is_none_when_unavailable() -> None:
    """No payload -> native_value None (don't surface placeholder zeros)."""
    coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry()
    sensor = EngieBeEpexCurrentSensor(coordinator, subentry, _EPEX_CURRENT)
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Current sensor: extra_state_attributes
# ---------------------------------------------------------------------------


def test_current_sensor_attributes_partition_today_and_tomorrow() -> None:
    """
    The today/tomorrow arrays must split slots by Brussels-local date.

    Dashboard cards (ApexCharts in particular) consume these arrays to
    render two distinct series -- a regression here would smear them
    together or drop one entirely.
    """
    payload = _build_payload(
        today=[(14, -0.0123), (15, 0.02565), (18, 0.19840)],
        tomorrow=[(0, 0.08810), (18, 0.21050)],
    )
    last_fetch = datetime(2026, 5, 4, 13, 7, 21, tzinfo=UTC)
    coordinator = _make_epex_coordinator(payload, last_fetch=last_fetch)
    subentry = _make_subentry()
    sensor = EngieBeEpexCurrentSensor(coordinator, subentry, _EPEX_CURRENT)

    with patch(
        "custom_components.engie_be.sensor.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        attrs = sensor.extra_state_attributes

    assert len(attrs["today"]) == 3
    assert len(attrs["tomorrow"]) == 2
    # Each entry exposes start/end ISO strings + EUR/kWh + EUR/MWh.
    today_first = attrs["today"][0]
    assert set(today_first) == {"start", "end", "value", "value_eur_per_mwh"}
    assert today_first["start"] == "2026-05-04T14:00:00+02:00"
    assert today_first["end"] == "2026-05-04T15:00:00+02:00"
    assert today_first["value"] == pytest.approx(-0.0123)
    # EUR/MWh is exposed alongside for users who think in wholesale units.
    assert today_first["value_eur_per_mwh"] == pytest.approx(-12.3)
    # Slot duration carried for forward-compat with 15-min publication.
    assert attrs["slot_duration_minutes"] == 60
    # Publication metadata + last_fetched are present.
    assert "publication_time" in attrs
    assert attrs["market_date"] == "2026-05-05"
    assert attrs["last_fetched"] == last_fetch.isoformat()


def test_current_sensor_attributes_empty_when_unavailable() -> None:
    """No payload -> empty attribute dict (don't leak stale keys)."""
    coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry()
    sensor = EngieBeEpexCurrentSensor(coordinator, subentry, _EPEX_CURRENT)
    assert sensor.extra_state_attributes == {}


def test_current_sensor_attributes_omit_optional_metadata_when_absent() -> None:
    """
    ``publication_time``/``market_date``/``last_fetched`` are optional.

    They must be omitted (not emitted as ``None``) when missing, so
    template authors don't have to defend against ``None``.
    """
    slot = _make_slot(hour=15, value_eur_per_kwh=0.02565)
    payload = EpexPayload(
        slots=(slot,),
        publication_time=None,
        market_date=None,
    )
    coordinator = _make_epex_coordinator(payload, last_fetch=None)
    subentry = _make_subentry()
    sensor = EngieBeEpexCurrentSensor(coordinator, subentry, _EPEX_CURRENT)

    with patch(
        "custom_components.engie_be.sensor.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        attrs = sensor.extra_state_attributes

    assert "publication_time" not in attrs
    assert "market_date" not in attrs
    assert "last_fetched" not in attrs


# ---------------------------------------------------------------------------
# Extrema sensors: min/max selection
# ---------------------------------------------------------------------------


def test_low_today_sensor_selects_minimum_of_today_only() -> None:
    """
    ``epex_low_today`` reduces over Brussels-local TODAY only.

    Tomorrow's slots must NOT influence today's extremum -- that would
    show a wrong "low" the moment tomorrow's slate publishes.
    Negative values are valid wholesale prices and must win the min.
    """
    payload = _build_payload(
        today=[(8, 0.115), (14, -0.0123), (18, 0.19840)],
        tomorrow=[(14, -0.5)],  # Lower than today, but must be ignored.
    )
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexExtremaSensor(
        coordinator,
        subentry,
        _EPEX_LOW_TODAY,
        mode="min",
    )

    with patch(
        "custom_components.engie_be.sensor.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        assert sensor.native_value == pytest.approx(-0.0123)
        attrs = sensor.extra_state_attributes

    assert attrs["slot_start"] == "2026-05-04T14:00:00+02:00"
    assert attrs["slot_end"] == "2026-05-04T15:00:00+02:00"
    assert attrs["slot_duration_minutes"] == 60


def test_high_today_sensor_selects_maximum_of_today_only() -> None:
    """``epex_high_today`` mirrors low's contract on the max side."""
    payload = _build_payload(
        today=[(8, 0.115), (14, -0.0123), (18, 0.19840)],
        tomorrow=[(18, 0.99999)],  # Higher than today, but must be ignored.
    )
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexExtremaSensor(
        coordinator,
        subentry,
        _EPEX_HIGH_TODAY,
        mode="max",
    )

    with patch(
        "custom_components.engie_be.sensor.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        assert sensor.native_value == pytest.approx(0.19840)
        attrs = sensor.extra_state_attributes

    assert attrs["slot_start"] == "2026-05-04T18:00:00+02:00"
    assert attrs["slot_end"] == "2026-05-04T19:00:00+02:00"


def test_extrema_sensor_returns_none_when_no_today_slots() -> None:
    """
    If today has no slots (only tomorrow's), value is ``None``.

    Prevents the sensor from publishing tomorrow's extremum as today's
    in the rare window where the cached payload only covers tomorrow.
    """
    payload = _build_payload(today=[], tomorrow=[(18, 0.21050)])
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor_min = EngieBeEpexExtremaSensor(
        coordinator,
        subentry,
        _EPEX_LOW_TODAY,
        mode="min",
    )
    sensor_max = EngieBeEpexExtremaSensor(
        coordinator,
        subentry,
        _EPEX_HIGH_TODAY,
        mode="max",
    )

    with patch(
        "custom_components.engie_be.sensor.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        assert sensor_min.native_value is None
        assert sensor_max.native_value is None
        assert sensor_min.extra_state_attributes == {}
        assert sensor_max.extra_state_attributes == {}


def test_extrema_sensor_rejects_invalid_mode() -> None:
    """Defensive: only ``min``/``max`` are valid reducers."""
    coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry()
    with pytest.raises(ValueError, match="mode must be 'min' or 'max'"):
        EngieBeEpexExtremaSensor(
            coordinator,
            subentry,
            _EPEX_LOW_TODAY,
            mode="median",
        )


# ---------------------------------------------------------------------------
# Entity description metadata
# ---------------------------------------------------------------------------


def test_epex_sensors_use_eur_per_kwh_unit_and_measurement_state_class() -> None:
    """
    Unit must be ``EUR/kWh`` (MONETARY device class would reject this).

    HA's MONETARY device class requires a bare ISO 4217 currency code
    as the unit and rejects per-kWh forms; if a future refactor adds
    ``device_class=MONETARY`` here the entity will fail to register.
    State class must be MEASUREMENT (not TOTAL_INCREASING) because
    wholesale prices can go negative and reset arbitrarily.
    """
    for desc in (_EPEX_CURRENT, _EPEX_LOW_TODAY, _EPEX_HIGH_TODAY):
        assert desc.native_unit_of_measurement == "EUR/kWh"
        assert desc.state_class == SensorStateClass.MEASUREMENT
        assert desc.device_class is None
        # Wholesale prices fluctuate at the cent level; 4 digits is the
        # documented precision.  More digits would just show noise.
        assert desc.suggested_display_precision == 4
        # Translation key must match strings.json entries.
        assert desc.translation_key == desc.key
