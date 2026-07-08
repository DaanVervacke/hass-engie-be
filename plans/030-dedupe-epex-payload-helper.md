# Plan 030: Dedupe `_epex_payload` helper across sensor.py and binary_sensor.py

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report - do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 91bf9e6..HEAD -- custom_components/engie_be/sensor.py custom_components/engie_be/binary_sensor.py custom_components/engie_be/_epex.py`
> If any of these changed since this plan was written, re-read the
> "Current state" excerpts against the live files before proceeding.
> On a material mismatch, treat as a STOP condition.

## Status

- **Priority**: P3 (housekeeping)
- **Effort**: S (single-digit LoC delta, 8 caller sites, 0 behavior change)
- **Risk**: LOW
- **Depends on**: -
- **Category**: tech-debt / shrink
- **Planned at**: commit `91bf9e6`, 2026-07-08

## Why this matters

`_epex_payload(coordinator) -> EpexPayload | None` is defined twice, verbatim (modulo a one-word docstring difference), in `sensor.py` and `binary_sensor.py`. Both copies do the same three-line isinstance guard against `coordinator.data`. The helper is a pure function of the coordinator; there is no reason it lives on a platform module. `_epex.py` already exists as the "pure helpers for EPEX" module and imports `EpexPayload` under `TYPE_CHECKING` - the natural home.

The cut is 7 lines and one duplicate maintenance point. Small, but genuinely free: 8 caller sites in `sensor.py`, 3 in `binary_sensor.py`, all identical signature.

## Current state

### `custom_components/engie_be/sensor.py:909-912`

```python
def _epex_payload(coordinator: EngieBeEpexCoordinator) -> EpexPayload | None:
    """Return the cached EPEX payload, or ``None`` if not yet fetched."""
    payload = coordinator.data
    return payload if isinstance(payload, EpexPayload) else None
```

Callers in `sensor.py`: lines 961, 972, 984, 1004, 1039, 1058, 1095 (7 call sites).

### `custom_components/engie_be/binary_sensor.py:220-223`

```python
def _epex_payload(coordinator: EngieBeEpexCoordinator) -> EpexPayload | None:
    """Return the cached EPEX payload, or ``None`` if not yet available."""
    payload = coordinator.data
    return payload if isinstance(payload, EpexPayload) else None
```

Callers in `binary_sensor.py`: lines 295, 306, 325 (3 call sites).

### `custom_components/engie_be/_epex.py`

Already imports `EpexPayload` under `TYPE_CHECKING`. Currently contains only `next_epex_slot_boundary`. This is the destination.

### Repo conventions to preserve

- Module-level helpers in `_epex.py` are named without a leading underscore (`next_epex_slot_boundary` is the existing exemplar) - they are the module's public surface to sibling platforms. Follow that: name the moved helper `epex_payload` (no leading underscore) at its new home. In the platform files, import it and use it directly as `epex_payload(...)`; no local rebinding.
- `_epex.py` uses `from __future__ import annotations` and `TYPE_CHECKING`-guarded imports for `EpexPayload` and `EngieBeEpexCoordinator`-adjacent types. Match that style - do not add runtime imports for types used only in signatures.
- Docstrings in `_epex.py` are terse, one line where a single line does. Match.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Confirm callers | `grep -n "_epex_payload\b\|epex_payload\b" custom_components/engie_be/sensor.py custom_components/engie_be/binary_sensor.py custom_components/engie_be/_epex.py` | All references resolve to the new import + call sites |
| Tests | `.venv/bin/pytest tests/ -q --tb=line` | 873 pass (same count as pre-change) |
| Ruff | `.venv/bin/ruff check custom_components/engie_be/ tests/` | clean |
| Ruff format | `.venv/bin/ruff format --check custom_components/engie_be/ tests/` | clean |

## Scope

**In scope**:

- `custom_components/engie_be/_epex.py` - add the moved helper.
- `custom_components/engie_be/sensor.py` - remove local definition, add import, keep call sites.
- `custom_components/engie_be/binary_sensor.py` - remove local definition, add import, keep call sites.

**Out of scope**:

- Any change to `EpexPayload` shape, `EngieBeEpexCoordinator`, or slot-boundary logic.
- Renaming or restructuring `_slots_for_date` (also a duplicate candidate in principle, but it lives in `sensor.py` only - do NOT touch).
- Test file changes. No test asserts against the private name; call-site behavior is unchanged.
- Docstring rewrites elsewhere.

## Git workflow

- Branch: `advisor/030-dedupe-epex-payload`
- Commit style: `refactor(epex): move _epex_payload to _epex module`
- One commit. The change is atomic.

## Steps

### Step 1: Add `epex_payload` to `_epex.py`

Append below the existing `next_epex_slot_boundary` function:

```python
def epex_payload(coordinator: EngieBeEpexCoordinator) -> EpexPayload | None:
    """Return the cached EPEX payload, or ``None`` if not yet fetched."""
    from .data import EpexPayload  # noqa: PLC0415 - runtime isinstance check

    payload = coordinator.data
    return payload if isinstance(payload, EpexPayload) else None
```

The runtime `EpexPayload` import inside the function keeps `_epex.py`'s module-level import graph dependency-free (matching its current shape - see the module docstring). The `EngieBeEpexCoordinator` reference in the signature is annotation-only under `from __future__ import annotations`, so no runtime import is needed for it; add it to the existing `TYPE_CHECKING` block if not already present.

Check the `TYPE_CHECKING` block in `_epex.py`. It currently imports `datetime` and `EpexPayload`. Add `EngieBeEpexCoordinator` alongside them, sourced from `.coordinator`:

```python
if TYPE_CHECKING:
    from datetime import datetime

    from .coordinator import EngieBeEpexCoordinator
    from .data import EpexPayload
```

**Alternative** (equally acceptable): if adding a `.coordinator` import to `_epex.py` triggers a circular-import concern at type-check time (it should not - `TYPE_CHECKING` blocks do not evaluate at runtime), fall back to typing the parameter as a `Protocol` with a `data: object` attribute, or just leave the annotation as a string forward-reference. Prefer the direct import; only switch if `mypy`/`pyright` complains, which they should not.

**Verify**:

```
.venv/bin/ruff check custom_components/engie_be/_epex.py
.venv/bin/python -c "from custom_components.engie_be import _epex; print(_epex.epex_payload.__doc__)"
```

Expected: ruff clean; docstring prints.

### Step 2: Remove the duplicate from `sensor.py` and switch call sites

Delete `sensor.py:909-912` (the local `_epex_payload` definition).

In the imports section of `sensor.py`, add:

```python
from ._epex import epex_payload
```

Rename every call site in `sensor.py` from `_epex_payload(` to `epex_payload(`. The 7 sites are at lines 961, 972, 984, 1004, 1039, 1058, 1095 (pre-edit line numbers - use your editor's find/replace on the token `_epex_payload` scoped to `sensor.py`).

**Verify**:

```
grep -n "_epex_payload\b" custom_components/engie_be/sensor.py
```

Expected: zero matches.

```
grep -c "epex_payload(" custom_components/engie_be/sensor.py
```

Expected: 7 (the caller count).

### Step 3: Remove the duplicate from `binary_sensor.py` and switch call sites

Same operation:

- Delete `binary_sensor.py:220-223`.
- Add `from ._epex import epex_payload` to the imports section.
- Rename `_epex_payload(` → `epex_payload(` at lines 295, 306, 325.

**Verify**:

```
grep -n "_epex_payload\b" custom_components/engie_be/binary_sensor.py
```

Expected: zero matches.

```
grep -c "epex_payload(" custom_components/engie_be/binary_sensor.py
```

Expected: 3.

### Step 4: Global verification

```
grep -rn "_epex_payload\b" custom_components/engie_be/ tests/
```

Expected: zero matches anywhere. The private-with-leading-underscore name is fully retired.

```
grep -rn "\bepex_payload\b" custom_components/engie_be/
```

Expected: 11 matches total - 1 definition in `_epex.py`, 1 import each in `sensor.py`/`binary_sensor.py`, 7 calls in `sensor.py`, 3 calls in `binary_sensor.py`. (11 = 1 + 2 + 7 + 3, but the two imports count as 2 more `epex_payload` tokens - actual count is 13. Sanity-check by inspecting the grep output rather than the number.)

```
.venv/bin/pytest tests/ -q --tb=line
```

Expected: 873 passed (or whatever the current baseline is - MUST match the pre-change count exactly; behavior is unchanged).

```
.venv/bin/ruff check custom_components/engie_be/ tests/
.venv/bin/ruff format --check custom_components/engie_be/ tests/
```

Expected: clean.

### Step 5: Update `plans/README.md`

Add a row:

```
| 030 | Dedupe `_epex_payload` helper across sensor.py and binary_sensor.py | P3 | S | - | DONE |
```

Under "Execution order & status", after row 029.

## Test plan

No new tests. This is a pure move-and-rename; the existing EPEX sensor and binary-sensor tests exercise every call site. If the pytest count drops or any single test fails, STOP - the refactor has changed behavior.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `_epex.py` defines `epex_payload` (no leading underscore) with the isinstance guard.
- [ ] `grep -rn "_epex_payload\b" custom_components/engie_be/ tests/` returns zero matches.
- [ ] `sensor.py` has exactly 7 calls to `epex_payload(` and no local definition.
- [ ] `binary_sensor.py` has exactly 3 calls to `epex_payload(` and no local definition.
- [ ] `.venv/bin/pytest tests/ -q --tb=line` — pass count unchanged from baseline.
- [ ] `.venv/bin/ruff check custom_components/engie_be/ tests/` — clean.
- [ ] `.venv/bin/ruff format --check custom_components/engie_be/ tests/` — clean.
- [ ] `git diff --stat` shows changes only in `_epex.py`, `sensor.py`, `binary_sensor.py`, and `plans/README.md`.
- [ ] `plans/README.md` status row for plan 030 marked DONE.

## STOP conditions

Stop and report back (do not improvise) if:

- Ruff or mypy flags a circular import between `_epex.py` and `.coordinator`. Fall back to the string forward-reference variant described in Step 1 and report.
- Any test fails after the move. The refactor is not supposed to change behavior; a failure signals either a hidden import-order dependency or a caller you missed.
- `grep` finds additional `_epex_payload` references outside `sensor.py` / `binary_sensor.py` (e.g. in `tests/`, `diagnostics.py`, or a helper not in the current file list). Report the extra locations; do NOT rewrite tests to switch names unless they are asserting on the private name (which they should not be).
- `_epex.py`'s module docstring ("Kept dependency-free...") makes the runtime `EpexPayload` import feel wrong to you. It is fine - `EpexPayload` is a dataclass in `.data` with no downstream dependencies. Proceed; do not restructure `_epex.py`.

## Maintenance notes

- Future EPEX platforms (calendar entities, future automations) should import `epex_payload` from `_epex.py` rather than reintroducing a local copy. This plan retires the duplicate; a code review that lets a third copy land is a regression.
- If `_slots_for_date` grows a second caller in `binary_sensor.py` in the future, apply the same treatment - move to `_epex.py`, drop the leading underscore, import from callers. Not part of this plan.
- The `noqa: PLC0415` on the runtime `EpexPayload` import is defensible: it is the standard pattern for keeping a helper module runtime-lean while still doing an `isinstance` check. Leave it in place.
