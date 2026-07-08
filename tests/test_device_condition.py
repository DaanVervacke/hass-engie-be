"""Tests for custom_components.engie_be.device_condition."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SOLAR_SURPLUS_LEVELS,
    TOU_SLOT_CODES,
)
from custom_components.engie_be.device_condition import (
    _EPEX_NEGATIVE_TYPE,
    _INJECTION_SLOT_TYPE,
    _OFFTAKE_SLOT_TYPE,
    _SOLAR_LEVEL_TYPE,
    async_condition_from_config,
    async_get_conditions,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BAN = "000000000000"
_EAN = "541448820070000000"
_SUBENTRY_ID = "test_subentry_id"

# ---------------------------------------------------------------------------
# Config entry helpers
# ---------------------------------------------------------------------------


def _add_config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a minimal v5 config entry to hass and return it."""
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


def _register_device(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Register a bare device and return its generated device_id."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, _SUBENTRY_ID)},
        name="ENGIE Belgium Test",
    )
    return device.id


def _register_entity(  # noqa: PLR0913
    hass: HomeAssistant,
    device_id: str,
    entry: MockConfigEntry,
    *,
    platform: str = "sensor",
    entity_suffix: str,
    unique_id: str,
) -> str:
    """Register an entity in the entity registry and return its entity_id."""
    ent_reg = er.async_get(hass)
    # ``domain`` is the entity domain (e.g. "sensor"), ``platform`` is the
    # integration that owns it.  ``suggested_object_id`` drives the slug so
    # the resulting entity_id matches production naming.
    suggested = f"engie_belgium_{_BAN}_{entity_suffix}"
    reg_entry = ent_reg.async_get_or_create(
        platform,
        DOMAIN,
        unique_id,
        device_id=device_id,
        config_entry=entry,
        suggested_object_id=suggested,
    )
    return reg_entry.entity_id


# ---------------------------------------------------------------------------
# async_get_conditions - solar surplus
# ---------------------------------------------------------------------------


async def test_get_conditions_solar_surplus(hass: HomeAssistant) -> None:
    """solar_surplus_is_at_level appears once per level for the forecast entity."""
    entry = _add_config_entry(hass)
    device_id = _register_device(hass, entry)
    _register_entity(
        hass,
        device_id,
        entry,
        platform="sensor",
        entity_suffix=f"{_EAN}_solar_surplus_forecast",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_solar_surplus",
    )

    conditions = await async_get_conditions(hass, device_id)

    solar = [c for c in conditions if c["type"] == _SOLAR_LEVEL_TYPE]
    assert len(solar) == len(SOLAR_SURPLUS_LEVELS)
    levels_found = {c["level"] for c in solar}
    assert levels_found == set(SOLAR_SURPLUS_LEVELS)
    for cond in solar:
        assert cond["condition"] == "device"
        assert cond["domain"] == DOMAIN
        assert cond["device_id"] == device_id
        assert cond["entity_id"].endswith("_solar_surplus_forecast")


# ---------------------------------------------------------------------------
# async_get_conditions - TOU offtake
# ---------------------------------------------------------------------------


async def test_get_conditions_offtake_slot(hass: HomeAssistant) -> None:
    """offtake_slot_is appears once per TOU slot code for the offtake entity."""
    entry = _add_config_entry(hass)
    device_id = _register_device(hass, entry)
    _register_entity(
        hass,
        device_id,
        entry,
        platform="sensor",
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )

    conditions = await async_get_conditions(hass, device_id)

    offtake = [c for c in conditions if c["type"] == _OFFTAKE_SLOT_TYPE]
    assert len(offtake) == len(TOU_SLOT_CODES)
    slots_found = {c["slot"] for c in offtake}
    assert slots_found == set(TOU_SLOT_CODES)
    for cond in offtake:
        assert cond["condition"] == "device"
        assert cond["domain"] == DOMAIN
        assert cond["device_id"] == device_id
        assert cond["entity_id"].endswith("_offtake_slot")


# ---------------------------------------------------------------------------
# async_get_conditions - TOU injection
# ---------------------------------------------------------------------------


async def test_get_conditions_injection_slot(hass: HomeAssistant) -> None:
    """injection_slot_is appears once per TOU slot code for the injection entity."""
    entry = _add_config_entry(hass)
    device_id = _register_device(hass, entry)
    _register_entity(
        hass,
        device_id,
        entry,
        platform="sensor",
        entity_suffix=f"{_EAN}_injection_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_injection_slot",
    )

    conditions = await async_get_conditions(hass, device_id)

    injection = [c for c in conditions if c["type"] == _INJECTION_SLOT_TYPE]
    assert len(injection) == len(TOU_SLOT_CODES)
    slots_found = {c["slot"] for c in injection}
    assert slots_found == set(TOU_SLOT_CODES)
    for cond in injection:
        assert cond["condition"] == "device"
        assert cond["domain"] == DOMAIN
        assert cond["device_id"] == device_id
        assert cond["entity_id"].endswith("_injection_slot")


# ---------------------------------------------------------------------------
# async_get_conditions - EPEX negative
# ---------------------------------------------------------------------------


async def test_get_conditions_epex_negative(hass: HomeAssistant) -> None:
    """epex_price_is_negative appears exactly once for the binary sensor."""
    entry = _add_config_entry(hass)
    device_id = _register_device(hass, entry)
    _register_entity(
        hass,
        device_id,
        entry,
        platform="binary_sensor",
        entity_suffix="epex_negative",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative",
    )

    conditions = await async_get_conditions(hass, device_id)

    epex = [c for c in conditions if c["type"] == _EPEX_NEGATIVE_TYPE]
    assert len(epex) == 1
    assert epex[0]["condition"] == "device"
    assert epex[0]["domain"] == DOMAIN
    assert epex[0]["device_id"] == device_id
    assert epex[0]["entity_id"].endswith("_epex_negative")


# ---------------------------------------------------------------------------
# async_get_conditions - combined (all four sensors on one device)
# ---------------------------------------------------------------------------


async def test_get_conditions_all_types_combined(hass: HomeAssistant) -> None:
    """All four condition types appear when all sensors are on the same device."""
    entry = _add_config_entry(hass)
    device_id = _register_device(hass, entry)
    _register_entity(
        hass,
        device_id,
        entry,
        platform="sensor",
        entity_suffix=f"{_EAN}_solar_surplus_forecast",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_solar_surplus",
    )
    _register_entity(
        hass,
        device_id,
        entry,
        platform="sensor",
        entity_suffix=f"{_EAN}_offtake_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_offtake_slot",
    )
    _register_entity(
        hass,
        device_id,
        entry,
        platform="sensor",
        entity_suffix=f"{_EAN}_injection_slot",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_{_EAN}_injection_slot",
    )
    _register_entity(
        hass,
        device_id,
        entry,
        platform="binary_sensor",
        entity_suffix="epex_negative",
        unique_id=f"{entry.entry_id}_{_SUBENTRY_ID}_epex_negative",
    )

    conditions = await async_get_conditions(hass, device_id)

    types_found = {c["type"] for c in conditions}
    assert types_found == {
        _SOLAR_LEVEL_TYPE,
        _OFFTAKE_SLOT_TYPE,
        _INJECTION_SLOT_TYPE,
        _EPEX_NEGATIVE_TYPE,
    }
    expected_count = len(SOLAR_SURPLUS_LEVELS) + len(TOU_SLOT_CODES) * 2 + 1
    assert len(conditions) == expected_count


# ---------------------------------------------------------------------------
# async_get_conditions - entities from a different platform are ignored
# ---------------------------------------------------------------------------


async def test_get_conditions_skips_other_platforms(hass: HomeAssistant) -> None:
    """Entities registered under a different platform are not exposed."""
    entry = _add_config_entry(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "other_sub")},
        name="ENGIE Belgium Other",
    )
    ent_reg = er.async_get(hass)
    # Register with a different platform: "other_integration" instead of DOMAIN.
    ent_reg.async_get_or_create(
        "sensor.some_other_solar_surplus_forecast",
        "other_integration",
        "other_uid",
        device_id=device.id,
        config_entry=entry,
    )

    conditions = await async_get_conditions(hass, device.id)

    assert conditions == []


# ---------------------------------------------------------------------------
# async_condition_from_config - solar surplus returns True on match
# ---------------------------------------------------------------------------


async def test_solar_surplus_condition_true_on_match(hass: HomeAssistant) -> None:
    """Solar surplus condition returns True when state matches the expected level."""
    entity_id = "sensor.engie_belgium_000000000000_solar_surplus_forecast"
    hass.states.async_set(entity_id, "high_surplus")

    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _SOLAR_LEVEL_TYPE,
        "entity_id": entity_id,
        "level": "high_surplus",
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is True


# ---------------------------------------------------------------------------
# async_condition_from_config - solar surplus returns False on mismatch
# ---------------------------------------------------------------------------


async def test_solar_surplus_condition_false_on_mismatch(hass: HomeAssistant) -> None:
    """Solar surplus condition returns False when state does not match."""
    entity_id = "sensor.engie_belgium_000000000000_solar_surplus_forecast"
    hass.states.async_set(entity_id, "low_surplus")

    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _SOLAR_LEVEL_TYPE,
        "entity_id": entity_id,
        "level": "high_surplus",
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is False


# ---------------------------------------------------------------------------
# async_condition_from_config - solar surplus returns False when entity absent
# ---------------------------------------------------------------------------


async def test_solar_surplus_condition_false_when_entity_missing(
    hass: HomeAssistant,
) -> None:
    """Solar surplus condition returns False when entity is not in hass.states."""
    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _SOLAR_LEVEL_TYPE,
        "entity_id": "sensor.engie_belgium_000000000000_solar_surplus_forecast",
        "level": "high_surplus",
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is False


# ---------------------------------------------------------------------------
# async_condition_from_config - offtake slot returns True on match
# ---------------------------------------------------------------------------


async def test_offtake_slot_condition_true_on_match(hass: HomeAssistant) -> None:
    """Offtake slot condition returns True when state matches expected slot."""
    entity_id = f"sensor.engie_belgium_{_BAN}_{_EAN}_offtake_slot"
    hass.states.async_set(entity_id, "offpeak")

    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _OFFTAKE_SLOT_TYPE,
        "entity_id": entity_id,
        "slot": "offpeak",
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is True


# ---------------------------------------------------------------------------
# async_condition_from_config - offtake slot returns False on mismatch
# ---------------------------------------------------------------------------


async def test_offtake_slot_condition_false_on_mismatch(hass: HomeAssistant) -> None:
    """Offtake slot condition returns False when state does not match."""
    entity_id = f"sensor.engie_belgium_{_BAN}_{_EAN}_offtake_slot"
    hass.states.async_set(entity_id, "peak")

    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _OFFTAKE_SLOT_TYPE,
        "entity_id": entity_id,
        "slot": "offpeak",
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is False


# ---------------------------------------------------------------------------
# async_condition_from_config - offtake slot returns False when entity absent
# ---------------------------------------------------------------------------


async def test_offtake_slot_condition_false_when_entity_missing(
    hass: HomeAssistant,
) -> None:
    """Offtake slot condition returns False when entity is not in hass.states."""
    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _OFFTAKE_SLOT_TYPE,
        "entity_id": f"sensor.engie_belgium_{_BAN}_{_EAN}_offtake_slot",
        "slot": "offpeak",
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is False


# ---------------------------------------------------------------------------
# async_condition_from_config - injection slot returns True on match
# ---------------------------------------------------------------------------


async def test_injection_slot_condition_true_on_match(hass: HomeAssistant) -> None:
    """Injection slot condition returns True when state matches expected slot."""
    entity_id = f"sensor.engie_belgium_{_BAN}_{_EAN}_injection_slot"
    hass.states.async_set(entity_id, "peak")

    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _INJECTION_SLOT_TYPE,
        "entity_id": entity_id,
        "slot": "peak",
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is True


# ---------------------------------------------------------------------------
# async_condition_from_config - injection slot returns False on mismatch
# ---------------------------------------------------------------------------


async def test_injection_slot_condition_false_on_mismatch(hass: HomeAssistant) -> None:
    """Injection slot condition returns False when state does not match."""
    entity_id = f"sensor.engie_belgium_{_BAN}_{_EAN}_injection_slot"
    hass.states.async_set(entity_id, "offpeak")

    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _INJECTION_SLOT_TYPE,
        "entity_id": entity_id,
        "slot": "peak",
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is False


# ---------------------------------------------------------------------------
# async_condition_from_config - injection slot returns False when entity absent
# ---------------------------------------------------------------------------


async def test_injection_slot_condition_false_when_entity_missing(
    hass: HomeAssistant,
) -> None:
    """Injection slot condition returns False when entity is not in hass.states."""
    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _INJECTION_SLOT_TYPE,
        "entity_id": f"sensor.engie_belgium_{_BAN}_{_EAN}_injection_slot",
        "slot": "peak",
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is False


# ---------------------------------------------------------------------------
# async_condition_from_config - EPEX negative returns True when on
# ---------------------------------------------------------------------------


async def test_epex_negative_condition_true_when_on(hass: HomeAssistant) -> None:
    """EPEX negative condition returns True when binary sensor state is 'on'."""
    entity_id = f"binary_sensor.engie_belgium_{_BAN}_epex_negative"
    hass.states.async_set(entity_id, "on")

    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _EPEX_NEGATIVE_TYPE,
        "entity_id": entity_id,
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is True


# ---------------------------------------------------------------------------
# async_condition_from_config - EPEX negative returns False when off
# ---------------------------------------------------------------------------


async def test_epex_negative_condition_false_when_off(hass: HomeAssistant) -> None:
    """EPEX negative condition returns False when binary sensor state is 'off'."""
    entity_id = f"binary_sensor.engie_belgium_{_BAN}_epex_negative"
    hass.states.async_set(entity_id, "off")

    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _EPEX_NEGATIVE_TYPE,
        "entity_id": entity_id,
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is False


# ---------------------------------------------------------------------------
# async_condition_from_config - EPEX negative returns False when entity absent
# ---------------------------------------------------------------------------


async def test_epex_negative_condition_false_when_entity_missing(
    hass: HomeAssistant,
) -> None:
    """EPEX negative condition returns False when entity is not in hass.states."""
    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": _EPEX_NEGATIVE_TYPE,
        "entity_id": f"binary_sensor.engie_belgium_{_BAN}_epex_negative",
    }
    check = async_condition_from_config(hass, config)

    assert check(hass) is False


# ---------------------------------------------------------------------------
# async_condition_from_config - unknown type raises ValueError
# ---------------------------------------------------------------------------


async def test_condition_from_config_unknown_type_raises(
    hass: HomeAssistant,
) -> None:
    """async_condition_from_config raises ValueError for an unknown condition type."""
    config = {
        "condition": "device",
        "device_id": "fake_device",
        "domain": DOMAIN,
        "type": "totally_unknown_type",
        "entity_id": "sensor.engie_belgium_000000000000_something",
    }
    with pytest.raises(ValueError, match="Unknown condition type"):
        async_condition_from_config(hass, config)
