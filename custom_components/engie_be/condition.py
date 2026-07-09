"""
Purpose-specific conditions for the ENGIE Belgium integration.

Exposes ENGIE state as first-class conditions in the HA automation editor.

Supported conditions:

- ``engie_be.epex_price_is_negative``    -> binary sensor is ``on``
- ``engie_be.solar_surplus_is_at_level`` -> enum sensor matches a surplus level
- ``engie_be.offtake_slot_is``           -> enum sensor matches a TOU slot code
- ``engie_be.injection_slot_is``         -> enum sensor matches a TOU slot code
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_ON
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.automation import DomainSpec
from homeassistant.helpers.condition import (
    ENTITY_STATE_CONDITION_SCHEMA_ANY_ALL,
    Condition,
    ConditionConfig,
    EntityStateConditionBase,
)

from .const import (
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

_LEVEL = "level"
_SLOT = "slot"

# ---------------------------------------------------------------------------
# Shared entity-filter helper
# ---------------------------------------------------------------------------


def _filter_by_translation_key(
    hass: HomeAssistant,
    entities: set[str],
    translation_key: str,
) -> set[str]:
    """Return entities owned by this integration with the given translation_key."""
    reg = er.async_get(hass)
    result: set[str] = set()
    for entity_id in entities:
        entry = reg.async_get(entity_id)
        if (
            entry is not None
            and entry.platform == DOMAIN
            and entry.translation_key == translation_key
        ):
            result.add(entity_id)
    return result


# ---------------------------------------------------------------------------
# epex_price_is_negative - direct EntityStateConditionBase subclass
# ---------------------------------------------------------------------------


class EpexPriceIsNegativeCondition(EntityStateConditionBase):
    """Condition: EPEX price is negative (binary sensor is on)."""

    _domain_specs: ClassVar[dict[str, DomainSpec]] = {
        BINARY_SENSOR_DOMAIN: DomainSpec()
    }

    def __init__(self, hass: HomeAssistant, config: ConditionConfig) -> None:
        """Initialise and set the expected state."""
        super().__init__(hass, config)
        self._states = {STATE_ON}

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict to the ENGIE epex_negative binary sensor."""
        candidates = super().entity_filter(entities)
        return _filter_by_translation_key(
            self._hass, candidates, TRANSLATION_KEY_EPEX_NEGATIVE
        )


# ---------------------------------------------------------------------------
# Shared base for option-parameterised conditions
# ---------------------------------------------------------------------------


class _OptionBasedStateCondition(EntityStateConditionBase):
    """Base for conditions that match a sensor state from a config option."""

    _option_key: ClassVar[str]
    _translation_key: ClassVar[str]
    _domain_specs: ClassVar[dict[str, DomainSpec]] = {SENSOR_DOMAIN: DomainSpec()}

    def __init__(self, hass: HomeAssistant, config: ConditionConfig) -> None:
        """Initialise and set the expected state from config options."""
        super().__init__(hass, config)
        options: dict[str, Any] = config.options or {}
        self._states = {options[self._option_key]}

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict to ENGIE entities with the correct translation_key."""
        candidates = super().entity_filter(entities)
        return _filter_by_translation_key(self._hass, candidates, self._translation_key)


# ---------------------------------------------------------------------------
# solar_surplus_is_at_level
# ---------------------------------------------------------------------------

_SOLAR_SURPLUS_SCHEMA = ENTITY_STATE_CONDITION_SCHEMA_ANY_ALL.extend(
    {
        vol.Required("options"): {
            vol.Required(_LEVEL): vol.In(SOLAR_SURPLUS_LEVELS),
        },
    }
)


class SolarSurplusIsAtLevelCondition(_OptionBasedStateCondition):
    """Condition: solar surplus forecast is at a specific level."""

    _option_key = _LEVEL
    _translation_key = TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST
    _schema = _SOLAR_SURPLUS_SCHEMA


# ---------------------------------------------------------------------------
# offtake_slot_is
# ---------------------------------------------------------------------------

_TOU_SLOT_SCHEMA = ENTITY_STATE_CONDITION_SCHEMA_ANY_ALL.extend(
    {
        vol.Required("options"): {
            vol.Required(_SLOT): vol.In(TOU_SLOT_CODES),
        },
    }
)


class OfftakeSlotIsCondition(_OptionBasedStateCondition):
    """Condition: current TOU offtake slot matches the expected code."""

    _option_key = _SLOT
    _translation_key = TRANSLATION_KEY_TOU_OFFTAKE_SLOT
    _schema = _TOU_SLOT_SCHEMA


# ---------------------------------------------------------------------------
# injection_slot_is
# ---------------------------------------------------------------------------


class InjectionSlotIsCondition(_OptionBasedStateCondition):
    """Condition: current TOU injection slot matches the expected code."""

    _option_key = _SLOT
    _translation_key = TRANSLATION_KEY_TOU_INJECTION_SLOT
    _schema = _TOU_SLOT_SCHEMA


# ---------------------------------------------------------------------------
# Public registry
# ---------------------------------------------------------------------------

CONDITIONS: dict[str, type[Condition]] = {
    "epex_price_is_negative": EpexPriceIsNegativeCondition,
    "solar_surplus_is_at_level": SolarSurplusIsAtLevelCondition,
    "offtake_slot_is": OfftakeSlotIsCondition,
    "injection_slot_is": InjectionSlotIsCondition,
}


async def async_get_conditions(
    hass: HomeAssistant,  # noqa: ARG001
) -> dict[str, type[Condition]]:
    """Return the integration-scoped ENGIE Belgium conditions."""
    return CONDITIONS
