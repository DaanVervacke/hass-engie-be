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

from ._peaks import captar_peak_events
from .const import CONF_CUSTOMER_NUMBER, LOGGER, SUBENTRY_TYPE_CUSTOMER_ACCOUNT
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
        if subentry.subentry_type != SUBENTRY_TYPE_CUSTOMER_ACCOUNT:
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

    _attr_name = None
    _attr_icon = "mdi:calendar"

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
        # Suggest a CAN-prefixed entity_id slug so each customer
        # account gets its own predictable calendar entity_id without
        # HA auto-suffixing on the friendly name. There is only one
        # calendar entity per subentry, so no trailing ``_calendar``
        # is needed. Only effective on first registration; existing
        # installs are migrated via ``_async_migrate_entity_id_slugs``
        # in ``__init__``.
        can = subentry.data.get(CONF_CUSTOMER_NUMBER)
        if can:
            self._attr_suggested_object_id = f"engie_belgium_{can}"

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
        from homeassistant.util import dt as dt_util  # noqa: PLC0415

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
