# Plan 016: Test happy-hour + solar-surplus simultaneous flips debounce into one reload

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 6753af3..HEAD -- custom_components/engie_be/coordinator.py tests/test_coordinator_solar_surplus.py tests/test_coordinator_happy_hour_enrollment.py`
> Solar-surplus is uncommitted at "Planned at". Compare "Current state"
> excerpts against the live file before proceeding.

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: MED (this covers a subtle debounce contract that plan 015
  starts to lock in; the interaction between two features is easy to
  misconfigure)
- **Depends on**: 015 (plan 015 establishes the reload-mock pattern for
  the solar path; extend it here to cover the cross-feature case)
- **Category**: tests
- **Planned at**: commit `6753af3`, 2026-07-08

## Why this matters

`EngieBeData.reload_pending` is shared between two independent flip
handlers:

- `_async_apply_enrollment` (happy-hour, `coordinator.py:390-449`) checks
  and sets `runtime.reload_pending`.
- `_async_apply_has_solar` (solar surplus, `coordinator.py:659-687`) does
  the same.

If a customer signs up for Happy Hours AND ENGIE flips their solar-surplus
flag on in the same refresh cycle, both handlers fire in one
`_async_update_data` call. The contract is: **one reload per refresh
cycle, regardless of how many features flipped.** Nothing verifies this.

Two failure modes are possible if the debounce breaks:
1. Both handlers schedule reloads → two concurrent `async_reload` tasks
   race for the same entry (HA deduplicates today, but future HA versions
   might not).
2. The second handler's flip is silently lost — its `has_solar` /
   `is_happy_hour_enrolled` cache updates but no reload runs, and
   platforms don't reconcile.

The reviewer flagged this as an untested interaction in the v0.13.0b0
audit. This plan closes that gap with one integration-style test.

## Current state

### Files

- `custom_components/engie_be/coordinator.py` — both flip handlers.
- `custom_components/engie_be/data.py` — `EngieBeData.reload_pending: bool = False`
  (default at line ~136).
- `tests/test_coordinator_solar_surplus.py` — solar coordinator tests,
  opts into `pytest.mark.solar_surplus`.
- `tests/test_coordinator_happy_hour_enrollment.py` — happy-hour tests
  (does not opt into `solar_surplus`, so the flag-probe autouse stub applies).

### Debounce mechanism

Both handlers follow this pattern:

```python
        if runtime.reload_pending:
            return
        runtime.reload_pending = True
        LOGGER.info(...)
        self.hass.async_create_background_task(
            self.hass.config_entries.async_reload(self.config_entry.entry_id),
            name=...,
        )
```

Whichever handler runs first sets the flag; the second one sees it and
skips its own reload. `reload_pending` is never reset in the coordinator —
the flag is cleared by the reload itself (the entry is torn down and
runtime data is rebuilt from scratch, so the new `EngieBeData` instance
starts with `reload_pending=False` again).

### Existing test primitives

`tests/test_coordinator_solar_surplus.py::_make_client` currently defaults
the happy-hour flag to `_FLAGS_NOT_ENROLLED`. To test the cross-feature
scenario, override the flag to enrolled + also supply a happy-hour event
payload.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Target test | `.venv/bin/pytest tests/test_coordinator_solar_surplus.py -v -k debounce` | new tests pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass |

## Scope

**In scope**:
- `tests/test_coordinator_solar_surplus.py` — add one cross-feature test.
- `tests/test_coordinator_happy_hour_enrollment.py` — read-only reference.

**Out of scope**:
- `_async_apply_enrollment`, `_async_apply_has_solar`, `EngieBeData` —
  behaviour is what we're locking in; the test doesn't change them.
- Adding a shared debounce helper on `EngieBeData` — noted in the plan 015
  maintenance section as a candidate for future refactoring, but out of
  scope here.

## Git workflow

- Branch: `advisor/016-cross-feature-reload-debouncing-test`.
- Commit style: `test(coordinator): cover cross-feature reload debouncing`.

## Steps

### Step 1: Extend `_make_client` to allow enrolling happy hours

Locate `_make_client` in `tests/test_coordinator_solar_surplus.py` (around
line 119). Add an optional `happy_hour_enrolled` parameter — but keep the
default at `False` (via `_FLAGS_NOT_ENROLLED`) so the existing 8 tests
don't need updates:

```python
def _make_client(
    *,
    solar_payload: dict | Exception,
    solar_flag: dict | Exception | None = None,
    happy_hour_enrolled: bool = False,
    happy_hour_event: dict | None = None,
) -> MagicMock:
    """Build a client mock primed for a full coordinator refresh."""
    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=_load(_PRICES))
    client.async_get_monthly_peaks = AsyncMock(return_value=_load(_PEAKS))
    if happy_hour_enrolled:
        client.async_get_happy_hours_service_enabled_flag = AsyncMock(
            return_value={"value": True},
        )
    else:
        client.async_get_happy_hours_service_enabled_flag = AsyncMock(
            return_value=_load(_FLAGS_NOT_ENROLLED),
        )
    client.async_get_happy_hour_event = AsyncMock(
        return_value=happy_hour_event if happy_hour_event is not None else {},
    )
    client.async_get_month_report = AsyncMock(return_value={})
    # ... rest identical to current
```

**Verify**: `.venv/bin/pytest tests/test_coordinator_solar_surplus.py -v` →
all existing 8 tests still pass with the extended signature.

### Step 2: Add the cross-feature debounce test

Append to `tests/test_coordinator_solar_surplus.py`:

```python
async def test_simultaneous_happy_hour_and_solar_flips_debounce_to_one_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Happy-hour + solar flip in the same refresh must schedule exactly one reload.

    Both handlers share ``EngieBeData.reload_pending`` — whichever fires first
    sets the flag; the second sees it and skips its own reload. The test
    verifies the debounce holds under both flips happening in one cycle.
    """
    entry = _build_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        solar_payload=_load(_SOLAR_HIGH),  # non-NO_DATA -> has_solar True
        happy_hour_enrolled=True,          # flip is_happy_hour_enrolled to True
        happy_hour_event={},
    )
    coord = _coord(hass, entry, subentry)
    _wire(entry, client, subentry, coord)

    # Seed BOTH caches at the pre-flip state so this refresh causes two flips.
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    sub_data.is_happy_hour_enrolled = False  # will flip to True
    sub_data.has_solar = False               # will flip to True

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()
    await hass.async_block_till_done()

    # Both flags flipped in the runtime cache.
    assert sub_data.is_happy_hour_enrolled is True
    assert sub_data.has_solar is True
    # But only ONE reload was scheduled.
    assert entry.runtime_data.reload_pending is True
    reload_mock.assert_awaited_once_with(entry.entry_id)


async def test_reload_pending_blocks_second_flip_from_re_scheduling(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    If ``reload_pending`` is already True (e.g. set by an earlier tick), a
    fresh solar flip must NOT reschedule the reload — the flag is the debounce.
    """
    entry = _build_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=_load(_SOLAR_HIGH))
    coord = _coord(hass, entry, subentry)
    _wire(entry, client, subentry, coord)

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    sub_data.has_solar = False  # will flip to True
    entry.runtime_data.reload_pending = True  # simulate earlier reload queued

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()
    await hass.async_block_till_done()

    # Cache still updates.
    assert sub_data.has_solar is True
    # But no new reload was scheduled — the debounce holds.
    reload_mock.assert_not_awaited()
```

### Step 3: Run the new tests

```bash
.venv/bin/pytest tests/test_coordinator_solar_surplus.py -v -k "debounce or reload_pending"
```

Both must pass.

### Step 4: Full gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

- Two new tests in `tests/test_coordinator_solar_surplus.py`:
  - `test_simultaneous_happy_hour_and_solar_flips_debounce_to_one_reload` —
    the primary cross-feature debounce assertion.
  - `test_reload_pending_blocks_second_flip_from_re_scheduling` — the
    single-feature debounce ratchet.
- Model after: this file's own plan-015 tests (once landed) and
  `tests/test_coordinator_happy_hour_enrollment.py::test_enrolment_flip_schedules_reload`.

## Done criteria

- [ ] `.venv/bin/pytest tests/test_coordinator_solar_surplus.py -v` all pass, 10 tests (8 pre-existing at time of plan writing + 2 new; plan 015's 3 tests may or may not have landed yet).
- [ ] Both new tests assert `reload_mock.assert_awaited_once_with(entry.entry_id)` (first test) and `reload_mock.assert_not_awaited()` (second test).
- [ ] Both new tests assert that BOTH cache flags update in-cache even when the second flip skips the reload.
- [ ] `.venv/bin/ruff check custom_components tests` exits 0.
- [ ] Total coverage ≥ 95%.
- [ ] No files outside "In scope" modified.
- [ ] `plans/README.md` status row for 016 flipped to DONE.

## STOP conditions

- The extended `_make_client` signature breaks any existing test — that
  would mean the previous test-file's `_make_client` has additional
  callers with keyword arguments this plan didn't foresee. Investigate.
- The happy-hour flip handler at `coordinator.py:390-449` no longer sets
  `runtime.reload_pending`. That contract was assumed; if drift, plan 016
  cannot proceed without re-planning.
- The debounce order (happy-hour fires first vs solar fires first) matters
  for the assertion in the first test. As of the "Planned at" SHA the
  order is: happy-hour first (line 268), then solar (line 302). If code
  reorders them, revisit whether the test's assertions still hold.

## Maintenance notes

- If a third feature adds a third `runtime.reload_pending` flip site, add
  a corresponding test to this file rather than a new module — keeps the
  cross-feature debounce assertions consolidated.
- Consider promoting the debounce into an `EngieBeData.schedule_reload()`
  method so no future flip site can forget the `if reload_pending: return`
  guard. That's a refactor beyond this plan's scope; note it as a
  follow-up finding after both 015 and 016 land.
- Reviewer should confirm that `hass.async_block_till_done()` is called
  after `_async_update_data()` so the background-task-scheduled reload
  mock resolves before the assertion.
