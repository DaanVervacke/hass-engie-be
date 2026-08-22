"""
End-to-end seam: wire /tou-schedules payload → sensor state → trigger/condition.

Existing trigger and condition tests set state directly with
``hass.states.async_set``. They cover the automation-surface logic but
would not catch a normalization regression that dropped or misrouted a
wire code. This module drives raw wire-shape payloads (direction-prefixed
supplier codes, aliased ``HIGH_LOAD_HOURS``, TOTAL_HOURS) through
``normalize_tou_payload`` and the slot sensor to prove the derived state
still lands as an option that the five triggers and two conditions accept.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.condition import ConditionConfig
from homeassistant.helpers.trigger import TriggerConfig
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be._tou import normalize_tou_payload
from custom_components.engie_be._tou_calendar import format_tou_event_summary
from custom_components.engie_be.condition import (
    InjectionSlotIsCondition,
    OfftakeSlotIsCondition,
)
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
    TRANSLATION_KEY_TOU_INJECTION_SLOT,
    TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
)
from custom_components.engie_be.sensor import (
    _TOU_INJECTION_SLOT,
    _TOU_OFFTAKE_SLOT,
    EngieBeTouSlotSensor,
)
from custom_components.engie_be.trigger import (
    InjectionSlotBecameTrigger,
    InjectionSlotChangedTrigger,
    OfftakeSlotBecameTrigger,
    OfftakeSlotChangedTrigger,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

_BAN = "000000000000"
_EAN = "541448820070000000"
_EAN_ID = f"{_EAN}_ID1"


def _all_day(slot_code: str, cost: int) -> list[dict[str, Any]]:
    """Return a single-slot weekday list covering the full day."""
    return [
        {
            "startTime": "00:00:00",
            "endTime": "00:00:00",
            "slotCode": slot_code,
            "costIndicator": cost,
        }
    ]


_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def _direction_block(slot_code: str, cost: int = 2) -> dict[str, Any]:
    """Fill every weekday with the same slot code so the current slot is fixed."""
    return {day: _all_day(slot_code, cost) for day in _WEEKDAYS}


def _wire_payload(offtake_code: str, injection_code: str) -> dict[str, Any]:
    """
    Return a raw wire-shape /tou-schedules response.

    ``offtake_code`` and ``injection_code`` are the RAW slot codes as they
    would arrive from ENGIE: direction-prefixed for the supplier product
    (``S_TOU1_OFFTAKE_PEAK``), aliased (``HIGH_LOAD_HOURS``), or bare
    (``TOTAL_HOURS``). This is the shape the coordinator hands to
    ``normalize_tou_payload``.
    """
    return {
        "items": [
            {
                "eanWithSuffix": _EAN_ID,
                "gridMeterTimeOfUseSchedules": [
                    {
                        "gridMeterNumber": "1SAG0000000000",
                        "exclusiveNightMeter": False,
                        "supplierSchedule": {
                            "activeConfigurationId": "TOU001",
                            "offtake": _direction_block(offtake_code, cost=3),
                            "injection": _direction_block(injection_code, cost=1),
                        },
                    }
                ],
            }
        ]
    }


def _wrap(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a canonical payload in the coordinator storage shape."""
    return {
        "tou_schedules": {
            "data": payload,
            "fetched_at": "2026-07-08T10:00:00+00:00",
        }
    }


def _make_subentry() -> MagicMock:
    """Build a MagicMock ConfigSubentry."""
    subentry = MagicMock()
    subentry.subentry_id = "sub_abc"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {CONF_BUSINESS_AGREEMENT_NUMBER: _BAN}
    return subentry


def _make_coordinator(data: object) -> MagicMock:
    """Build a MagicMock coordinator carrying the given ``.data``."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry"
    return coordinator


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a minimal config entry added to hass."""
    entry: MockConfigEntry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        title="user@example.com",
        data={
            "username": "user@example.com",
            "password": "hunter2",
            CONF_ACCESS_TOKEN: "fake_access",
            CONF_REFRESH_TOKEN: "fake_refresh",
        },
        unique_id="user_example_com_test",
    )
    entry.add_to_hass(hass)
    return entry


def _register_slot_entity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    direction: str,
) -> str:
    """Register the offtake or injection slot sensor and return its entity_id."""
    translation_key = (
        TRANSLATION_KEY_TOU_OFFTAKE_SLOT
        if direction == "offtake"
        else TRANSLATION_KEY_TOU_INJECTION_SLOT
    )
    ent_reg = er.async_get(hass)
    reg_entry = ent_reg.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{entry.entry_id}_sub_abc_{_EAN}_{direction}_slot",
        config_entry=entry,
        suggested_object_id=f"engie_belgium_{_BAN}_{_EAN}_{direction}_slot",
        translation_key=translation_key,
    )
    return reg_entry.entity_id


def _derive_state(payload: dict[str, Any], direction: str) -> str:
    """Push the wire payload through the sensor and return its native_value."""
    description = _TOU_OFFTAKE_SLOT if direction == "offtake" else _TOU_INJECTION_SLOT
    sensor = EngieBeTouSlotSensor(
        _make_coordinator(_wrap(normalize_tou_payload(payload))),
        _make_subentry(),
        description,
        ean=_EAN,
        direction=direction,
    )
    value = sensor.native_value
    assert value is not None, "sensor produced no state for the wire payload"
    return value


def _make_run_action() -> tuple[MagicMock, list[dict]]:
    """Return a (run_action mock, fired list) pair for trigger assertions."""
    fired: list[dict] = []

    async def _coro(*_a: object, **_kw: object) -> None:
        return None

    mock = MagicMock()

    def _run(extra: dict, _desc: str, _ctx: object = None) -> asyncio.Task:
        fired.append(extra)
        return asyncio.get_event_loop().create_task(_coro())

    mock.side_effect = _run
    return mock, fired


# ---------------------------------------------------------------------------
# Sensor seam
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wire_code", "expected"),
    [
        ("S_TOU1_OFFTAKE_PEAK", "peak"),
        ("HIGH_LOAD_HOURS", "peak"),
        ("LOW_LOAD_HOURS", "offpeak"),
        ("TOTAL_HOURS", "total_hours"),
    ],
)
def test_wire_code_reaches_offtake_sensor_state(
    freezer: FrozenDateTimeFactory,
    wire_code: str,
    expected: str,
) -> None:
    """Prefixed, aliased and bare wire codes all normalise to a valid state."""
    freezer.move_to("2026-07-08T10:00:00Z")
    payload = _wire_payload(wire_code, "S_TOU1_INJECTION_PEAK")
    assert _derive_state(payload, "offtake") == expected


@pytest.mark.parametrize(
    ("wire_code", "expected"),
    [
        ("S_TOU1_INJECTION_PEAK", "peak"),
        ("HIGH_LOAD_HOURS", "peak"),
        ("TOTAL_HOURS", "total_hours"),
    ],
)
def test_wire_code_reaches_injection_sensor_state(
    freezer: FrozenDateTimeFactory,
    wire_code: str,
    expected: str,
) -> None:
    """Injection direction normalises the same way as offtake."""
    freezer.move_to("2026-07-08T10:00:00Z")
    payload = _wire_payload("S_TOU1_OFFTAKE_PEAK", wire_code)
    assert _derive_state(payload, "injection") == expected


# ---------------------------------------------------------------------------
# Trigger seam
# ---------------------------------------------------------------------------


async def _drive_slot_trigger(  # noqa: PLR0913 - test seam wiring
    hass: HomeAssistant,
    *,
    direction: str,
    trigger_cls: type,
    from_wire: str,
    to_wire: str,
    options: dict[str, Any] | None,
) -> int:
    """Push two wire payloads through, transition sensor state, count fires."""
    entry = _make_entry(hass)
    entity_id = _register_slot_entity(hass, entry, direction=direction)

    if direction == "offtake":
        from_payload = _wire_payload(from_wire, "S_TOU1_INJECTION_PEAK")
        to_payload = _wire_payload(to_wire, "S_TOU1_INJECTION_PEAK")
    else:
        from_payload = _wire_payload("S_TOU1_OFFTAKE_PEAK", from_wire)
        to_payload = _wire_payload("S_TOU1_OFFTAKE_PEAK", to_wire)

    from_state = _derive_state(from_payload, direction)
    to_state = _derive_state(to_payload, direction)

    config = TriggerConfig(
        key=f"{DOMAIN}.test",
        target={"entity_id": entity_id},
        options=options or {},
    )
    trigger = trigger_cls(hass, config)
    run_action, fired = _make_run_action()

    unsub = await trigger.async_attach_runner(run_action)
    try:
        hass.states.async_set(entity_id, from_state)
        await hass.async_block_till_done()
        hass.states.async_set(entity_id, to_state)
        await hass.async_block_till_done()
    finally:
        unsub()
    return len(fired)


async def test_offtake_slot_changed_fires_on_wire_transition(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Offtake changed fires when the raw wire code changes between refreshes."""
    freezer.move_to("2026-07-08T10:00:00Z")
    fires = await _drive_slot_trigger(
        hass,
        direction="offtake",
        trigger_cls=OfftakeSlotChangedTrigger,
        from_wire="S_TOU1_OFFTAKE_PEAK",
        to_wire="S_TOU1_OFFTAKE_OFFPEAK",
        options=None,
    )
    assert fires == 1


async def test_injection_slot_changed_fires_on_wire_transition(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Injection changed fires when the raw wire code changes between refreshes."""
    freezer.move_to("2026-07-08T10:00:00Z")
    fires = await _drive_slot_trigger(
        hass,
        direction="injection",
        trigger_cls=InjectionSlotChangedTrigger,
        from_wire="S_TOU1_INJECTION_OFFPEAK",
        to_wire="S_TOU1_INJECTION_PEAK",
        options=None,
    )
    assert fires == 1


async def test_offtake_slot_became_fires_from_aliased_wire_code(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An aliased HIGH_LOAD_HOURS payload reaches offtake_slot_became as ``peak``."""
    freezer.move_to("2026-07-08T10:00:00Z")
    fires = await _drive_slot_trigger(
        hass,
        direction="offtake",
        trigger_cls=OfftakeSlotBecameTrigger,
        from_wire="LOW_LOAD_HOURS",
        to_wire="HIGH_LOAD_HOURS",
        options={"slot": "peak"},
    )
    assert fires == 1


async def test_injection_slot_became_fires_on_total_hours(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A TOTAL_HOURS payload reaches injection_slot_became as TOTAL_HOURS."""
    freezer.move_to("2026-07-08T10:00:00Z")
    fires = await _drive_slot_trigger(
        hass,
        direction="injection",
        trigger_cls=InjectionSlotBecameTrigger,
        from_wire="S_TOU1_INJECTION_PEAK",
        to_wire="TOTAL_HOURS",
        options={"slot": "total_hours"},
    )
    assert fires == 1


# ---------------------------------------------------------------------------
# Condition seam
# ---------------------------------------------------------------------------


async def _run_slot_condition(
    hass: HomeAssistant,
    *,
    direction: str,
    condition_cls: type,
    wire_code: str,
    slot_option: str,
) -> bool:
    """Push a wire payload through, set the state, evaluate the condition."""
    entry = _make_entry(hass)
    entity_id = _register_slot_entity(hass, entry, direction=direction)
    payload = (
        _wire_payload(wire_code, "S_TOU1_INJECTION_PEAK")
        if direction == "offtake"
        else _wire_payload("S_TOU1_OFFTAKE_PEAK", wire_code)
    )
    state = _derive_state(payload, direction)
    hass.states.async_set(entity_id, state)
    condition = condition_cls(
        hass,
        ConditionConfig(
            target={"entity_id": entity_id},
            options={"behavior": "any", "slot": slot_option},
        ),
    )
    await condition.async_setup()
    try:
        return condition(hass)
    finally:
        condition.async_unload()


async def test_offtake_slot_is_matches_prefixed_wire_code(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """offtake_slot_is matches when a direction-prefixed code lands as peak."""
    freezer.move_to("2026-07-08T10:00:00Z")
    assert (
        await _run_slot_condition(
            hass,
            direction="offtake",
            condition_cls=OfftakeSlotIsCondition,
            wire_code="S_TOU1_OFFTAKE_PEAK",
            slot_option="peak",
        )
        is True
    )


async def test_injection_slot_is_matches_aliased_wire_code(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """injection_slot_is matches when an aliased HIGH_LOAD_HOURS lands as peak."""
    freezer.move_to("2026-07-08T10:00:00Z")
    assert (
        await _run_slot_condition(
            hass,
            direction="injection",
            condition_cls=InjectionSlotIsCondition,
            wire_code="HIGH_LOAD_HOURS",
            slot_option="peak",
        )
        is True
    )


# ---------------------------------------------------------------------------
# Calendar-summary seam (tou_slot_started)
# ---------------------------------------------------------------------------


def test_format_tou_event_summary_matches_normalised_code() -> None:
    """
    A wire-code payload produces the same event summary the trigger matches.

    ``TouSlotStartedTrigger`` compares ``event.summary`` against
    ``format_tou_event_summary(slot_option, direction)``. The calendar
    provider passes the already-normalised code to that formatter, so a
    wire-shape payload whose supplier code is ``S_TOU1_OFFTAKE_PEAK`` must
    end up as ``Peak (offtake)`` — the same string
    ``TouSlotStartedTrigger`` produces from ``{"slot": "peak"}``.
    """
    payload = normalize_tou_payload(
        _wire_payload("S_TOU1_OFFTAKE_PEAK", "S_TOU1_INJECTION_PEAK")
    )
    supplier = payload["items"][0]["supplierSchedule"]
    offtake_monday_code = supplier["offtake"]["monday"][0]["slotCode"]
    injection_monday_code = supplier["injection"]["monday"][0]["slotCode"]

    assert format_tou_event_summary(offtake_monday_code, "offtake") == "Peak (offtake)"
    assert (
        format_tou_event_summary(injection_monday_code, "injection")
        == "Peak (injection)"
    )
    assert format_tou_event_summary("peak", "offtake") == format_tou_event_summary(
        offtake_monday_code, "offtake"
    )
