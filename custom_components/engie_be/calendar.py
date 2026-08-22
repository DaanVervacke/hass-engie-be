"""Calendar platform: one entity per business agreement."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.util import dt as dt_util

from ._happy_hour import happy_hour_events
from ._peaks import captar_peak_events
from ._tou_calendar import tou_slot_events
from .const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_EXPOSE_ALL_ENTITIES,
    LOGGER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from .entity import EngieBeEntity

# Coordinator centralises updates. Entities never poll individually.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import EngieBeDataUpdateCoordinator
    from .data import EngieBeConfigEntry

EventProvider = Callable[["EngieBeDataUpdateCoordinator"], list[CalendarEvent]]

# Always-on providers. Feature-gated ones are appended in ``__init__``.
EVENT_PROVIDERS: list[EventProvider] = [
    captar_peak_events,
]


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the calendar platform, one entity per customer-account subentry."""
    expose_all = entry.options.get(CONF_EXPOSE_ALL_ENTITIES, False)
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
            [
                EngieBeCalendar(
                    sub_data.coordinator,
                    subentry,
                    happy_hour_enrolled=bool(sub_data.feature_flags.happy_hour_enrolled)
                    or expose_all,
                    tou_active=bool(sub_data.feature_flags.tou_active) or expose_all,
                )
            ],
            config_subentry_id=subentry.subentry_id,
        )


class EngieBeCalendar(EngieBeEntity, CalendarEntity):
    """Aggregated calendar entity for one ENGIE Belgium customer account."""

    # ``engie_belgium`` translation key + inherited ``_attr_has_entity_name``
    # composes as ``<address> ENGIE Belgium`` on HA 2026.4+.
    _attr_translation_key = "engie_belgium"

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        *,
        happy_hour_enrolled: bool,
        tou_active: bool = False,
    ) -> None:
        """Initialise the calendar entity for one customer-account subentry."""
        super().__init__(coordinator, subentry)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{subentry.subentry_id}_calendar"
        )
        self._event_providers: list[EventProvider] = list(EVENT_PROVIDERS)
        if happy_hour_enrolled:
            self._event_providers.append(happy_hour_events)
        if tou_active:
            self._event_providers.append(tou_slot_events)
        # Force a BAN-prefixed entity_id. HA's slug otherwise collides on
        # shared addresses. Direct ``entity_id`` assignment is the supported
        # escape hatch, effective on first registration only.
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = f"calendar.engie_belgium_{ban}"

    def _all_events(self) -> list[CalendarEvent]:
        """Collect events from every registered provider."""
        events: list[CalendarEvent] = []
        for provider in self._event_providers:
            events.extend(provider(self.coordinator))
        return events

    @property
    def event(self) -> CalendarEvent | None:
        """Return the active event, else the soonest upcoming, else the last past."""
        events = self._all_events()
        if not events:
            return None
        now = dt_util.utcnow()
        best_active: CalendarEvent | None = None
        best_upcoming: CalendarEvent | None = None
        best_past: CalendarEvent | None = None
        for e in events:
            if e.start <= now < e.end:
                if best_active is None or e.start < best_active.start:
                    best_active = e
            elif e.start >= now:
                if best_upcoming is None or e.start < best_upcoming.start:
                    best_upcoming = e
            elif best_past is None or e.end > best_past.end:
                best_past = e
        return best_active or best_upcoming or best_past

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
