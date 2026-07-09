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
    EpexPriceIsNegativeCondition,
    InjectionSlotIsCondition,
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
    TRANSLATION_KEY_EPEX_NEGATIVE,
    TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
    TRANSLATION_KEY_TOU_INJECTION_SLOT,
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


async def test_async_get_conditions_returns_all_four(hass: HomeAssistant) -> None:
    """async_get_conditions returns all four condition names."""
    conditions = await async_get_conditions(hass)

    assert set(conditions.keys()) == {
        "epex_price_is_negative",
        "solar_surplus_is_at_level",
        "offtake_slot_is",
        "injection_slot_is",
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
