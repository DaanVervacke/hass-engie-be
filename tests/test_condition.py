"""Tests for custom_components.engie_be.condition."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import voluptuous as vol
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.condition import ConditionConfig
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.condition import (
    _SOLAR_SURPLUS_SCHEMA,
    _TOU_SLOT_SCHEMA,
    CONDITIONS,
    CaptarPeakIsAboveThresholdCondition,
    EpexPriceIsAboveThresholdCondition,
    EpexPriceIsAboveThresholdQuarterHourCondition,
    EpexPriceIsBelowThresholdCondition,
    EpexPriceIsBelowThresholdQuarterHourCondition,
    EpexPriceIsNegativeCondition,
    EpexPriceIsNegativeQuarterHourCondition,
    HappyHoursIsActiveCondition,
    InjectionIsOptimalCondition,
    InjectionSlotIsCondition,
    OfftakeIsOptimalCondition,
    OfftakeSlotIsCondition,
    SolarSurplusIsAtLevelCondition,
    async_get_conditions,
)
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SOLAR_SURPLUS_LEVELS,
    TOU_SLOT_CODES,
    TRANSLATION_KEY_AUTHENTICATION,
    TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER,
    TRANSLATION_KEY_EPEX_CURRENT,
    TRANSLATION_KEY_EPEX_CURRENT_QUARTER_HOUR,
    TRANSLATION_KEY_EPEX_NEGATIVE,
    TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR,
    TRANSLATION_KEY_HAPPY_HOURS_ACTIVE,
    TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
    TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL,
    TRANSLATION_KEY_TOU_INJECTION_SLOT,
    TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL,
    TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
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


def _make_config(
    entity_id: str,
    options: dict | None = None,
) -> ConditionConfig:
    """Build a ConditionConfig targeting a single entity_id."""
    return ConditionConfig(
        target={"entity_id": entity_id},
        options={"behavior": "any", **(options or {})},
    )


async def _run_condition(
    hass: HomeAssistant,
    condition_cls: type,
    entity_id: str,
    options: dict | None,
    *,
    expected: bool,
) -> None:
    """Register state, build condition, assert result, then unload."""
    condition = condition_cls(hass, _make_config(entity_id, options))
    await condition.async_setup()
    assert condition(hass) is expected
    condition.async_unload()


# ---------------------------------------------------------------------------
# Step 1 check: ABCs importable
# ---------------------------------------------------------------------------


def test_abcs_importable() -> None:
    """EntityStateConditionBase and make_entity_state_condition are importable."""
    from homeassistant.helpers.condition import (  # noqa: PLC0415
        EntityStateConditionBase,
        make_entity_state_condition,
    )

    assert EntityStateConditionBase is not None
    assert make_entity_state_condition is not None


# ---------------------------------------------------------------------------
# async_get_conditions
# ---------------------------------------------------------------------------


async def test_async_get_conditions_returns_all_thirteen(hass: HomeAssistant) -> None:
    """async_get_conditions returns all thirteen condition names."""
    conditions = await async_get_conditions(hass)

    assert set(conditions.keys()) == {
        # Original four
        "epex_price_is_negative",
        "solar_surplus_is_at_level",
        "offtake_slot_is",
        "injection_slot_is",
        # Phase D additions
        "epex_price_is_below_threshold",
        "epex_price_is_above_threshold",
        "offtake_is_optimal",
        "injection_is_optimal",
        "happy_hours_is_active",
        "captar_peak_is_above_threshold",
        # Quarter-hourly additions
        "epex_price_is_negative_quarter_hour",
        "epex_price_is_below_threshold_quarter_hour",
        "epex_price_is_above_threshold_quarter_hour",
    }


async def test_async_get_conditions_matches_conditions_dict(
    hass: HomeAssistant,
) -> None:
    """async_get_conditions returns the module-level CONDITIONS dict."""
    conditions = await async_get_conditions(hass)

    assert conditions is CONDITIONS


# ---------------------------------------------------------------------------
# epex_price_is_negative
# ---------------------------------------------------------------------------


async def test_epex_negative_true_when_on(hass: HomeAssistant) -> None:
    """EpexPriceIsNegativeCondition is True when binary sensor state is 'on'."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
        entity_suffix="epex_negative",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative",
    )
    hass.states.async_set(entity_id, "on")
    await _run_condition(
        hass, EpexPriceIsNegativeCondition, entity_id, None, expected=True
    )


async def test_epex_negative_false_when_off(hass: HomeAssistant) -> None:
    """EpexPriceIsNegativeCondition is False when binary sensor state is 'off'."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
        entity_suffix="epex_negative",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative",
    )
    hass.states.async_set(entity_id, "off")
    await _run_condition(
        hass, EpexPriceIsNegativeCondition, entity_id, None, expected=False
    )


async def test_epex_negative_false_when_entity_missing(hass: HomeAssistant) -> None:
    """EpexPriceIsNegativeCondition is False when entity has no state."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
        entity_suffix="epex_negative",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative",
    )
    await _run_condition(
        hass, EpexPriceIsNegativeCondition, entity_id, None, expected=False
    )


async def test_epex_negative_rejects_wrong_translation_key(
    hass: HomeAssistant,
) -> None:
    """Entity from a different integration is excluded by entity_filter."""
    entry = _make_entry(hass)
    # Register with a different translation_key (not 'epex_negative').
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key="connectivity",
        entity_suffix="other_sensor",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_other",
    )
    hass.states.async_set(entity_id, "on")
    # Filtered out - treated as no matching entity, returns False.
    await _run_condition(
        hass, EpexPriceIsNegativeCondition, entity_id, None, expected=False
    )


# ---------------------------------------------------------------------------
# solar_surplus_is_at_level
# ---------------------------------------------------------------------------


async def test_solar_surplus_true_on_match(hass: HomeAssistant) -> None:
    """SolarSurplusIsAtLevelCondition is True when state matches the level."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
        entity_suffix=f"{_EAN}_solar_surplus_forecast",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_solar_surplus",
    )
    hass.states.async_set(entity_id, "high_surplus")
    await _run_condition(
        hass,
        SolarSurplusIsAtLevelCondition,
        entity_id,
        {"level": "high_surplus"},
        expected=True,
    )


async def test_solar_surplus_false_on_mismatch(hass: HomeAssistant) -> None:
    """SolarSurplusIsAtLevelCondition is False when state differs from level."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
        entity_suffix=f"{_EAN}_solar_surplus_forecast",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_solar_surplus",
    )
    hass.states.async_set(entity_id, "low_surplus")
    await _run_condition(
        hass,
        SolarSurplusIsAtLevelCondition,
        entity_id,
        {"level": "high_surplus"},
        expected=False,
    )


async def test_solar_surplus_false_when_entity_missing(hass: HomeAssistant) -> None:
    """SolarSurplusIsAtLevelCondition is False when entity has no state."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
        entity_suffix=f"{_EAN}_solar_surplus_forecast",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_solar_surplus",
    )
    await _run_condition(
        hass,
        SolarSurplusIsAtLevelCondition,
        entity_id,
        {"level": "high_surplus"},
        expected=False,
    )


@pytest.mark.parametrize("level", SOLAR_SURPLUS_LEVELS)
async def test_solar_surplus_each_level_accepted(
    hass: HomeAssistant, level: str
) -> None:
    """SolarSurplusIsAtLevelCondition works for every valid level."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
        entity_suffix=f"{_EAN}_solar_surplus_forecast",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_solar_surplus_{level}",
    )
    hass.states.async_set(entity_id, level)
    await _run_condition(
        hass, SolarSurplusIsAtLevelCondition, entity_id, {"level": level}, expected=True
    )


async def test_solar_surplus_rejects_wrong_translation_key(
    hass: HomeAssistant,
) -> None:
    """Solar surplus entity filter rejects sensors with a different translation_key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )
    hass.states.async_set(entity_id, "high_surplus")
    await _run_condition(
        hass,
        SolarSurplusIsAtLevelCondition,
        entity_id,
        {"level": "high_surplus"},
        expected=False,
    )


# ---------------------------------------------------------------------------
# offtake_slot_is
# ---------------------------------------------------------------------------


async def test_offtake_slot_true_on_match(hass: HomeAssistant) -> None:
    """OfftakeSlotIsCondition is True when state matches the slot."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )
    hass.states.async_set(entity_id, "offpeak")
    await _run_condition(
        hass, OfftakeSlotIsCondition, entity_id, {"slot": "offpeak"}, expected=True
    )


async def test_offtake_slot_false_on_mismatch(hass: HomeAssistant) -> None:
    """OfftakeSlotIsCondition is False when state differs from the slot."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )
    hass.states.async_set(entity_id, "peak")
    await _run_condition(
        hass, OfftakeSlotIsCondition, entity_id, {"slot": "offpeak"}, expected=False
    )


async def test_offtake_slot_false_when_entity_missing(hass: HomeAssistant) -> None:
    """OfftakeSlotIsCondition is False when entity has no state."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )
    await _run_condition(
        hass, OfftakeSlotIsCondition, entity_id, {"slot": "offpeak"}, expected=False
    )


@pytest.mark.parametrize("slot", TOU_SLOT_CODES)
async def test_offtake_slot_each_code_accepted(hass: HomeAssistant, slot: str) -> None:
    """OfftakeSlotIsCondition works for every valid TOU slot code."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_{slot}",
    )
    hass.states.async_set(entity_id, slot)
    await _run_condition(
        hass, OfftakeSlotIsCondition, entity_id, {"slot": slot}, expected=True
    )


async def test_offtake_slot_rejects_injection_slot_entity(
    hass: HomeAssistant,
) -> None:
    """OfftakeSlotIsCondition rejects a tou_injection_slot entity."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_SLOT,
        entity_suffix=f"{_EAN}_injection_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_injection_slot",
    )
    hass.states.async_set(entity_id, "offpeak")
    # entity_filter should reject the injection entity.
    await _run_condition(
        hass, OfftakeSlotIsCondition, entity_id, {"slot": "offpeak"}, expected=False
    )


# ---------------------------------------------------------------------------
# injection_slot_is
# ---------------------------------------------------------------------------


async def test_injection_slot_true_on_match(hass: HomeAssistant) -> None:
    """InjectionSlotIsCondition is True when state matches the slot."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_SLOT,
        entity_suffix=f"{_EAN}_injection_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_injection_slot",
    )
    hass.states.async_set(entity_id, "peak")
    await _run_condition(
        hass, InjectionSlotIsCondition, entity_id, {"slot": "peak"}, expected=True
    )


async def test_injection_slot_false_on_mismatch(hass: HomeAssistant) -> None:
    """InjectionSlotIsCondition is False when state differs from the slot."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_SLOT,
        entity_suffix=f"{_EAN}_injection_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_injection_slot",
    )
    hass.states.async_set(entity_id, "offpeak")
    await _run_condition(
        hass, InjectionSlotIsCondition, entity_id, {"slot": "peak"}, expected=False
    )


async def test_injection_slot_false_when_entity_missing(hass: HomeAssistant) -> None:
    """InjectionSlotIsCondition is False when entity has no state."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_SLOT,
        entity_suffix=f"{_EAN}_injection_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_injection_slot",
    )
    await _run_condition(
        hass, InjectionSlotIsCondition, entity_id, {"slot": "peak"}, expected=False
    )


@pytest.mark.parametrize("slot", TOU_SLOT_CODES)
async def test_injection_slot_each_code_accepted(
    hass: HomeAssistant, slot: str
) -> None:
    """InjectionSlotIsCondition works for every valid TOU slot code."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_SLOT,
        entity_suffix=f"{_EAN}_injection_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_injection_{slot}",
    )
    hass.states.async_set(entity_id, slot)
    await _run_condition(
        hass, InjectionSlotIsCondition, entity_id, {"slot": slot}, expected=True
    )


async def test_injection_slot_rejects_offtake_slot_entity(
    hass: HomeAssistant,
) -> None:
    """InjectionSlotIsCondition rejects a tou_offtake_slot entity."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )
    hass.states.async_set(entity_id, "peak")
    await _run_condition(
        hass, InjectionSlotIsCondition, entity_id, {"slot": "peak"}, expected=False
    )


# ---------------------------------------------------------------------------
# Schema validation - rejection tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schema", "options"),
    [
        (_SOLAR_SURPLUS_SCHEMA, {"level": "not_a_level"}),
        (_TOU_SLOT_SCHEMA, {"slot": "not_a_slot"}),
    ],
)
def test_schema_rejects_invalid_option(
    schema: vol.Schema, options: dict[str, str]
) -> None:
    """Schemas raise vol.Invalid when an unknown level or slot value is passed."""
    with pytest.raises(vol.Invalid):
        schema(
            {
                "condition": "engie_be.x",
                "entity_id": "sensor.foo",
                "options": options,
            }
        )


# ---------------------------------------------------------------------------
# Phase D - offtake_is_optimal
# ---------------------------------------------------------------------------


async def test_offtake_is_optimal_true_when_on(hass: HomeAssistant) -> None:
    """OfftakeIsOptimalCondition is True when the binary sensor is on."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL,
        entity_suffix="tou_offtake_is_optimal",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_offtake_optimal",
    )
    hass.states.async_set(entity_id, "on")
    await _run_condition(
        hass, OfftakeIsOptimalCondition, entity_id, None, expected=True
    )


async def test_offtake_is_optimal_false_when_off(hass: HomeAssistant) -> None:
    """OfftakeIsOptimalCondition is False when the binary sensor is off."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL,
        entity_suffix="tou_offtake_is_optimal",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_offtake_optimal",
    )
    hass.states.async_set(entity_id, "off")
    await _run_condition(
        hass, OfftakeIsOptimalCondition, entity_id, None, expected=False
    )


async def test_offtake_is_optimal_rejects_wrong_translation_key(
    hass: HomeAssistant,
) -> None:
    """OfftakeIsOptimalCondition rejects entities with wrong translation_key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL,
        entity_suffix="tou_injection_is_optimal",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_injection_optimal",
    )
    hass.states.async_set(entity_id, "on")
    await _run_condition(
        hass, OfftakeIsOptimalCondition, entity_id, None, expected=False
    )


# ---------------------------------------------------------------------------
# Phase D - injection_is_optimal
# ---------------------------------------------------------------------------


async def test_injection_is_optimal_true_when_on(hass: HomeAssistant) -> None:
    """InjectionIsOptimalCondition is True when the binary sensor is on."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL,
        entity_suffix="tou_injection_is_optimal",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_injection_optimal",
    )
    hass.states.async_set(entity_id, "on")
    await _run_condition(
        hass, InjectionIsOptimalCondition, entity_id, None, expected=True
    )


async def test_injection_is_optimal_false_when_off(hass: HomeAssistant) -> None:
    """InjectionIsOptimalCondition is False when the binary sensor is off."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL,
        entity_suffix="tou_injection_is_optimal",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_injection_optimal",
    )
    hass.states.async_set(entity_id, "off")
    await _run_condition(
        hass, InjectionIsOptimalCondition, entity_id, None, expected=False
    )


# ---------------------------------------------------------------------------
# Phase D - happy_hours_is_active
# ---------------------------------------------------------------------------


async def test_happy_hours_is_active_true_when_on(hass: HomeAssistant) -> None:
    """HappyHoursIsActiveCondition is True when the binary sensor is on."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_HAPPY_HOURS_ACTIVE,
        entity_suffix="happy_hours_active",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_hh_active",
    )
    hass.states.async_set(entity_id, "on")
    await _run_condition(
        hass, HappyHoursIsActiveCondition, entity_id, None, expected=True
    )


async def test_happy_hours_is_active_false_when_off(hass: HomeAssistant) -> None:
    """HappyHoursIsActiveCondition is False when the binary sensor is off."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_HAPPY_HOURS_ACTIVE,
        entity_suffix="happy_hours_active",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_hh_active",
    )
    hass.states.async_set(entity_id, "off")
    await _run_condition(
        hass, HappyHoursIsActiveCondition, entity_id, None, expected=False
    )


async def test_happy_hours_is_active_rejects_authentication_entity(
    hass: HomeAssistant,
) -> None:
    """HappyHoursIsActiveCondition rejects entities with wrong translation_key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_AUTHENTICATION,
        entity_suffix="authentication",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_auth",
    )
    hass.states.async_set(entity_id, "on")
    await _run_condition(
        hass, HappyHoursIsActiveCondition, entity_id, None, expected=False
    )


# ---------------------------------------------------------------------------
# Phase D - epex_price_is_below_threshold / epex_price_is_above_threshold
# ---------------------------------------------------------------------------


def _make_numerical_condition_config(
    entity_id: str,
    threshold_type: str,
    value: float,
) -> ConditionConfig:
    """Build a numerical condition ConditionConfig."""
    return ConditionConfig(
        target={"entity_id": entity_id},
        options={
            "behavior": "any",
            "threshold": {
                "type": threshold_type,
                "value": {"number": value},
            },
        },
    )


async def test_epex_price_is_below_threshold_true_when_below(
    hass: HomeAssistant,
) -> None:
    """EpexPriceIsBelowThresholdCondition is True when price is below threshold."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT,
        entity_suffix="epex_current",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current",
    )
    hass.states.async_set(entity_id, "0.05")
    config = _make_numerical_condition_config(entity_id, "below", 0.10)
    condition = EpexPriceIsBelowThresholdCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is True
    condition.async_unload()


async def test_epex_price_is_below_threshold_false_when_above(
    hass: HomeAssistant,
) -> None:
    """EpexPriceIsBelowThresholdCondition is False when price is above threshold."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT,
        entity_suffix="epex_current",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current",
    )
    hass.states.async_set(entity_id, "0.15")
    config = _make_numerical_condition_config(entity_id, "below", 0.10)
    condition = EpexPriceIsBelowThresholdCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is False
    condition.async_unload()


async def test_epex_price_is_above_threshold_true_when_above(
    hass: HomeAssistant,
) -> None:
    """EpexPriceIsAboveThresholdCondition is True when price is above threshold."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT,
        entity_suffix="epex_current",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current",
    )
    hass.states.async_set(entity_id, "0.20")
    config = _make_numerical_condition_config(entity_id, "above", 0.10)
    condition = EpexPriceIsAboveThresholdCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is True
    condition.async_unload()


async def test_epex_price_is_above_threshold_false_when_below(
    hass: HomeAssistant,
) -> None:
    """EpexPriceIsAboveThresholdCondition is False when price is below threshold."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT,
        entity_suffix="epex_current",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current",
    )
    hass.states.async_set(entity_id, "0.05")
    config = _make_numerical_condition_config(entity_id, "above", 0.10)
    condition = EpexPriceIsAboveThresholdCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is False
    condition.async_unload()


async def test_epex_threshold_rejects_wrong_translation_key(
    hass: HomeAssistant,
) -> None:
    """EpexPriceIsBelowThresholdCondition rejects sensors with wrong key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER,
        entity_suffix="captar_monthly_peak_power",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_captar_peak",
    )
    hass.states.async_set(entity_id, "0.05")
    config = _make_numerical_condition_config(entity_id, "below", 0.10)
    condition = EpexPriceIsBelowThresholdCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is False
    condition.async_unload()


# ---------------------------------------------------------------------------
# Phase D - captar_peak_is_above_threshold
# ---------------------------------------------------------------------------


async def test_captar_peak_is_above_threshold_true_when_above(
    hass: HomeAssistant,
) -> None:
    """CaptarPeakIsAboveThresholdCondition is True when peak is above threshold."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER,
        entity_suffix="captar_monthly_peak_power",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_captar_peak",
    )
    hass.states.async_set(entity_id, "7.5")
    config = _make_numerical_condition_config(entity_id, "above", 5.0)
    condition = CaptarPeakIsAboveThresholdCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is True
    condition.async_unload()


async def test_captar_peak_is_above_threshold_false_when_below(
    hass: HomeAssistant,
) -> None:
    """CaptarPeakIsAboveThresholdCondition is False when peak is below threshold."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER,
        entity_suffix="captar_monthly_peak_power",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_captar_peak",
    )
    hass.states.async_set(entity_id, "3.0")
    config = _make_numerical_condition_config(entity_id, "above", 5.0)
    condition = CaptarPeakIsAboveThresholdCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is False
    condition.async_unload()


async def test_captar_peak_is_above_threshold_rejects_wrong_key(
    hass: HomeAssistant,
) -> None:
    """CaptarPeakIsAboveThresholdCondition rejects sensors with wrong key."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT,
        entity_suffix="epex_current",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current",
    )
    hass.states.async_set(entity_id, "7.5")
    config = _make_numerical_condition_config(entity_id, "above", 5.0)
    condition = CaptarPeakIsAboveThresholdCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is False
    condition.async_unload()


# ---------------------------------------------------------------------------
# Quarter-hourly EPEX conditions
# ---------------------------------------------------------------------------


async def test_epex_price_is_negative_qh_true_when_on(
    hass: HomeAssistant,
) -> None:
    """Test QH negative condition returns True when on."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR,
        entity_suffix="epex_negative_quarter_hour",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative_qh",
    )
    hass.states.async_set(entity_id, "on")
    await _run_condition(
        hass,
        EpexPriceIsNegativeQuarterHourCondition,
        entity_id,
        None,
        expected=True,
    )


async def test_epex_price_is_negative_qh_false_when_off(
    hass: HomeAssistant,
) -> None:
    """Test QH negative condition returns False when off."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR,
        entity_suffix="epex_negative_quarter_hour",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative_qh",
    )
    hass.states.async_set(entity_id, "off")
    await _run_condition(
        hass,
        EpexPriceIsNegativeQuarterHourCondition,
        entity_id,
        None,
        expected=False,
    )


async def test_epex_price_is_negative_qh_rejects_hourly(
    hass: HomeAssistant,
) -> None:
    """Test QH negative condition rejects hourly entity."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=BINARY_SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
        entity_suffix="epex_negative",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative",
    )
    hass.states.async_set(entity_id, "on")
    await _run_condition(
        hass,
        EpexPriceIsNegativeQuarterHourCondition,
        entity_id,
        None,
        expected=False,
    )


async def test_epex_price_below_threshold_qh_true(
    hass: HomeAssistant,
) -> None:
    """Test QH below threshold condition returns True when below."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT_QUARTER_HOUR,
        entity_suffix="epex_current_quarter_hour",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current_qh",
    )
    hass.states.async_set(entity_id, "0.05")
    config = _make_numerical_condition_config(entity_id, "below", 0.10)
    condition = EpexPriceIsBelowThresholdQuarterHourCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is True
    condition.async_unload()


async def test_epex_price_below_threshold_qh_false(
    hass: HomeAssistant,
) -> None:
    """Test QH below threshold condition returns False when above."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT_QUARTER_HOUR,
        entity_suffix="epex_current_quarter_hour",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current_qh",
    )
    hass.states.async_set(entity_id, "0.15")
    config = _make_numerical_condition_config(entity_id, "below", 0.10)
    condition = EpexPriceIsBelowThresholdQuarterHourCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is False
    condition.async_unload()


async def test_epex_price_above_threshold_qh_true(
    hass: HomeAssistant,
) -> None:
    """Test QH above threshold condition returns True when above."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT_QUARTER_HOUR,
        entity_suffix="epex_current_quarter_hour",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current_qh",
    )
    hass.states.async_set(entity_id, "0.20")
    config = _make_numerical_condition_config(entity_id, "above", 0.10)
    condition = EpexPriceIsAboveThresholdQuarterHourCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is True
    condition.async_unload()


async def test_epex_price_above_threshold_qh_false(
    hass: HomeAssistant,
) -> None:
    """Test QH above threshold condition returns False when below."""
    entry = _make_entry(hass)
    entity_id = _register_entity(
        hass,
        entry,
        platform=SENSOR_DOMAIN,
        translation_key=TRANSLATION_KEY_EPEX_CURRENT_QUARTER_HOUR,
        entity_suffix="epex_current_quarter_hour",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_current_qh",
    )
    hass.states.async_set(entity_id, "0.05")
    config = _make_numerical_condition_config(entity_id, "above", 0.10)
    condition = EpexPriceIsAboveThresholdQuarterHourCondition(hass, config)
    await condition.async_setup()
    assert condition(hass) is False
    condition.async_unload()
