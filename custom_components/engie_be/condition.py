"""
Purpose-specific conditions for the ENGIE Belgium integration.

Exposes ENGIE state as first-class conditions in the HA automation editor.

Supported conditions:

- ``engie_be.epex_price_is_negative`` -> binary sensor is ``on``
- ``engie_be.epex_price_is_negative_quarter_hour`` -> quarter-hourly binary sensor
- ``engie_be.solar_surplus_is_at_level`` -> enum sensor matches a surplus level
- ``engie_be.offtake_slot_is`` -> enum sensor matches a TOU slot code
- ``engie_be.injection_slot_is`` -> enum sensor matches a TOU slot code
- ``engie_be.epex_price_is_below_threshold`` -> EPEX price below threshold
- ``engie_be.epex_price_is_below_threshold_quarter_hour`` -> quarter-hourly below thresh
- ``engie_be.epex_price_is_above_threshold`` -> EPEX price above threshold
- ``engie_be.epex_price_is_above_threshold_quarter_hour`` -> quarter-hourly above thresh
- ``engie_be.offtake_is_optimal`` -> offtake binary sensor is ``on``
- ``engie_be.injection_is_optimal`` -> injection binary sensor is ``on``
- ``engie_be.happy_hours_is_active`` -> happy hours binary sensor is ``on``
- ``engie_be.captar_peak_is_above_threshold`` -> captar peak power above threshold
- ``engie_be.outstanding_balance_is_above_threshold`` -> outstanding balance above
  threshold
- ``engie_be.overdue_amount_is_above_threshold`` -> overdue amount above threshold
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_ON
from homeassistant.helpers.automation import DomainSpec
from homeassistant.helpers.condition import (
    ENTITY_STATE_CONDITION_SCHEMA_ANY_ALL,
    NUMERICAL_CONDITION_SCHEMA,
    Condition,
    ConditionConfig,
    EntityNumericalConditionBase,
    EntityStateConditionBase,
)

from ._automation_helpers import filter_by_translation_key
from .const import (
    SOLAR_SURPLUS_LEVELS,
    TOU_SLOT_CODES,
    TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER,
    TRANSLATION_KEY_EPEX_CURRENT,
    TRANSLATION_KEY_EPEX_CURRENT_QUARTER_HOUR,
    TRANSLATION_KEY_EPEX_NEGATIVE,
    TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR,
    TRANSLATION_KEY_HAPPY_HOURS_ACTIVE,
    TRANSLATION_KEY_OUTSTANDING_BALANCE,
    TRANSLATION_KEY_OVERDUE_AMOUNT,
    TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
    TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL,
    TRANSLATION_KEY_TOU_INJECTION_SLOT,
    TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL,
    TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LEVEL = "level"
_SLOT = "slot"

# ---------------------------------------------------------------------------
# Shared base for binary-sensor "on" conditions
# ---------------------------------------------------------------------------


class _BinaryOnCondition(EntityStateConditionBase):
    """
    Base for binary-sensor conditions that check the sensor is ``on``.

    Subclasses declare ``_translation_key`` to restrict entity_filter to
    a single ENGIE binary sensor per entity class.
    """

    _domain_specs: ClassVar[dict[str, DomainSpec]] = {
        BINARY_SENSOR_DOMAIN: DomainSpec()
    }
    _translation_key: ClassVar[str]

    def __init__(self, hass: HomeAssistant, config: ConditionConfig) -> None:
        """Initialise and set the expected state."""
        super().__init__(hass, config)
        self._states = {STATE_ON}

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict to the matching ENGIE binary sensor."""
        candidates = super().entity_filter(entities)
        return filter_by_translation_key(self._hass, candidates, self._translation_key)


# ---------------------------------------------------------------------------
# epex_price_is_negative
# ---------------------------------------------------------------------------


class EpexPriceIsNegativeCondition(_BinaryOnCondition):
    """Condition: EPEX price is negative (binary sensor is on)."""

    _translation_key = TRANSLATION_KEY_EPEX_NEGATIVE


class EpexPriceIsNegativeQuarterHourCondition(_BinaryOnCondition):
    """Condition: EPEX quarter-hourly price is negative (binary sensor is on)."""

    _translation_key = TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR


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
        return filter_by_translation_key(self._hass, candidates, self._translation_key)


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
# Phase D - Binary "is" conditions (_BinaryOnCondition subclasses)
# ---------------------------------------------------------------------------


class OfftakeIsOptimalCondition(_BinaryOnCondition):
    """Condition: TOU offtake slot is currently the optimal slot."""

    _translation_key = TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL


class InjectionIsOptimalCondition(_BinaryOnCondition):
    """Condition: TOU injection slot is currently the optimal slot."""

    _translation_key = TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL


class HappyHoursIsActiveCondition(_BinaryOnCondition):
    """Condition: a Happy Hours window is currently active."""

    _translation_key = TRANSLATION_KEY_HAPPY_HOURS_ACTIVE


# ---------------------------------------------------------------------------
# Phase D - Numerical threshold conditions (EntityNumericalConditionBase)
# ---------------------------------------------------------------------------


class _NumericalThresholdCondition(EntityNumericalConditionBase):
    """
    Base for numerical threshold conditions tied to a specific ENGIE sensor.

    Subclasses declare ``_translation_key`` to restrict entity_filter to
    a single sensor per entity class.
    """

    _domain_specs: ClassVar[dict[str, DomainSpec]] = {SENSOR_DOMAIN: DomainSpec()}
    _schema = NUMERICAL_CONDITION_SCHEMA
    _translation_key: ClassVar[str]

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict to the matching ENGIE sensor."""
        candidates = super().entity_filter(entities)
        return filter_by_translation_key(self._hass, candidates, self._translation_key)


class EpexPriceIsBelowThresholdCondition(_NumericalThresholdCondition):
    """Condition: EPEX current price is below a configured threshold."""

    _translation_key = TRANSLATION_KEY_EPEX_CURRENT


class EpexPriceIsAboveThresholdCondition(_NumericalThresholdCondition):
    """Condition: EPEX current price is above a configured threshold."""

    _translation_key = TRANSLATION_KEY_EPEX_CURRENT


class EpexPriceIsBelowThresholdQuarterHourCondition(_NumericalThresholdCondition):
    """Condition: EPEX current quarter-hourly price is below a configured threshold."""

    _translation_key = TRANSLATION_KEY_EPEX_CURRENT_QUARTER_HOUR


class EpexPriceIsAboveThresholdQuarterHourCondition(_NumericalThresholdCondition):
    """Condition: EPEX current quarter-hourly price is above a configured threshold."""

    _translation_key = TRANSLATION_KEY_EPEX_CURRENT_QUARTER_HOUR


class CaptarPeakIsAboveThresholdCondition(_NumericalThresholdCondition):
    """Condition: captar monthly peak power is above a configured threshold."""

    _translation_key = TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER


class OutstandingBalanceIsAboveThresholdCondition(_NumericalThresholdCondition):
    """Condition: outstanding balance is above a configured threshold."""

    _translation_key = TRANSLATION_KEY_OUTSTANDING_BALANCE


class OverdueAmountIsAboveThresholdCondition(_NumericalThresholdCondition):
    """Condition: overdue amount is above a configured threshold."""

    _translation_key = TRANSLATION_KEY_OVERDUE_AMOUNT


# ---------------------------------------------------------------------------
# Public registry
# ---------------------------------------------------------------------------

CONDITIONS: dict[str, type[Condition]] = {
    "epex_price_is_negative": EpexPriceIsNegativeCondition,
    "epex_price_is_negative_quarter_hour": EpexPriceIsNegativeQuarterHourCondition,
    "solar_surplus_is_at_level": SolarSurplusIsAtLevelCondition,
    "offtake_slot_is": OfftakeSlotIsCondition,
    "injection_slot_is": InjectionSlotIsCondition,
    # Phase D additions
    "epex_price_is_below_threshold": EpexPriceIsBelowThresholdCondition,
    "epex_price_is_below_threshold_quarter_hour": (
        EpexPriceIsBelowThresholdQuarterHourCondition
    ),
    "epex_price_is_above_threshold": EpexPriceIsAboveThresholdCondition,
    "epex_price_is_above_threshold_quarter_hour": (
        EpexPriceIsAboveThresholdQuarterHourCondition
    ),
    "offtake_is_optimal": OfftakeIsOptimalCondition,
    "injection_is_optimal": InjectionIsOptimalCondition,
    "happy_hours_is_active": HappyHoursIsActiveCondition,
    "captar_peak_is_above_threshold": CaptarPeakIsAboveThresholdCondition,
    "outstanding_balance_is_above_threshold": (
        OutstandingBalanceIsAboveThresholdCondition
    ),
    "overdue_amount_is_above_threshold": OverdueAmountIsAboveThresholdCondition,
}


async def async_get_conditions(
    hass: HomeAssistant,  # noqa: ARG001
) -> dict[str, type[Condition]]:
    """Return the integration-scoped ENGIE Belgium conditions (15 total)."""
    return CONDITIONS
