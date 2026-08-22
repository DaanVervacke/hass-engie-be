"""Pure helpers for Solar Surplus forecast payload parsing."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .const import BRUSSELS_TZ
from .data import unwrap_dict_payload

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator


def solar_surplus_payload(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """Return the inner per-EAN solar-surplus dict from coordinator data, or None."""
    return unwrap_dict_payload(coordinator, "solar_surplus")


def parse_slot_start(raw: Any) -> datetime | None:
    """Parse a ``startTime`` string into a timezone-aware datetime, or None."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def flat_slots(forecasts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten a per-EAN forecasts list into a list of hourly slot dicts."""
    flat: list[dict[str, Any]] = []
    for day in forecasts:
        if not isinstance(day, dict):
            continue
        details = day.get("details")
        if not isinstance(details, list):
            continue
        for slot in details:
            if not isinstance(slot, dict):
                continue
            flat.append(slot)
    return flat


def slot_covering(
    slots: list[dict[str, Any]], instant: datetime
) -> dict[str, Any] | None:
    """Return the slot whose [start, start+1h) interval covers ``instant``."""
    for slot in slots:
        start = parse_slot_start(slot.get("startTime"))
        if start is None:
            continue
        if start <= instant < start + timedelta(hours=1):
            return slot
    return None


def slots_for_local_date(
    slots: list[dict[str, Any]], target_date: date
) -> list[dict[str, Any]]:
    """Return every slot whose Brussels-local date matches ``target_date``."""
    matching: list[dict[str, Any]] = []
    for slot in slots:
        start = parse_slot_start(slot.get("startTime"))
        if start is None:
            continue
        if start.astimezone(BRUSSELS_TZ).date() == target_date:
            matching.append(slot)
    return matching


def next_hour_boundary(slots: list[dict[str, Any]], now: datetime) -> datetime | None:
    """Return the next slot-start strictly after ``now``, in UTC."""
    future_starts = [
        start
        for slot in slots
        if (start := parse_slot_start(slot.get("startTime"))) is not None
        and start > now
    ]
    if not future_starts:
        return None
    return min(future_starts).astimezone(UTC)


def derive_has_solar(wrapper: dict[str, Any] | None) -> bool | None:
    """
    Infer whether the customer has a solar installation from a wrapper.

    Returns ``True`` when any slot has a level other than ``NO_DATA``,
    ``False`` when the wrapper is present but every slot is ``NO_DATA``,
    and ``None`` when no wrapper is available so callers preserve the
    last-known value.
    """
    if not isinstance(wrapper, dict):
        return None
    per_ean = wrapper.get("data")
    if not isinstance(per_ean, dict):
        return None
    for forecasts in per_ean.values():
        if not isinstance(forecasts, list):
            continue
        for day in forecasts:
            if not isinstance(day, dict):
                continue
            details = day.get("details")
            if not isinstance(details, list):
                continue
            for slot in details:
                if not isinstance(slot, dict):
                    continue
                level = slot.get("level")
                if isinstance(level, str) and level.upper() != "NO_DATA":
                    return True
    return False
