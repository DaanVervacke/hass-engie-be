# Plan 022: Extract the shared feature-flag-gated coordinator scaffold

> **Executor instructions**: Follow this plan step by step. Run every
> verification command. This is a REFACTOR — behavior must be
> byte-identical after the change; every existing test that passes now
> must still pass. STOP if any coordinator test fails after your edits.
> Update `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be/coordinator.py`

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: 018 (soft-fail alignment) — recommended to land first
  so the extracted helper has one unambiguous policy to encode
- **Category**: tech-debt
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

Three coordinator features implement the same "boolean feature-flag
probe + apply-state + reload-on-flip" pattern:

1. Happy Hours enrolment (`is_happy_hour_enrolled`) — `coordinator.py`
   `_async_fetch_enrollment` + `_async_apply_enrollment`
2. Solar Surplus availability (`has_solar`) —
   `_async_fetch_solar_flag` + `_async_apply_has_solar`
3. Time-of-Use activation (`is_tou_active`) —
   `_async_fetch_tou_flag` + `_async_apply_is_tou_active`

Every "apply" method has the same structural body: 25 lines, reading
runtime data, updating the field, first-observation branch, no-change
branch, `runtime.reload_pending` gate, background-task reload. Every
"fetch" method has the same 20-line body: try/except with
`ConfigEntryAuthFailed` escalation on auth and warn-and-soft-fail on
transient error. Adding a fourth flag-gated feature (see plan 025:
account balance) means copy-pasting 45 more lines.

Extraction reduces:
- ~135 lines of duplicated coordinator code to ~50 lines of shared
  helper + 3 declarative one-liners per feature
- Autouse-fixture drift risk in `conftest.py` (two identical stubs
  already; a third for account-balance would compound)
- The likelihood of a bug fix being applied to one flag but not the
  others

## Current state

### Duplicated bodies

Read the three fetch methods end-to-end:

- `coordinator.py::_async_fetch_enrollment` (~360-405, ~45 lines)
- `coordinator.py::_async_fetch_solar_flag` (~470-510, ~40 lines)
- `coordinator.py::_async_fetch_tou_flag` (~720-757, ~38 lines)

And the three apply methods:

- `coordinator.py::_async_apply_enrollment` (~406-470, ~65 lines)
- `coordinator.py::_async_apply_has_solar` (~659-687, ~30 lines)
- `coordinator.py::_async_apply_is_tou_active` (~813-865, ~55 lines)

The bodies are near-identical modulo:
- Field name on `EngieBeSubentryData` (`is_happy_hour_enrolled` /
  `has_solar` / `is_tou_active`)
- Log message prefix (e.g., "Happy Hours enrolment" / "solar-surplus" /
  "TOU")
- API method to call for the probe
- The "reload task name" string
- Happy Hours' fetch inspects an extra `reason` field; solar/TOU only
  read `value`

### Excerpt to preserve (canonical version — from `_async_apply_enrollment`)

```python
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None:
            return
        subentry_data = runtime.subentry_data.get(self.subentry.subentry_id)
        if subentry_data is None:
            return

        # <-- set the field here -->

        if previous is None:
            LOGGER.debug("... initial ... observed as %s ...", new)
            return
        if previous == new:
            return
        if runtime.reload_pending:
            return

        runtime.reload_pending = True
        LOGGER.info("... changed for BAN %s (%s -> %s); reloading ...", ...)
        self.hass.async_create_background_task(
            self.hass.config_entries.async_reload(self.config_entry.entry_id),
            name=f"engie_be_reload_on_<feature>_change_{self.config_entry.entry_id}",
        )
```

### Design of the extraction

**Do NOT introduce a full "gate class" abstraction with dataclasses and
runtime dispatch.** That's the version this codebase does not want.
Instead, add two helper *methods* to `EngieBeDataUpdateCoordinator` and
have each existing method delegate.

Target shape:

```python
    async def _async_probe_boolean_flag(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
        *,
        flag_name: str,
        api_method: str,
        log_prefix: str,
        previous_value: bool | None,
    ) -> bool:
        """
        Probe a boolean feature flag with the shared soft-fail discipline.

        Auth failures escalate via ``ConfigEntryAuthFailed``. Any other
        API error logs a warning at ``log_prefix`` and soft-fails to
        ``previous_value`` (falling back to ``False`` if that is None) —
        matching the fail-open discipline documented in plan 018.

        Returns the ``value`` field of the flag response coerced to bool.
        """
        ...

    @callback
    def _async_apply_flag_state(
        self,
        *,
        field_name: str,
        previous: bool | None,
        new: bool,
        log_prefix: str,
        task_name_suffix: str,
    ) -> None:
        """
        Persist a boolean flag state and schedule a reload on a flip.

        Shared implementation of the first-observation, no-change, and
        flip branches used by Happy Hours enrolment, solar-surplus
        availability, and TOU activation. ``field_name`` is the
        attribute on ``EngieBeSubentryData`` to mutate.
        """
        ...
```

Then rewrite each of the six existing methods to be thin wrappers.
The Happy Hours probe stays slightly custom because it also reads
`reason` for logging — factor that out with a small callback or keep
one shared shell and one Happy Hours-specific wrapper.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass, ≥95% |
| Coordinator tests | `.venv/bin/pytest tests/test_coordinator_happy_hour_enrollment.py tests/test_coordinator_solar_surplus.py tests/test_coordinator_tou.py -v` | all pass |

## Scope

**In scope**:
- `custom_components/engie_be/coordinator.py` — add two helpers, rewrite
  three fetch methods + three apply methods to delegate.
- `tests/conftest.py` — optionally consolidate the two autouse stubs
  (`_disable_solar_surplus_flag_probe`, `_disable_tou_flag_probe`) into
  a single parameterized fixture. Skip this if it complicates existing
  test opt-outs.

**Out of scope**:
- Behavior changes. This is a pure refactor. If any test needs updating,
  the refactor is wrong.
- `EngieBeSubentryData` field names. Do not rename `has_solar`,
  `is_tou_active`, or `is_happy_hour_enrolled`.
- Any sensor / binary sensor / API-client code.
- Adding a new gated feature to prove the abstraction. Plan 025 will
  exercise it.

## Steps

### Step 1: Add `_async_probe_boolean_flag`

Insert immediately below the existing `_read_cached_enrollment` (or
wherever helper methods cluster). Skeleton above; use `getattr(client,
api_method)` to invoke the specific client method by name.

Handle the `previous_value is None` case: fall back to whatever the
current per-feature default is. After plan 018 lands, all three
soft-fail to True on transient error, so:

```python
        except EngieBeApiClientError as exception:
            LOGGER.warning(
                "Failed to fetch %s feature flag for BAN %s, "
                "assuming enabled and continuing: %s",
                log_prefix,
                mask_identifier(business_agreement_number),
                exception,
            )
            return True if previous_value is None else bool(previous_value)
```

If plan 018 has NOT landed yet, keep the divergent behavior for now —
each caller passes an explicit `soft_fail_default: bool` kwarg. In that
case the helper needs one more parameter.

### Step 2: Add `_async_apply_flag_state`

Same insertion area. Use `setattr(subentry_data, field_name, new)` for
the field write. Format the log messages with `log_prefix` and use
`task_name_suffix` to disambiguate the background-task names.

### Step 3: Rewrite the three fetch methods

Each becomes ~5 lines. Example for solar:

```python
    async def _async_fetch_solar_flag(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
    ) -> bool:
        """Probe the ``solar-surplus-shown-dashboard`` feature flag."""
        return await self._async_probe_boolean_flag(
            client,
            business_agreement_number,
            flag_name=SOLAR_SURPLUS_SHOWN_DASHBOARD_KEY,
            api_method="async_get_solar_surplus_shown_dashboard_flag",
            log_prefix="solar-surplus",
            previous_value=self._read_cached_has_solar(),
        )
```

Keep the debug log at the caller site if it differs from the shared
one (Happy Hours logs `reason`, solar/TOU don't).

### Step 4: Rewrite the three apply methods

Each becomes ~5 lines. Example for TOU:

```python
    @callback
    def _async_apply_is_tou_active(
        self,
        *,
        previous_is_tou_active: bool | None,
        new_is_tou_active: bool | None,
    ) -> None:
        """Persist is_tou_active and schedule reload on a flip."""
        if new_is_tou_active is None:
            return
        self._async_apply_flag_state(
            field_name="is_tou_active",
            previous=previous_is_tou_active,
            new=new_is_tou_active,
            log_prefix="TOU",
            task_name_suffix="tou_change",
        )
```

Keep the `None` guard at the caller — apply-helper assumes non-None.

### Step 5: Preserve exception behavior

Verify:
- `ConfigEntryAuthFailed` is still raised on `EngieBeApiClientAuthenticationError`.
- The helper does NOT swallow errors it shouldn't. Add a specific
  test-lookup: `grep -n "ConfigEntryAuthFailed" custom_components/engie_be/coordinator.py`
  should return the same number of matches as before your edits (or
  one fewer if the auth branch consolidates into the helper).

### Step 6: Full gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/pytest tests/test_coordinator_happy_hour_enrollment.py tests/test_coordinator_solar_surplus.py tests/test_coordinator_tou.py -v` → all pass
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

No new tests. This refactor's success is defined by **no existing test
needing modification**. Any test change during this plan is a signal
the refactor changed behavior and needs to be undone.

If any behavior test does fail, the executor should revert its edits
and STOP.

## Done criteria

- [ ] `grep -c "_async_apply_flag_state\b" custom_components/engie_be/coordinator.py` returns at least 4 (definition + 3 callers).
- [ ] `grep -c "_async_probe_boolean_flag\b" custom_components/engie_be/coordinator.py` returns at least 4.
- [ ] `wc -l custom_components/engie_be/coordinator.py` reports fewer lines than the pre-refactor version.
- [ ] Coordinator method count: `grep -c "^    async def\|^    def" custom_components/engie_be/coordinator.py` is same or higher (two helpers added, none removed).
- [ ] `.venv/bin/pytest tests/ -q --cov-fail-under=95` passes with NO test file modified.
- [ ] `plans/README.md` status row for 022 flipped to DONE.

## STOP conditions

- Any test in `tests/test_coordinator_*.py` fails after your edits.
  Revert the failing extraction step; the shared helper does not
  fit all three cases as designed.
- Plan 018 hasn't landed and you see the three fetch methods have
  different soft-fail defaults (some True, some False). Either land
  018 first, or add a `soft_fail_default` parameter to
  `_async_probe_boolean_flag` and pass each caller's default
  explicitly.
- The Happy Hours fetch reads a `reason` field that the other two
  don't. If factoring this out (via a callback or optional extra-log
  parameter) makes the helper signature ugly, keep the Happy Hours
  method entirely custom and only extract solar + TOU. Two-of-three
  extraction is still a win.

## Maintenance notes

- Plan 025 (account balance + invoices) will use this helper for a
  fourth boolean flag. If the abstraction turns out badly under real
  use, this is when to revisit.
- The `runtime.reload_pending` debounce is central to correctness.
  Reviewer should scrutinize the extracted `_async_apply_flag_state`
  carefully — the `if runtime.reload_pending: return` gate MUST fire
  before the flag is set to `True`, otherwise two simultaneous flips
  both queue reloads.
- If the conftest autouse fixtures are also consolidated, make sure
  the marker names (`solar_surplus`, `tou`) still work as opt-outs
  independently; the parameterized version should register one
  autouse per marker.
