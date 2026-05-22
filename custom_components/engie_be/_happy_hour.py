"""
Shared helpers for Happy Hour event data.

The Happy Hour endpoint (``/business-agreements/{BAN}/happy-hour-event``)
returns either ``{}`` (no event scheduled) or
``{"tomorrow": {"startTime": "...", "endTime": "..."}}``.

The coordinator stores the response under ``coordinator.data["happy_hour"]``
as ``{"data": <payload-or-None>}``. These helpers unwrap that wrapper so
payload-shape knowledge lives in a single place, and produce the data
shapes needed by the sensor, binary_sensor, and calendar platforms.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import CalendarEvent

from .const import HAPPY_HOURS_SERVICE_ENABLED_KEY, LOGGER

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator

_HAPPY_HOUR_EVENT_SUMMARY = "Happy Hour"
_HAPPY_HOUR_EVENT_DESCRIPTION = "Free energy window"


def is_enrolled_from_flags(flags: dict[str, Any] | None) -> bool:
    """
    Return True iff the feature-flags response reports Happy Hour enrolled.

    The feature-flags endpoint returns a mapping keyed by flag name; each
    value is itself a dict with a ``value`` boolean (and usually a
    ``reason`` string). The integration treats only the
    ``happy-hours-service-enabled`` flag as the enrolment signal because
    its sibling ``happy-hours-shown`` governs Smart App UI visibility
    rather than the service state itself.

    Defensive against every observed and plausible non-enrolled shape
    (``None``, non-dict, missing key, missing ``value``, falsy ``value``)
    so callers can pass the raw API response without pre-validation.
    """
    if not isinstance(flags, dict):
        return False
    flag = flags.get(HAPPY_HOURS_SERVICE_ENABLED_KEY)
    if not isinstance(flag, dict):
        return False
    return bool(flag.get("value"))


def happy_hour_flag_reason(flags: dict[str, Any] | None) -> str | None:
    """
    Return ENGIE's ``reason`` string for the Happy Hour service flag, or ``None``.

    Useful for debug logging so beta users can see *why* enrolment
    flipped (e.g. ``HAPPY_HOUR_ACTIVE`` vs ``HAPPY_HOUR_INACTIVE``)
    without having to read the raw feature-flags JSON.
    """
    if not isinstance(flags, dict):
        return None
    flag = flags.get(HAPPY_HOURS_SERVICE_ENABLED_KEY)
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
    if not isinstance(coordinator.data, dict):
        return None
    wrapper = coordinator.data.get("happy_hour")
    if not isinstance(wrapper, dict):
        return None
    payload = wrapper.get("data")
    return payload if isinstance(payload, dict) else None


def happy_hour_window(
    coordinator: EngieBeDataUpdateCoordinator,
) -> tuple[datetime, datetime] | None:
    """
    Return the upcoming happy-hour ``(start, end)``, or ``None``.

    Returns ``None`` when no event is scheduled, when the payload is
    missing, or when timestamps fail to parse. Both datetimes are
    timezone-aware (ENGIE returns explicit offsets, e.g. ``+02:00``).
    """
    payload = happy_hour_payload(coordinator)
    if not payload:
        return None
    tomorrow = payload.get("tomorrow")
    if not isinstance(tomorrow, dict):
        return None
    start_raw = tomorrow.get("startTime")
    end_raw = tomorrow.get("endTime")
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


def is_happy_hour_active(
    coordinator: EngieBeDataUpdateCoordinator,
    now: datetime,
) -> bool:
    """Return True iff ``now`` falls inside the scheduled happy-hour window."""
    window = happy_hour_window(coordinator)
    if window is None:
        return False
    start, end = window
    return start <= now < end


def happy_hour_events(
    coordinator: EngieBeDataUpdateCoordinator,
) -> list[CalendarEvent]:
    """
    Return calendar events for every known Happy Hour window.

    Combines persisted historical windows (from the per-subentry
    Happy Hour history store) with the live ``tomorrow`` window from
    the current coordinator payload. Entries are deduplicated by
    ``start`` so the live payload does not produce a duplicate event
    when the store has already recorded it during an earlier refresh.

    The integration can only ever surface windows it has observed
    while running because ENGIE does not expose Happy Hour history.
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

    window = happy_hour_window(coordinator)
    if window is not None:
        start, end = window
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
