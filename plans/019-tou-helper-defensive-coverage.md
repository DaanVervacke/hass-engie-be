# Plan 019: Cover the 9 defensive branches in `_tou.py` (85% → 100%)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report — do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be/_tou.py tests/test_sensor_tou.py tests/test_coordinator_tou.py`

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

`custom_components/engie_be/_tou.py` is the pure helper for TOU slot
parsing. Coverage report (`--cov-report=term-missing`) shows 9 uncovered
lines out of 61 (85%). Every uncovered branch is a defensive guard
against malformed API responses:

- lines 28, 31, 34-35, 38: `_parse_hhmm` early-returns for non-string
  input, wrong colon count, non-int digits, out-of-range values
- line 72: `current_slot` skips a slot with missing/malformed times or
  non-string code
- lines 93, 97: `schedule_for_ean` returns None when `items` is not a
  list or when the EAN isn't found
- line 111: `has_multiple_slot_codes` skips a weekday whose value isn't
  a list

These are exactly the branches a future ENGIE payload shape change
could trigger. Testing them is cheap and prevents a silent regression
from turning into an "TOU sensor mysteriously went unknown" bug report.

## Current state

### File `custom_components/engie_be/_tou.py`

Confirmed at 61 statements, 85% covered. Uncovered lines listed above.

### Test files that already exist and can host the new tests

- `tests/test_sensor_tou.py` — already imports and uses fixtures
- `tests/test_coordinator_tou.py` — has the coordinator paths
- **Recommended**: create a dedicated `tests/test_tou_helpers.py` since
  the target is a pure helper module and mixing helper unit tests into
  the sensor test file dilutes the sensor coverage story.

### The tests must opt into the `tou` marker

`tests/conftest.py` has an autouse `_disable_tou_flag_probe` fixture
that stubs `_async_fetch_tou_flag`. New test file needs
`pytestmark = pytest.mark.tou` at module top to opt in. Reference
pattern: `tests/test_coordinator_tou.py:48` (`pytestmark = pytest.mark.tou`).

Actually — the helper tests don't touch the coordinator at all, so
the marker doesn't strictly matter for correctness. But for
consistency with other tou-* files in the tests directory, add it.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format tests` | no diffs |
| Lint | `.venv/bin/ruff check tests` | `All checks passed!` |
| Target coverage | `.venv/bin/python -m pytest tests/test_tou_helpers.py --cov=custom_components.engie_be._tou --cov-report=term-missing` | `_tou.py` 100% |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov=custom_components.engie_be --cov-fail-under=95` | pass, ≥95% |

## Scope

**In scope**:
- `tests/test_tou_helpers.py` — new file with 6-8 focused unit tests.

**Out of scope**:
- Any change to `custom_components/engie_be/_tou.py`. This plan is
  test-only.
- Boundary-scheduler behavior of TOU sensors — covered by plan 024.
- Coordinator-level TOU behavior — covered by `test_coordinator_tou.py`.

## Steps

### Step 1: Create the new test file

`tests/test_tou_helpers.py`:

```python
"""Pure-helper unit tests for _tou.py defensive branches.

The helper is tested via its public functions; this file specifically
covers the malformed-input guards that the coordinator/sensor tests do
not exercise.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.engie_be._tou import (
    _parse_hhmm,
    current_slot,
    has_multiple_slot_codes,
    schedule_for_ean,
)

pytestmark = pytest.mark.tou

_BRUSSELS = ZoneInfo("Europe/Brussels")


# --- _parse_hhmm defensive branches (lines 28, 31, 34-35, 38) ---


def test_parse_hhmm_returns_none_for_non_string() -> None:
    """Non-string input (int, None, dict) returns None."""
    assert _parse_hhmm(None) is None
    assert _parse_hhmm(6) is None
    assert _parse_hhmm({"hour": 6}) is None


def test_parse_hhmm_returns_none_for_missing_colon() -> None:
    """'0600' with no colon returns None."""
    assert _parse_hhmm("0600") is None
    assert _parse_hhmm("6") is None


def test_parse_hhmm_returns_none_for_non_integer_parts() -> None:
    """'HH:MM' with non-integer digits returns None."""
    assert _parse_hhmm("ab:cd") is None
    assert _parse_hhmm("06:xx") is None


def test_parse_hhmm_returns_none_for_out_of_range() -> None:
    """Hour >= 24 or minute >= 60 returns None."""
    assert _parse_hhmm("25:00") is None
    assert _parse_hhmm("06:60") is None
    assert _parse_hhmm("-1:00") is None


def test_parse_hhmm_accepts_zero_zero_sentinel() -> None:
    """'00:00' parses cleanly (end-of-day sentinel)."""
    assert _parse_hhmm("00:00") == time(hour=0, minute=0)


def test_parse_hhmm_accepts_normal_values() -> None:
    """Regular HH:MM values parse to the expected time object."""
    assert _parse_hhmm("06:30") == time(hour=6, minute=30)
    assert _parse_hhmm("23:59") == time(hour=23, minute=59)


# --- current_slot defensive branch (line 72) ---


def test_current_slot_skips_malformed_slot() -> None:
    """A slot with missing startTime is skipped; the next valid one wins."""
    schedule = {
        "monday": [
            {"startTime": "bogus", "endTime": "06:00", "slotCode": "PEAK"},
            {"startTime": "00:00", "endTime": "06:00", "slotCode": "OFFPEAK"},
        ],
        "tuesday": [],
        "wednesday": [],
        "thursday": [],
        "friday": [],
        "saturday": [],
        "sunday": [],
    }
    # 04:00 Brussels on Monday 2026-07-06.
    now = datetime(2026, 7, 6, 4, 0, tzinfo=_BRUSSELS)
    code, end_dt = current_slot(schedule, now=now)
    assert code == "offpeak"
    assert end_dt is not None


def test_current_slot_returns_none_when_no_slot_covers_now() -> None:
    """Empty schedule for today returns (None, None)."""
    schedule = {k: [] for k in (
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday",
    )}
    now = datetime(2026, 7, 6, 4, 0, tzinfo=_BRUSSELS)
    assert current_slot(schedule, now=now) == (None, None)


# --- schedule_for_ean defensive branches (lines 93, 97) ---


def test_schedule_for_ean_returns_none_when_items_not_list() -> None:
    """No ``items`` key or wrong type returns None."""
    assert schedule_for_ean({}, "any_ID1") is None
    assert schedule_for_ean({"items": "not a list"}, "any_ID1") is None
    assert schedule_for_ean({"items": None}, "any_ID1") is None


def test_schedule_for_ean_returns_none_when_ean_absent() -> None:
    """EAN not present in items returns None."""
    data = {"items": [
        {"eanWithSuffix": "other_ID1", "supplierSchedule": {}},
    ]}
    assert schedule_for_ean(data, "missing_ID1") is None


def test_schedule_for_ean_ignores_non_dict_items() -> None:
    """Non-dict items are skipped without raising."""
    data = {"items": [
        "not a dict",
        None,
        {"eanWithSuffix": "wanted_ID1", "supplierSchedule": {}},
    ]}
    result = schedule_for_ean(data, "wanted_ID1")
    assert result is not None
    assert result["eanWithSuffix"] == "wanted_ID1"


# --- has_multiple_slot_codes defensive branch (line 111) ---


def test_has_multiple_slot_codes_skips_non_list_weekday() -> None:
    """A weekday whose value isn't a list is silently skipped."""
    schedule = {
        "monday": "oops",  # not a list
        "tuesday": [{"slotCode": "PEAK"}, {"slotCode": "OFFPEAK"}],
        "wednesday": [],
        "thursday": [],
        "friday": [],
        "saturday": [],
        "sunday": [],
    }
    assert has_multiple_slot_codes(schedule) is True


def test_has_multiple_slot_codes_false_for_flat_schedule() -> None:
    """All-OFFPEAK schedule returns False (no meaningful transitions)."""
    schedule = {
        k: [{"slotCode": "OFFPEAK"}]
        for k in (
            "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday",
        )
    }
    assert has_multiple_slot_codes(schedule) is False


def test_has_multiple_slot_codes_ignores_non_dict_slots() -> None:
    """Non-dict slots and slots without slotCode are skipped."""
    schedule = {
        "monday": ["oops", {"otherKey": "value"}, {"slotCode": "PEAK"}],
        "tuesday": [{"slotCode": "OFFPEAK"}],
        "wednesday": [],
        "thursday": [],
        "friday": [],
        "saturday": [],
        "sunday": [],
    }
    assert has_multiple_slot_codes(schedule) is True
```

### Step 2: Verify coverage

```bash
.venv/bin/python -m pytest tests/test_tou_helpers.py -v
.venv/bin/python -m pytest tests/ --cov=custom_components.engie_be._tou --cov-report=term-missing 2>&1 | grep _tou.py
```

Expected: `_tou.py 61 0 100%`.

### Step 3: Full gate

- `.venv/bin/ruff format tests` → no diffs
- `.venv/bin/ruff check tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Done criteria

- [ ] `tests/test_tou_helpers.py` exists and contains at least 12 test functions.
- [ ] `.venv/bin/python -m pytest tests/ --cov=custom_components.engie_be._tou --cov-report=term-missing 2>&1 | grep _tou.py` reports 100% or shows no `Missing` column entries.
- [ ] Full-suite coverage stays ≥ 95%.
- [ ] `.venv/bin/ruff check custom_components tests` exits 0.
- [ ] `plans/README.md` status row for 019 flipped to DONE.

## STOP conditions

- `_tou.py` has changed since the plan was written and the uncovered
  line numbers no longer match. Recompute coverage before authoring
  tests.
- A test named identically to any in the fresh file already exists
  in `tests/test_coordinator_tou.py` or `tests/test_sensor_tou.py` —
  rename to avoid confusion, or move the existing test into the new
  helpers file.

## Maintenance notes

- If ENGIE ever returns a payload with a new slot code (e.g. `DAY`,
  `SUPEROFFPEAK`, `EXCLUSIVE_NIGHT`), the sensor's ENUM device class
  already whitelists them (see `const.py::TOU_SLOT_CODES`). No test
  update needed — the helper doesn't care what the string is.
- If the DST-boundary handling of `current_slot` is ever changed
  (e.g., to handle 25/23-hour days on transition Sundays), add a
  targeted test here rather than to the coordinator/sensor file.
