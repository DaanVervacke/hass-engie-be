"""
Device conditions for the ENGIE Belgium integration.

Exposes ENGIE state as first-class dropdown conditions in the HA automation
editor so users do not have to write template conditions.

Supported condition types:

- ``solar_surplus_is_at_level`` -- matches ``sensor.*_solar_surplus_forecast``
- ``offtake_slot_is``           -- matches ``sensor.*_offtake_slot``
- ``injection_slot_is``         -- matches ``sensor.*_injection_slot``
- ``epex_price_is_negative``    -- matches ``binary_sensor.*_epex_negative``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_CONDITION,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.config_validation import DEVICE_CONDITION_BASE_SCHEMA

from .const import DOMAIN, SOLAR_SURPLUS_LEVELS, TOU_SLOT_CODES

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.helpers.condition import ConditionCheckerType
    from homeassistant.helpers.typing import ConfigType, TemplateVarsType

CONF_LEVEL = "level"
CONF_SLOT = "slot"

_SOLAR_LEVEL_TYPE = "solar_surplus_is_at_level"
_OFFTAKE_SLOT_TYPE = "offtake_slot_is"
_INJECTION_SLOT_TYPE = "injection_slot_is"
_EPEX_NEGATIVE_TYPE = "epex_price_is_negative"

CONDITION_TYPES: frozenset[str] = frozenset(
    {
        _SOLAR_LEVEL_TYPE,
        _OFFTAKE_SLOT_TYPE,
        _INJECTION_SLOT_TYPE,
        _EPEX_NEGATIVE_TYPE,
    }
)

CONDITION_SCHEMA = vol.All(
    DEVICE_CONDITION_BASE_SCHEMA.extend(
        {
            vol.Required(CONF_TYPE): vol.In(CONDITION_TYPES),
            vol.Required(ATTR_ENTITY_ID): cv.entity_id,
            vol.Optional(CONF_LEVEL): vol.In(SOLAR_SURPLUS_LEVELS),
            vol.Optional(CONF_SLOT): vol.In(TOU_SLOT_CODES),
        }
    ),
)


async def async_get_conditions(
    hass: HomeAssistant,
    device_id: str,
) -> list[dict[str, Any]]:
    """Enumerate available conditions for an engie_be device."""
    registry = er.async_get(hass)
    conditions: list[dict[str, Any]] = []
    for entry in er.async_entries_for_device(registry, device_id):
        if entry.platform != DOMAIN:
            continue
        entity_id = entry.entity_id
        if entity_id.endswith("_solar_surplus_forecast"):
            conditions.extend(
                {
                    CONF_CONDITION: "device",
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_TYPE: _SOLAR_LEVEL_TYPE,
                    ATTR_ENTITY_ID: entity_id,
                    CONF_LEVEL: level,
                }
                for level in SOLAR_SURPLUS_LEVELS
            )
        elif entity_id.endswith("_offtake_slot"):
            conditions.extend(
                {
                    CONF_CONDITION: "device",
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_TYPE: _OFFTAKE_SLOT_TYPE,
                    ATTR_ENTITY_ID: entity_id,
                    CONF_SLOT: slot,
                }
                for slot in TOU_SLOT_CODES
            )
        elif entity_id.endswith("_injection_slot"):
            conditions.extend(
                {
                    CONF_CONDITION: "device",
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_TYPE: _INJECTION_SLOT_TYPE,
                    ATTR_ENTITY_ID: entity_id,
                    CONF_SLOT: slot,
                }
                for slot in TOU_SLOT_CODES
            )
        elif entity_id.endswith("_epex_negative"):
            conditions.append(
                {
                    CONF_CONDITION: "device",
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_TYPE: _EPEX_NEGATIVE_TYPE,
                    ATTR_ENTITY_ID: entity_id,
                }
            )
    return conditions


def _make_state_check(
    entity_id: str,
    expected: str,
) -> Callable[[HomeAssistant, TemplateVarsType], bool]:
    """Return a condition checker that compares an entity state to expected."""

    def _check(
        hass: HomeAssistant,
        variables: TemplateVarsType = None,  # noqa: ARG001
    ) -> bool:
        state = hass.states.get(entity_id)
        return state is not None and state.state == expected

    return _check


@callback
def async_condition_from_config(
    hass: HomeAssistant,  # noqa: ARG001
    config: ConfigType,
) -> ConditionCheckerType:
    """Return a callable that evaluates the condition."""
    condition_type: str = config[CONF_TYPE]
    entity_id: str = config[ATTR_ENTITY_ID]

    if condition_type == _SOLAR_LEVEL_TYPE:
        return _make_state_check(entity_id, config[CONF_LEVEL])

    if condition_type in (_OFFTAKE_SLOT_TYPE, _INJECTION_SLOT_TYPE):
        return _make_state_check(entity_id, config[CONF_SLOT])

    if condition_type == _EPEX_NEGATIVE_TYPE:
        return _make_state_check(entity_id, "on")

    msg = f"Unknown condition type: {condition_type}"
    raise ValueError(msg)
