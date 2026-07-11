"""Tests for the EPEX day-ahead price sensors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.engie_be.const import EPEX_TZ, SUBENTRY_TYPE_BUSINESS_AGREEMENT
from custom_components.engie_be.data import EpexPayload, EpexSlot
from custom_components.engie_be.sensor import (
    _EPEX_CURRENT,
    _EPEX_CURRENT_QUARTER_HOUR,
    _EPEX_HIGH_TODAY,
    _EPEX_HIGH_TODAY_QUARTER_HOUR,
    _EPEX_LOW_TODAY,
    _EPEX_LOW_TODAY_QUARTER_HOUR,
    _EPEX_NEXT_HOUR,
    _EPEX_NEXT_QUARTER_HOUR,
    EngieBeEpexCurrentSensor,
    EngieBeEpexExtremaSensor,
    EngieBeEpexNextHourSensor,
    EngieBeEpexNextQuarterHourSensor,
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
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
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
    (no longer a dict). The current sensor
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


def _build_epex_payload_qh() -> EpexPayload:
    """Build a QH payload with 96 quarter-hourly slots."""
    slots: list[EpexSlot] = []
    for i in range(96):
        hour = i // 4
        minute = 15 * (i % 4)
        # Use day 4 for all slots to keep it simple
        slot_start = datetime(2026, 5, 4, hour, minute, 0, tzinfo=_BRUSSELS)
        value = 0.100 + (i * 0.001)  # Distinct values for testing
        slots.append(
            EpexSlot(
                start=slot_start,
                end=slot_start + timedelta(minutes=15),
                value_eur_per_kwh=value,
            )
        )

    return EpexPayload(
        slots=tuple(slots),
        publication_time=datetime(2026, 5, 4, 13, 0, 0, tzinfo=_BRUSSELS),
        market_date="2026-05-04",
        slot_duration=timedelta(minutes=15),
    )


def _make_epex_qh_coordinator(
    payload: EpexPayload | None,
) -> MagicMock:
    """Build a MagicMock QH EPEX coordinator stub."""
    coordinator = MagicMock()
    coordinator.data = payload
    coordinator.last_update_success = True
    if payload is not None:
        start_time = datetime(2026, 5, 4, 14, 0, 0, tzinfo=UTC)
        coordinator.last_update_success_time = start_time
    else:
        coordinator.last_update_success_time = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def test_build_epex_sensors_creates_four_entities() -> None:
    """
    The builder always returns the four documented EPEX sensors.

    The set + count is part of the public contract; a regression here
    would break user dashboards. Unique IDs are subentry-scoped because
    the EPEX descriptors repeat across every dynamic-tariff customer
    account on a single login.
    """
    payload = _build_payload([(15, 0.02565)])
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry(subentry_id="sub_xyz")

    sensors = _build_epex_sensors(coordinator, None, subentry)

    keys = {s.entity_description.key for s in sensors}
    assert keys == {
        "epex_current",
        "epex_low_today",
        "epex_high_today",
        "epex_next_hour",
    }
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
    assert len(_build_epex_sensors(coordinator, None, subentry)) == 4


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

    for sensor in _build_epex_sensors(coordinator, None, subentry):
        assert sensor.available is False


def test_sensors_available_with_payload() -> None:
    """Happy path: payload present -> available."""
    payload = _build_payload([(15, 0.02565)])
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()

    for sensor in _build_epex_sensors(coordinator, None, subentry):
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
    assert set(today_first) == {
        "start",
        "end",
        "value",
        "value_eur_per_mwh",
        "slot_duration_minutes",
    }
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
    last_fetch = datetime(2026, 5, 4, 13, 7, 21, tzinfo=UTC)
    coordinator = _make_epex_coordinator(payload, last_fetch=last_fetch)
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
    assert attrs["last_fetched"] == last_fetch.isoformat()


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


def test_extrema_sensor_native_value_is_none_without_payload() -> None:
    """
    ``native_value`` returns ``None`` when no payload is cached.

    ``available`` already gates the entity to ``unavailable`` in this
    state, but the value path is exercised independently to defend
    against future changes that decouple the two (e.g. an availability
    refactor that no longer short-circuits on payload presence).
    """
    coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry()
    sensor = EngieBeEpexExtremaSensor(
        coordinator,
        subentry,
        _EPEX_LOW_TODAY,
        mode="min",
    )
    assert sensor.native_value is None


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
    for desc in (_EPEX_CURRENT, _EPEX_LOW_TODAY, _EPEX_HIGH_TODAY, _EPEX_NEXT_HOUR):
        assert desc.native_unit_of_measurement == "EUR/kWh"
        assert desc.state_class == SensorStateClass.MEASUREMENT
        assert desc.device_class is None
        # Wholesale prices fluctuate at the cent level; 4 digits is the
        # documented precision.  More digits would just show noise.
        assert desc.suggested_display_precision == 4
        # Translation key must match strings.json entries.
        assert desc.translation_key == desc.key


# ---------------------------------------------------------------------------
# Next-hour sensor
# ---------------------------------------------------------------------------


def test_next_hour_sensor_picks_slot_covering_now_plus_one_hour() -> None:
    """
    ``native_value`` must return the price of the slot containing ``now+1h``.

    With the 15:30 Brussels anchor, ``now+1h`` is 16:30, which falls
    inside the 16:00 slot. The 15:00 slot (current) and the 17:00 slot
    must NOT be selected -- a regression here would either re-publish
    the current price or skip a slot ahead.
    """
    payload = _build_payload(
        [
            (15, 0.02565),
            (16, 0.08040),
            (17, 0.11200),
        ],
    )
    last_fetch = datetime(2026, 5, 4, 13, 7, 21, tzinfo=UTC)
    coordinator = _make_epex_coordinator(payload, last_fetch=last_fetch)
    subentry = _make_subentry()
    sensor = EngieBeEpexNextHourSensor(coordinator, subentry, _EPEX_NEXT_HOUR)

    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_NOW_UTC,
    ):
        assert sensor.native_value == pytest.approx(0.08040)
        attrs = sensor.extra_state_attributes

    assert attrs["slot_start"] == "2026-05-04T16:00:00+02:00"
    assert attrs["slot_end"] == "2026-05-04T17:00:00+02:00"
    assert attrs["slot_duration_minutes"] == 60
    assert attrs["last_fetched"] == last_fetch.isoformat()


def test_next_hour_sensor_crosses_midnight_into_tomorrow() -> None:
    """
    ``next_hour`` must follow into tomorrow's slate when ``now+1h`` does.

    Late-evening anchor (23:30 Brussels) means ``now+1h`` is 00:30 the
    next day, so the sensor must surface tomorrow's 00:00 slot rather
    than fall back to ``None``.
    """
    payload = _build_payload(
        today=[(23, 0.07000)],
        tomorrow=[(0, 0.04500), (1, 0.04200)],
    )
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexNextHourSensor(coordinator, subentry, _EPEX_NEXT_HOUR)

    late_brussels = datetime(2026, 5, 4, 23, 30, 0, tzinfo=_BRUSSELS)
    late_utc = late_brussels.astimezone(UTC)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=late_utc,
    ):
        assert sensor.native_value == pytest.approx(0.04500)


def test_next_hour_sensor_returns_none_when_no_covering_slot() -> None:
    """
    No covering slot -> ``None`` value, empty attributes.

    If the cached EPEX payload is too old (or tomorrow's slate is not
    yet published in the late-evening window), ``native_value`` is
    ``None`` and attributes are empty so we don't surface a stale slot.
    """
    # Slots only cover 14:00 and 15:00; ``now+1h`` (16:30) falls outside.
    payload = _build_payload([(14, -0.0123), (15, 0.02565)])
    coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexNextHourSensor(coordinator, subentry, _EPEX_NEXT_HOUR)

    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_NOW_UTC,
    ):
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}


def test_next_hour_sensor_native_value_is_none_when_unavailable() -> None:
    """No payload -> native_value None (mirrors the current sensor)."""
    coordinator = _make_epex_coordinator(None)
    subentry = _make_subentry()
    sensor = EngieBeEpexNextHourSensor(coordinator, subentry, _EPEX_NEXT_HOUR)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


# =============================================================================
# QH Sensor Tests (Remediation for EPEX v2)
# =============================================================================


def test_build_epex_sensors_includes_qh_when_coordinator_provided() -> None:
    """QH sensor is added when epex_qh_coordinator is not None."""
    payload = _build_payload([(0, 0.1), (1, 0.2), (2, 0.3)])
    epex_coordinator = _make_epex_coordinator(payload)
    epex_qh_coordinator = _make_epex_qh_coordinator(_build_epex_payload_qh())
    subentry = _make_subentry()

    sensors = _build_epex_sensors(epex_coordinator, epex_qh_coordinator, subentry)

    keys = {s.entity_description.key for s in sensors}
    assert "epex_current_quarter_hour" in keys


def test_build_epex_sensors_excludes_qh_when_coordinator_none() -> None:
    """QH sensor is NOT added when epex_qh_coordinator is None."""
    payload = _build_payload([(0, 0.1), (1, 0.2)])
    epex_coordinator = _make_epex_coordinator(payload)
    subentry = _make_subentry()

    sensors = _build_epex_sensors(epex_coordinator, None, subentry)

    keys = {s.entity_description.key for s in sensors}
    assert "epex_current_quarter_hour" not in keys


def test_qh_sensor_uses_qh_coordinator() -> None:
    """QH sensor uses epex_qh_coordinator, not epex_coordinator."""
    payload_h = _build_payload([(0, 0.1), (1, 0.2)])
    payload_qh = _build_epex_payload_qh()
    epex_coordinator = _make_epex_coordinator(payload_h)
    epex_qh_coordinator = _make_epex_qh_coordinator(payload_qh)
    subentry = _make_subentry()

    sensors = _build_epex_sensors(epex_coordinator, epex_qh_coordinator, subentry)
    qh_sensors = [
        s for s in sensors if s.entity_description.key == "epex_current_quarter_hour"
    ]

    assert len(qh_sensors) == 1
    assert qh_sensors[0].coordinator is epex_qh_coordinator


def test_qh_sensor_with_none_payload() -> None:
    """QH sensor handles None payload gracefully."""
    epex_coordinator = _make_epex_coordinator(_build_payload([(0, 0.1)]))
    epex_qh_coordinator = _make_epex_qh_coordinator(None)
    subentry = _make_subentry()

    sensors = _build_epex_sensors(epex_coordinator, epex_qh_coordinator, subentry)
    qh_sensors = [
        s for s in sensors if s.entity_description.key == "epex_current_quarter_hour"
    ]

    assert len(qh_sensors) == 1
    assert qh_sensors[0].available is False


def test_qh_next_sensor_picks_slot_covering_now_plus_15min() -> None:
    """QH next sensor returns price for slot covering ``now + 15min``."""
    payload = _build_epex_payload_qh()
    coordinator = _make_epex_qh_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexNextQuarterHourSensor(
        coordinator, subentry, _EPEX_NEXT_QUARTER_HOUR
    )

    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_NOW_UTC,
    ):
        # _NOW_UTC is 2026-05-04T13:30:00+00:00
        # now + 15min = 13:45 UTC = 15:45 Brussels
        # The QH payload has slots at :00, :15, :30, :45 each hour
        # Slot covering 15:45 should be the 15:45-16:00 slot
        # In the payload, hour=15, minute=45 -> index i=15*4+3=63
        # value = 0.100 + (63 * 0.001) = 0.163
        expected_value = 0.100 + (63 * 0.001)
        assert sensor.native_value == pytest.approx(expected_value)
        attrs = sensor.extra_state_attributes
        assert attrs["slot_duration_minutes"] == 15


def test_qh_next_sensor_attributes() -> None:
    """QH next sensor extra state attributes include correct slot info."""
    payload = _build_epex_payload_qh()
    coordinator = _make_epex_qh_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexNextQuarterHourSensor(
        coordinator, subentry, _EPEX_NEXT_QUARTER_HOUR
    )

    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_NOW_UTC,
    ):
        attrs = sensor.extra_state_attributes
        assert "slot_start" in attrs
        assert "slot_end" in attrs
        assert attrs["slot_duration_minutes"] == 15
        assert "last_fetched" in attrs


def test_qh_current_sensor_slots_have_15min_duration() -> None:
    """QH current sensor today/tomorrow slots report 15-min duration."""
    payload = _build_epex_payload_qh()
    coordinator = _make_epex_qh_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexCurrentSensor(coordinator, subentry, _EPEX_CURRENT_QUARTER_HOUR)

    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_NOW_UTC,
    ):
        attrs = sensor.extra_state_attributes
        # Check today slots
        for slot_attrs in attrs.get("today", []):
            assert slot_attrs["slot_duration_minutes"] == 15
        # Check tomorrow slots
        for slot_attrs in attrs.get("tomorrow", []):
            assert slot_attrs["slot_duration_minutes"] == 15


def test_qh_low_today_sensor_selects_minimum_of_today_only() -> None:
    """QH low today sensor selects minimum of today's slots only."""
    payload = _build_epex_payload_qh()
    coordinator = _make_epex_qh_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexExtremaSensor(
        coordinator, subentry, _EPEX_LOW_TODAY_QUARTER_HOUR, mode="min"
    )

    with patch(
        "custom_components.engie_be.sensor.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        # The payload has distinct values: 0.100 + (i * 0.001) for i in 0..95
        # Today is 2026-05-04, all slots are on day 4
        # Minimum should be the first slot: i=0, value=0.100
        assert sensor.native_value == pytest.approx(0.100)
        attrs = sensor.extra_state_attributes
        assert attrs["slot_duration_minutes"] == 15


def test_qh_high_today_sensor_selects_maximum_of_today_only() -> None:
    """QH high today sensor selects maximum of today's slots only."""
    payload = _build_epex_payload_qh()
    coordinator = _make_epex_qh_coordinator(payload)
    subentry = _make_subentry()
    sensor = EngieBeEpexExtremaSensor(
        coordinator, subentry, _EPEX_HIGH_TODAY_QUARTER_HOUR, mode="max"
    )

    with patch(
        "custom_components.engie_be.sensor.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        # The payload has distinct values: 0.100 + (i * 0.001) for i in 0..95
        # Today is 2026-05-04, all slots are on day 4
        # Maximum should be the last slot: i=95, value=0.100 + (95 * 0.001) = 0.195
        assert sensor.native_value == pytest.approx(0.195)
        attrs = sensor.extra_state_attributes
        assert attrs["slot_duration_minutes"] == 15
