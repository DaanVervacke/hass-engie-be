"""
Shared helpers for Happy Hours event data.

ENGIE announces a window under ``tomorrow`` the day before and re-publishes
the same window under ``today`` once midnight passes. Both keys are honoured
so a window is not lost across a post-midnight restart.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import CalendarEvent

from .const import LOGGER
from .data import unwrap_dict_payload

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator

# Public: trigger.py uses this to identify Happy Hours events in the calendar.
HAPPY_HOUR_EVENT_SUMMARY = "Happy Hours"
_HAPPY_HOUR_EVENT_DESCRIPTION = "Free energy window"


def is_enrolled_from_flag(flag: dict[str, Any] | None) -> bool:
    """
    Return True iff the boolean-feature-flag response reports Happy Hours enrolled.

    Fails closed against every observed non-enrolled shape so a transient
    response never signals enrolment.
    """
    if not isinstance(flag, dict):
        return False
    return bool(flag.get("value"))


def happy_hour_flag_reason(flag: dict[str, Any] | None) -> str | None:
    """Return ENGIE's ``reason`` from the Happy Hours flag response, or ``None``."""
    if not isinstance(flag, dict):
        return None
    reason = flag.get("reason")
    return reason if isinstance(reason, str) else None


def happy_hour_payload(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """
    Return the inner happy-hour dict from coordinator data, or ``None``.

    Returns ``{}`` when the API explicitly reported no event scheduled.
    Callers must distinguish that from ``None`` (no data) themselves.
    """
    return unwrap_dict_payload(coordinator, "happy_hour")


_HAPPY_HOUR_PAYLOAD_KEYS = ("today", "tomorrow")


def _parse_window(sub: Any) -> tuple[datetime, datetime] | None:
    """Parse one happy-hour sub-payload into a tz-aware (start, end), or None."""
    if not isinstance(sub, dict):
        return None
    start_raw = sub.get("startTime")
    end_raw = sub.get("endTime")
    if not isinstance(start_raw, str) or not isinstance(end_raw, str):
        return None
    try:
        start = datetime.fromisoformat(start_raw)
        end = datetime.fromisoformat(end_raw)
    except ValueError:
        return None
    if start.tzinfo is None or end.tzinfo is None:
        return None
    return start, end


def happy_hour_windows(
    coordinator: EngieBeDataUpdateCoordinator,
) -> list[tuple[datetime, datetime]]:
    """Return every scheduled happy-hour ``(start, end)`` window, earliest first."""
    payload = happy_hour_payload(coordinator)
    if not payload:
        return []
    windows: list[tuple[datetime, datetime]] = []
    for key in _HAPPY_HOUR_PAYLOAD_KEYS:
        window = _parse_window(payload.get(key))
        if window is not None:
            windows.append(window)
    windows.sort(key=lambda window: window[0])
    return windows


def happy_hour_window(
    coordinator: EngieBeDataUpdateCoordinator,
) -> tuple[datetime, datetime] | None:
    """
    Return the earliest scheduled happy-hour ``(start, end)``, or ``None``.

    ``now``-agnostic: may return a window whose start already lies in the past.
    """
    windows = happy_hour_windows(coordinator)
    return windows[0] if windows else None


def is_happy_hour_active(
    coordinator: EngieBeDataUpdateCoordinator,
    now: datetime,
) -> bool:
    """Return True iff ``now`` falls inside any scheduled happy-hour window."""
    return any(start <= now < end for start, end in happy_hour_windows(coordinator))


def happy_hour_events(
    coordinator: EngieBeDataUpdateCoordinator,
) -> list[CalendarEvent]:
    """
    Return calendar events for every known Happy Hours window.

    Combines persisted historical windows with the live payload,
    deduplicated by ``start``. ENGIE exposes no history, so archives
    only grow from install time forward.
    """
    events_by_start: dict[str, CalendarEvent] = {}

    runtime = getattr(coordinator.config_entry, "runtime_data", None)
    subentry_data = (
        runtime.subentry_data.get(coordinator.subentry.subentry_id)
        if runtime is not None
        else None
    )
    store = (
        getattr(subentry_data, "happy_hours_store", None)
        if subentry_data is not None
        else None
    )
    if store is not None:
        for entry in store.windows:
            event = _build_event(entry.get("start"), entry.get("end"))
            if event is not None:
                events_by_start[entry["start"]] = event

    for start, end in happy_hour_windows(coordinator):
        event = _build_event(start.isoformat(), end.isoformat())
        if event is not None:
            events_by_start[start.isoformat()] = event

    return list(events_by_start.values())


def _build_event(start_raw: Any, end_raw: Any) -> CalendarEvent | None:
    """Build a single Happy Hours ``CalendarEvent`` from raw fields."""
    if not isinstance(start_raw, str) or not isinstance(end_raw, str):
        LOGGER.debug(
            "Skipping malformed Happy Hour entry: start/end not strings "
            "(start=%r end=%r)",
            start_raw,
            end_raw,
        )
        return None
    try:
        start = datetime.fromisoformat(start_raw)
        end = datetime.fromisoformat(end_raw)
    except ValueError:
        LOGGER.debug(
            "Skipping malformed Happy Hour entry: cannot parse ISO timestamps "
            "(start=%s end=%s)",
            start_raw,
            end_raw,
        )
        return None
    if start.tzinfo is None or end.tzinfo is None:
        # CalendarEntity requires tz-aware datetimes for timed events.
        LOGGER.debug(
            "Skipping malformed Happy Hour entry: timezone-naive timestamps "
            "(start=%s end=%s)",
            start_raw,
            end_raw,
        )
        return None
    return CalendarEvent(
        start=start,
        end=end,
        summary=HAPPY_HOUR_EVENT_SUMMARY,
        description=_HAPPY_HOUR_EVENT_DESCRIPTION,
    )
