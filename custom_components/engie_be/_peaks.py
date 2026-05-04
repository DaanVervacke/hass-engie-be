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
    Return calendar events for every known captar peak window.

    Combines persisted historical peaks (from the per-entry peak store)
    with the current month's peak window from the live coordinator
    payload. Entries are deduplicated by ``(year, month)`` so the live
    payload does not produce a duplicate event when the store already
    has it.
    """
    events_by_key: dict[tuple[int, int], CalendarEvent] = {}

    runtime = getattr(coordinator.config_entry, "runtime_data", None)
    subentry_data = (
        runtime.subentry_data.get(coordinator.subentry.subentry_id)
        if runtime is not None
        else None
    )
    store = (
        getattr(subentry_data, "peaks_store", None)
        if subentry_data is not None
        else None
    )
    if store is not None:
        for entry in store.peaks:
            event = _build_event(
                entry.get("start"),
                entry.get("end"),
                entry.get("peakKW"),
                entry.get("peakKWh"),
            )
            if event is not None:
                events_by_key[(entry["year"], entry["month"])] = event

    meta = peaks_meta(coordinator)
    payload = peaks_payload(coordinator)
    if meta is not None and isinstance(payload, dict):
        monthly = payload.get("peakOfTheMonth")
        if isinstance(monthly, dict):
            event = _build_event(
                monthly.get("start"),
                monthly.get("end"),
                monthly.get("peakKW"),
                monthly.get("peakKWh"),
            )
            if event is not None:
                events_by_key[(meta["year"], meta["month"])] = event

    return list(events_by_key.values())


def _build_event(
    start_raw: Any,
    end_raw: Any,
    peak_kw: Any,
    peak_kwh: Any,
) -> CalendarEvent | None:
    """Build a single captar ``CalendarEvent`` from raw fields."""
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

    description_parts: list[str] = []
    if peak_kw is not None:
        description_parts.append(f"Peak power: {peak_kw} kW")
    if peak_kwh is not None:
        description_parts.append(f"Peak energy: {peak_kwh} kWh")
    description = "\n".join(description_parts) or None

    return CalendarEvent(
        start=start,
        end=end,
        summary=_CAPTAR_EVENT_SUMMARY,
        description=description,
    )
