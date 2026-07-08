# Plan 010: Return `False` (not `None`) from `_derive_has_solar` when wrapper is present but per_ean is empty

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 6753af3..HEAD -- custom_components/engie_be/coordinator.py tests/test_coordinator_solar_surplus.py`
> The v0.13.0b0 Solar Surplus feature is uncommitted in the working tree as
> of the "Planned at" SHA — the diff will report many changed lines. Confirm
> the "Current state" excerpts below match the live file before proceeding;
> on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `6753af3`, 2026-07-08

## Why this matters

`_derive_has_solar` claims in its docstring that it returns `False` when "the
wrapper is present but every slot is `NO_DATA`" and `None` "when no wrapper is
available so callers know to preserve the last-known value." The code has one
edge case that violates its own contract: when `wrapper.get("data")` returns an
empty dict `{}`, the function returns `None` on line 1250 rather than falling
through to the `seen_any_slot=False` path that would return `False`.

In practice, `coordinator.py:614` already refuses to build a wrapper with an
empty `per_ean`, so a fresh coordinator refresh never surfaces this shape.
However, the function is also called with `previous_solar_wrapper` at
`coordinator.py:301`; a wrapper that has been mutated in place (or persisted
across reloads in the future) could reach this branch. Fixing the semantics
keeps the code honest with its docstring and eliminates a defensive-coding
foot-gun for a future maintainer who adds another caller.

## Current state

### Files

- `custom_components/engie_be/coordinator.py` — contains the `_derive_has_solar`
  module-level function.
- `tests/test_coordinator_solar_surplus.py` — pytest module for
  coordinator-level solar-surplus behaviour; uses the `_load` helper against
  `tests/fixtures/solar_surplus_*.json`.

### Excerpt to correct

`coordinator.py:1236-1270`:

```python
def _derive_has_solar(wrapper: dict[str, Any] | None) -> bool | None:
    """
    Infer whether the customer has a solar installation from a wrapper.

    Returns ``True`` when any hourly slot across any EAN and any day
    carries a level other than ``NO_DATA``, ``False`` when the wrapper
    is present but every slot is ``NO_DATA`` (the shape ENGIE returns
    for customers without solar), and ``None`` when no wrapper is
    available so callers know to preserve the last-known value.
    """
    if not isinstance(wrapper, dict):
        return None
    per_ean = wrapper.get("data")
    if not isinstance(per_ean, dict) or not per_ean:
        return None
    seen_any_slot = False
    for forecasts in per_ean.values():
        if not isinstance(forecasts, list):
            continue
        for day in forecasts:
            ...
```

The `or not per_ean` clause on the third-to-last line above short-circuits an
empty dict into `None`. Since the wrapper *is* a dict, semantics say the
answer is `False`.

### Repo conventions

- Small module-level helpers in `coordinator.py` are pure functions with
  concise docstrings following the pattern above.
- Type hints use `X | None` (PEP 604) throughout; imports from
  `__future__ import annotations` allow forward-ref syntax.
- Tests live in `tests/test_coordinator_solar_surplus.py` and use the
  `_load(_FIXTURES / "solar_surplus_no_data.json")` idiom. Fixtures live under
  `tests/fixtures/`.
- Existing test opt-in: this file starts with
  `pytestmark = pytest.mark.solar_surplus` so the autouse flag-probe stub is
  skipped for solar tests. New tests within the same file inherit this.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | "N files left unchanged" or "N files reformatted" |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Tests | `.venv/bin/pytest tests/test_coordinator_solar_surplus.py -v` | all pass |
| Coverage floor | `.venv/bin/python -m pytest tests/ -q --cov=custom_components.engie_be --cov-report=term --cov-fail-under=95` | `Required test coverage of 95% reached` |

## Scope

**In scope**:
- `custom_components/engie_be/coordinator.py` — one-line semantic fix
- `tests/test_coordinator_solar_surplus.py` — one new unit test

**Out of scope**:
- The `_async_fetch_solar_surplus` guard at `coordinator.py:614` — that
  correctly refuses to build empty wrappers on the fresh path and stays as is.
- The docstring — already accurate about the intent; only the implementation
  drifts.

## Git workflow

- Branch: `advisor/010-derive-has-solar-empty-per-ean` (create with `git switch -c`).
- Commit style follows conventional commits with the `fix(coordinator):` prefix
  matching prior commits like `refactor(entity):` in `git log`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Split the guard so an empty dict falls through

Edit `custom_components/engie_be/coordinator.py`. Replace:

```python
    if not isinstance(wrapper, dict):
        return None
    per_ean = wrapper.get("data")
    if not isinstance(per_ean, dict) or not per_ean:
        return None
    seen_any_slot = False
```

with:

```python
    if not isinstance(wrapper, dict):
        return None
    per_ean = wrapper.get("data")
    if not isinstance(per_ean, dict):
        return None
    seen_any_slot = False
```

An empty `per_ean` now reaches the loop, which iterates zero times, leaves
`seen_any_slot=False`, and returns `False` at the bottom — matching the
docstring.

**Verify**: `.venv/bin/ruff check custom_components/engie_be/coordinator.py` → exit 0.

### Step 2: Add a regression test

Append to `tests/test_coordinator_solar_surplus.py`. Model the test after the
existing `test_no_data_response_marks_has_solar_false` — but call
`_derive_has_solar` directly (it is imported from the coordinator module).

Add near the top of the file (with the other imports):

```python
from custom_components.engie_be.coordinator import _derive_has_solar
```

Then append this test function to the module:

```python
def test_derive_has_solar_returns_false_for_empty_per_ean() -> None:
    """
    Wrapper present but no per-EAN forecasts (empty dict) is a valid
    'no solar' shape. The helper must return False so callers can
    reconcile entity presence, not None (which means 'no signal').
    """
    assert _derive_has_solar({"data": {}, "fetched_at": "x"}) is False


def test_derive_has_solar_returns_none_for_non_dict_wrapper() -> None:
    """A non-dict wrapper (or None) is the 'no signal' case."""
    assert _derive_has_solar(None) is None
    assert _derive_has_solar([]) is None  # type: ignore[arg-type]
```

**Verify**: `.venv/bin/pytest tests/test_coordinator_solar_surplus.py -v` → the
two new tests pass, all existing pass.

### Step 3: Run the full gate

**Verify**:
- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov=custom_components.engie_be --cov-report=term --cov-fail-under=95` → passes, coverage ≥ 95%

## Test plan

- New tests to write, in `tests/test_coordinator_solar_surplus.py`:
  - `test_derive_has_solar_returns_false_for_empty_per_ean` — the regression
    for this plan.
  - `test_derive_has_solar_returns_none_for_non_dict_wrapper` — pinpoints the
    `None`-signal shape as the remaining path to `None`.
- Model after: the existing `test_no_data_response_marks_has_solar_false` test
  above in the same file uses a coordinator-level flow; the new tests call
  `_derive_has_solar` directly since the bug is in that pure helper.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `.venv/bin/ruff check custom_components tests` exits 0.
- [ ] `.venv/bin/pytest tests/test_coordinator_solar_surplus.py -v` exits 0.
- [ ] `grep -n "or not per_ean" custom_components/engie_be/coordinator.py` returns no matches.
- [ ] `.venv/bin/python -m pytest tests/ -q --cov=custom_components.engie_be --cov-report=term --cov-fail-under=95` passes.
- [ ] No files outside "In scope" modified (`git status`).
- [ ] `plans/README.md` status row for plan 010 flipped to DONE.

## STOP conditions

Stop and report back (do not improvise) if:

- The "Current state" excerpt on `coordinator.py:1236-1270` no longer matches
  the live file (drift).
- After the change, any *existing* test that expected `None` from an empty
  per_ean dict starts failing — that would mean a caller depended on the old
  semantics; investigate and report which test.
- Coverage drops below 95% because the loop's zero-iteration path is not
  otherwise exercised.

## Maintenance notes

- The `_async_apply_has_solar` handler treats `None` as "no signal, preserve
  cached value" and `False` as "flip observed". A future caller that passes a
  post-reload cached wrapper (or a diagnostic replay of an old wrapper) will
  now correctly get `False` instead of a stale `has_solar=True`. Reviewer
  should verify no consumer treats `None` and `False` as interchangeable.
- Docstring already documents the intended semantics; no update needed.
