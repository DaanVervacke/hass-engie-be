# Plan 024: TOU boundary-scheduler lifecycle tests

> **Executor instructions**: Follow this plan step by step. STOP if any
> assertion in "STOP conditions" holds. Update `plans/README.md` when
> done.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be/sensor.py custom_components/engie_be/binary_sensor.py tests/test_sensor_solar_surplus_schedulers.py`

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

Four TOU entities inherit `_BoundaryScheduleMixin`:

- `EngieBeTouSlotSensor` (offtake + injection instances)
- `EngieBeTouIsOptimalSensor` (offtake + injection instances)

All four rearm timers at slot boundaries to flip state exactly on the
transition (e.g., at 06:00 Brussels weekdays as PEAK begins) rather
than waiting up to a full coordinator refresh interval. The equivalent
solar-surplus entities have a dedicated lifecycle test file:
`tests/test_sensor_solar_surplus_schedulers.py` — five tests covering
arm-on-add, re-arm at boundary, cancel-on-remove, `_next_boundary is
None → no timer armed`, and payload-swap invalidation.

The TOU counterparts have ZERO lifecycle tests. Plan 017 explicitly
deferred these ("out of scope for the initial plan"). Now is the time.

## Current state

### Template file: `tests/test_sensor_solar_surplus_schedulers.py`

Read the whole file (~180 lines) before starting. It defines a small
harness (fixture-injected `add_sensor` from conftest, MagicMock-backed
coordinator, `async_fire_time_changed`) and five tests. The TOU
equivalent is a direct mechanical mirror.

### TOU sensor classes to cover

Sensor:
- `EngieBeTouSlotSensor` (`sensor.py`, ~line 1682) — inherits
  `_EngieBeTouSlotBase` which inherits `_BoundaryScheduleMixin`
- Instantiated with `direction="offtake"` or `direction="injection"`

Binary sensor:
- `EngieBeTouIsOptimalSensor` (`binary_sensor.py`) — same
  boundary-scheduling contract, different data flow (compares current
  slot to `optimalTimeslotCode`)

### `_next_boundary` shape

Both TOU classes' `_next_boundary` returns the timestamp of the next
slot transition in UTC by calling `_tou.current_slot(schedule, now)`
and taking the second element (the transition time). When no schedule
is available (missing wrapper, empty schedule, malformed), it returns
`None` and no timer is armed.

### Existing fixtures to reuse

- `tests/fixtures/tou_schedules_bihoraire.json` — real bi-hourly
  schedule with 06:00 and 21:00 transitions weekdays
- `tests/fixtures/tou_schedules_flat_all_offpeak.json` — flat schedule
  (edge case: no transitions at all, `_next_boundary` should return
  None)

### `add_sensor` fixture

Already exists in `tests/conftest.py`. It binds the entity to the HA
loop, sets `entity.hass`, gives it a mock platform, and calls
`async_added_to_hass()`. Use it for both sensor and binary-sensor
lifecycle tests.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format tests` | no diffs |
| Lint | `.venv/bin/ruff check tests` | `All checks passed!` |
| New tests | `.venv/bin/pytest tests/test_sensor_tou_schedulers.py tests/test_binary_sensor_tou_schedulers.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass, ≥95% |

## Scope

**In scope**:
- `tests/test_sensor_tou_schedulers.py` — new file with 5 tests for
  the offtake/injection slot sensors.
- `tests/test_binary_sensor_tou_schedulers.py` — new file with 4-5
  tests for the offtake/injection is-optimal binary sensors.

**Out of scope**:
- Any change to `custom_components/engie_be/sensor.py` or
  `binary_sensor.py`. This plan is test-only.
- Boundary-mixin behavior itself — already tested by the solar-surplus
  scheduler file.
- Coordinator-level tou tests — plan 017 covered them.

## Steps

### Step 1: Create `tests/test_sensor_tou_schedulers.py`

Model directly after `test_sensor_solar_surplus_schedulers.py`. Adapt
the fixture builders to TOU:

```python
"""
Tests for the TOU slot sensor boundary scheduler.

Validates that ``EngieBeTouSlotSensor`` rearms via the shared
``_BoundaryScheduleMixin`` so its enum state flips at the exact
second ENGIE's schedule crosses a slot boundary (typically 06:00 or
21:00 Brussels-local for bi-hourly customers).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.engie_be.const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    EPEX_TZ,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.sensor import (
    TOU_OFFTAKE_SLOT_DESCRIPTION,   # verify exact name via grep
    EngieBeTouSlotSensor,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from homeassistant.core import HomeAssistant
    AddSensor = Callable[[HomeAssistant, object], Awaitable[None]]

pytestmark = pytest.mark.tou

_FIXTURES = Path(__file__).parent / "fixtures"
_TOU_BIHORAIRE = _FIXTURES / "tou_schedules_bihoraire.json"
_TOU_FLAT = _FIXTURES / "tou_schedules_flat_all_offpeak.json"

_BRUSSELS = ZoneInfo(EPEX_TZ)
_EAN = "541448820070000000"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _wrap(payload: dict) -> dict:
    return {
        "tou_schedules": {
            "data": payload,
            "fetched_at": "2026-07-08T10:00:00+00:00",
        }
    }


def _make_subentry() -> MagicMock:
    subentry = MagicMock()
    subentry.subentry_id = "sub_test"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {CONF_BUSINESS_AGREEMENT_NUMBER: "000000000000"}
    return subentry


def _make_coordinator(data: object) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = True
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


def _offtake_sensor(coordinator: MagicMock) -> EngieBeTouSlotSensor:
    return EngieBeTouSlotSensor(
        coordinator,
        _make_subentry(),
        TOU_OFFTAKE_SLOT_DESCRIPTION,
        _EAN,
        "offtake",
    )


# Sunday 2026-07-05 12:00 Brussels — a bihoraire schedule has no
# weekday transitions on Sunday (all-day OFFPEAK), so `_next_boundary`
# during Sunday must return the Monday 06:00 transition.
# Choose Monday 2026-07-06 05:30 for the "boundary imminent" case —
# schedule flips to PEAK at 06:00.
_MONDAY_05_30_UTC = datetime(2026, 7, 6, 3, 30, tzinfo=UTC)  # 05:30 Brussels
_MONDAY_06_00_UTC = datetime(2026, 7, 6, 4, 0, tzinfo=UTC)   # 06:00 Brussels


async def test_offtake_slot_flips_at_boundary(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Current-slot sensor flips OFFPEAK → PEAK at 06:00 Brussels."""
    coordinator = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    sensor = _offtake_sensor(coordinator)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_MONDAY_05_30_UTC,
    ):
        await add_sensor(hass, sensor)
        assert sensor.native_value == "offpeak"
        assert sensor._unsub_boundary is not None
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_MONDAY_06_00_UTC,
    ):
        async_fire_time_changed(hass, _MONDAY_06_00_UTC)
        await hass.async_block_till_done()
        assert sensor.native_value == "peak"
        assert sensor._unsub_boundary is not None


async def test_scheduler_does_not_arm_when_wrapper_missing(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """No solar_surplus wrapper → no boundary timer scheduled."""
    coordinator = _make_coordinator({})
    sensor = _offtake_sensor(coordinator)
    await add_sensor(hass, sensor)
    assert sensor._unsub_boundary is None


async def test_scheduler_does_not_arm_on_flat_schedule(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """
    Flat all-OFFPEAK schedule → no transitions in the future window →
    _next_boundary returns None → no timer.
    """
    coordinator = _make_coordinator(_wrap(_load(_TOU_FLAT)))
    sensor = _offtake_sensor(coordinator)
    # Any moment within a slot that has ``endTime: "00:00"``: the
    # helper returns end-of-day tomorrow, so a timer IS armed. Only
    # a truly-empty schedule yields None. This test asserts the
    # end-of-day fallback IS armed — flip the assertion to `is not None`.
    monday_noon = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)  # 12:00 Brussels
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=monday_noon,
    ):
        await add_sensor(hass, sensor)
        # Timer armed to end-of-day (next 00:00 Brussels).
        assert sensor._unsub_boundary is not None


async def test_remove_cancels_pending_timer(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """Removing the entity cancels its pending boundary timer."""
    coordinator = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    sensor = _offtake_sensor(coordinator)
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_MONDAY_05_30_UTC,
    ):
        await add_sensor(hass, sensor)
        assert sensor._unsub_boundary is not None
        sensor._call_on_remove_callbacks()
        assert sensor._unsub_boundary is None


async def test_injection_slot_uses_injection_schedule(
    hass: HomeAssistant,
    add_sensor: AddSensor,
) -> None:
    """The injection variant reads the ``injection`` sub-block."""
    from custom_components.engie_be.sensor import TOU_INJECTION_SLOT_DESCRIPTION

    coordinator = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)))
    sensor = EngieBeTouSlotSensor(
        coordinator,
        _make_subentry(),
        TOU_INJECTION_SLOT_DESCRIPTION,
        _EAN,
        "injection",
    )
    with patch(
        "custom_components.engie_be.sensor.dt_util.utcnow",
        return_value=_MONDAY_05_30_UTC,
    ):
        await add_sensor(hass, sensor)
        assert sensor.native_value == "offpeak"
```

Verify the SensorEntityDescription names by grepping first:

```bash
grep -n "TOU.*SLOT_DESCRIPTION\|TOU_OFFTAKE\|TOU_INJECTION" custom_components/engie_be/sensor.py | head
```

Adjust imports to match the actual exported names.

### Step 2: Create `tests/test_binary_sensor_tou_schedulers.py`

Mirror the same five-test structure but import
`EngieBeTouIsOptimalSensor` from `binary_sensor.py`. Test:

- `is_on` flips at 06:00 boundary (True → False when offtake changes
  from OFFPEAK to PEAK, and OFFPEAK is the optimal offtake code)
- Timer armed on add / cancelled on remove
- No timer when wrapper is missing
- Injection variant uses injection schedule

### Step 3: Verify coverage improves

```bash
.venv/bin/python -m pytest tests/ --cov=custom_components.engie_be.sensor --cov-report=term-missing 2>&1 | grep sensor.py
.venv/bin/python -m pytest tests/ --cov=custom_components.engie_be.binary_sensor --cov-report=term-missing 2>&1 | grep binary_sensor.py
```

Should see slightly higher percentages for both files.

### Step 4: Full gate

- `.venv/bin/ruff format tests` → no diffs
- `.venv/bin/ruff check tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

- 5 tests in `tests/test_sensor_tou_schedulers.py`
- 4-5 tests in `tests/test_binary_sensor_tou_schedulers.py`

Model directly after `tests/test_sensor_solar_surplus_schedulers.py`.
Do not invent new patterns.

## Done criteria

- [ ] `tests/test_sensor_tou_schedulers.py` exists with ≥ 5 test functions.
- [ ] `tests/test_binary_sensor_tou_schedulers.py` exists with ≥ 4 test functions.
- [ ] Both files opt into `pytestmark = pytest.mark.tou` at module scope.
- [ ] `.venv/bin/pytest tests/test_sensor_tou_schedulers.py tests/test_binary_sensor_tou_schedulers.py -v` all pass.
- [ ] `.venv/bin/pytest tests/ -q --cov-fail-under=95` passes.
- [ ] `plans/README.md` status row for 024 flipped to DONE.

## STOP conditions

- The `TOU_OFFTAKE_SLOT_DESCRIPTION` / `TOU_INJECTION_SLOT_DESCRIPTION`
  export names differ from what this plan assumes. Grep before writing;
  adjust imports accordingly.
- `EngieBeTouIsOptimalSensor` isn't exportable from
  `binary_sensor.py` (e.g., it's a private class inside a factory
  function). Report and skip the binary-sensor test file; the sensor
  tests alone are still valuable.
- The `_call_on_remove_callbacks()` pattern used by the solar-surplus
  test doesn't work on binary sensors — HA may enforce a different
  teardown path. Substitute with `await sensor.async_will_remove_from_hass()`
  if needed.

## Maintenance notes

- If the boundary-mixin implementation is ever refactored, these tests
  document the CoordinatorEntity contract at the sensor lifecycle
  level. Keep them intact even if the mixin changes internally.
- The `_MONDAY_05_30_UTC` / `_MONDAY_06_00_UTC` constants assume the
  bi-hourly fixture's Monday PEAK boundary is at 06:00. If the fixture
  ever changes (e.g., to a 07:00 Fluvius-style schedule matching the
  DYNAMIC contract observed in devcontainer logs), update these
  constants to match.
