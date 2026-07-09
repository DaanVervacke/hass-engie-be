"""
Shared helpers for Happy Hours event data.

The Happy Hours endpoint (``/business-agreements/{BAN}/happy-hour-event``)
returns ``{}`` (no event scheduled) or a payload carrying the upcoming
window under a ``tomorrow`` key (announced the day before) and/or a
``today`` key (the *same* window, re-published once midnight passes).
Both keys are honoured so a window is not lost across a post-midnight
restart.

The coordinator stores the response under ``coordinator.data["happy_hour"]``
as ``{"data": <payload-or-None>}``. These helpers unwrap that wrapper so
payload-shape knowledge lives in a single place, and produce the data
shapes needed by the sensor, binary_sensor, and calendar platforms.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import CalendarEvent

from .const import LOGGER
from .data import unwrap_dict_payload

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator

_HAPPY_HOUR_EVENT_SUMMARY = "Happy Hours"
_HAPPY_HOUR_EVENT_DESCRIPTION = "Free energy window"


def is_enrolled_from_flag(flag: dict[str, Any] | None) -> bool:
    """
    Return True iff the boolean-feature-flag response reports Happy Hours enrolled.

    The boolean-feature-flags endpoint returns a flat dict for the named
    flag with a top-level ``value`` boolean (and usually a ``reason``
    string). Callers pass the raw API response dict without pre-validation.

    Defensive against every observed and plausible non-enrolled shape
    (``None``, non-dict, missing ``value``, falsy ``value``) so a transient
    or unexpected response never incorrectly signals enrolment.
    """
    if not isinstance(flag, dict):
        return False
    return bool(flag.get("value"))


def happy_hour_flag_reason(flag: dict[str, Any] | None) -> str | None:
    """
    Return ENGIE's ``reason`` string from the Happy Hours flag response, or ``None``.

    Useful for debug logging so beta users can see *why* enrolment
    flipped (e.g. ``HAPPY_HOUR_ACTIVE`` vs ``HAPPY_HOUR_INACTIVE``)
    without having to read the raw API JSON.
    """
    if not isinstance(flag, dict):
        return None
    reason = flag.get("reason")
    return reason if isinstance(reason, str) else None


def happy_hour_payload(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """
    Return the inner happy-hour dict from coordinator data, or ``None``.

    Returns ``None`` when:

    * the coordinator has no data yet,
    * the coordinator failed to fetch happy-hour data and has no
      last-known wrapper, or
    * the wrapper is present but the API returned a non-dict payload.

    Returns the empty dict ``{}`` when the API explicitly reported no
    event scheduled. Callers must distinguish ``None`` (no data) from
    ``{}`` (no event scheduled) themselves when that matters.
    """
    return unwrap_dict_payload(coordinator, "happy_hour")


_HAPPY_HOUR_PAYLOAD_KEYS = ("today", "tomorrow")


def _parse_window(sub: Any) -> tuple[datetime, datetime] | None:
    """
    Parse one happy-hour sub-payload (``{"startTime": ..., "endTime": ...}``).

    Returns a timezone-aware ``(start, end)`` tuple, or ``None`` when the
    sub-payload is missing, not a dict, malformed, unparseable, or
    timezone-naive (ENGIE returns explicit offsets, e.g. ``+02:00``).
    """
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
    """
    Return every scheduled happy-hour ``(start, end)`` window, earliest first.

    ENGIE announces a window under a ``tomorrow`` key the day before, then
    re-publishes the same window under a ``today`` key once midnight passes;
    both keys are parsed so a window observed after a post-midnight restart
    is not lost. Returns ``[]`` when no event is scheduled or the payload is
    missing. Malformed or timezone-naive sub-payloads are skipped. Windows
    are sorted by start, so the earliest-starting window is first.
    """
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
    Return the earliest upcoming happy-hour ``(start, end)``, or ``None``.

    Thin wrapper over :func:`happy_hour_windows` returning the first
    (earliest-start) window, or ``None`` when none is scheduled. Both
    datetimes are timezone-aware. Intentionally ``now``-agnostic: it may
    return a window whose start already lies in the past.
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

    Combines persisted historical windows (from the per-subentry
    Happy Hours history store) with the live window(s) from the current
    coordinator payload (``today`` and/or ``tomorrow``). Entries are
    deduplicated by ``start`` so the live payload does not produce a
    duplicate event when the store has already recorded it during an
    earlier refresh, nor when the same window appears under both keys.

    The integration can only ever surface windows it has observed
    while running because ENGIE does not expose Happy Hours history.
    Newly-installed integrations build the archive up from the moment
    they first see an enrolled account.
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
    """Build a single Happy Hour ``CalendarEvent`` from raw fields."""
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
        summary=_HAPPY_HOUR_EVENT_SUMMARY,
        description=_HAPPY_HOUR_EVENT_DESCRIPTION,
    )
