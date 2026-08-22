"""Pure helpers for parsing ENGIE time-of-use schedules."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from .api import mask_identifier
from .const import BRUSSELS_TZ, LOGGER
from .const import TOU_WEEKDAY_KEYS as _WEEKDAY_KEYS
from .data import unwrap_dict_payload

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator

_MAX_HOUR = 23
_MAX_MINUTE = 59
_ACCEPTED_PARTS = (2, 3)

_DIRECTION_KEYWORDS = ("OFFTAKE_", "INJECTION_")

# ENGIE's registry gives HIGH_LOAD_HOURS and PEAK identical label, order
# and colour, and likewise for LOW_LOAD_HOURS and OFFPEAK, so we treat
# them as one category rather than two states.
_SLOT_CODE_ALIASES: dict[str, str] = {
    "HIGH_LOAD_HOURS": "PEAK",
    "LOW_LOAD_HOURS": "OFFPEAK",
}


def normalize_slot_code(raw_code: str) -> str:
    """Strip the direction prefix and resolve aliases, returning the rate portion."""
    for keyword in _DIRECTION_KEYWORDS:
        idx = raw_code.rfind(keyword)
        if idx != -1:
            raw_code = raw_code[idx + len(keyword) :]
            break
    return _SLOT_CODE_ALIASES.get(raw_code, raw_code)


# Optimal offtake is the cheapest slot, optimal injection the dearest.
_OPTIMAL_PICKER: dict[str, Any] = {"offtake": min, "injection": max}


def _normalize_direction(
    block: dict[str, Any],
    direction: str,
) -> dict[str, Any]:
    """Return one direction block with canonical codes and derived optimal_slot_code."""
    out: dict[str, Any] = {}
    best: tuple[int, str] | None = None
    picker = _OPTIMAL_PICKER[direction]
    for key in _WEEKDAY_KEYS:
        slots = block.get(key)
        if not isinstance(slots, list):
            continue
        day: list[dict[str, Any]] = []
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            raw = slot.get("slotCode")
            if not isinstance(raw, str):
                continue
            code = normalize_slot_code(raw)
            canonical = dict(slot)
            canonical["slotCode"] = code
            day.append(canonical)
            cost = slot.get("costIndicator")
            if isinstance(cost, int):
                candidate = (cost, code)
                best = candidate if best is None else picker(best, candidate)
        out[key] = day
    wire_optimal = block.get("optimalTimeslotCode")
    if isinstance(wire_optimal, str):
        out["optimal_slot_code"] = normalize_slot_code(wire_optimal)
    else:
        out["optimal_slot_code"] = best[1] if best is not None else None
    return out


def _normalize_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Return one supplier or DGO schedule with both directions canonicalised."""
    out: dict[str, Any] = {}
    config_id = schedule.get("activeConfigurationId")
    if isinstance(config_id, str):
        out["activeConfigurationId"] = config_id
    for direction in _OPTIMAL_PICKER:
        block = schedule.get(direction)
        if isinstance(block, dict):
            out[direction] = _normalize_direction(block, direction)
    return out


def _pick_meter(meters: list[Any], ean: str) -> dict[str, Any]:
    """Return the first non-exclusive-night meter, logging on ambiguity."""
    usable = [meter for meter in meters if isinstance(meter, dict)]
    if not usable:
        return {}
    chosen = next(
        (meter for meter in usable if meter.get("exclusiveNightMeter") is not True),
        usable[0],
    )
    if len(usable) > 1:
        LOGGER.warning(
            "TOU schedules for %s carry %d grid meters, using %s. "
            "Please open an issue if that is the wrong register.",
            mask_identifier(ean),
            len(usable),
            chosen.get("gridMeterNumber", "unknown"),
        )
    return chosen


def normalize_tou_payload(payload: Any) -> dict[str, Any]:
    """
    Adapt a /tou-schedules response into the integration's canonical shape.

    Always returns ``{"items": [...]}``; malformed input yields an empty
    list rather than raising, so a bad refresh does not blank unrelated
    sensors.
    """
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {"items": []}

    out_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ean = item.get("eanWithSuffix")
        if not isinstance(ean, str):
            continue
        meters = item.get("gridMeterTimeOfUseSchedules")
        if isinstance(meters, list) and meters:
            source = _pick_meter(meters, ean)
        elif "supplierSchedule" in item or "dgoTgoSchedule" in item:
            # Legacy flat shape, still accepted so this can be reverted to
            # the energy-insights base URL by editing one constant.
            source = item
        else:
            LOGGER.warning(
                "TOU schedules for %s carry neither gridMeterTimeOfUseSchedules "
                "nor a supplierSchedule, so no TOU entity will have data",
                mask_identifier(ean),
            )
            continue
        canonical: dict[str, Any] = {"eanWithSuffix": ean}
        for key in ("supplierSchedule", "dgoTgoSchedule"):
            schedule = source.get(key)
            if isinstance(schedule, dict):
                canonical[key] = _normalize_schedule(schedule)
        for key in ("gridMeterNumber", "exclusiveNightMeter"):
            if key in source:
                canonical[key] = source[key]
        out_items.append(canonical)
    return {"items": out_items}


def tou_schedules_payload(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """Return the inner TOU schedules dict from coordinator data, or ``None``."""
    return unwrap_dict_payload(coordinator, "tou_schedules")


def _parse_hhmm(raw: Any) -> time | None:
    """
    Parse a ``"HH:MM"`` or ``"HH:MM:SS"`` string into a time, or ``None``.

    Seconds are accepted and discarded: the two ``/tou-schedules`` routes
    disagree on the format and no observed payload has a non-zero seconds
    field.
    """
    if not isinstance(raw, str):
        return None
    parts = raw.split(":")
    if len(parts) not in _ACCEPTED_PARTS:
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
    now_local = now.astimezone(BRUSSELS_TZ) if now else datetime.now(BRUSSELS_TZ)
    weekday = now_local.weekday()
    today_slots = _weekday_slots(schedule, weekday)
    for slot in today_slots:
        start = _parse_hhmm(slot.get("startTime"))
        end = _parse_hhmm(slot.get("endTime"))
        code = slot.get("slotCode")
        if start is None or end is None or not isinstance(code, str):
            continue
        start_dt = datetime.combine(
            now_local.date(), start, tzinfo=BRUSSELS_TZ
        ).replace(fold=now_local.fold)
        # end="00:00" means end-of-day (midnight tonight -> tomorrow 00:00)
        if end == time(0, 0):
            end_dt = datetime.combine(
                now_local.date() + timedelta(days=1), time(0, 0), tzinfo=BRUSSELS_TZ
            ).replace(fold=now_local.fold)
        else:
            end_dt = datetime.combine(
                now_local.date(), end, tzinfo=BRUSSELS_TZ
            ).replace(fold=now_local.fold)
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
