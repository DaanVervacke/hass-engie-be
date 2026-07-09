"""Pure helpers for parsing ENGIE time-of-use schedules."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from .data import unwrap_payload

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator

_BRUSSELS = ZoneInfo("Europe/Brussels")
_WEEKDAY_KEYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_MAX_HOUR = 23
_MAX_MINUTE = 59
_EXPECTED_PARTS = 2


def tou_schedules_payload(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """Return the inner TOU schedules dict from coordinator data, or ``None``."""
    return unwrap_payload(coordinator, "tou_schedules")


def _parse_hhmm(raw: Any) -> time | None:
    """Parse a ``"HH:MM"`` string into a :class:`datetime.time`, or ``None``."""
    if not isinstance(raw, str):
        return None
    parts = raw.split(":", 1)
    if len(parts) != _EXPECTED_PARTS:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    # Allow 00:00 (end-of-day sentinel) plus normal range.
    if not ((0 <= h <= _MAX_HOUR and 0 <= m <= _MAX_MINUTE) or (h == 0 and m == 0)):
        return None
    return time(hour=h % 24, minute=m)


def _weekday_slots(
    schedule: dict[str, Any],
    weekday_index: int,
) -> list[dict[str, Any]]:
    """Return slot list for a given weekday index (0=Monday)."""
    key = _WEEKDAY_KEYS[weekday_index]
    slots = schedule.get(key)
    return slots if isinstance(slots, list) else []


def current_slot(
    schedule: dict[str, Any],
    now: datetime | None = None,
) -> tuple[str | None, datetime | None]:
    """
    Return (current_slot_code_lowercase, next_transition_aware) or (None, None).

    ``schedule`` is one direction's block (has monday-sunday keys).
    ``now`` defaults to Brussels-local now. Handles the ``00:00`` end-time
    (== midnight/end-of-day) convention. Returns (None, None) if the
    schedule is empty, malformed, or no slot covers the current moment.
    """
    now_local = now.astimezone(_BRUSSELS) if now else datetime.now(_BRUSSELS)
    weekday = now_local.weekday()
    today_slots = _weekday_slots(schedule, weekday)
    for slot in today_slots:
        start = _parse_hhmm(slot.get("startTime"))
        end = _parse_hhmm(slot.get("endTime"))
        code = slot.get("slotCode")
        if start is None or end is None or not isinstance(code, str):
            continue
        start_dt = datetime.combine(now_local.date(), start, tzinfo=_BRUSSELS)
        # end="00:00" means end-of-day (midnight tonight -> tomorrow 00:00)
        if end == time(0, 0):
            end_dt = datetime.combine(
                now_local.date() + timedelta(days=1), time(0, 0), tzinfo=_BRUSSELS
            )
        else:
            end_dt = datetime.combine(now_local.date(), end, tzinfo=_BRUSSELS)
        if start_dt <= now_local < end_dt:
            return code.lower(), end_dt.astimezone(now_local.tzinfo)
    return None, None


def schedule_for_ean(
    tou_data: dict[str, Any],
    ean_with_suffix: str,
) -> dict[str, Any] | None:
    """Return the item dict for the given EAN-with-suffix, or ``None``."""
    items = tou_data.get("items") if isinstance(tou_data, dict) else None
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("eanWithSuffix") == ean_with_suffix:
            return item
    return None


def has_multiple_slot_codes(direction_schedule: dict[str, Any]) -> bool:
    """
    Return True when the schedule has more than one distinct slot code across the week.

    Used to gate "is optimal" binary sensors: a flat schedule where every
    hour is the same code has no meaningful optimal vs non-optimal distinction.
    """
    codes: set[str] = set()
    for key in _WEEKDAY_KEYS:
        slots = direction_schedule.get(key)
        if not isinstance(slots, list):
            continue
        for slot in slots:
            if isinstance(slot, dict) and isinstance(slot.get("slotCode"), str):
                codes.add(slot["slotCode"])
    return len(codes) > 1
