# Plan 015: Test the `has_solar` flip triggers `hass.config_entries.async_reload` exactly once

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 6753af3..HEAD -- custom_components/engie_be/coordinator.py tests/test_coordinator_solar_surplus.py`
> Solar-surplus is uncommitted at "Planned at"; compare "Current state"
> excerpts against the live files before proceeding.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (mocking `hass.config_entries.async_reload` correctly is
  finicky; verify the exact API surface with the installed HA version)
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `6753af3`, 2026-07-08

## Why this matters

`_async_apply_has_solar` (`coordinator.py:659-687`) schedules
`hass.config_entries.async_reload(entry_id)` via
`async_create_background_task` when `has_solar` flips (True ↔ False). This
is the mechanism that makes solar sensors appear or disappear when a
customer signs up for (or drops) solar mid-session. **Nothing in the test
suite verifies this actually fires.**

`tests/test_coordinator_solar_surplus.py` covers the state cache — every
test that flips `has_solar` asserts `sub_data.has_solar is <expected>`,
none assert `async_reload` was called. The debounce flag
`runtime.reload_pending` is set but never validated end-to-end. A regression
that silently drops the background-task scheduling would pass all current
tests and only surface as a user report ("my solar sensors didn't show up
after I got panels installed").

## Current state

### Files

- `custom_components/engie_be/coordinator.py` — `_async_apply_has_solar` at
  lines 630-687.
- `tests/test_coordinator_solar_surplus.py` — 8 tests, all opt-in to
  `pytest.mark.solar_surplus` (module-level `pytestmark`).

### `_async_apply_has_solar` (lines 630-687)

```python
    @callback
    def _async_apply_has_solar(
        self,
        *,
        previous_has_solar: bool | None,
        new_has_solar: bool | None,
    ) -> None:
        """
        Persist the has_solar signal and schedule a reload on a flip.

        First observation (previous is None) just seeds the cache;
        subsequent flips (True <-> False) reload the parent entry so the
        surplus sensor appears or disappears cleanly. Debounced by the
        shared ``reload_pending`` flag on the parent runtime.
        """
        if new_has_solar is None:
            return
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None:
            return
        subentry_data = runtime.subentry_data.get(self.subentry.subentry_id)
        if subentry_data is None:
            return

        subentry_data.has_solar = new_has_solar

        if previous_has_solar is None:
            LOGGER.debug(
                "BAN %s: initial solar-surplus availability observed as %s",
                mask_identifier(self.business_agreement_number),
                new_has_solar,
            )
            return
        if previous_has_solar == new_has_solar:
            return
        if runtime.reload_pending:
            return

        runtime.reload_pending = True
        LOGGER.info(
            "Solar-surplus availability changed for BAN %s (%s -> %s); "
            "reloading config entry to reconcile entities",
            mask_identifier(self.business_agreement_number),
            previous_has_solar,
            new_has_solar,
        )
        self.hass.async_create_background_task(
            self.hass.config_entries.async_reload(self.config_entry.entry_id),
            name=(
                "engie_be_reload_on_solar_surplus_change_"
                f"{self.config_entry.entry_id}"
            ),
        )
```

### Existing test structure

`tests/test_coordinator_solar_surplus.py` uses these helpers already:
- `_build_entry(hass)` — creates a v5 MockConfigEntry with one subentry.
- `_coord(hass, entry, subentry)` — instantiates the coordinator.
- `_wire(entry, client, subentry, coord, service_points=...)` — attaches
  runtime data with `EngieBeSubentryData`.
- `_make_client(*, solar_payload, solar_flag=None)` — API mock.

`tests/test_coordinator_happy_hour_enrollment.py` around line 256 already
demonstrates the reload-mock pattern using
`monkeypatch.setattr(hass.config_entries, "async_reload", AsyncMock())`. Use
the same idiom.

### Repo conventions

- Solar-surplus coordinator tests opt out of the autouse flag-probe stub
  via `pytestmark = pytest.mark.solar_surplus` at the top of the file.
- Reload assertions verify both the mock call count AND
  `runtime.reload_pending == True` after the flip.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Target tests | `.venv/bin/pytest tests/test_coordinator_solar_surplus.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass |

## Scope

**In scope**:
- `tests/test_coordinator_solar_surplus.py` — add three new tests.

**Out of scope**:
- `_async_apply_has_solar` — behaviour is what we want to lock in; the
  test asserts it, does not change it.
- Cross-feature reload debouncing (happy-hour + solar simultaneous flip) —
  that's plan 016.
- Any change to `runtime.reload_pending` semantics.

## Git workflow

- Branch: `advisor/015-has-solar-reload-end-to-end-test`.
- Commit style: `test(coordinator): verify has_solar flip schedules exactly one reload`.

## Steps

### Step 1: Read the reload-mock pattern in the happy-hour test

Open `tests/test_coordinator_happy_hour_enrollment.py` and locate
`test_enrolment_flip_schedules_reload` (around line 256). Note:
- It uses `monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)`.
- The mock is an `AsyncMock()`.
- After the coordinator refresh, it asserts `reload_pending is True`.

Model the new tests after this exactly.

### Step 2: Add three tests

Append to `tests/test_coordinator_solar_surplus.py`:

```python
async def test_first_has_solar_observation_seeds_cache_without_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First refresh (previous_has_solar=None) must NOT schedule a reload."""
    entry = _build_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=_load(_SOLAR_HIGH))
    coord = _coord(hass, entry, subentry)
    _wire(entry, client, subentry, coord)

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is True
    assert entry.runtime_data.reload_pending is False
    reload_mock.assert_not_awaited()


async def test_has_solar_true_to_false_flip_schedules_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """True->False flip must set reload_pending and call async_reload once."""
    entry = _build_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=_load(_SOLAR_NO_DATA))
    coord = _coord(hass, entry, subentry)
    _wire(entry, client, subentry, coord)

    # Seed: cache says the customer HAS solar; new refresh returns all NO_DATA.
    entry.runtime_data.subentry_data[subentry.subentry_id].has_solar = True

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()
    await hass.async_block_till_done()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is False
    assert entry.runtime_data.reload_pending is True
    reload_mock.assert_awaited_once_with(entry.entry_id)


async def test_has_solar_no_flip_does_not_schedule_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same value on consecutive refreshes → no reload, reload_pending stays False."""
    entry = _build_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=_load(_SOLAR_HIGH))
    coord = _coord(hass, entry, subentry)
    _wire(entry, client, subentry, coord)

    # Seed: cache says True; new refresh also returns True (has data).
    entry.runtime_data.subentry_data[subentry.subentry_id].has_solar = True

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is True
    assert entry.runtime_data.reload_pending is False
    reload_mock.assert_not_awaited()
```

Note: the second test above requires `_SOLAR_NO_DATA` to be imported at the
top of the file. Verify by grepping:

```bash
grep -n "_SOLAR_NO_DATA\|_SOLAR_HIGH" tests/test_coordinator_solar_surplus.py | head
```

Both should already be imported per the current state (`_SOLAR_NO_DATA` at
line 40, `_SOLAR_HIGH` at line 41 based on the audit).

### Step 3: Run the new tests

```bash
.venv/bin/pytest tests/test_coordinator_solar_surplus.py::test_first_has_solar_observation_seeds_cache_without_reload -v
.venv/bin/pytest tests/test_coordinator_solar_surplus.py::test_has_solar_true_to_false_flip_schedules_reload -v
.venv/bin/pytest tests/test_coordinator_solar_surplus.py::test_has_solar_no_flip_does_not_schedule_reload -v
```

All three must pass.

### Step 4: Full gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

Three new tests in `tests/test_coordinator_solar_surplus.py`:

- `test_first_has_solar_observation_seeds_cache_without_reload` — the
  first-observation branch (previous_has_solar is None).
- `test_has_solar_true_to_false_flip_schedules_reload` — the primary
  happy-path for the debounce and reload logic.
- `test_has_solar_no_flip_does_not_schedule_reload` — same-value refresh.

The False→True flip is symmetric to True→False and covered indirectly by
the existing `test_non_no_data_response_marks_has_solar_true` (which seeds
`has_solar = False` via the coordinator's initial `None` state — extend if
you want to catch it explicitly, otherwise the two-way symmetry is
verified by inspection).

Model after: `test_enrolment_flip_schedules_reload` in
`tests/test_coordinator_happy_hour_enrollment.py`.

## Done criteria

- [ ] `.venv/bin/pytest tests/test_coordinator_solar_surplus.py -v` all pass, 11 tests (8 existing + 3 new).
- [ ] The True→False test asserts `reload_mock.assert_awaited_once_with(entry.entry_id)`.
- [ ] The no-flip test asserts `reload_mock.assert_not_awaited()`.
- [ ] `.venv/bin/ruff check custom_components tests` exits 0.
- [ ] Total coverage ≥ 95%.
- [ ] No files outside "In scope" modified.
- [ ] `plans/README.md` status row for 015 flipped to DONE.

## STOP conditions

- The reload-mock pattern (monkeypatching `hass.config_entries.async_reload`
  with an `AsyncMock`) fails because the method has been renamed or moved
  in the pinned HA version. Report the actual API before working around it.
- The True→False test asserts `reload_pending is True` but the assertion
  fails — that means `_async_apply_has_solar` semantics have drifted. Do
  not adjust the test to match new behaviour; report the drift.
- The `_SOLAR_NO_DATA` fixture doesn't produce `has_solar=False` at the
  coordinator layer — verify the fixture's `level` values are all
  `NO_DATA` by opening `tests/fixtures/solar_surplus_no_data.json`.

## Maintenance notes

- If HA's config-entry state machine ever adds true idempotency guarantees
  around `async_reload` (i.e. "reload calls are silently deduplicated"),
  the debounce logic in `_async_apply_has_solar` becomes redundant. Keep
  the debounce anyway — it avoids extra work on the coordinator side.
- If a future feature adds a third condition that flips
  `runtime.reload_pending` (currently only happy-hour + solar), consider
  centralising the debounce into a helper on `EngieBeData`. See
  plan 016 for a related concern.
- Reviewer should verify the `AsyncMock` return value doesn't accidentally
  swallow real reload work in tests that use the same fixture.
