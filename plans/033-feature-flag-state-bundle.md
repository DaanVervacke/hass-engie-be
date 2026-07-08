# Plan 033: Bundle per-subentry feature-flag booleans into `FeatureFlagState`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report - do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat main..HEAD -- custom_components/engie_be/data.py custom_components/engie_be/coordinator.py`
> If either file has changed materially since this plan was written,
> re-read the "Current state" excerpts against the live files before
> proceeding. A structural change to the flag-apply methods is a STOP.

## Status

- **Priority**: P3 (Fowler "Data Clumps" - preemptive tidy)
- **Effort**: M (single-digit net LoC, but touches coordinator hot path)
- **Risk**: LOW-MEDIUM (behavior must be byte-identical, ~5 read/apply
  method pairs move behind a namespace)
- **Depends on**: 022 (shared flag scaffold - already DONE)
- **Category**: refactor / shrink

## Why this matters

Three per-subentry feature-flag booleans travel together on
`EngieBeSubentryData` and share the same lifecycle (fail-open, `None`
until first observation, flip triggers debounced reload):

- `is_happy_hour_enrolled` (Happy Hours)
- `has_solar` (Solar Surplus)
- `is_tou_active` (Time-of-Use supplier billing)

Each one has parallel `_read_cached_X()` and `_async_apply_X()` helpers
on the coordinator, and each expands the field surface of
`EngieBeSubentryData` by one line. Adding a fourth flag today would
mean another `_read_cached_` + `_async_apply_` pair plus another field -
that's the shotgun-surgery smell the bundle prevents.

Two-axis code-review called this out as a "not-a-violation but a
FeatureFlagState bundle would earn its keep before a fourth flag lands"
finding. Landing it now (while the pattern is still fresh) is cheaper
than after another feature ships. Plan 022 already extracted the shared
`_async_probe_boolean_flag` + `_async_apply_flag_state` scaffold. This
plan is the natural next step - collapse the storage side to match.

**Non-goal**: this is not a behavior change. If a test fails, the
refactor is wrong. `reload_pending` (which lives on the parent
`EngieBeData`, not `EngieBeSubentryData`) is out of scope - it is a
cross-subentry debounce, not a per-subentry flag.

## Current state

### `custom_components/engie_be/data.py` (post-v0.13.0)

```python
@dataclass
class EngieBeSubentryData:
    ...
    is_happy_hour_enrolled: bool | None = field(default=None)
    has_solar: bool | None = field(default=None)
    is_tou_active: bool | None = field(default=None)
```

Three independent optional booleans, all `None`-defaulted, all with the
same three-state semantics documented in the class docstring
(`None` = not-yet-observed, `True` = enabled, `False` = disabled).

### `custom_components/engie_be/coordinator.py`

Six methods form three parallel pairs:

```
_read_cached_happy_hour_enrolled  -> bool | None
_async_apply_happy_hour_enrolled  -> None
_read_cached_has_solar            -> bool | None
_async_apply_has_solar            -> None
_read_cached_is_tou_active        -> bool | None
_async_apply_is_tou_active        -> None
```

Each `_async_apply_*` delegates to the shared
`_async_apply_flag_state(field_name=..., previous=..., new=...)` helper
from plan 022. Each `_read_cached_*` returns
`self._subentry_data().<field>` or `None` if the subentry data is
missing.

### Callers

Grep for `is_happy_hour_enrolled\|has_solar\|is_tou_active` across the
repo. Expected consumers:

- `coordinator.py` - reads/writes via the six methods above.
- `diagnostics.py` - reads each flag directly from
  `subentry_data.<field>` for the diagnostics dump.
- `sensor.py`, `binary_sensor.py`, `calendar.py` - gate on
  `subentry_data.is_tou_active`, `has_solar`, or
  `is_happy_hour_enrolled` at entity-creation time.
- Tests - many test files access these fields directly on
  `EngieBeSubentryData` instances.

Every direct read from outside the coordinator must continue to work
after the refactor.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Enumerate direct readers | `grep -rn "is_happy_hour_enrolled\|has_solar\|is_tou_active" custom_components/engie_be/ tests/` | ~30-60 hits, enumerate before editing |
| Enumerate cached-read helpers | `grep -n "_read_cached_\|_async_apply_happy_hour\|_async_apply_has_solar\|_async_apply_is_tou" custom_components/engie_be/coordinator.py` | 8 hits (3 read + 3 async_apply + 2 defs) |
| Tests | `.venv/bin/pytest tests/ -q --tb=line` | 873 pass (or current baseline). MUST NOT change |
| Ruff | `.venv/bin/ruff check custom_components/engie_be/ tests/` | clean |
| Format | `.venv/bin/ruff format --check custom_components/engie_be/ tests/` | clean |
| Coverage regression check | `.venv/bin/pytest --cov=custom_components.engie_be --cov-report=term-missing -q` | delta <= +1 uncovered line |

## Scope

**In scope**:

- `custom_components/engie_be/data.py` - introduce
  `FeatureFlagState` frozen dataclass with three fields
  (`happy_hour_enrolled`, `solar`, `tou_active`), replace the three
  loose booleans on `EngieBeSubentryData` with a single
  `feature_flags: FeatureFlagState = field(default_factory=FeatureFlagState)`
  field.
- `custom_components/engie_be/coordinator.py` - collapse the six
  helpers into two: `_read_cached_flag(name)` and
  `_async_apply_flag(name, previous, new)`. Extend
  `_async_apply_flag_state` (from plan 022) with an optional `target`
  parameter so it can mutate a nested object instead of only
  `EngieBeSubentryData`. Keep the fail-open + reload debounce semantics
  identical.
- `custom_components/engie_be/diagnostics.py` - update the diagnostics
  dump to read from `subentry_data.feature_flags.<field>`.
- `custom_components/engie_be/sensor.py`,
  `custom_components/engie_be/binary_sensor.py`,
  `custom_components/engie_be/calendar.py`,
  `custom_components/engie_be/energy.py` - update entity-creation gates
  and the solar-forecast hook to read from
  `subentry_data.feature_flags.<field>`.
- `tests/` - update every direct field access. Prefer a small helper on
  the test conftest if the pattern shows up more than 3 times
  (`_set_flags(subentry_data, solar=True)` is idiomatic).

**Out of scope**:

- `reload_pending` on `EngieBeData` - stays where it is (cross-subentry
  debounce, different lifecycle).
- Any change to `_async_probe_boolean_flag` from plan 022 (the probe
  side is independent of storage shape).
- Behavioral change to `_async_apply_flag_state`: it gains an optional
  `target` parameter but the flat-`setattr` branch (target=None,
  writes to `EngieBeSubentryData` directly) must remain byte-identical
  to today for any remaining callers. This plan migrates all three
  current callers to the nested-target branch, so the flat branch
  becomes dead code and MAY be removed - but that removal is opt-in.
  Keep it if in doubt.
- Renaming the flags themselves. Keep the leaf names
  (`happy_hour_enrolled`, `solar`, `tou_active`) short since the
  namespace prefix carries the "flag" context.
- Any behavior change. Fail-open discipline and reload debouncing MUST
  be byte-identical. If in doubt, STOP.

## Git workflow

- Branch: `advisor/033-feature-flag-state-bundle`
- Commit style: `refactor(data): bundle per-subentry feature flags into FeatureFlagState`
- One commit if the diff fits under ~200 lines net, or two commits (data
  layer + call-site sweep) if larger. Do NOT split the data-layer
  change from the coordinator update - they must land atomically to
  keep the tree green.

## Steps

### Step 0: Preflight - clean working tree for the in-scope files

This plan refactors seven files (`data.py`, `coordinator.py`,
`diagnostics.py`, `sensor.py`, `binary_sensor.py`, `calendar.py`,
`energy.py`) plus `tests/`. `git status` at plan-write time showed
uncommitted changes in `sensor.py`, `binary_sensor.py`, and other
files unrelated to this plan. Mixing that dirt with a refactor makes
review impossible.

Before starting:

```
git status --short custom_components/engie_be/ tests/
```

Every in-scope file above must show either no entry (clean) or only
staged changes that belong to a preceding plan (e.g. 032). If
`sensor.py`, `binary_sensor.py`, `data.py`, `coordinator.py`,
`diagnostics.py`, `calendar.py`, or `energy.py` shows unstaged
modifications from unrelated v0.13.0 work, STOP and ask the user to
commit or stash first. Do not start the refactor on a dirty tree.

### Step 1: Add `FeatureFlagState` to `data.py`

Above `EngieBeSubentryData`:

```python
@dataclass(frozen=False)
class FeatureFlagState:
    """
    Per-subentry ENGIE feature-flag snapshot.

    Each field mirrors a boolean flag surfaced by the ENGIE API and
    follows the same three-state lifecycle documented on
    :class:`EngieBeSubentryData`:

    - ``None`` until the first successful refresh observes the flag.
    - ``True`` when the customer is enrolled / eligible.
    - ``False`` when the flag is explicitly off (or the endpoint is
      absent under the fail-open policy).

    Bundled as one object so future flags land as a single field
    addition here, not another pair of fields + helpers on the
    coordinator.
    """

    happy_hour_enrolled: bool | None = None
    solar: bool | None = None
    tou_active: bool | None = None
```

`frozen=False` because the coordinator mutates fields in place on each
refresh (matching the existing pattern - the loose booleans are
mutable). Do NOT switch to `frozen=True` and replace-per-flip. That
would silently churn identity and break any code path that captured a
reference.

Replace the three loose fields on `EngieBeSubentryData` with:

```python
feature_flags: FeatureFlagState = field(default_factory=FeatureFlagState)
```

Update the class docstring: replace the three per-flag paragraphs with
one paragraph explaining the bundle and pointing at
`FeatureFlagState` for details.

**Verify**:

```
.venv/bin/python -c "from custom_components.engie_be.data import FeatureFlagState, EngieBeSubentryData; ff = FeatureFlagState(); print(ff.happy_hour_enrolled, ff.solar, ff.tou_active)"
```

Expected: `None None None`.

### Step 2: Collapse the coordinator helpers

The current `_async_apply_flag_state` signature (coordinator.py:645) is:

```python
def _async_apply_flag_state(
    self,
    *,
    field_name: str,
    previous: bool | None,
    new: bool,
    log_prefix: str,
    task_name_suffix: str,
) -> None:
    ...
    setattr(subentry_data, field_name, new)   # flat setattr on EngieBeSubentryData
```

It does a **flat `setattr` on `EngieBeSubentryData`** and requires
`log_prefix` and `task_name_suffix` kwargs. It does NOT walk dotted
paths. Passing `field_name="feature_flags.solar"` would create a
literal attribute named `"feature_flags.solar"` and silently corrupt
state.

**Sub-step 2a: extend `_async_apply_flag_state` with an optional `target`**

Change the signature to accept an optional target object. When
provided, the helper mutates that instead of `EngieBeSubentryData`:

```python
def _async_apply_flag_state(
    self,
    *,
    field_name: str,
    previous: bool | None,
    new: bool,
    log_prefix: str,
    task_name_suffix: str,
    target: object | None = None,
) -> None:
    ...
    subentry_data = runtime.subentry_data.get(self.subentry.subentry_id)
    if subentry_data is None:
        return

    write_target = target if target is not None else subentry_data
    setattr(write_target, field_name, new)

    # ... rest of the method (log, reload debounce) unchanged
```

The `subentry_data is None` early-return is retained because the
reload debounce still keys off `runtime.reload_pending` and the log
lines still mask the BAN via `self.business_agreement_number` -
neither of which needs the target. Only the `setattr` gets the
`write_target` swap.

**Sub-step 2b: add per-flag metadata and two thin wrappers**

Above the three call sites in the coordinator body, add a module- or
class-level constant with the log/task metadata that used to be
inlined at each `_async_apply_*` site:

```python
_FEATURE_FLAG_METADATA: dict[str, tuple[str, str]] = {
    "happy_hour_enrolled": ("happy-hours enrolment", "happy_hours_change"),
    "solar": ("solar-surplus availability", "solar_surplus_change"),
    "tou_active": ("TOU activation", "tou_active_change"),
}
```

Match the log-prefix strings and task-suffix strings **verbatim** from
the existing `_async_apply_has_solar`, `_async_apply_happy_hour_enrolled`,
and `_async_apply_is_tou_active` methods. A byte-diff of the log lines
they produce must be empty. If you cannot find the current string for
happy-hour enrolment or TOU activation because the field name differs,
STOP and read the current method body before guessing.

Then add the two new helpers:

```python
def _read_cached_flag(self, name: str) -> bool | None:
    """Return the previously-observed value for ``name``, or ``None``."""
    subentry_data = self._subentry_data()
    if subentry_data is None:
        return None
    return getattr(subentry_data.feature_flags, name)

@callback
def _async_apply_flag(
    self,
    name: str,
    *,
    previous: bool | None,
    new: bool | None,
) -> None:
    """Persist ``name`` on ``feature_flags`` and schedule a reload on a flip."""
    if new is None:
        return
    subentry_data = self._subentry_data()
    if subentry_data is None:
        return
    log_prefix, task_suffix = _FEATURE_FLAG_METADATA[name]
    self._async_apply_flag_state(
        field_name=name,
        previous=previous,
        new=new,
        log_prefix=log_prefix,
        task_name_suffix=task_suffix,
        target=subentry_data.feature_flags,
    )
```

Then delete the three old `_async_apply_happy_hour_enrolled`,
`_async_apply_has_solar`, `_async_apply_is_tou_active` methods and the
three `_read_cached_happy_hour_enrolled`, `_read_cached_has_solar`,
`_read_cached_is_tou_active` methods.

**Sub-step 2c: update the three call sites in `_async_update_data`**

Each of the three sites in the orchestrator (Happy Hours, solar, TOU)
uses the shape:

```python
previous_X = self._read_cached_X()
...
self._async_apply_X(previous_X=..., new_X=...)
```

Rewrite each to:

```python
previous = self._read_cached_flag("solar")   # or "happy_hour_enrolled" / "tou_active"
...
self._async_apply_flag("solar", previous=previous, new=new_value)
```

Keep the surrounding orchestration (contract fetching, derivation of
`new_value`) unchanged.

**Verify**:

```
grep -n "_read_cached_\|_async_apply_happy_hour\|_async_apply_has_solar\|_async_apply_is_tou" custom_components/engie_be/coordinator.py
```

Expected: only the two new methods (`_read_cached_flag`,
`_async_apply_flag`) plus the shared `_async_apply_flag_state`
(from plan 022) surface. Three old-name pairs are gone.

```
.venv/bin/python -c "from custom_components.engie_be.coordinator import _FEATURE_FLAG_METADATA; print(sorted(_FEATURE_FLAG_METADATA))"
```

Expected: `['happy_hour_enrolled', 'solar', 'tou_active']`.

### Step 3: Update the direct readers

Grep the callers table above and update each site:

- `diagnostics.py`: `subentry_data.is_tou_active` → `subentry_data.feature_flags.tou_active`. Ditto for `has_solar` and `is_happy_hour_enrolled` (dropping the `is_` prefix that lived on the loose field).
- `sensor.py`, `binary_sensor.py`, `calendar.py`: same substitution at the entity-creation gates.
- `energy.py`: the `async_get_solar_forecast` hook reads `sub_data.has_solar` around line 52. Update to `sub_data.feature_flags.solar`.
- `tests/`: every fixture that constructs an `EngieBeSubentryData` (or the conftest factory that does) must pass a `FeatureFlagState` instead of the loose booleans. Update the shared factory in `tests/conftest.py` first, then any test that overrides fields directly.

**Verify**:

```
grep -rn "\.is_happy_hour_enrolled\|\.has_solar\|\.is_tou_active" custom_components/engie_be/ tests/
```

Expected: zero matches. Every access must go through
`.feature_flags.<name>`.

### Step 4: Full test sweep

```
.venv/bin/pytest tests/ -q --tb=line
```

Expected: pass count unchanged from baseline (873 as of `91bf9e6`).
If a single test fails, the refactor is wrong - re-check that every
direct field access was translated correctly. Do NOT change a test
assertion to accommodate the new shape unless the assertion was
reading the raw field name (in which case update it to read the new
path).

```
.venv/bin/pytest --cov=custom_components.engie_be --cov-report=term-missing -q
```

Expected: coverage delta ≤ +1 uncovered line. The new
`_read_cached_flag` / `_async_apply_flag` methods should be exercised
by existing coordinator tests (they replace code that was already
covered).

```
.venv/bin/ruff check custom_components/engie_be/ tests/
.venv/bin/ruff format --check custom_components/engie_be/ tests/
```

Expected: clean.

### Step 5: Update `plans/README.md`

Add a row after 032:

```
| 033 | Bundle per-subentry feature-flag booleans into `FeatureFlagState` | P3 | M | 022 | DONE |
```

## Test plan

No new tests. The existing coordinator, diagnostics, sensor, and
binary-sensor tests already cover every path this refactor touches. If
you find yourself writing a new test to "cover the FeatureFlagState
dataclass," STOP - the dataclass is a dumb bag of fields and adding a
test for the assignment semantics would be over-testing.

The one exception: if you introduce a helper on `tests/conftest.py`
(e.g. `_set_feature_flags(subentry_data, *, solar=None, ...)`), add
one small `test_` case that exercises it end-to-end via an existing
coordinator test - reuse an existing test file, don't create a new one.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `custom_components/engie_be/data.py` defines `FeatureFlagState` with three `bool | None` fields.
- [ ] `EngieBeSubentryData` no longer has loose `is_happy_hour_enrolled`, `has_solar`, `is_tou_active` fields.
- [ ] `EngieBeSubentryData.feature_flags: FeatureFlagState` field exists with a `default_factory`.
- [ ] `grep -rn "\.is_happy_hour_enrolled\|\.has_solar\|\.is_tou_active" custom_components/engie_be/ tests/` returns zero matches.
- [ ] `coordinator.py` has exactly two flag helpers (`_read_cached_flag`, `_async_apply_flag`), and the six old-name methods are gone.
- [ ] `.venv/bin/pytest tests/ -q --tb=line` pass count unchanged from baseline.
- [ ] `.venv/bin/pytest --cov=custom_components.engie_be` coverage delta ≤ +1 uncovered line.
- [ ] `.venv/bin/ruff check custom_components/engie_be/ tests/` clean.
- [ ] `.venv/bin/ruff format --check custom_components/engie_be/ tests/` clean.
- [ ] `plans/README.md` row 033 marked DONE.

## STOP conditions

Stop and report back (do not improvise) if:

- Any test fails after Step 3 that was green before Step 1. This is a
  refactor - behavior must be identical. A failure means a call site was
  missed or the field-mapping is wrong.
- The `_async_apply_flag_state` helper (from plan 022) turns out to be
  incompatible with a nested attribute path like `feature_flags.solar`.
  In that case fall back to keeping the flag-state helper's field-name
  string but pass the leaf name only, and mutate the nested field in
  `_async_apply_flag` before delegating. Report the incompatibility.
- Coverage drops by more than +1 uncovered line. That signals dead code
  in the new helpers (e.g. a branch that never fires). Simplify rather
  than adding a new test.
- The direct-reader grep in Step 3 turns up a match outside the
  in-scope files (`data.py`, `coordinator.py`, `diagnostics.py`,
  `sensor.py`, `binary_sensor.py`, `calendar.py`, `energy.py`,
  `tests/`). Report the extra location before proceeding.

## Maintenance notes

- Future feature flags (a fourth one, whenever it lands) become one
  new field on `FeatureFlagState` and one new call in the coordinator
  orchestrator to `_read_cached_flag(...)` / `_async_apply_flag(...)`.
  No new methods, no new fields on `EngieBeSubentryData`.
- If a flag ever needs richer state than `bool | None` (e.g. an enum,
  a per-EAN dict), promote its field to a nested dataclass rather than
  expanding `FeatureFlagState` with mixed types. Keep the bundle
  strictly for `bool | None` flags.
- The `reload_pending` flag on `EngieBeData` is deliberately outside
  this bundle - it is a cross-subentry debounce with a different
  lifecycle (one-shot per refresh tick, not persisted across ticks).
  Do NOT fold it in.
