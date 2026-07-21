"""
Event platform for the ENGIE Belgium integration.

Records every meaningful state transition that the 34 automation
triggers already detect (EPEX going negative, Happy Hours activating,
TOU slot changing, authentication lost/restored, ...) as a logbook-
visible ``event`` entity. This platform does not duplicate any
transition-detection logic: it watches the existing binary sensors and
sensors via ``async_track_state_change_event`` and re-fires their
transitions as HA events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_EXPOSE_ALL_ENTITIES,
    DOMAIN,
    LOGGER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
    TRANSLATION_KEY_AUTHENTICATION,
    TRANSLATION_KEY_EPEX_NEGATIVE,
    TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR,
    TRANSLATION_KEY_HAPPY_HOURS_ACTIVE,
    TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
    TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL,
    TRANSLATION_KEY_TOU_INJECTION_SLOT,
    TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL,
    TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
)
from .entity import login_device_info, subentry_device_info

# Coordinator centralises updates; this platform doesn't poll at all -- it
# reacts to sibling-entity state changes instead.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import Event, EventStateChangedData, HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .data import EngieBeConfigEntry


@dataclass(frozen=True)
class WatchedSibling:
    """
    Transition rule for one watched sibling entity's translation_key.

    ``transitions`` maps an exact ``(old_state, new_state)`` pair to the
    event_type it fires -- used for binary sensors with a fixed pair of
    on/off event types. ``changed_event_type`` fires on any state change
    where the value actually differs -- used for enum sensors (e.g. the
    TOU slot sensors) where every distinct value is a valid transition
    target, not just two fixed endpoints.
    """

    translation_key: str
    transitions: dict[tuple[str, str], str] = field(default_factory=dict)
    changed_event_type: str | None = None

    def resolve(self, old: str, new: str) -> tuple[str, dict[str, str]] | None:
        """Return the ``(event_type, attributes)`` pair for a transition, or None."""
        event_type = self.transitions.get((old, new))
        if event_type is not None:
            return event_type, {}
        if self.changed_event_type is not None and old != new:
            return self.changed_event_type, {"previous": old, "current": new}
        return None


@dataclass(kw_only=True, frozen=True)
class EngieBeEventEntityDescription(EventEntityDescription):
    """Event entity description extended with sibling-entity watch rules."""

    watched_translation_keys: tuple[WatchedSibling, ...] = ()


EPEX_EVENTS_DESCRIPTION = EngieBeEventEntityDescription(
    key="epex_events",
    translation_key="epex_events",
    event_types=["price_negative", "price_positive"],
    watched_translation_keys=(
        WatchedSibling(
            translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
            transitions={
                ("off", "on"): "price_negative",
                ("on", "off"): "price_positive",
            },
        ),
    ),
)

EPEX_EVENTS_QUARTER_HOURLY_DESCRIPTION = EngieBeEventEntityDescription(
    key="epex_events_quarter_hourly",
    translation_key="epex_events_quarter_hourly",
    event_types=["price_negative", "price_positive"],
    watched_translation_keys=(
        WatchedSibling(
            translation_key=TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR,
            transitions={
                ("off", "on"): "price_negative",
                ("on", "off"): "price_positive",
            },
        ),
    ),
)

HAPPY_HOURS_EVENTS_DESCRIPTION = EngieBeEventEntityDescription(
    key="happy_hours_events",
    translation_key="happy_hours_events",
    event_types=["activated", "deactivated"],
    watched_translation_keys=(
        WatchedSibling(
            translation_key=TRANSLATION_KEY_HAPPY_HOURS_ACTIVE,
            transitions={("off", "on"): "activated", ("on", "off"): "deactivated"},
        ),
    ),
)

TOU_EVENTS_DESCRIPTION = EngieBeEventEntityDescription(
    key="tou_events",
    translation_key="tou_events",
    event_types=[
        "offtake_slot_changed",
        "injection_slot_changed",
        "offtake_optimal",
        "offtake_not_optimal",
        "injection_optimal",
        "injection_not_optimal",
    ],
    watched_translation_keys=(
        WatchedSibling(
            translation_key=TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL,
            transitions={
                ("off", "on"): "offtake_optimal",
                ("on", "off"): "offtake_not_optimal",
            },
        ),
        WatchedSibling(
            translation_key=TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL,
            transitions={
                ("off", "on"): "injection_optimal",
                ("on", "off"): "injection_not_optimal",
            },
        ),
        WatchedSibling(
            translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
            changed_event_type="offtake_slot_changed",
        ),
        WatchedSibling(
            translation_key=TRANSLATION_KEY_TOU_INJECTION_SLOT,
            changed_event_type="injection_slot_changed",
        ),
    ),
)

AUTHENTICATION_EVENTS_DESCRIPTION = EngieBeEventEntityDescription(
    key="authentication_events",
    translation_key="authentication_events",
    event_types=["lost", "restored"],
    watched_translation_keys=(
        WatchedSibling(
            translation_key=TRANSLATION_KEY_AUTHENTICATION,
            transitions={("on", "off"): "lost", ("off", "on"): "restored"},
        ),
    ),
)

SOLAR_SURPLUS_EVENTS_DESCRIPTION = EngieBeEventEntityDescription(
    key="solar_surplus_events",
    translation_key="solar_surplus_events",
    event_types=["level_changed"],
    watched_translation_keys=(
        WatchedSibling(
            translation_key=TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST,
            changed_event_type="level_changed",
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """
    Set up the event platform.

    Mirrors ``binary_sensor.py``'s gating pattern: the authentication
    event entity is created once per parent config entry (login-scoped,
    same as ``EngieBeAuthSensor``), while the remaining event entities
    are created once per business-agreement subentry, gated on the same
    feature flags that gate their watched sibling entities.
    """
    expose_all = entry.options.get(CONF_EXPOSE_ALL_ENTITIES, False)

    async_add_entities([EngieBeAuthenticationEvent(entry=entry)])

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_BUSINESS_AGREEMENT:
            continue

        sub_data = entry.runtime_data.subentry_data.get(subentry.subentry_id)
        if sub_data is None:
            LOGGER.warning(
                "No runtime data for subentry %s; skipping event setup",
                subentry.subentry_id,
            )
            continue

        subentry_entities: list[EngieBeTransitionEvent] = []

        if sub_data.coordinator.is_dynamic or expose_all:
            subentry_entities.append(
                EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
            )
            if entry.runtime_data.epex_qh_coordinator is not None:
                subentry_entities.append(
                    EngieBeTransitionEvent(
                        EPEX_EVENTS_QUARTER_HOURLY_DESCRIPTION, entry, subentry
                    )
                )

        if sub_data.feature_flags.happy_hour_enrolled or expose_all:
            subentry_entities.append(
                EngieBeTransitionEvent(HAPPY_HOURS_EVENTS_DESCRIPTION, entry, subentry)
            )

        if sub_data.feature_flags.tou_active or expose_all:
            subentry_entities.append(
                EngieBeTransitionEvent(TOU_EVENTS_DESCRIPTION, entry, subentry)
            )

        if sub_data.feature_flags.solar or expose_all:
            subentry_entities.append(
                EngieBeTransitionEvent(
                    SOLAR_SURPLUS_EVENTS_DESCRIPTION, entry, subentry
                )
            )

        if not subentry_entities:
            continue

        async_add_entities(
            subentry_entities,
            config_subentry_id=subentry.subentry_id,
        )


if TYPE_CHECKING:
    _EventMixinBase = EventEntity
else:
    _EventMixinBase = object


class _TransitionWatcherMixin(_EventMixinBase):
    """
    Shared watched-entity subscription and event-firing logic.

    Both event entities in this module (per-subentry and the entry-wide
    authentication one) build an ``_entity_watch_map`` during
    ``async_added_to_hass`` and then delegate every subsequent state
    change to :meth:`_handle_state_change`. Factoring the map-driven
    firing logic out here keeps the two ``async_added_to_hass``
    overrides -- which differ only in *how* they populate the map --
    the only place that differs between the two entities.
    """

    _entity_watch_map: dict[str, WatchedSibling]

    def _subscribe_to_watch_map(self) -> None:
        """Arm the state-change subscription if any sibling entities were found."""
        if self._entity_watch_map:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    list(self._entity_watch_map),
                    self._handle_state_change,
                )
            )

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Fire the mapped event_type when a watched sibling transitions."""
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        if old_state is None or new_state is None:
            return
        if old_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE) or new_state.state in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            return
        watched = self._entity_watch_map.get(event.data["entity_id"])
        if watched is None:
            return
        result = watched.resolve(old_state.state, new_state.state)
        if result is None:
            return
        event_type, attributes = result
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()


class EngieBeTransitionEvent(_TransitionWatcherMixin, EventEntity):
    """
    Event entity that records state transitions on sibling entities.

    Does not inherit from ``EngieBeEntity`` / ``CoordinatorEntity``:
    ``EventEntity`` extends ``RestoreEntity`` instead, and this entity's
    state comes from watching sibling entities via
    ``async_track_state_change_event`` rather than from a coordinator.
    Attached to the same device as its sibling entities so the events
    show up on the same business-agreement device page.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True

    entity_description: EngieBeEventEntityDescription

    def __init__(
        self,
        description: EngieBeEventEntityDescription,
        entry: EngieBeConfigEntry,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialise the transition-event entity for one subentry."""
        self.entity_description = description
        self._entry = entry
        self._subentry = subentry
        self._attr_translation_key = description.translation_key
        self._attr_event_types = list(description.event_types or [])
        self._attr_unique_id = (
            f"{entry.entry_id}_{subentry.subentry_id}_{description.key}"
        )
        self._attr_device_info = subentry_device_info(subentry)
        self._entity_watch_map: dict[str, WatchedSibling] = {}
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = f"event.engie_belgium_{ban}_{description.key}"

    async def async_added_to_hass(self) -> None:
        """Build the watch map from sibling entities in this subentry."""
        await super().async_added_to_hass()
        reg = er.async_get(self.hass)
        watch_specs = {
            watched.translation_key: watched
            for watched in self.entity_description.watched_translation_keys
        }
        for reg_entry in reg.entities.values():
            if (
                reg_entry.platform != DOMAIN
                or reg_entry.config_subentry_id != self._subentry.subentry_id
                or reg_entry.translation_key not in watch_specs
            ):
                continue
            self._entity_watch_map[reg_entry.entity_id] = watch_specs[
                reg_entry.translation_key
            ]
        self._subscribe_to_watch_map()


class EngieBeAuthenticationEvent(_TransitionWatcherMixin, EventEntity):
    """
    Event entity that records authentication lost/restored transitions.

    Entry-scoped (one per login), mirroring ``EngieBeAuthSensor``: it
    watches the authentication binary sensor across the whole config
    entry rather than being restricted to one business-agreement
    subentry, because a single ENGIE login's Auth0 session is shared
    across every customer account on that login.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True

    entity_description: EngieBeEventEntityDescription = (
        AUTHENTICATION_EVENTS_DESCRIPTION
    )

    def __init__(self, entry: EngieBeConfigEntry) -> None:
        """Initialise the entry-scoped authentication event entity."""
        self._entry = entry
        self._attr_translation_key = self.entity_description.translation_key
        self._attr_event_types = list(self.entity_description.event_types or [])
        self._attr_unique_id = f"{entry.entry_id}_authentication_events"
        self._attr_device_info = login_device_info(entry)
        self._entity_watch_map: dict[str, WatchedSibling] = {}

    async def async_added_to_hass(self) -> None:
        """Build the watch map from the auth binary sensor entry-wide."""
        await super().async_added_to_hass()
        reg = er.async_get(self.hass)
        watched = self.entity_description.watched_translation_keys[0]
        for reg_entry in reg.entities.values():
            if (
                reg_entry.platform != DOMAIN
                or reg_entry.config_entry_id != self._entry.entry_id
                or reg_entry.translation_key != watched.translation_key
            ):
                continue
            self._entity_watch_map[reg_entry.entity_id] = watched
        self._subscribe_to_watch_map()
