# Plan 009: Type `_BoundaryScheduleMixin` and drop its 7 `# type: ignore` markers

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report — do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat f7d7fec..HEAD -- custom_components/engie_be/entity.py`
> If the file changed since this plan was written, compare the "Current
> state" excerpts below against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M (typing-only refactor + one small test)
- **Risk**: LOW (no runtime behavior change)
- **Depends on**: none
- **Category**: tech-debt / IQS Platinum (`strict-typing`)
- **Planned at**: commit `f7d7fec`, 2026-07-08

## Why this matters

`_BoundaryScheduleMixin` (`custom_components/engie_be/entity.py:27-188`) is a pure mixin — at runtime it inherits from `object`. Type-checkers therefore have no idea that `self.hass`, `self.async_on_remove`, `self.async_write_ha_state`, `self.async_added_to_hass`, `self._handle_coordinator_update`, and `self.is_on` exist. Every call to those attributes is silenced with `# type: ignore[misc]` / `# type: ignore[attr-defined]`. There are **seven** such markers (lines 96, 97, 112, 140, 168, 187 — line 187 has two logical accesses guarded by `hasattr`; only one carries the marker).

Consequences:
- The mixin's contract (which cooperating attributes must exist) is undocumented in the type system, so a future subclass ordering bug can only be caught at runtime.
- The integration self-declares `strict-typing: todo` in `custom_components/engie_be/quality_scale.yaml:67`. This is one of two remaining Platinum blockers.
- The lazy fix (bulk `# type: ignore`) has already been paid; removing the markers now costs less than they cost to keep.

The clean fix is to give the mixin a *type-checking-only* view of `CoordinatorEntity`. At runtime the mixin remains inheriting from `object` (unchanged MRO, unchanged behavior). Under `TYPE_CHECKING`, it appears to inherit from `CoordinatorEntity[Any]`, so the checker resolves `hass`, `async_on_remove`, `async_write_ha_state`, `async_added_to_hass`, and `_handle_coordinator_update`. The `is_on` access is guarded by `hasattr` and comes from `BinarySensorEntity`, not `CoordinatorEntity`; a `cast` handles it without an ignore.

## Current state

### File

- `custom_components/engie_be/entity.py`

### Ignore-marker sites (verified via `grep -n "type: ignore" custom_components/engie_be/entity.py`)

```
96:        await super().async_added_to_hass()  # type: ignore[misc]
97:        self.async_on_remove(self._cancel_boundary)  # type: ignore[attr-defined]
112:        super()._handle_coordinator_update()  # type: ignore[misc]
140:            self.hass,  # type: ignore[attr-defined]
168:        self.async_write_ha_state()  # type: ignore[attr-defined]
187:            return self.is_on  # type: ignore[attr-defined]
```

Six markers on six distinct call sites (the file has seven total `# type: ignore` occurrences; two are on the same construct at lines 96–97).

### Mixin declaration excerpt (`entity.py:27-30`)

```python
class _BoundaryScheduleMixin:
    """
    Mixin that re-evaluates entity state at the next "boundary" instant.
    ...
```

### Callers (verified via `grep -n "_BoundaryScheduleMixin" custom_components/engie_be/ tests/`)

- `binary_sensor.py:217` — `class EngieBeHappyHourActiveSensor(_BoundaryScheduleMixin, EngieBeEntity, BinarySensorEntity)`
- `binary_sensor.py:322` — `class EngieBeEpexNegativeSensor(_BoundaryScheduleMixin, EngieBeEpexEntity, BinarySensorEntity)`
- `sensor.py:884` — `class _EngieBeEpexSensorBase(_BoundaryScheduleMixin, EngieBeEpexEntity, SensorEntity)`

Every real caller pairs the mixin with a `CoordinatorEntity` subclass (via `EngieBeEntity` or `EngieBeEpexEntity`), which is already the *runtime* MRO requirement documented in the mixin's own docstring (lines 46-50). Formalizing this in the type system is exactly what this plan does.

### Existing imports at top of `entity.py:1-24`

```python
"""Base entities for the ENGIE Belgium integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import mask_identifier
from .const import ATTRIBUTION, DOMAIN, LOGGER
from .coordinator import EngieBeDataUpdateCoordinator, EngieBeEpexCoordinator

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.config_entries import ConfigSubentry

    from .data import EngieBeConfigEntry
```

`CoordinatorEntity` is already imported unconditionally. `Any` is not imported yet.

## Target state

### Change 1 — Add the `TYPE_CHECKING`-only mixin base

In `entity.py`, add `Any` to the `typing` import and introduce a `_MIXIN_BASE` alias that resolves to `CoordinatorEntity[Any]` under type-checkers and `object` at runtime:

```python
from typing import TYPE_CHECKING, Any
```

Then, immediately after the existing `if TYPE_CHECKING:` block (around line 24), append:

```python
if TYPE_CHECKING:
    _MixinBase = CoordinatorEntity[Any]
else:
    _MixinBase = object
```

Keep `_MixinBase` module-private (underscore prefix); it must not appear in `__all__` (there is no `__all__` in this file — do not add one).

### Change 2 — Inherit the mixin from `_MixinBase`

```python
class _BoundaryScheduleMixin(_MixinBase):
    """
    Mixin that re-evaluates entity state at the next "boundary" instant.
    ...
```

Leave the docstring content unchanged. The "MRO requirement" paragraph (lines 46-50) remains correct: the mixin still must come **before** the coordinator entity base in the concrete subclass's bases so cooperative `super()` chaining works — the `TYPE_CHECKING` shim doesn't change that runtime requirement, it just teaches mypy about it.

### Change 3 — Drop five ignore markers on inherited attributes

Delete the `# type: ignore[...]` comment from lines 96, 97, 112, 140, 168. The bare statements now type-check because `CoordinatorEntity` provides `async_added_to_hass`, `async_on_remove`, `_handle_coordinator_update`, `hass`, and `async_write_ha_state`.

Post-change excerpt (`entity.py:94-98`):
```python
    async def async_added_to_hass(self) -> None:
        """Arm the next-boundary timer when the entity joins HA."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_boundary)
        self._schedule_next_boundary()
```

Post-change excerpt (`entity.py:110-112`):
```python
        self._cancel_boundary()
        self._schedule_next_boundary()
        super()._handle_coordinator_update()
```

Post-change excerpt (`entity.py:139-143`):
```python
        self._unsub_boundary = async_track_point_in_utc_time(
            self.hass,
            self._boundary_fired,
            target,
        )
```

Post-change excerpt (`entity.py:168`):
```python
        self.async_write_ha_state()
```

### Change 4 — Replace the `is_on` marker with a `cast`

Line 187 is inside `_boundary_state_for_log`, which reads `self.is_on` behind a `hasattr(self, "is_on")` guard. `is_on` is defined on `BinarySensorEntity`, not `CoordinatorEntity`, so the ignore cannot be dropped without either widening the mixin base (bad — would need `BinarySensorEntity`, which is a platform-specific import) or narrowing the access via `cast(Any, self)`.

Add `from typing import cast` (append `cast` to the existing `typing` import — that import already includes `TYPE_CHECKING` and, after Change 1, `Any`).

Replace lines 186-188:

```python
        if hasattr(self, "is_on"):
            return self.is_on  # type: ignore[attr-defined]
        return getattr(self, "native_value", None)
```

with:

```python
        if hasattr(self, "is_on"):
            return cast("Any", self).is_on
        return getattr(self, "native_value", None)
```

The `cast` keeps the runtime `hasattr` guard authoritative and simply tells the checker "trust me, the guard proved it exists." No behavior change.

## Steps (ordered)

1. **Baseline verification** — Confirm the current markers and test suite pass on your branch.
   - `grep -c "type: ignore" custom_components/engie_be/entity.py` → **6**
   - `.venv/bin/pytest tests/test_boundary_logging.py tests/test_binary_sensor_happy_hour.py tests/test_binary_sensor_epex.py tests/test_sensor_epex_schedulers.py -q` → all pass
   - If either fails, STOP — the drift check has failed. Report and do not proceed.

2. **Apply Changes 1–4** in `custom_components/engie_be/entity.py` as specified above. Do not touch any other file yet. Do not reorder existing methods. Do not "clean up" adjacent unrelated code.

3. **Local lint** — Run the repo's linter.
   - `.venv/bin/ruff format custom_components/engie_be/entity.py`
   - `.venv/bin/ruff check custom_components/engie_be/entity.py`
   - Expected: no output (clean pass). If ruff complains about the `_MixinBase = object` / `= CoordinatorEntity[Any]` conditional (unused `Any`, etc.), do NOT add `# noqa` — instead re-read this plan; the imports were specified precisely.

4. **Verify markers dropped** — Confirm six of the seven markers are gone.
   - `grep -c "type: ignore" custom_components/engie_be/entity.py` → **0**
   - Expected value is zero because all seven original markers were removed (five on inherited `CoordinatorEntity` attrs, plus the `is_on` marker replaced by `cast`). If any survive, list them and STOP.

5. **Run the boundary-mixin tests** — These are the runtime-behavior regression net.
   - `.venv/bin/pytest tests/test_boundary_logging.py tests/test_binary_sensor_happy_hour.py tests/test_binary_sensor_epex.py tests/test_binary_sensor_epex_negative_scheduler.py tests/test_sensor_epex_schedulers.py -q`
   - Expected: all pass. If any fail, revert Change 2 first (the mixin base) and re-run; a failure there would indicate the `TYPE_CHECKING` shim leaked into runtime (which shouldn't happen — `TYPE_CHECKING` is `False` at runtime).

6. **Add a smoke test for the mixin contract** — See "Test plan" below. Create the new test file, then:
   - `.venv/bin/pytest tests/test_boundary_mixin_typing.py -q`
   - Expected: 1 passed.

7. **Full suite + coverage gate**:
   - `.venv/bin/python -m pytest tests/ -v --tb=short --cov=custom_components.engie_be --cov-report=term-missing --cov-fail-under=95`
   - Expected: full suite passes, coverage ≥ 95%.

8. **Manifest / changelog** — This is a code refactor with no user-visible change, so DO NOT bump `manifest.json` `version`. Add a **Changed** entry to `CHANGELOG.md` under `## [Unreleased]`:
   - `- Type _BoundaryScheduleMixin against CoordinatorEntity to drop seven type-ignore markers.`
   - Use hyphens, not em-dashes. No `Co-Authored-By`, no Claude attribution.

9. **Update `plans/README.md`** — Add a row for plan 009 with status `DONE` and set the status column accordingly. Preserve monotonic numbering.

## Files in scope

- `custom_components/engie_be/entity.py` (modify)
- `tests/test_boundary_mixin_typing.py` (create, one test — see below)
- `CHANGELOG.md` (add one line under `## [Unreleased]` → `### Changed`)
- `plans/README.md` (add status row)

## Files explicitly OUT OF SCOPE

- `custom_components/engie_be/binary_sensor.py`, `sensor.py` — concrete subclasses. Their class declarations already have the correct MRO. Do not "improve" them.
- `custom_components/engie_be/coordinator.py` — provides `EngieBeDataUpdateCoordinator` / `EngieBeEpexCoordinator`; unrelated.
- `custom_components/engie_be/quality_scale.yaml` — do NOT flip `strict-typing: todo` to `done`. Removing markers in one file is a step toward the Platinum rule, but the rule requires the whole codebase to pass a strict typecheck. That gate does not exist in CI yet. A separate future plan will flip it.
- `custom_components/engie_be/manifest.json` — do NOT bump `version`. No user-visible change.
- Adding `mypy` or `basedpyright` to CI — explicitly deferred. See "Follow-up work" at the bottom.
- Any other `# type: ignore` in the codebase (there are several in `api.py`, etc.). This plan is *only* about the seven in `entity.py`.

## Test plan

The existing suite (`tests/test_boundary_logging.py`, `test_binary_sensor_happy_hour.py`, `test_binary_sensor_epex_negative_scheduler.py`, `test_sensor_epex_schedulers.py`) already exercises the runtime behavior of every method whose ignore marker was removed. Those tests are the primary regression net; if they pass, runtime behavior is preserved.

Add one **new** small file, `tests/test_boundary_mixin_typing.py`, that pins the mixin's structural contract so a future MRO break fails fast:

```python
"""
Regression guard for `_BoundaryScheduleMixin`'s type-checker contract.

`_BoundaryScheduleMixin` inherits from `CoordinatorEntity[Any]` under
`TYPE_CHECKING` only. At runtime it inherits from `object`. This test
locks both facts in so a future edit that switches the runtime base
(and quietly changes MRO for every concrete subclass) fails here first,
before it fails in production as a leaked timer or a missed state write.
"""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.engie_be.binary_sensor import (
    EngieBeEpexNegativeSensor,
    EngieBeHappyHourActiveSensor,
)
from custom_components.engie_be.entity import _BoundaryScheduleMixin


def test_mixin_runtime_base_is_object() -> None:
    """
    Mixin must inherit from `object` at runtime so its MRO composes
    cooperatively with the concrete entity's `CoordinatorEntity` base.
    A stray runtime inheritance from `CoordinatorEntity` would collapse
    the MRO and can silently break `super()` chaining in
    `async_added_to_hass` / `_handle_coordinator_update`.
    """
    assert _BoundaryScheduleMixin.__bases__ == (object,)


def test_concrete_subclasses_place_mixin_before_coordinator_base() -> None:
    """
    Every concrete subclass must list `_BoundaryScheduleMixin` before
    its `CoordinatorEntity` subclass so cooperative `super()` reaches
    the coordinator base. This is the MRO invariant documented in the
    mixin's docstring; without it, boundary timers do not re-arm.
    """
    for cls in (EngieBeHappyHourActiveSensor, EngieBeEpexNegativeSensor):
        mro = cls.__mro__
        mixin_idx = mro.index(_BoundaryScheduleMixin)
        coord_idx = next(
            i
            for i, base in enumerate(mro)
            if i > 0 and issubclass(base, CoordinatorEntity)
        )
        assert mixin_idx < coord_idx, (
            f"{cls.__name__}: _BoundaryScheduleMixin must precede "
            f"CoordinatorEntity in MRO (got mixin@{mixin_idx}, "
            f"coordinator@{coord_idx})"
        )
```

Two tests, no HA fixtures needed (both are pure introspection). Coverage impact: neutral — the mixin's methods are already covered by the boundary-behavior tests; these new tests pin metadata, not behavior.

## Done criteria (machine-checkable)

Run each command; the expected result MUST match:

| Command | Expected |
|---------|----------|
| `grep -c "type: ignore" custom_components/engie_be/entity.py` | `0` |
| `.venv/bin/ruff check custom_components/engie_be/entity.py` | (no output, exit 0) |
| `.venv/bin/ruff format --check custom_components/engie_be/entity.py` | (no output, exit 0) |
| `.venv/bin/pytest tests/test_boundary_mixin_typing.py -q` | `2 passed` |
| `.venv/bin/python -m pytest tests/ --cov=custom_components.engie_be --cov-fail-under=95 -q` | full suite passes, coverage line ≥ 95% |
| `grep -c "_MixinBase" custom_components/engie_be/entity.py` | `3` (one definition alias, one runtime branch, one class base) |

## STOP conditions

Stop and report to the human reviewer if any of the following happens; do NOT try to work around them:

- The drift check (Step 1) shows `entity.py` has moved on since commit `f7d7fec` in a way that changes the mixin, `EngieBeEntity`, or `EngieBeEpexEntity`. Recompare the excerpts before continuing.
- After Change 2, any of the existing boundary tests fail. Something about the runtime MRO has become sensitive to the shim (it shouldn't be). Revert and report.
- `ruff` reports errors about the conditional `_MixinBase` binding that a plain `# noqa` would silence. Adding `# noqa` here would be scope creep and hide a real ruff-config decision the maintainer needs to make.
- Coverage drops below 95%. This plan should be coverage-neutral; a drop means an ignore-guarded branch was actually reachable at runtime and needs a test, not a marker.

## Maintenance notes

- The `if TYPE_CHECKING: _MixinBase = CoordinatorEntity[Any] else: _MixinBase = object` idiom is the same pattern Home Assistant core uses in a handful of its own mixins. If a reviewer asks "why not just inherit `CoordinatorEntity` directly," the answer is: at runtime the concrete class already inherits `CoordinatorEntity` via `EngieBeEntity`/`EngieBeEpexEntity`, and adding a second inheritance path to the mixin changes MRO in ways that Python's C3 linearization handles today but that could break under a future `CoordinatorEntity` re-parenting in HA core.
- If a new time-boundary entity is added, its class MUST list `_BoundaryScheduleMixin` first among its bases. The `test_concrete_subclasses_place_mixin_before_coordinator_base` test doesn't automatically pick it up; add the new class to the tuple in that test.
- The `cast(Any, self).is_on` at `_boundary_state_for_log` is safe because it lives behind `hasattr(self, "is_on")`. If a future refactor removes that guard, replace the cast with a proper protocol or fold the log helper into the `BinarySensorEntity`-facing subclass; do not delete the guard alone.

## Follow-up work (not part of this plan)

Once this plan is merged and stable, the natural next step for the `strict-typing` Platinum rule is to:

1. Add `basedpyright` or `mypy --strict` to `.github/workflows/lint.yml` as a required check.
2. Address the remaining `# type: ignore` occurrences across `api.py`, `__init__.py`, etc. (grep for the full list before scoping — a partial gate is worse than none).
3. Flip `custom_components/engie_be/quality_scale.yaml:67` `strict-typing: todo` → `done`.

That work is deliberately separated because (1) the CI gate choice (basedpyright vs mypy) is a maintainer decision, and (2) the remaining ignores may take longer to resolve than this plan and shouldn't block landing this one.
