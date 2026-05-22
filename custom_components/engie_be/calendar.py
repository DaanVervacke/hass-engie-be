"""
Calendar platform for the ENGIE Belgium integration.

One calendar entity is created per customer-account ConfigSubentry,
attached to that subentry's device. Today this exposes only the monthly
capacity-tariff (captar) peak window, but new event types can be added
without spawning a new calendar entity by registering an additional
``EventProvider`` below.

Each ``EventProvider`` is a callable that takes the per-subentry
coordinator and returns zero or more ``CalendarEvent`` instances. The
data is sourced from the existing coordinator payload, so no additional
API calls are made.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.util import dt as dt_util

from ._peaks import captar_peak_events
from .const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    LOGGER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from .entity import EngieBeEntity

# Coordinator centralises updates; entities never poll individually.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import EngieBeDataUpdateCoordinator
    from .data import EngieBeConfigEntry

EventProvider = Callable[["EngieBeDataUpdateCoordinator"], list[CalendarEvent]]

# Add new event sources by appending a provider here. Each provider returns
# zero or more CalendarEvent objects from the coordinator payload.
EVENT_PROVIDERS: list[EventProvider] = [
    captar_peak_events,
]


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the calendar platform, one entity per customer-account subentry."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_BUSINESS_AGREEMENT:
            continue

        sub_data = entry.runtime_data.subentry_data.get(subentry.subentry_id)
        if sub_data is None:
            LOGGER.warning(
                "No runtime data for subentry %s; skipping calendar setup",
                subentry.subentry_id,
            )
            continue

        async_add_entities(
            [EngieBeCalendar(sub_data.coordinator, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class EngieBeCalendar(EngieBeEntity, CalendarEntity):
    """Aggregated calendar entity for one ENGIE Belgium customer account."""

    # Inherit ``_attr_has_entity_name = True`` from ``EngieBeEntity`` and
    # let HA compose the friendly name as ``<device-name> <entity-name>``,
    # which on HA 2026.4+ resolves to ``<address> ENGIE Belgium``. The
    # entity name itself is supplied via the ``engie_belgium`` translation
    # key below so it stays consistent with every other engie_be entity
    # naming pattern. Earlier versions hard-coded a brand-prefixed
    # ``_attr_name`` and set ``_attr_has_entity_name = False`` to suppress
    # composition; HA 2026.4 changed the composition logic so that opt-out
    # no longer prevents the device-name prefix from being prepended,
    # producing a doubled friendly name ("<address> ENGIE Belgium
    # <address>"). Aligning with the standard convention fixes that and
    # also lets the calendar count toward the ``has-entity-name`` quality
    # scale rule.
    _attr_icon = "mdi:calendar"
    _attr_translation_key = "engie_belgium"

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialise the calendar entity for one customer-account subentry."""
        super().__init__(coordinator, subentry)
        # Subentry-scoped unique ID: the calendar descriptor repeats
        # across every customer account on a single login.
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{subentry.subentry_id}_calendar"
        )
        # Force a BAN-prefixed entity_id so each business agreement
        # gets a predictable, collision-proof calendar entity_id
        # regardless of address. HA's auto-derived slug would key off
        # the friendly name (which embeds the address) and append
        # ``_2`` if two agreements share an address. Setting
        # ``self.entity_id`` directly is the supported escape hatch
        # (``_attr_suggested_object_id`` is not honoured by
        # ``Entity.suggested_object_id``, which reads ``self.name``).
        # Only effective on first registration; entity registry
        # overrides on subsequent boots.
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = f"calendar.engie_belgium_{ban}"

    def _all_events(self) -> list[CalendarEvent]:
        """Collect events from every registered provider."""
        events: list[CalendarEvent] = []
        for provider in EVENT_PROVIDERS:
            events.extend(provider(self.coordinator))
        return events

    @property
    def event(self) -> CalendarEvent | None:
        """
        Return the current or next upcoming event across all providers.

        Active events (``start <= now < end``) win over future ones; among
        future events the soonest ``start`` wins.
        """
        events = self._all_events()
        if not events:
            return None
        now = dt_util.utcnow()
        active = [e for e in events if e.start <= now < e.end]
        if active:
            return min(active, key=lambda e: e.start)
        upcoming = [e for e in events if e.start >= now]
        if upcoming:
            return min(upcoming, key=lambda e: e.start)
        # Otherwise return the most recent past event so users can still see
        # the last billable peak in card-style frontends.
        return max(events, key=lambda e: e.end)

    async def async_get_events(
        self,
        hass: HomeAssistant,  # noqa: ARG002
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return all events overlapping the requested window."""
        return [
            event
            for event in self._all_events()
            if event.end > start_date and event.start < end_date
        ]
