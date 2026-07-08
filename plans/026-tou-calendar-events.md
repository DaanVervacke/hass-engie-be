# Plan 026: TOU schedule as calendar events (DIR-02)

> **Executor instructions**: Follow this plan step by step. STOP if any
> STOP condition triggers. Update `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be/calendar.py custom_components/engie_be/_tou.py`

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none. Uses existing TOU coordinator wrapper.
- **Category**: direction (feature)
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

Solar surplus + TOU sensors expose the **current** state; users see
"we're in PEAK right now." They cannot see "PEAK ends at 21:00 then
OFFPEAK until 06:00, then PEAK again all Monday" without opening the
Smart App. A calendar event provider that emits one event per TOU
slot per day is:

- Cheap: reuses the existing cached `tou_schedules` wrapper (no new
  HTTP calls, no new coordinator work)
- Familiar UX: HA calendar cards render weekly grids natively
- Composable: sits alongside the existing captar peak + happy hours
  calendar entries; no new entity is created — just a new
  `EventProvider` registered in `calendar.py`

## Current state

### The calendar entity architecture

`custom_components/engie_be/calendar.py` already implements a
provider-based pattern. `EVENT_PROVIDERS` is a list of callables
(`Callable[[EngieBeDataUpdateCoordinator], list[CalendarEvent]]`), and
the entity concatenates all provider outputs when HA polls it.

Current providers:
- `captar_peak_events` (always active) — from `_peaks.py`
- `happy_hour_events` (conditional on enrolment) — from `_happy_hour.py`

Adding a third provider needs:
1. A helper function `tou_slot_events(coordinator) -> list[CalendarEvent]`
   in a new file `_tou_calendar.py` (or add it to the existing
   `_tou.py` since it's a small, related function). Prefer keeping
   `_tou.py` HA-free; put calendar-event construction in
   `calendar.py` or a new `_tou_calendar.py`.
2. A conditional append in `EngieBeCalendar.__init__` based on
   `sub_data.is_tou_active`.

### The existing conditional-provider pattern (from calendar.py)

Look at how Happy Hours is conditionally added. The pattern is:

```python
def _build_event_providers(sub_data) -> list[EventProvider]:
    providers = list(EVENT_PROVIDERS)
    if sub_data.is_happy_hour_enrolled:
        providers.append(happy_hour_events)
    if sub_data.is_tou_active:  # <-- this plan adds
        providers.append(tou_slot_events)
    return providers
```

### Data flow

`tou_slot_events` receives the coordinator, reads
`coordinator.data["tou_schedules"]["data"]["items"]`, iterates each
electricity EAN in `sub_data.service_points`, and emits events for
the next 7 days of slots per EAN.

Per-slot event fields:
- `summary`: e.g. `"TOU: PEAK (offtake)"` — direction-aware summary
- `start`: `datetime` in Brussels-local, Timezone-aware
- `end`: `datetime` — either `endTime` on same day, or midnight of
  next day when `endTime == "00:00"`
- `description`: brief context (schedule source, e.g.
  `"activeConfigurationId: RTPCMP_004"`)

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Calendar tests | `.venv/bin/pytest tests/test_calendar.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass, ≥95% |

## Scope

**In scope**:
- `custom_components/engie_be/_tou.py` — add small helper (below) OR
  create `custom_components/engie_be/_tou_calendar.py`.
- `custom_components/engie_be/calendar.py` — conditional provider
  append + import.
- `tests/test_calendar.py` — extend with TOU event tests.
- `README.md` — one sentence added to the existing TOU section
  describing the calendar.
- `CHANGELOG.md` — Unreleased entry.

**Out of scope**:
- Any change to coordinator, sensor, or binary sensor. The events are
  a pure read-side transform on the already-cached wrapper.
- Multi-EAN aggregation (one event per EAN per slot is fine).
- A separate TOU calendar entity — reuse the existing per-subentry
  calendar.

## Steps

### Step 1: Decide file placement

Two options:
- **A**: Add `tou_slot_events(coordinator) -> list[CalendarEvent]` to
  `_tou.py`. Downside: `_tou.py` currently has NO Home Assistant
  imports; adding `CalendarEvent` breaks that invariant.
- **B**: Create `custom_components/engie_be/_tou_calendar.py` — HA-
  aware, mirrors `_happy_hour.py`'s calendar-adjacent helpers.

**Choose B.** Keep the pure helper HA-free (per repo convention
documented in CLAUDE.md).

### Step 2: Write `_tou_calendar.py`

```python
"""Calendar-event provider for ENGIE time-of-use schedules.

Emits one CalendarEvent per per-EAN, per-direction slot for the
next 7 days. Reads only the cached ``tou_schedules`` wrapper on the
coordinator; no additional network calls.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt as dt_util

from ._tou import _parse_hhmm, _WEEKDAY_KEYS
from .const import EPEX_TZ

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator

_BRUSSELS = ZoneInfo(EPEX_TZ)
_LOOKAHEAD_DAYS = 7


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
    data = getattr(coordinator, "data", None)
    if not isinstance(data, dict):
        return []
    wrapper = data.get("tou_schedules")
    if not isinstance(wrapper, dict):
        return []
    payload = wrapper.get("data")
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []

    events: list[CalendarEvent] = []
    now_local = dt_util.now(_BRUSSELS)
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
                    ean=ean,
                    direction=direction,
                    schedule=direction_sched,
                    start=now_local,
                    horizon=horizon,
                )
            )
    return events


def _slots_to_events(
    *,
    ean: str,
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
                start_dt = datetime.combine(day_cursor, slot_start, tzinfo=_BRUSSELS)
                if slot_end == time(0, 0):
                    end_dt = datetime.combine(
                        day_cursor + timedelta(days=1), time(0, 0), tzinfo=_BRUSSELS
                    )
                else:
                    end_dt = datetime.combine(day_cursor, slot_end, tzinfo=_BRUSSELS)
                # Clip past events but include the currently-active one.
                if end_dt <= start:
                    continue
                if start_dt >= horizon:
                    continue
                events.append(
                    CalendarEvent(
                        start=start_dt,
                        end=end_dt,
                        summary=f"TOU: {code} ({direction})",
                        description=(
                            f"EAN {ean} — supplier {direction} schedule"
                        ),
                    )
                )
        day_cursor += timedelta(days=1)
    return events
```

### Step 3: Register the provider conditionally in `calendar.py`

Locate the `EngieBeCalendar.__init__` (or the closest equivalent to
where `happy_hour_events` is conditionally appended). Add:

```python
from ._tou_calendar import tou_slot_events

# ... in __init__ / provider setup:
if getattr(sub_data, "is_tou_active", False):
    providers.append(tou_slot_events)
```

Preserve the existing `happy_hour_events` conditional wiring
unchanged.

### Step 4: Tests

`tests/test_calendar.py` already exists. Add tests that:

1. When `sub_data.is_tou_active is True` and the coordinator has a
   bi-hourly wrapper, the calendar contains TOU events for the next
   7 days.
2. When `is_tou_active` is False, no TOU events are added.
3. When the wrapper is missing, `tou_slot_events` returns an empty
   list (already covered in `_tou_calendar.py` guard clauses).
4. Event `summary` includes the slot code AND direction.
5. The 00:00 end-of-day sentinel produces an event ending at midnight
   of the next day.

Use `tests/fixtures/tou_schedules_bihoraire.json` as the input. Model
after existing captar-peak calendar tests.

### Step 5: README + CHANGELOG

Add one sentence to the existing "Time-of-Use tariff schedules"
section in README explaining that calendar events are emitted for
`is_tou_active` accounts. Add an `[Unreleased]` Added entry to
CHANGELOG.

### Step 6: Full gate + smoke test

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass
- `podman restart ha-plugin-test` → check the calendar entity for both
  BANs; since both are `is_tou_active=False` per the devcontainer
  logs, calendar should show only captar peaks (no TOU noise). Add
  a note in the plan-completion report confirming this.

## Test plan

- Extend `tests/test_calendar.py` with 4-5 TOU-specific event tests.
- Do NOT create a new test file for such a small addition.
- Use the fixture from `tests/fixtures/tou_schedules_bihoraire.json`
  (already sanitized).

## Done criteria

- [ ] `custom_components/engie_be/_tou_calendar.py` exists and imports only from `_tou` + HA calendar + `const`.
- [ ] `grep "tou_slot_events" custom_components/engie_be/calendar.py` returns 2 matches (import + append).
- [ ] `tests/test_calendar.py` has ≥ 4 new TOU-related test functions.
- [ ] `.venv/bin/pytest tests/test_calendar.py -v` all pass.
- [ ] `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` passes.
- [ ] Devcontainer smoke test: HA restarts cleanly; the calendar shows captar peaks (no TOU events on this account); no errors.
- [ ] `plans/README.md` status row for 026 flipped to DONE.

## STOP conditions

- `_WEEKDAY_KEYS` is not exported from `_tou.py` (it's a module-level
  private constant). Add it to the module's `__all__` or import it
  under its private name; do NOT duplicate the tuple.
- `EngieBeCalendar.__init__` no longer follows the append-provider
  pattern (someone refactored it). Look for where `happy_hour_events`
  is added and follow whatever the new pattern is; do not force the
  old pattern.
- The calendar entity has a hard event-count cap that 7-day × 2-EAN
  × 2-direction × ~14-slots = ~78 events would blow. Check HA
  calendar limits before enabling.

## Maintenance notes

- 7-day lookahead is conservative. Increase if user demand appears
  ("I want to see next month's TOU schedule to plan appliance
  purchases"). Watch calendar event count.
- When `dgoTgoSchedule` differs from `supplierSchedule` (rare but
  possible), the current plan emits only supplier events. Future
  extension: emit a second set of events tagged `(dgo)` — but only
  if there's demand and the two schedules genuinely differ; otherwise
  the calendar doubles in event count for no user value.
- If TOU rollout brings a 3-slot schedule (`peak` + `offpeak` +
  `exclusive_night`) or a 4-slot one, the events widen naturally
  without code change.
