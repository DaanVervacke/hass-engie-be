# Plan 023: Extract shared per-EAN sensor `__init__` into a common base

> **Executor instructions**: Follow this plan step by step. Every step
> ends with a verification command. STOP if any behavior test fails.
> Update `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be/sensor.py custom_components/engie_be/binary_sensor.py`

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

Two sensor base classes have byte-identical constructor bodies:

- `_EngieBeSolarSurplusBase` (sensor.py, ~line 1263)
- `_EngieBeTouSlotBase` (sensor.py, ~line 1607)

Both do the same five things in `__init__`:
1. Call `super().__init__(coordinator, subentry)`
2. Store `self.entity_description = entity_description`
3. Store `self._ean = ean`
4. Build `self._attr_unique_id = f"{entry_id}_{subentry_id}_{ean}_{key}"`
5. Build `self.entity_id = f"sensor.engie_belgium_{ban}_{ean}_{key}"`
6. Set `self._attr_translation_placeholders = {"ean": ean}`

TOU adds a sixth step: `self._direction = direction`. Solar has no
direction.

Extracting the common wiring into a `_EngieBePerEanBase` class
eliminates ~30 lines of duplication and gives future per-EAN sensors
(e.g., account balance already isn't per-EAN, but a hypothetical
per-EAN import-status sensor would benefit) a stable base.

## Current state

### Solar base (`sensor.py:1263`)

```python
class _EngieBeSolarSurplusBase(EngieBeEntity, SensorEntity):
    """Common wiring for every per-EAN solar-surplus sensor."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
        ean: str,
    ) -> None:
        """Bind coordinator, subentry, entity description, and EAN."""
        super().__init__(coordinator, subentry)
        self.entity_description = entity_description
        self._ean = ean
        self._slots_cache: tuple[int, list[dict[str, Any]]] | None = None
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{subentry.subentry_id}_{ean}_{entity_description.key}"
        )
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = (
                f"sensor.engie_belgium_{ban}_{ean}_{entity_description.key}"
            )
        self._attr_translation_placeholders = {"ean": ean}

    # ... _forecasts_for_ean, _cached_flat_slots, etc.
```

**Note**: `_slots_cache` is solar-specific (added by plan 011 for perf).
It must stay in the solar subclass, not move into the shared base.

### TOU base (`sensor.py:1607`)

Identical modulo `_direction` and the trailing helper methods
(`_tou_item`, `_supplier_schedule`, `_dgo_schedule`, `_next_boundary`).

Note the MRO: `_BoundaryScheduleMixin` is first, `EngieBeEntity`
second, `SensorEntity` third. The shared base must preserve this MRO
requirement for subclasses that use boundary scheduling.

### Binary sensor TOU is-optimal base

`binary_sensor.py::EngieBeTouIsOptimalSensor` has a similar
`__init__` shape. Include it in the refactor if it's cheap; skip if
the MRO differs from the sensor path.

Grep to confirm:
```bash
grep -B1 -A15 "class EngieBeTouIsOptimalSensor" custom_components/engie_be/binary_sensor.py | head -30
```

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Sensor tests | `.venv/bin/pytest tests/test_sensor_solar_surplus.py tests/test_sensor_tou.py tests/test_binary_sensor_tou.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass, ≥95% |

## Scope

**In scope**:
- `custom_components/engie_be/sensor.py` — add `_EngieBePerEanBase`,
  refactor `_EngieBeSolarSurplusBase` and `_EngieBeTouSlotBase` to
  inherit from it.
- Optionally: `custom_components/engie_be/binary_sensor.py` — extend
  `EngieBeTouIsOptimalSensor` to use the same base **only if MRO is
  clean**.

**Out of scope**:
- `_slots_cache` optimization (plan 011). Stays in the solar subclass.
- Feature-specific helper methods (`_forecasts_for_ean`,
  `_tou_item`, etc.). Stay in their respective subclasses.
- EPEX and Happy Hours sensor classes — they don't fit the per-EAN
  pattern and are out of scope.

## Steps

### Step 1: Add `_EngieBePerEanBase` in sensor.py

Insert immediately above `_EngieBeSolarSurplusBase` (around line 1250):

```python
class _EngieBePerEanBase(EngieBeEntity, SensorEntity):
    """
    Shared per-EAN wiring: unique_id, entity_id, translation placeholders.

    Subclasses store per-feature payloads / helpers but let this base
    handle the EAN-scoped identifier and slug construction.
    """

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
        ean: str,
    ) -> None:
        """Bind coordinator, subentry, entity description, and EAN."""
        super().__init__(coordinator, subentry)
        self.entity_description = entity_description
        self._ean = ean
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{subentry.subentry_id}_{ean}_{entity_description.key}"
        )
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = (
                f"sensor.engie_belgium_{ban}_{ean}_{entity_description.key}"
            )
        self._attr_translation_placeholders = {"ean": ean}
```

### Step 2: Refactor `_EngieBeSolarSurplusBase`

Change the class declaration to inherit from `_EngieBePerEanBase`:

```python
class _EngieBeSolarSurplusBase(_EngieBePerEanBase):
    """Common wiring for every per-EAN solar-surplus sensor."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
        ean: str,
    ) -> None:
        """Bind coordinator, subentry, entity description, and EAN."""
        super().__init__(coordinator, subentry, entity_description, ean)
        self._slots_cache: tuple[int, list[dict[str, Any]]] | None = None

    # ... keep _forecasts_for_ean, _cached_flat_slots verbatim
```

**Note the special unique_id for `EngieBeSolarSurplusSensor`**: plan 012
kept its legacy `_solar_surplus` suffix (no `_forecast`). Verify by
grepping:

```bash
grep -n "_solar_surplus\b" custom_components/engie_be/sensor.py | head -10
```

If `EngieBeSolarSurplusSensor.__init__` overrides `_attr_unique_id`
after calling super's `__init__`, that must be preserved.

### Step 3: Refactor `_EngieBeTouSlotBase`

```python
class _EngieBeTouSlotBase(_BoundaryScheduleMixin, _EngieBePerEanBase):
    """Per-EAN, per-direction current TOU slot with boundary scheduling."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
        ean: str,
        direction: str,
    ) -> None:
        """Bind coordinator, subentry, entity description, EAN, and direction."""
        super().__init__(coordinator, subentry, entity_description, ean)
        self._direction = direction

    # ... keep _tou_item, _supplier_schedule, _dgo_schedule, _next_boundary verbatim
```

The class hierarchy is now:
`_BoundaryScheduleMixin > _EngieBePerEanBase > EngieBeEntity, SensorEntity`

Confirm the MRO with a quick script:

```bash
.venv/bin/python -c "from custom_components.engie_be.sensor import _EngieBeTouSlotBase; print([c.__name__ for c in _EngieBeTouSlotBase.__mro__])"
```

Expected: `_BoundaryScheduleMixin` appears before `_EngieBePerEanBase`
which appears before `EngieBeEntity`, `SensorEntity`, `CoordinatorEntity`.

### Step 4: Optional — binary_sensor TOU class

If `EngieBeTouIsOptimalSensor.__init__` has the same 6-line
unique_id/entity_id/translation-placeholder body, extract it too.

The MRO must be: `_BoundaryScheduleMixin > EngieBeEntity > BinarySensorEntity`.
A `_EngieBePerEanBinarySensorBase` counterpart may be needed (different
base entity type). If the extraction requires more complexity than it
saves, **skip it and note it in the plan-completion report** — leave
`binary_sensor.py` for a follow-up.

### Step 5: Full gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/pytest tests/test_sensor_solar_surplus.py tests/test_sensor_solar_surplus_schedulers.py tests/test_sensor_tou.py tests/test_binary_sensor_tou.py -v` → all pass
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

No new tests. This is a refactor — every existing sensor/binary-sensor
test must continue to pass without modification.

The `sensor.entity_id` and `sensor.unique_id` assertions in the sensor
tests are the critical invariants; if they still pass, the extraction
preserved behavior. Watch particularly for
`test_entity_id_carries_ban_and_ean` (in
`tests/test_sensor_solar_surplus.py`) and its TOU counterpart.

## Done criteria

- [ ] `grep -c "class _EngieBePerEanBase\b" custom_components/engie_be/sensor.py` returns 1.
- [ ] `grep -c "class _EngieBeSolarSurplusBase(_EngieBePerEanBase)" custom_components/engie_be/sensor.py` returns 1.
- [ ] `grep -c "class _EngieBeTouSlotBase(_BoundaryScheduleMixin, _EngieBePerEanBase)" custom_components/engie_be/sensor.py` returns 1.
- [ ] `wc -l custom_components/engie_be/sensor.py` reports fewer lines than before.
- [ ] Existing sensor tests all pass without modification.
- [ ] `.venv/bin/python -c "from custom_components.engie_be.sensor import _EngieBeTouSlotBase; print(_EngieBeTouSlotBase.__mro__[:4])"` shows `_BoundaryScheduleMixin` before `_EngieBePerEanBase`.
- [ ] `.venv/bin/pytest tests/ -q --cov-fail-under=95` passes.
- [ ] `plans/README.md` status row for 023 flipped to DONE.

## STOP conditions

- `EngieBeSolarSurplusSensor.__init__` overrides `_attr_unique_id`
  after `super().__init__()` and the override still contains the raw
  string `f"..._solar_surplus"` — DO NOT let the extracted base
  overwrite this override. Order matters: base sets the default; the
  subclass overrides. If the override is instead a class attribute or
  gets erased, entity IDs will change and installed users will see
  duplicated entities on upgrade.
- The MRO fails a sanity check — e.g., `_BoundaryScheduleMixin`
  hooks (`async_added_to_hass`) don't fire on a TOU sensor. Verify
  with `test_sensor_solar_surplus_schedulers.py` running to prove
  boundary scheduling still works.
- Binary-sensor extraction turns out messier than expected — skip
  Step 4, report the skip, plan-020-style follow-up.

## Maintenance notes

- Any future per-EAN sensor (e.g., a hypothetical
  "per-EAN import-status") should inherit from `_EngieBePerEanBase`
  directly. Add a note to the class docstring pointing new
  contributors at this pattern.
- The `_slots_cache` field stayed on `_EngieBeSolarSurplusBase` on
  purpose (plan 011 memoization). If TOU ever adds a similar cache,
  do NOT lift it to the shared base — cache invalidation semantics
  differ per feature.
- Reviewer should scrutinize: `entity_id` stability. HA does not
  auto-migrate entity IDs if the slug changes. Anything that alters
  the entity_id string during this refactor breaks user dashboards.
