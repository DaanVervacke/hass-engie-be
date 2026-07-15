"""
Calendar-event provider for ENGIE time-of-use schedules.

Emits one CalendarEvent per per-EAN, per-direction slot for the
next 7 days. Reads only the cached ``tou_schedules`` wrapper on the
coordinator; no additional network calls.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt as dt_util

from ._tou import _WEEKDAY_KEYS, _parse_hhmm, tou_schedules_payload
from .const import BRUSSELS_TZ

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator

_LOOKAHEAD_DAYS = 7

_SLOT_LABELS: dict[str, str] = {
    "peak": "Peak",
    "offpeak": "Off-peak",
    "superoffpeak": "Super off-peak",
    "exclusive_night": "Exclusive night",
    "day": "Day",
}

_DIRECTION_LABELS: dict[str, str] = {
    "offtake": "Offtake",
    "injection": "Injection",
}


def format_tou_event_summary(slot: str, direction: str) -> str:
    """Return the canonical TOU calendar event summary string."""
    label = _SLOT_LABELS.get(slot.lower(), slot)
    dir_label = _DIRECTION_LABELS.get(direction.lower(), direction)
    return f"{label} ({dir_label.lower()})"


def tou_slot_events(
    coordinator: EngieBeDataUpdateCoordinator,
) -> list[CalendarEvent]:
    """
    Return the next 7 days of TOU slot transitions as CalendarEvent objects.

    One event per (EAN, direction, slot) with the supplier schedule as
    the source of truth. When the supplier and DGO schedules match
    (common case for Belgian bi-hourly), only supplier events are
    emitted to keep the calendar readable.

    Returns an empty list when the coordinator has no ``tou_schedules``
    wrapper (feature flag off) or when no items are present.
    """
    payload = tou_schedules_payload(coordinator)
    if payload is None:
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []

    events: list[CalendarEvent] = []
    now_local = dt_util.now(BRUSSELS_TZ)
    horizon = now_local + timedelta(days=_LOOKAHEAD_DAYS)

    for item in items:
        if not isinstance(item, dict):
            continue
        ean = item.get("eanWithSuffix")
        if not isinstance(ean, str):
            continue
        supplier = item.get("supplierSchedule")
        if not isinstance(supplier, dict):
            continue
        for direction in ("offtake", "injection"):
            direction_sched = supplier.get(direction)
            if not isinstance(direction_sched, dict):
                continue
            events.extend(
                _slots_to_events(
                    direction=direction,
                    schedule=direction_sched,
                    start=now_local,
                    horizon=horizon,
                )
            )
    return events


def _slots_to_events(
    *,
    direction: str,
    schedule: dict,
    start: datetime,
    horizon: datetime,
) -> list[CalendarEvent]:
    """Materialize slots between ``start`` and ``horizon`` into events."""
    events: list[CalendarEvent] = []
    day_cursor = start.date()
    horizon_date = horizon.date()
    while day_cursor <= horizon_date:
        weekday_index = day_cursor.weekday()
        key = _WEEKDAY_KEYS[weekday_index]
        day_slots = schedule.get(key, [])
        if isinstance(day_slots, list):
            for slot in day_slots:
                if not isinstance(slot, dict):
                    continue
                slot_start = _parse_hhmm(slot.get("startTime"))
                slot_end = _parse_hhmm(slot.get("endTime"))
                code = slot.get("slotCode")
                if slot_start is None or slot_end is None or not isinstance(code, str):
                    continue
                start_dt = dt_util.as_utc(
                    datetime.combine(
                        day_cursor, slot_start, tzinfo=BRUSSELS_TZ
                    ).replace(fold=0)
                )
                if slot_end == time(0, 0):
                    end_dt = dt_util.as_utc(
                        datetime.combine(
                            day_cursor + timedelta(days=1),
                            time(0, 0),
                            tzinfo=BRUSSELS_TZ,
                        ).replace(fold=0)
                    )
                else:
                    end_dt = dt_util.as_utc(
                        datetime.combine(
                            day_cursor, slot_end, tzinfo=BRUSSELS_TZ
                        ).replace(fold=0)
                    )
                # Clip past events but include the currently-active one.
                if end_dt <= start:
                    continue
                if start_dt >= horizon:
                    continue
                events.append(
                    CalendarEvent(
                        start=start_dt,
                        end=end_dt,
                        summary=format_tou_event_summary(code, direction),
                    )
                )
        day_cursor += timedelta(days=1)
    return events
