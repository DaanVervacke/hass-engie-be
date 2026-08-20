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
# ``HH:MM`` on the legacy energy-insights route, ``HH:MM:SS`` on
# billing/customer/v1. Seconds are always zero in observed payloads
# and are discarded.
_ACCEPTED_PARTS = (2, 3)

# Direction keywords used to split prefixed slot codes. Supplier TOU
# products send codes like ``S_TOU1_OFFTAKE_PEAK``; the rate portion is
# what every consumer wants, and it is what ``TOU_SLOT_CODES`` lists.
_DIRECTION_KEYWORDS = ("OFFTAKE_", "INJECTION_")

# Codes ENGIE renders identically to one of ours, so they are the same
# category and must not become separate states. Derived by grouping its own
# registry by label:
# https://www.engie.be/api/ebl/cms/mobile-tou/v1/configurations
# Offtake "Dal" holds S_TOU1_OFFTAKE_OFFPEAK, LOW_LOAD_HOURS and OFFPEAK.
# Offtake "Piek" holds S_TOU1_OFFTAKE_PEAK, HIGH_LOAD_HOURS and PEAK.
#
# The label is the grouping key. Colour is not: ``S_TOU1_OFFTAKE_PEAK`` and
# ``TOTAL_HOURS`` share #004B48 while being different categories. Order is
# not either: injection ranks the supplier and network families
# differently, putting supplier PEAK at 1 and network PEAK at 2.
#
# Baked rather than fetched. The app pulls that file at runtime for labels,
# colours and ordering, but all we need from it is these two equivalences,
# and tariff structure moves more slowly than presentation. If ENGIE ever
# prices HIGH_LOAD_HOURS apart from PEAK this needs a release, and the
# unknown-code warning will not catch it because the code is known.
_SLOT_CODE_ALIASES: dict[str, str] = {
    "HIGH_LOAD_HOURS": "PEAK",
    "LOW_LOAD_HOURS": "OFFPEAK",
}


def normalize_slot_code(raw_code: str) -> str:
    """
    Return the rate portion of a slot code, with any direction prefix stripped.

    Two steps. Any direction prefix is stripped, so
    ``S_TOU1_OFFTAKE_PEAK`` becomes ``PEAK``. Then :data:`_SLOT_CODE_ALIASES`
    resolves codes ENGIE treats as an existing category, so
    ``HIGH_LOAD_HOURS`` becomes ``PEAK``. Anything else comes back
    unchanged. Case is preserved: the wire is uppercase and read sites
    lowercase on output.
    """
    for keyword in _DIRECTION_KEYWORDS:
        idx = raw_code.rfind(keyword)
        if idx != -1:
            raw_code = raw_code[idx + len(keyword) :]
            break
    return _SLOT_CODE_ALIASES.get(raw_code, raw_code)


# Which end of the cost scale is the good end, per direction. Verified
# against one account observed on both routes on 2026-08-20: the legacy
# route's ``optimalTimeslotCode`` equals the minimum-``costIndicator``
# code for offtake and the maximum for injection. Cheapest is best when
# you are buying, dearest is best when you are selling.
_OPTIMAL_PICKER: dict[str, Any] = {"offtake": min, "injection": max}


def _normalize_direction(
    block: dict[str, Any],
    direction: str,
) -> dict[str, Any]:
    """
    Return one direction block with canonical slot codes and a derived optimal.

    ``optimal_slot_code`` prefers the wire's ``optimalTimeslotCode`` when
    the route still sends one, and otherwise is derived from
    ``costIndicator`` per :data:`_OPTIMAL_PICKER`. It is ``None`` when
    neither is available, which read sites already treat as "no opinion".
    Ties break on the code itself, so the result is deterministic.
    """
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
    """
    Return the grid meter whose schedules represent the main register.

    Multi-meter installations have not been observed, so this prefers the
    first meter that is not flagged ``exclusiveNightMeter`` and logs loudly
    when it had to choose. Guessing silently is how a household with an
    exclusive-night register would end up with night-only TOU sensors and
    no way to tell from the UI.
    """
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
    Adapt a ``/tou-schedules`` response into the integration's canonical shape.

    ``billing/customer/v1`` nests schedules under a per-meter list
    (``items[].gridMeterTimeOfUseSchedules[]``) and sends no
    ``optimalTimeslotCode``. This flattens that level, canonicalises every
    slot code, and synthesises ``optimal_slot_code``, so the sensor,
    binary-sensor and calendar read sites never learn which route the data
    came from.

    Always returns ``{"items": [...]}``. Malformed input yields an empty
    list rather than an exception: this runs inside a coordinator refresh,
    where raising would blank every unrelated sensor on the account.
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
            # ponytail: the legacy energy-insights shape, kept so the base
            # URL in api.py can be reverted by editing one constant if
            # billing/customer/v1 misbehaves for an account we have not
            # seen. Delete this branch once billing has shipped for a few
            # releases without complaint.
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
