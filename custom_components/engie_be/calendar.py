"""
Calendar platform for the ENGIE Belgium integration.

Surfaces the monthly capacity-tariff (captar) peak window as a single calendar
event so users can see when their billable peak occurred without opening any
extra dashboard cards.

The data is sourced from the existing coordinator payload at
``coordinator.data["peaks"]["data"]["peakOfTheMonth"]``. No additional API
calls are made by this platform.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.components.calendar import CalendarEntity, CalendarEvent

from ._peaks import peaks_meta, peaks_payload
from .entity import EngieBeEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EngieBeDataUpdateCoordinator
    from .data import EngieBeConfigEntry

_EVENT_SUMMARY = "Captar monthly peak"


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the calendar platform."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities([EngieBeCaptarPeakCalendar(coordinator)])


class EngieBeCaptarPeakCalendar(EngieBeEntity, CalendarEntity):
    """Calendar entity exposing the monthly captar peak window."""

    _attr_translation_key = "captar_monthly_peak"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: EngieBeDataUpdateCoordinator) -> None:
        """Initialise the calendar entity."""
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_captar_monthly_peak"
        )

    def _build_event(self) -> CalendarEvent | None:
        """Build the single peak event from coordinator data."""
        peaks = peaks_payload(self.coordinator)
        if peaks is None:
            return None
        monthly = peaks.get("peakOfTheMonth")
        if not isinstance(monthly, dict):
            return None
        start_raw = monthly.get("start")
        end_raw = monthly.get("end")
        if not isinstance(start_raw, str) or not isinstance(end_raw, str):
            return None
        try:
            start = datetime.fromisoformat(start_raw)
            end = datetime.fromisoformat(end_raw)
        except ValueError:
            return None
        if start.tzinfo is None or end.tzinfo is None:
            # CalendarEntity requires tz-aware datetimes for timed events.
            return None

        peak_kw = monthly.get("peakKW")
        peak_kwh = monthly.get("peakKWh")
        description_parts: list[str] = []
        if peak_kw is not None:
            description_parts.append(f"Peak power: {peak_kw} kW")
        if peak_kwh is not None:
            description_parts.append(f"Peak energy: {peak_kwh} kWh")

        meta = peaks_meta(self.coordinator)
        if meta is not None and meta.get("is_fallback"):
            description_parts.append(
                f"Fallback: showing {meta['year']:04d}-{meta['month']:02d} "
                "while current month is unavailable.",
            )

        description = "\n".join(description_parts) or None
        return CalendarEvent(
            start=start,
            end=end,
            summary=_EVENT_SUMMARY,
            description=description,
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next or current upcoming event."""
        return self._build_event()

    async def async_get_events(
        self,
        hass: HomeAssistant,  # noqa: ARG002
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return all events within the requested window."""
        event = self._build_event()
        if event is None:
            return []
        if event.end <= start_date or event.start >= end_date:
            return []
        return [event]
