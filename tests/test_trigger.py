"""Tests for custom_components.engie_be.trigger."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.trigger import TriggerConfig
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SOLAR_SURPLUS_LEVELS,
    TOU_SLOT_CODES,
    TRANSLATION_KEY_AUTHENTICATION,
    TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER,
    TRANSLATION_KEY_EPEX_CURRENT,
    TRANSLATION_KEY_EPEX_HIGH_TODAY,
    TRANSLATION_KEY_EPEX_LOW_TODAY,
    TRANSLATION_KEY_EPEX_NEGATIVE,
    TRANSLATION_KEY_EPEX_NEXT_HOUR,
    TRANSLATION_KEY_HAPPY_HOURS_ACTIVE,
    TRANSLATION_KEY_SOLAR_SURPLUS_CURRENT,
    TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
    TRANSLATION_KEY_SOLAR_SURPLUS_NEXT_HOUR,
    TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL,
    TRANSLATION_KEY_TOU_INJECTION_SLOT,
    TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL,
    TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
)
from custom_components.engie_be.trigger import (
    _SOLAR_SURPLUS_BECAME_SCHEMA,
    _TOU_SLOT_BECAME_SCHEMA,
    TRIGGERS,
    AuthenticationLostTrigger,
    AuthenticationRestoredTrigger,
    CaptarPeakCrossedThresholdTrigger,
    CaptarPeakUpdatedTrigger,
    EpexBecameNegativeTrigger,
    EpexCurrentCrossedThresholdTrigger,
    EpexHighTodayUpdatedTrigger,
    EpexLowTodayUpdatedTrigger,
    EpexNextHourCrossedThresholdTrigger,
    EpexNoLongerNegativeTrigger,
    HappyHoursBecameActiveTrigger,
    HappyHoursBecameInactiveTrigger,
    InjectionBecameOptimalTrigger,
    InjectionNoLongerOptimalTrigger,
    InjectionSlotBecameTrigger,
    InjectionSlotChangedTrigger,
    OfftakeBecameOptimalTrigger,
    OfftakeNoLongerOptimalTrigger,
    OfftakeSlotBecameTrigger,
    OfftakeSlotChangedTrigger,
    SolarSurplusBecameTrigger,
    SolarSurplusCurrentCrossedThresholdTrigger,
    SolarSurplusLevelChangedTrigger,
    SolarSurplusNextHourCrossedThresholdTrigger,
    async_get_triggers,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_BAN = "000000000000"
_EAN = "541448820070000000"
_SUBENTRY_ID = "test_subentry_id"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _register_entity(  # noqa: PLR0913
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    platform: str,
    translation_key: str,
    entity_suffix: str,
    unique_id: str,
) -> str:
    """Register an entity in the entity registry and return its entity_id."""
    ent_reg = er.async_get(hass)
    suggested = f"engie_belgium_{_BAN}_{entity_suffix}"
    reg_entry = ent_reg.async_get_or_create(
        platform,
        DOMAIN,
        unique_id,
        config_entry=entry,
        suggested_object_id=suggested,
        translation_key=translation_key,
    )
    return reg_entry.entity_id


def _make_trigger_config(
    entity_id: str,
    options: dict | None = None,
) -> TriggerConfig:
    """Build a TriggerConfig targeting a single entity_id."""
    return TriggerConfig(
        key=f"{DOMAIN}.test",
        target={"entity_id": entity_id},
        options=options or {},
    )


def _make_threshold_options(threshold_type: str, value: float) -> dict:
    """
    Build options dict for a numerical threshold trigger.

    The ``value`` key maps to a ThresholdConfig dict with a ``number`` field,
    not a bare float. See ``ThresholdConfig.from_config``.
    """
    return {
        "threshold": {
            "type": threshold_type,
            "value": {"number": value},
        }
    }


def _make_run_action() -> tuple[MagicMock, list[dict]]:
    """Return a (run_action mock, fired_payloads list) pair."""
    fired: list[dict] = []

    mock = MagicMock()

    async def _coro(*_args: object, **_kwargs: object) -> None:
        pass

    def _run_action(
        extra_trigger_payload: dict,
        _description: str,
        _context: object = None,
    ) -> asyncio.Task:
        fired.append(extra_trigger_payload)
        loop = asyncio.get_event_loop()
        return loop.create_task(_coro())

    mock.side_effect = _run_action
    return mock, fired


async def _run_trigger(  # noqa: PLR0913
    hass: HomeAssistant,
    trigger_cls: type,
    entity_id: str,
    from_state: str,
    to_state: str,
    *,
    expected_fires: int,
    options: dict | None = None,
) -> None:
    """Attach trigger, transition state, verify fire count, then detach."""
    config = _make_trigger_config(entity_id, options)
    trigger = trigger_cls(hass, config)
    run_action, fired = _make_run_action()

    unsub = await trigger.async_attach_runner(run_action)
    try:
        hass.states.async_set(entity_id, from_state)
        await hass.async_block_till_done()
        hass.states.async_set(entity_id, to_state)
        await hass.async_block_till_done()
        assert len(fired) == expected_fires
    finally:
        unsub()


# ---------------------------------------------------------------------------
# Step 1 check: all base classes importable
# ---------------------------------------------------------------------------


def test_trigger_base_classes_importable() -> None:
    """All required HA trigger base classes are importable."""
    from homeassistant.helpers.trigger import (  # noqa: PLC0415
        EntityNumericalStateCrossedThresholdTriggerBase,
        EntityTargetStateTriggerBase,
        EntityTriggerBase,
        make_entity_numerical_state_crossed_threshold_trigger,
        make_entity_target_state_trigger,
    )

    assert EntityTriggerBase is not None
    assert EntityTargetStateTriggerBase is not None
    assert EntityNumericalStateCrossedThresholdTriggerBase is not None
    assert make_entity_target_state_trigger is not None
    assert make_entity_numerical_state_crossed_threshold_trigger is not None


# ---------------------------------------------------------------------------
# async_get_triggers
# ---------------------------------------------------------------------------


async def test_async_get_triggers_returns_all(hass: HomeAssistant) -> None:
    """async_get_triggers returns all expected trigger keys."""
    triggers = await async_get_triggers(hass)

    expected = {
        # Phase A binary transitions
        "epex_became_negative",
        "epex_no_longer_negative",
        "offtake_became_optimal",
        "offtake_no_longer_optimal",
        "injection_became_optimal",
        "injection_no_longer_optimal",
        "happy_hours_became_active",
        "happy_hours_became_inactive",
        "authentication_lost",
        "authentication_restored",
        # Phase A enum changed
        "solar_surplus_level_changed",
        "offtake_slot_changed",
        "injection_slot_changed",
        # Phase A enum became
        "solar_surplus_became",
        "offtake_slot_became",
        "injection_slot_became",
        # Phase B numerical
        "epex_current_crossed_threshold",
        "epex_next_hour_crossed_threshold",
        "solar_surplus_current_crossed_threshold",
        "solar_surplus_next_hour_crossed_threshold",
        "captar_peak_crossed_threshold",
        # Phase C value changed
        "captar_peak_updated",
        "epex_high_today_updated",
        "epex_low_today_updated",
    }
    assert set(triggers.keys()) == expected


async def test_async_get_triggers_matches_dict(hass: HomeAssistant) -> None:
    """async_get_triggers returns the module-level TRIGGERS dict."""
    triggers = await async_get_triggers(hass)
    assert triggers is TRIGGERS


def test_trigger_count_at_least_20() -> None:
    """TRIGGERS dict has at least 20 entries (plan done criteria)."""
    assert len(TRIGGERS) >= 20


# ---------------------------------------------------------------------------
# Phase A - Binary state-transition triggers
# ---------------------------------------------------------------------------


async def test_epex_became_negative_fires_on_off_to_on(
    hass: HomeAssistant,
) -> None:
    """EpexBecameNegativeTrigger fires when binary sensor goes off -> on."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
        entity_suffix="epex_negative",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative",
    )
    await _run_trigger(
        hass,
        EpexBecameNegativeTrigger,
        entity_id,
        "off",
        "on",
        expected_fires=1,
    )


async def test_epex_became_negative_does_not_fire_on_on_to_off(
    hass: HomeAssistant,
) -> None:
    """EpexBecameNegativeTrigger does not fire when binary sensor goes on -> off."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
        entity_suffix="epex_negative",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative",
    )
    await _run_trigger(
        hass,
        EpexBecameNegativeTrigger,
        entity_id,
        "on",
        "off",
        expected_fires=0,
    )


async def test_epex_became_negative_filters_wrong_translation_key(
    hass: HomeAssistant,
) -> None:
    """EpexBecameNegativeTrigger ignores entities with wrong translation_key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key="connectivity",
        entity_suffix="other_sensor",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_other",
    )
    await _run_trigger(
        hass,
        EpexBecameNegativeTrigger,
        entity_id,
        "off",
        "on",
        expected_fires=0,
    )


async def test_epex_no_longer_negative_fires_on_on_to_off(
    hass: HomeAssistant,
) -> None:
    """EpexNoLongerNegativeTrigger fires when binary sensor goes on -> off."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
        entity_suffix="epex_negative",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative",
    )
    await _run_trigger(
        hass,
        EpexNoLongerNegativeTrigger,
        entity_id,
        "on",
        "off",
        expected_fires=1,
    )


async def test_epex_no_longer_negative_does_not_fire_on_off_to_on(
    hass: HomeAssistant,
) -> None:
    """EpexNoLongerNegativeTrigger does not fire when binary sensor goes off -> on."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
        entity_suffix="epex_negative",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative",
    )
    await _run_trigger(
        hass,
        EpexNoLongerNegativeTrigger,
        entity_id,
        "off",
        "on",
        expected_fires=0,
    )


async def test_offtake_became_optimal_fires(hass: HomeAssistant) -> None:
    """OfftakeBecameOptimalTrigger fires on off -> on transition."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL,
        entity_suffix="tou_offtake_is_optimal",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_offtake_optimal",
    )
    await _run_trigger(
        hass,
        OfftakeBecameOptimalTrigger,
        entity_id,
        "off",
        "on",
        expected_fires=1,
    )


async def test_offtake_no_longer_optimal_fires(hass: HomeAssistant) -> None:
    """OfftakeNoLongerOptimalTrigger fires on on -> off transition."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL,
        entity_suffix="tou_offtake_is_optimal",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_offtake_optimal",
    )
    await _run_trigger(
        hass,
        OfftakeNoLongerOptimalTrigger,
        entity_id,
        "on",
        "off",
        expected_fires=1,
    )


async def test_injection_became_optimal_fires(hass: HomeAssistant) -> None:
    """InjectionBecameOptimalTrigger fires on off -> on transition."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL,
        entity_suffix="tou_injection_is_optimal",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_injection_optimal",
    )
    await _run_trigger(
        hass,
        InjectionBecameOptimalTrigger,
        entity_id,
        "off",
        "on",
        expected_fires=1,
    )


async def test_injection_no_longer_optimal_fires(hass: HomeAssistant) -> None:
    """InjectionNoLongerOptimalTrigger fires on on -> off transition."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL,
        entity_suffix="tou_injection_is_optimal",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_injection_optimal",
    )
    await _run_trigger(
        hass,
        InjectionNoLongerOptimalTrigger,
        entity_id,
        "on",
        "off",
        expected_fires=1,
    )


async def test_happy_hours_became_active_fires(hass: HomeAssistant) -> None:
    """HappyHoursBecameActiveTrigger fires on off -> on transition."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_HAPPY_HOURS_ACTIVE,
        entity_suffix="happy_hours_active",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_hh_active",
    )
    await _run_trigger(
        hass,
        HappyHoursBecameActiveTrigger,
        entity_id,
        "off",
        "on",
        expected_fires=1,
    )


async def test_happy_hours_became_inactive_fires(hass: HomeAssistant) -> None:
    """HappyHoursBecameInactiveTrigger fires on on -> off transition."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_HAPPY_HOURS_ACTIVE,
        entity_suffix="happy_hours_active",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_hh_active",
    )
    await _run_trigger(
        hass,
        HappyHoursBecameInactiveTrigger,
        entity_id,
        "on",
        "off",
        expected_fires=1,
    )


async def test_authentication_lost_fires(hass: HomeAssistant) -> None:
    """AuthenticationLostTrigger fires on on -> off transition."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_AUTHENTICATION,
        entity_suffix="authentication",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_auth",
    )
    await _run_trigger(
        hass,
        AuthenticationLostTrigger,
        entity_id,
        "on",
        "off",
        expected_fires=1,
    )


async def test_authentication_restored_fires(hass: HomeAssistant) -> None:
    """AuthenticationRestoredTrigger fires on off -> on transition."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_AUTHENTICATION,
        entity_suffix="authentication",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_auth",
    )
    await _run_trigger(
        hass,
        AuthenticationRestoredTrigger,
        entity_id,
        "off",
        "on",
        expected_fires=1,
    )


# ---------------------------------------------------------------------------
# Phase A - Enum "changed" triggers
# ---------------------------------------------------------------------------


async def test_solar_surplus_level_changed_fires_on_any_change(
    hass: HomeAssistant,
) -> None:
    """SolarSurplusLevelChangedTrigger fires when surplus level changes to any value."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
        entity_suffix=f"{_EAN}_solar_surplus_forecast",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_solar_surplus",
    )
    await _run_trigger(
        hass,
        SolarSurplusLevelChangedTrigger,
        entity_id,
        "no_surplus",
        "high_surplus",
        expected_fires=1,
    )


async def test_solar_surplus_level_changed_filters_wrong_key(
    hass: HomeAssistant,
) -> None:
    """SolarSurplusLevelChangedTrigger ignores sensors with wrong translation_key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )
    await _run_trigger(
        hass,
        SolarSurplusLevelChangedTrigger,
        entity_id,
        "no_surplus",
        "high_surplus",
        expected_fires=0,
    )


async def test_offtake_slot_changed_fires_on_any_change(
    hass: HomeAssistant,
) -> None:
    """OfftakeSlotChangedTrigger fires when offtake slot changes to any value."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )
    await _run_trigger(
        hass,
        OfftakeSlotChangedTrigger,
        entity_id,
        "peak",
        "offpeak",
        expected_fires=1,
    )


async def test_offtake_slot_changed_filters_injection_key(
    hass: HomeAssistant,
) -> None:
    """OfftakeSlotChangedTrigger ignores injection slot entities."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_SLOT,
        entity_suffix=f"{_EAN}_injection_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_injection_slot",
    )
    await _run_trigger(
        hass,
        OfftakeSlotChangedTrigger,
        entity_id,
        "peak",
        "offpeak",
        expected_fires=0,
    )


async def test_injection_slot_changed_fires_on_any_change(
    hass: HomeAssistant,
) -> None:
    """InjectionSlotChangedTrigger fires when injection slot changes to any value."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_SLOT,
        entity_suffix=f"{_EAN}_injection_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_injection_slot",
    )
    await _run_trigger(
        hass,
        InjectionSlotChangedTrigger,
        entity_id,
        "offpeak",
        "peak",
        expected_fires=1,
    )


# ---------------------------------------------------------------------------
# Phase A - Enum "became" triggers
# ---------------------------------------------------------------------------


async def test_solar_surplus_became_fires_on_match(hass: HomeAssistant) -> None:
    """SolarSurplusBecameTrigger fires when surplus reaches the chosen level."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
        entity_suffix=f"{_EAN}_solar_surplus_forecast",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_solar_surplus",
    )
    await _run_trigger(
        hass,
        SolarSurplusBecameTrigger,
        entity_id,
        "no_surplus",
        "high_surplus",
        expected_fires=1,
        options={"level": "high_surplus"},
    )


async def test_solar_surplus_became_does_not_fire_on_mismatch(
    hass: HomeAssistant,
) -> None:
    """SolarSurplusBecameTrigger does not fire when surplus reaches different level."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
        entity_suffix=f"{_EAN}_solar_surplus_forecast",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_solar_surplus",
    )
    await _run_trigger(
        hass,
        SolarSurplusBecameTrigger,
        entity_id,
        "no_surplus",
        "low_surplus",
        expected_fires=0,
        options={"level": "high_surplus"},
    )


@pytest.mark.parametrize("level", SOLAR_SURPLUS_LEVELS)
async def test_solar_surplus_became_each_level(hass: HomeAssistant, level: str) -> None:
    """SolarSurplusBecameTrigger works for every valid surplus level."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
        entity_suffix=f"{_EAN}_solar_surplus_{level}",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_ss_{level}",
    )
    other = "no_data" if level != "no_data" else "no_surplus"
    await _run_trigger(
        hass,
        SolarSurplusBecameTrigger,
        entity_id,
        other,
        level,
        expected_fires=1,
        options={"level": level},
    )


async def test_offtake_slot_became_fires_on_match(hass: HomeAssistant) -> None:
    """OfftakeSlotBecameTrigger fires when offtake slot enters the chosen slot."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )
    await _run_trigger(
        hass,
        OfftakeSlotBecameTrigger,
        entity_id,
        "peak",
        "offpeak",
        expected_fires=1,
        options={"slot": "offpeak"},
    )


async def test_offtake_slot_became_does_not_fire_on_mismatch(
    hass: HomeAssistant,
) -> None:
    """OfftakeSlotBecameTrigger does not fire when slot becomes a different value."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )
    await _run_trigger(
        hass,
        OfftakeSlotBecameTrigger,
        entity_id,
        "peak",
        "offpeak",
        expected_fires=0,
        options={"slot": "peak"},
    )


@pytest.mark.parametrize("slot", TOU_SLOT_CODES)
async def test_offtake_slot_became_each_slot(hass: HomeAssistant, slot: str) -> None:
    """OfftakeSlotBecameTrigger works for every valid TOU slot code."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_{slot}",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_{slot}",
    )
    other = "peak" if slot != "peak" else "offpeak"
    await _run_trigger(
        hass,
        OfftakeSlotBecameTrigger,
        entity_id,
        other,
        slot,
        expected_fires=1,
        options={"slot": slot},
    )


async def test_injection_slot_became_fires_on_match(hass: HomeAssistant) -> None:
    """InjectionSlotBecameTrigger fires when injection slot enters the chosen slot."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_SLOT,
        entity_suffix=f"{_EAN}_injection_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_injection_slot",
    )
    await _run_trigger(
        hass,
        InjectionSlotBecameTrigger,
        entity_id,
        "offpeak",
        "peak",
        expected_fires=1,
        options={"slot": "peak"},
    )


async def test_injection_slot_became_rejects_offtake_entity(
    hass: HomeAssistant,
) -> None:
    """InjectionSlotBecameTrigger ignores tou_offtake_slot entities."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )
    await _run_trigger(
        hass,
        InjectionSlotBecameTrigger,
        entity_id,
        "offpeak",
        "peak",
        expected_fires=0,
        options={"slot": "peak"},
    )


# ---------------------------------------------------------------------------
# Phase A - Schema validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schema", "options"),
    [
        (_SOLAR_SURPLUS_BECAME_SCHEMA, {"level": "not_a_level"}),
        (_TOU_SLOT_BECAME_SCHEMA, {"slot": "not_a_slot"}),
    ],
)
def test_schema_rejects_invalid_option(
    schema: vol.Schema, options: dict[str, str]
) -> None:
    """Schemas raise vol.Invalid when an unknown level or slot value is passed."""
    with pytest.raises(vol.Invalid):
        schema(
            {
                "trigger": f"{DOMAIN}.test",
                "entity_id": "sensor.foo",
                "options": options,
            }
        )


# ---------------------------------------------------------------------------
# Phase B - Numerical threshold triggers
# ---------------------------------------------------------------------------


async def test_epex_current_crossed_threshold_fires_above(
    hass: HomeAssistant,
) -> None:
    """EpexCurrentCrossedThresholdTrigger fires when price crosses above threshold."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT,
        entity_suffix="epex_current",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current",
    )
    await _run_trigger(
        hass,
        EpexCurrentCrossedThresholdTrigger,
        entity_id,
        "0.05",
        "0.15",
        expected_fires=1,
        options=_make_threshold_options("above", 0.10),
    )


async def test_epex_current_crossed_threshold_does_not_fire_when_already_above(
    hass: HomeAssistant,
) -> None:
    """EpexCurrentCrossedThresholdTrigger does not fire if already above threshold."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT,
        entity_suffix="epex_current",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current",
    )
    await _run_trigger(
        hass,
        EpexCurrentCrossedThresholdTrigger,
        entity_id,
        "0.15",
        "0.20",
        expected_fires=0,
        options=_make_threshold_options("above", 0.10),
    )


async def test_epex_current_crossed_threshold_filters_wrong_key(
    hass: HomeAssistant,
) -> None:
    """EpexCurrentCrossedThresholdTrigger ignores entities with wrong key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEXT_HOUR,
        entity_suffix="epex_next_hour",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_next_hour",
    )
    await _run_trigger(
        hass,
        EpexCurrentCrossedThresholdTrigger,
        entity_id,
        "0.05",
        "0.15",
        expected_fires=0,
        options=_make_threshold_options("above", 0.10),
    )


async def test_epex_next_hour_crossed_threshold_fires_below(
    hass: HomeAssistant,
) -> None:
    """EpexNextHourCrossedThresholdTrigger fires when price crosses below threshold."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEXT_HOUR,
        entity_suffix="epex_next_hour",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_next_hour",
    )
    await _run_trigger(
        hass,
        EpexNextHourCrossedThresholdTrigger,
        entity_id,
        "0.15",
        "0.05",
        expected_fires=1,
        options=_make_threshold_options("below", 0.10),
    )


async def test_solar_surplus_current_crossed_threshold_fires(
    hass: HomeAssistant,
) -> None:
    """SolarSurplusCurrentCrossedThresholdTrigger fires on threshold crossing."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_SOLAR_SURPLUS_CURRENT,
        entity_suffix="solar_surplus_current",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_ss_current",
    )
    await _run_trigger(
        hass,
        SolarSurplusCurrentCrossedThresholdTrigger,
        entity_id,
        "0.5",
        "2.0",
        expected_fires=1,
        options=_make_threshold_options("above", 1.0),
    )


async def test_solar_surplus_next_hour_crossed_threshold_fires(
    hass: HomeAssistant,
) -> None:
    """SolarSurplusNextHourCrossedThresholdTrigger fires on threshold crossing."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_SOLAR_SURPLUS_NEXT_HOUR,
        entity_suffix="solar_surplus_next_hour",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_ss_next_hour",
    )
    await _run_trigger(
        hass,
        SolarSurplusNextHourCrossedThresholdTrigger,
        entity_id,
        "0.5",
        "2.0",
        expected_fires=1,
        options=_make_threshold_options("above", 1.0),
    )


async def test_captar_peak_crossed_threshold_fires(hass: HomeAssistant) -> None:
    """CaptarPeakCrossedThresholdTrigger fires when captar peak crosses threshold."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER,
        entity_suffix="captar_monthly_peak_power",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_captar_peak",
    )
    await _run_trigger(
        hass,
        CaptarPeakCrossedThresholdTrigger,
        entity_id,
        "3.0",
        "6.0",
        expected_fires=1,
        options=_make_threshold_options("above", 5.0),
    )


async def test_captar_peak_crossed_threshold_filters_wrong_key(
    hass: HomeAssistant,
) -> None:
    """CaptarPeakCrossedThresholdTrigger ignores sensors with wrong translation_key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT,
        entity_suffix="epex_current",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current",
    )
    await _run_trigger(
        hass,
        CaptarPeakCrossedThresholdTrigger,
        entity_id,
        "3.0",
        "6.0",
        expected_fires=0,
        options=_make_threshold_options("above", 5.0),
    )


# ---------------------------------------------------------------------------
# Phase C - Value-changed triggers
# ---------------------------------------------------------------------------


async def test_captar_peak_updated_fires_on_any_change(hass: HomeAssistant) -> None:
    """CaptarPeakUpdatedTrigger fires when captar peak power value changes."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER,
        entity_suffix="captar_monthly_peak_power",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_captar_peak",
    )
    await _run_trigger(
        hass,
        CaptarPeakUpdatedTrigger,
        entity_id,
        "4.5",
        "5.2",
        expected_fires=1,
    )


async def test_captar_peak_updated_filters_wrong_key(hass: HomeAssistant) -> None:
    """CaptarPeakUpdatedTrigger ignores sensors with wrong translation_key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_HIGH_TODAY,
        entity_suffix="epex_high_today",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_high",
    )
    await _run_trigger(
        hass,
        CaptarPeakUpdatedTrigger,
        entity_id,
        "0.20",
        "0.25",
        expected_fires=0,
    )


async def test_epex_high_today_updated_fires_on_any_change(
    hass: HomeAssistant,
) -> None:
    """EpexHighTodayUpdatedTrigger fires when EPEX highest price today changes."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_HIGH_TODAY,
        entity_suffix="epex_high_today",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_high",
    )
    await _run_trigger(
        hass,
        EpexHighTodayUpdatedTrigger,
        entity_id,
        "0.20",
        "0.25",
        expected_fires=1,
    )


async def test_epex_high_today_updated_filters_wrong_key(
    hass: HomeAssistant,
) -> None:
    """EpexHighTodayUpdatedTrigger ignores sensors with wrong translation_key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_LOW_TODAY,
        entity_suffix="epex_low_today",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_low",
    )
    await _run_trigger(
        hass,
        EpexHighTodayUpdatedTrigger,
        entity_id,
        "0.05",
        "0.08",
        expected_fires=0,
    )


async def test_epex_low_today_updated_fires_on_any_change(
    hass: HomeAssistant,
) -> None:
    """EpexLowTodayUpdatedTrigger fires when EPEX lowest price today changes."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_LOW_TODAY,
        entity_suffix="epex_low_today",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_low",
    )
    await _run_trigger(
        hass,
        EpexLowTodayUpdatedTrigger,
        entity_id,
        "0.03",
        "0.01",
        expected_fires=1,
    )


async def test_epex_low_today_updated_filters_wrong_key(
    hass: HomeAssistant,
) -> None:
    """EpexLowTodayUpdatedTrigger ignores sensors with wrong translation_key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER,
        entity_suffix="captar_monthly_peak_power",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_captar_peak",
    )
    await _run_trigger(
        hass,
        EpexLowTodayUpdatedTrigger,
        entity_id,
        "4.0",
        "4.5",
        expected_fires=0,
    )
