"""
Purpose-specific triggers for the ENGIE Belgium integration.

Exposes ENGIE state transitions and calendar-event boundaries as first-class
triggers in the HA automation editor.

Supported trigger keys:

Binary transitions (fire on a specific edge):
- ``engie_be.epex_became_negative``         EPEX price goes negative
- ``engie_be.epex_no_longer_negative``       EPEX price returns positive
- ``engie_be.offtake_became_optimal``        offtake slot becomes optimal
- ``engie_be.offtake_no_longer_optimal``     offtake slot leaves optimal
- ``engie_be.injection_became_optimal``      injection slot becomes optimal
- ``engie_be.injection_no_longer_optimal``   injection slot leaves optimal
- ``engie_be.happy_hours_became_active``     Happy Hours window opens
- ``engie_be.happy_hours_became_inactive``   Happy Hours window closes
- ``engie_be.authentication_lost``           auth sensor drops to off
- ``engie_be.authentication_restored``       auth sensor recovers to on

Enum transitions (fires on any level/slot change):
- ``engie_be.solar_surplus_level_changed``   any surplus level change
- ``engie_be.offtake_slot_changed``          any offtake slot change
- ``engie_be.injection_slot_changed``        any injection slot change

Enum "became" (fires when the sensor enters a chosen level/slot):
- ``engie_be.solar_surplus_became``          surplus reaches a chosen level
- ``engie_be.offtake_slot_became``           offtake enters a chosen slot
- ``engie_be.injection_slot_became``         injection enters a chosen slot

Numerical threshold triggers (Phase B):
- ``engie_be.epex_current_crossed_threshold``
- ``engie_be.epex_next_hour_crossed_threshold``
- ``engie_be.solar_surplus_current_crossed_threshold``
- ``engie_be.solar_surplus_next_hour_crossed_threshold``
- ``engie_be.captar_peak_crossed_threshold``

Value-changed triggers (Phase C):
- ``engie_be.captar_peak_updated``
- ``engie_be.epex_high_today_updated``
- ``engie_be.epex_low_today_updated``

Calendar event-class triggers (Phase E):
- ``engie_be.captar_peak_window_started``    fires at start of captar peak window
- ``engie_be.captar_peak_window_ended``      fires at end of captar peak window
- ``engie_be.happy_hours_window_started``    fires at start of Happy Hours window
- ``engie_be.happy_hours_window_ended``      fires at end of Happy Hours window
- ``engie_be.tou_slot_started``              fires when a TOU slot boundary begins
"""

from __future__ import annotations

import abc
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.calendar import DOMAIN as CALENDAR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.automation import DomainSpec
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.trigger import (
    ENTITY_STATE_TRIGGER_SCHEMA,
    ENTITY_STATE_TRIGGER_SCHEMA_WITH_BEHAVIOR,
    EntityTargetStateTriggerBase,
    EntityTriggerBase,
    Trigger,
    TriggerActionRunner,
    TriggerConfig,
    make_entity_numerical_state_crossed_threshold_trigger,
    make_entity_target_state_trigger,
)
from homeassistant.util import dt as dt_util

from ._automation_helpers import filter_by_translation_key
from ._happy_hour import HAPPY_HOUR_EVENT_SUMMARY
from ._peaks import CAPTAR_EVENT_SUMMARY
from ._tou_calendar import format_tou_event_summary
from .const import (
    DOMAIN,
    LOGGER,
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

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LEVEL = "level"
_SLOT = "slot"

# ---------------------------------------------------------------------------
# Shared entity-filter helper (implementation lives in _automation_helpers)
# ---------------------------------------------------------------------------


def _filter_by_translation_key(
    hass: HomeAssistant,
    entities: set[str],
    translation_key: str,
) -> set[str]:
    """Return entities owned by this integration with the given translation_key."""
    return filter_by_translation_key(hass, entities, translation_key)


# ---------------------------------------------------------------------------
# Phase A - Binary state-transition triggers
# ---------------------------------------------------------------------------

# _BinaryEdgeTrigger provides a single class to inherit from instead of
# 10 separate factory-generated bases.  Subclasses declare _translation_key
# and _to_states; entity_filter is provided here.


class _BinaryEdgeTrigger(  # type: ignore[valid-type, misc]
    make_entity_target_state_trigger({BINARY_SENSOR_DOMAIN: DomainSpec()}, set())
):
    """
    Base for binary-sensor edge triggers restricted to a single ENGIE entity.

    Subclasses must declare:
    - ``_translation_key: ClassVar[str]`` - the entity translation key to match
    - ``_to_states: ClassVar[set[str]]`` - the target state(s) that trigger firing

    The ``_to_states`` class variable overrides the (empty) set baked into the
    factory base class.  ``make_entity_target_state_trigger`` stores the
    target-state set as a class attribute; overriding it here works because the
    instance-level lookup finds the subclass attribute first.
    """

    _translation_key: ClassVar[str]

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict to the matching ENGIE binary sensor."""
        candidates = super().entity_filter(entities)
        return _filter_by_translation_key(self._hass, candidates, self._translation_key)


class EpexBecameNegativeTrigger(_BinaryEdgeTrigger):
    """Trigger: EPEX price became negative (binary sensor off -> on)."""

    _translation_key = TRANSLATION_KEY_EPEX_NEGATIVE
    _to_states: ClassVar[set[str]] = {STATE_ON}


class EpexNoLongerNegativeTrigger(_BinaryEdgeTrigger):
    """Trigger: EPEX price no longer negative (binary sensor on -> off)."""

    _translation_key = TRANSLATION_KEY_EPEX_NEGATIVE
    _to_states: ClassVar[set[str]] = {STATE_OFF}


class OfftakeBecameOptimalTrigger(_BinaryEdgeTrigger):
    """Trigger: TOU offtake slot became optimal (binary sensor off -> on)."""

    _translation_key = TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL
    _to_states: ClassVar[set[str]] = {STATE_ON}


class OfftakeNoLongerOptimalTrigger(_BinaryEdgeTrigger):
    """Trigger: TOU offtake slot no longer optimal (binary sensor on -> off)."""

    _translation_key = TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL
    _to_states: ClassVar[set[str]] = {STATE_OFF}


class InjectionBecameOptimalTrigger(_BinaryEdgeTrigger):
    """Trigger: TOU injection slot became optimal (binary sensor off -> on)."""

    _translation_key = TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL
    _to_states: ClassVar[set[str]] = {STATE_ON}


class InjectionNoLongerOptimalTrigger(_BinaryEdgeTrigger):
    """Trigger: TOU injection slot no longer optimal (binary sensor on -> off)."""

    _translation_key = TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL
    _to_states: ClassVar[set[str]] = {STATE_OFF}


class HappyHoursBecameActiveTrigger(_BinaryEdgeTrigger):
    """Trigger: Happy Hours window opened (binary sensor off -> on)."""

    _translation_key = TRANSLATION_KEY_HAPPY_HOURS_ACTIVE
    _to_states: ClassVar[set[str]] = {STATE_ON}


class HappyHoursBecameInactiveTrigger(_BinaryEdgeTrigger):
    """Trigger: Happy Hours window closed (binary sensor on -> off)."""

    _translation_key = TRANSLATION_KEY_HAPPY_HOURS_ACTIVE
    _to_states: ClassVar[set[str]] = {STATE_OFF}


class AuthenticationLostTrigger(_BinaryEdgeTrigger):
    """Trigger: ENGIE authentication was lost (binary sensor on -> off)."""

    _translation_key = TRANSLATION_KEY_AUTHENTICATION
    _to_states: ClassVar[set[str]] = {STATE_OFF}


class AuthenticationRestoredTrigger(_BinaryEdgeTrigger):
    """Trigger: ENGIE authentication was restored (binary sensor off -> on)."""

    _translation_key = TRANSLATION_KEY_AUTHENTICATION
    _to_states: ClassVar[set[str]] = {STATE_ON}


# ---------------------------------------------------------------------------
# Phase A - Enum "changed" triggers (any state change)
# ---------------------------------------------------------------------------

# These fire whenever the sensor value changes at all. Use plain
# EntityTriggerBase - no target state restriction, no factory needed.

_SOLAR_SURPLUS_CHANGED_SCHEMA = ENTITY_STATE_TRIGGER_SCHEMA_WITH_BEHAVIOR
_TOU_SLOT_CHANGED_SCHEMA = ENTITY_STATE_TRIGGER_SCHEMA_WITH_BEHAVIOR


class SolarSurplusLevelChangedTrigger(EntityTriggerBase):
    """Trigger: solar surplus forecast level changed to any value."""

    _domain_specs: ClassVar[dict[str, DomainSpec]] = {SENSOR_DOMAIN: DomainSpec()}
    _schema = _SOLAR_SURPLUS_CHANGED_SCHEMA

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict to the ENGIE solar_surplus_forecast sensor."""
        candidates = super().entity_filter(entities)
        return _filter_by_translation_key(
            self._hass, candidates, TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST
        )


class OfftakeSlotChangedTrigger(EntityTriggerBase):
    """Trigger: TOU offtake slot changed to any value."""

    _domain_specs: ClassVar[dict[str, DomainSpec]] = {SENSOR_DOMAIN: DomainSpec()}
    _schema = _TOU_SLOT_CHANGED_SCHEMA

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict to the ENGIE tou_offtake_slot sensor."""
        candidates = super().entity_filter(entities)
        return _filter_by_translation_key(
            self._hass, candidates, TRANSLATION_KEY_TOU_OFFTAKE_SLOT
        )


class InjectionSlotChangedTrigger(EntityTriggerBase):
    """Trigger: TOU injection slot changed to any value."""

    _domain_specs: ClassVar[dict[str, DomainSpec]] = {SENSOR_DOMAIN: DomainSpec()}
    _schema = _TOU_SLOT_CHANGED_SCHEMA

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict to the ENGIE tou_injection_slot sensor."""
        candidates = super().entity_filter(entities)
        return _filter_by_translation_key(
            self._hass, candidates, TRANSLATION_KEY_TOU_INJECTION_SLOT
        )


# ---------------------------------------------------------------------------
# Phase A - Enum "became" triggers (option-parameterised)
# ---------------------------------------------------------------------------

# One trigger key per enum sensor. The user picks the desired level/slot via a
# `level` or `slot` option in the schema. The trigger fires only when the
# sensor transitions into that specific value (i.e. the previous value was NOT
# the target value). Mirror of _OptionBasedStateCondition in condition.py.

_SOLAR_SURPLUS_BECAME_SCHEMA = ENTITY_STATE_TRIGGER_SCHEMA_WITH_BEHAVIOR.extend(
    {
        vol.Required("options"): {
            vol.Required(_LEVEL): vol.In(SOLAR_SURPLUS_LEVELS),
        },
    }
)

_TOU_SLOT_BECAME_SCHEMA = ENTITY_STATE_TRIGGER_SCHEMA_WITH_BEHAVIOR.extend(
    {
        vol.Required("options"): {
            vol.Required(_SLOT): vol.In(TOU_SLOT_CODES),
        },
    }
)


class _OptionBasedStateTrigger(EntityTargetStateTriggerBase):
    """
    Base for triggers that fire when a sensor reaches a config-specified state.

    Subclasses declare ``_option_key`` (the field in ``options``) and
    ``_translation_key`` (used to restrict entity_filter).  ``__init__``
    reads the chosen value and sets ``_to_states`` so the inherited
    ``is_valid_state`` / ``is_valid_transition`` logic works unchanged.
    """

    _option_key: ClassVar[str]
    _translation_key: ClassVar[str]
    _domain_specs: ClassVar[dict[str, DomainSpec]] = {SENSOR_DOMAIN: DomainSpec()}

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        """Initialise and set the target state from config options."""
        super().__init__(hass, config)
        options: dict[str, Any] = config.options or {}
        self._to_states = {options[self._option_key]}

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict to ENGIE entities with the correct translation_key."""
        candidates = super().entity_filter(entities)
        return _filter_by_translation_key(self._hass, candidates, self._translation_key)


class SolarSurplusBecameTrigger(_OptionBasedStateTrigger):
    """Trigger: solar surplus forecast reached a specific level."""

    _option_key = _LEVEL
    _translation_key = TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST
    _schema = _SOLAR_SURPLUS_BECAME_SCHEMA


class OfftakeSlotBecameTrigger(_OptionBasedStateTrigger):
    """Trigger: TOU offtake slot entered a specific slot."""

    _option_key = _SLOT
    _translation_key = TRANSLATION_KEY_TOU_OFFTAKE_SLOT
    _schema = _TOU_SLOT_BECAME_SCHEMA


class InjectionSlotBecameTrigger(_OptionBasedStateTrigger):
    """Trigger: TOU injection slot entered a specific slot."""

    _option_key = _SLOT
    _translation_key = TRANSLATION_KEY_TOU_INJECTION_SLOT
    _schema = _TOU_SLOT_BECAME_SCHEMA


# ---------------------------------------------------------------------------
# Phase B - Numerical threshold triggers
# ---------------------------------------------------------------------------

# _ThresholdTrigger collapses the 5 numerical threshold trigger classes.
# Each factory call produced an EntityNumericalStateCrossedThresholdTriggerBase
# subclass; we now share a single base and override _translation_key.


class _ThresholdTrigger(  # type: ignore[valid-type, misc]
    make_entity_numerical_state_crossed_threshold_trigger({SENSOR_DOMAIN: DomainSpec()})
):
    """
    Base for numerical threshold triggers restricted to a single ENGIE sensor.

    Subclasses declare ``_translation_key`` to restrict entity_filter.
    """

    _translation_key: ClassVar[str]

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict to the matching ENGIE sensor."""
        candidates = super().entity_filter(entities)
        return _filter_by_translation_key(self._hass, candidates, self._translation_key)


class EpexCurrentCrossedThresholdTrigger(_ThresholdTrigger):
    """Trigger: EPEX current-hour price crossed a threshold."""

    _translation_key = TRANSLATION_KEY_EPEX_CURRENT


class EpexNextHourCrossedThresholdTrigger(_ThresholdTrigger):
    """Trigger: EPEX next-hour price crossed a threshold."""

    _translation_key = TRANSLATION_KEY_EPEX_NEXT_HOUR


class SolarSurplusCurrentCrossedThresholdTrigger(_ThresholdTrigger):
    """Trigger: solar surplus current-hour value crossed a threshold."""

    _translation_key = TRANSLATION_KEY_SOLAR_SURPLUS_CURRENT


class SolarSurplusNextHourCrossedThresholdTrigger(_ThresholdTrigger):
    """Trigger: solar surplus next-hour value crossed a threshold."""

    _translation_key = TRANSLATION_KEY_SOLAR_SURPLUS_NEXT_HOUR


class CaptarPeakCrossedThresholdTrigger(_ThresholdTrigger):
    """Trigger: captar monthly peak power crossed a threshold."""

    _translation_key = TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER


# ---------------------------------------------------------------------------
# Phase C - Value-changed triggers
# ---------------------------------------------------------------------------

# These fire whenever the numeric value changes at all. Use EntityTriggerBase
# (not the numerical variant) so that non-numeric states are not special-cased.
# The "any change" semantics come from the base class's default
# ``is_valid_transition`` (from_state.state != to_state.state).
# Plain ENTITY_STATE_TRIGGER_SCHEMA is used (no above/below options - those
# options are not meaningful for "any change" semantics).

_VALUE_CHANGED_SCHEMA = ENTITY_STATE_TRIGGER_SCHEMA


class _ValueChangedTrigger(EntityTriggerBase):
    """
    Base for value-changed triggers: fires on any state change.

    Subclasses only need to declare ``_translation_key``.
    """

    _domain_specs: ClassVar[dict[str, DomainSpec]] = {SENSOR_DOMAIN: DomainSpec()}
    _schema = _VALUE_CHANGED_SCHEMA
    _translation_key: ClassVar[str]

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict to the matching ENGIE sensor."""
        candidates = super().entity_filter(entities)
        return _filter_by_translation_key(self._hass, candidates, self._translation_key)


class CaptarPeakUpdatedTrigger(_ValueChangedTrigger):
    """Trigger: captar monthly peak power value changed."""

    _translation_key = TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER


class EpexHighTodayUpdatedTrigger(_ValueChangedTrigger):
    """Trigger: EPEX highest price today changed."""

    _translation_key = TRANSLATION_KEY_EPEX_HIGH_TODAY


class EpexLowTodayUpdatedTrigger(_ValueChangedTrigger):
    """Trigger: EPEX lowest price today changed."""

    _translation_key = TRANSLATION_KEY_EPEX_LOW_TODAY


# ---------------------------------------------------------------------------
# Phase E - Calendar event-class triggers
# ---------------------------------------------------------------------------

# These triggers watch the ENGIE Belgium calendar entity. At attach time they
# find all calendar entities registered by this integration, fetch events for
# the next 7 days, and schedule async_track_point_in_time for each matching
# event start or end. After firing they re-schedule for the next occurrence.
#
# Unlike Phases A-D these are NOT entity-state triggers. They subclass Trigger
# directly and implement async_attach_runner with a time-based scheduler.

_CAL_LOOKAHEAD_DAYS = 7
_DIRECTION = "direction"

_TOU_SLOT_CALENDAR_SCHEMA: vol.Schema = vol.Schema(
    {
        vol.Required("options"): {
            vol.Required(_DIRECTION): vol.In(["offtake", "injection"]),
            vol.Required(_SLOT): vol.In(TOU_SLOT_CODES),
        },
    },
    extra=vol.ALLOW_EXTRA,
)

_SIMPLE_CAL_SCHEMA: vol.Schema = vol.Schema({}, extra=vol.ALLOW_EXTRA)


def _engie_calendar_entity_ids(hass: HomeAssistant) -> list[str]:
    """Return entity_ids of all ENGIE Belgium calendar entities."""
    reg = er.async_get(hass)
    return [
        entry.entity_id
        for entry in reg.entities.values()
        if entry.platform == DOMAIN and entry.domain == CALENDAR_DOMAIN
    ]


async def _get_calendar_events(hass: HomeAssistant, entity_id: str) -> list[Any]:
    """
    Fetch upcoming calendar events from the entity object, if available.

    Returns an empty list if the calendar entity is not loaded or the
    EntityComponent is not registered.
    """
    component = hass.data.get(CALENDAR_DOMAIN)
    if component is None:
        return []
    calendar_entity = component.get_entity(entity_id)
    if calendar_entity is None:
        return []
    now = dt_util.utcnow()
    end = now + timedelta(days=_CAL_LOOKAHEAD_DAYS)
    try:
        return await calendar_entity.async_get_events(hass, now, end)
    except (HomeAssistantError, TimeoutError) as exc:
        LOGGER.debug("Failed to fetch events from %s: %s", entity_id, exc)
        return []


class _CalendarEventTrigger(Trigger, abc.ABC):
    """
    Base for calendar-event triggers that fire at event boundaries.

    Subclasses must implement ``_matches_event`` and declare ``_is_start``
    (True -> fire at event start, False -> fire at event end).
    Subclasses may also override ``_schema`` for option-bearing triggers.
    """

    _schema: ClassVar[vol.Schema] = _SIMPLE_CAL_SCHEMA
    _is_start: ClassVar[bool]

    @classmethod
    async def async_validate_config(
        cls,
        hass: HomeAssistant,  # noqa: ARG003
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate config against the trigger schema."""
        return cls._schema(config)

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        """Initialise the calendar trigger."""
        super().__init__(hass, config)
        self._options: dict[str, Any] = config.options or {}

    @abc.abstractmethod
    def _matches_event(self, event: Any) -> bool:
        """Return True if the calendar event should cause this trigger to fire."""

    async def async_attach_runner(self, run_action: TriggerActionRunner) -> Any:
        """Attach the trigger: schedule a listener per ENGIE calendar."""
        unsub_refs: list[Any] = []

        async def _schedule_next() -> None:
            """Find the next matching event boundary across all ENGIE calendars."""
            for entity_id in _engie_calendar_entity_ids(self._hass):
                events = await _get_calendar_events(self._hass, entity_id)
                now = dt_util.utcnow()
                candidates: list[tuple[datetime, Any]] = []
                for ev in events:
                    boundary = ev.start if self._is_start else ev.end
                    if boundary > now and self._matches_event(ev):
                        candidates.append((boundary, ev))
                if candidates:
                    candidates.sort(key=lambda t: t[0])
                    fire_at, ev = candidates[0]

                    def _make_callback(
                        fire_event: Any,
                        cal_entity_id: str,
                    ) -> Any:
                        async def _on_time(_now: datetime) -> None:
                            run_action(
                                {
                                    "event": fire_event,
                                    "entity_id": cal_entity_id,
                                },
                                f"calendar event {fire_event.summary}",
                                None,
                            )
                            await _schedule_next()

                        return _on_time

                    unsub = async_track_point_in_time(
                        self._hass, _make_callback(ev, entity_id), fire_at
                    )
                    unsub_refs.append(unsub)
                    # Each calendar gets its own listener; do NOT break here.
                    # Multi-BAN setups have one calendar per subentry and all
                    # should fire independently.

        await _schedule_next()

        def _cancel() -> None:
            for unsub in unsub_refs:
                unsub()
            unsub_refs.clear()

        return _cancel


class CaptarPeakWindowStartedTrigger(_CalendarEventTrigger):
    """Trigger: fires at the start of a captar monthly peak window."""

    _is_start = True

    def _matches_event(self, event: Any) -> bool:
        """Return True for captar peak events."""
        return event.summary == CAPTAR_EVENT_SUMMARY


class CaptarPeakWindowEndedTrigger(_CalendarEventTrigger):
    """Trigger: fires at the end of a captar monthly peak window."""

    _is_start = False

    def _matches_event(self, event: Any) -> bool:
        """Return True for captar peak events."""
        return event.summary == CAPTAR_EVENT_SUMMARY


class HappyHoursWindowStartedTrigger(_CalendarEventTrigger):
    """Trigger: fires at the start of a Happy Hours window."""

    _is_start = True

    def _matches_event(self, event: Any) -> bool:
        """Return True for Happy Hours events."""
        return event.summary == HAPPY_HOUR_EVENT_SUMMARY


class HappyHoursWindowEndedTrigger(_CalendarEventTrigger):
    """Trigger: fires at the end of a Happy Hours window."""

    _is_start = False

    def _matches_event(self, event: Any) -> bool:
        """Return True for Happy Hours events."""
        return event.summary == HAPPY_HOUR_EVENT_SUMMARY


class TouSlotStartedTrigger(_CalendarEventTrigger):
    """Trigger: fires when a TOU slot boundary begins (direction + slot match)."""

    _schema = _TOU_SLOT_CALENDAR_SCHEMA
    _is_start = True

    def _matches_event(self, event: Any) -> bool:
        """Return True when the TOU event summary matches direction and slot."""
        direction = self._options.get(_DIRECTION, "")
        slot = self._options.get(_SLOT, "")
        # Summary format: "TOU: {code} ({direction})" - slot code is lowercase.
        expected = format_tou_event_summary(slot, direction)
        return event.summary == expected


# ---------------------------------------------------------------------------
# Public registry
# ---------------------------------------------------------------------------

TRIGGERS: dict[str, type[Trigger]] = {
    # Phase A - binary transitions
    "epex_became_negative": EpexBecameNegativeTrigger,
    "epex_no_longer_negative": EpexNoLongerNegativeTrigger,
    "offtake_became_optimal": OfftakeBecameOptimalTrigger,
    "offtake_no_longer_optimal": OfftakeNoLongerOptimalTrigger,
    "injection_became_optimal": InjectionBecameOptimalTrigger,
    "injection_no_longer_optimal": InjectionNoLongerOptimalTrigger,
    "happy_hours_became_active": HappyHoursBecameActiveTrigger,
    "happy_hours_became_inactive": HappyHoursBecameInactiveTrigger,
    "authentication_lost": AuthenticationLostTrigger,
    "authentication_restored": AuthenticationRestoredTrigger,
    # Phase A - enum changed (any value)
    "solar_surplus_level_changed": SolarSurplusLevelChangedTrigger,
    "offtake_slot_changed": OfftakeSlotChangedTrigger,
    "injection_slot_changed": InjectionSlotChangedTrigger,
    # Phase A - enum became (specific value)
    "solar_surplus_became": SolarSurplusBecameTrigger,
    "offtake_slot_became": OfftakeSlotBecameTrigger,
    "injection_slot_became": InjectionSlotBecameTrigger,
    # Phase B - numerical thresholds
    "epex_current_crossed_threshold": EpexCurrentCrossedThresholdTrigger,
    "epex_next_hour_crossed_threshold": EpexNextHourCrossedThresholdTrigger,
    "solar_surplus_current_crossed_threshold": (
        SolarSurplusCurrentCrossedThresholdTrigger
    ),
    "solar_surplus_next_hour_crossed_threshold": (
        SolarSurplusNextHourCrossedThresholdTrigger
    ),
    "captar_peak_crossed_threshold": CaptarPeakCrossedThresholdTrigger,
    # Phase C - value changed
    "captar_peak_updated": CaptarPeakUpdatedTrigger,
    "epex_high_today_updated": EpexHighTodayUpdatedTrigger,
    "epex_low_today_updated": EpexLowTodayUpdatedTrigger,
    # Phase E - calendar event-class triggers
    "captar_peak_window_started": CaptarPeakWindowStartedTrigger,
    "captar_peak_window_ended": CaptarPeakWindowEndedTrigger,
    "happy_hours_window_started": HappyHoursWindowStartedTrigger,
    "happy_hours_window_ended": HappyHoursWindowEndedTrigger,
    "tou_slot_started": TouSlotStartedTrigger,
}


async def async_get_triggers(
    hass: HomeAssistant,  # noqa: ARG001
) -> dict[str, type[Trigger]]:
    """Return the integration-scoped ENGIE Belgium triggers."""
    return TRIGGERS
