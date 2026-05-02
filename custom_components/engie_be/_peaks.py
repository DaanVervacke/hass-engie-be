"""
Shared helpers for capacity-tariff (captar) peak data.

These helpers unwrap the ``peaks`` wrapper that the coordinator stores under
``coordinator.data["peaks"]``. They are imported by the sensor and calendar
platforms so payload-shape knowledge lives in a single place.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import CalendarEvent

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator

_CAPTAR_EVENT_SUMMARY = "Captar monthly peak"


def peaks_payload(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """
    Return the inner peaks dict from coordinator data, or ``None``.

    The coordinator wraps the API response as
    ``{"data", "year", "month", "is_fallback"}``. This helper unwraps it.
    """
    if not isinstance(coordinator.data, dict):
        return None
    wrapper = coordinator.data.get("peaks")
    if not isinstance(wrapper, dict):
        return None
    payload = wrapper.get("data")
    return payload if isinstance(payload, dict) else None


def peaks_meta(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """Return ``{year, month, is_fallback}`` for the active peaks payload."""
    if not isinstance(coordinator.data, dict):
        return None
    wrapper = coordinator.data.get("peaks")
    if not isinstance(wrapper, dict):
        return None
    year = wrapper.get("year")
    month = wrapper.get("month")
    if not isinstance(year, int) or not isinstance(month, int):
        return None
    return {
        "year": year,
        "month": month,
        "is_fallback": bool(wrapper.get("is_fallback", False)),
    }


def captar_peak_events(
    coordinator: EngieBeDataUpdateCoordinator,
) -> list[CalendarEvent]:
    """
    Return calendar events for the monthly captar peak window.

    Returns a single event when the coordinator has a valid
    ``peakOfTheMonth`` payload, otherwise an empty list.
    """
    peaks = peaks_payload(coordinator)
    if peaks is None:
        return []
    monthly = peaks.get("peakOfTheMonth")
    if not isinstance(monthly, dict):
        return []
    start_raw = monthly.get("start")
    end_raw = monthly.get("end")
    if not isinstance(start_raw, str) or not isinstance(end_raw, str):
        return []
    try:
        start = datetime.fromisoformat(start_raw)
        end = datetime.fromisoformat(end_raw)
    except ValueError:
        return []
    if start.tzinfo is None or end.tzinfo is None:
        # CalendarEntity requires tz-aware datetimes for timed events.
        return []

    peak_kw = monthly.get("peakKW")
    peak_kwh = monthly.get("peakKWh")
    description_parts: list[str] = []
    if peak_kw is not None:
        description_parts.append(f"Peak power: {peak_kw} kW")
    if peak_kwh is not None:
        description_parts.append(f"Peak energy: {peak_kwh} kWh")
    description = "\n".join(description_parts) or None

    return [
        CalendarEvent(
            start=start,
            end=end,
            summary=_CAPTAR_EVENT_SUMMARY,
            description=description,
        ),
    ]
