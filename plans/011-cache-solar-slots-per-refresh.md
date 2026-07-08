# Plan 011: Compute solar-surplus slot arrays once per coordinator refresh, not per property read

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 6753af3..HEAD -- custom_components/engie_be/sensor.py custom_components/engie_be/coordinator.py`
> The v0.13.0b0 Solar Surplus feature is uncommitted at the "Planned at" SHA;
> the diff will show many changed lines. Confirm the "Current state" excerpts
> below match the live file before proceeding.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (independent of 010, but land 010 first if both this
  session for a cleaner review pipeline)
- **Category**: perf
- **Planned at**: commit `6753af3`, 2026-07-08

## Why this matters

The five solar-surplus sensors per electricity EAN each read from
`coordinator.data["solar_surplus"]["data"][ean]` on every `native_value` or
`extra_state_attributes` access. Under the hood, three helpers rebuild the
same flattened structure on each call:

- `_solar_slots(forecasts)` — flattens `[day → details]` to a single list
- `_solar_slots_for_local_date(slots, target_date)` — filters that list
- `_solar_next_hour_boundary(slots, now)` — scans it again for the boundary

Every sensor property does one to three of these passes. With 5 sensors per
EAN and a ~72-slot 3-day forecast, a Lovelace card polling the entity states
retriggers the full flatten/filter chain up to ~15 times per state read. It's
not catastrophic (all pure-Python list traversal), but it is wasted work on a
data set that only changes on a coordinator refresh (default 60min).

## Current state

### Files

- `custom_components/engie_be/sensor.py` — contains the helpers
  (`_parse_solar_slot_start`, `_solar_slots`, `_solar_slot_covering`,
  `_solar_slots_for_local_date`, `_solar_next_hour_boundary`) at lines
  1180-1246, and the five sensor classes that call them.
- `tests/test_sensor_solar_surplus.py` — 24 tests; the helper-heavy pattern
  makes these already-good regression coverage.

### Current helpers (lines 1191-1246 in `custom_components/engie_be/sensor.py`)

```python
def _solar_slots(forecasts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten a per-EAN forecasts list into a list of hourly slot dicts."""
    flat: list[dict[str, Any]] = []
    for day in forecasts:
        if not isinstance(day, dict):
            continue
        details = day.get("details")
        if not isinstance(details, list):
            continue
        for slot in details:
            if not isinstance(slot, dict):
                continue
            flat.append(slot)
    return flat


def _solar_slot_covering(
    slots: list[dict[str, Any]], instant: datetime
) -> dict[str, Any] | None:
    ...


def _solar_slots_for_local_date(
    slots: list[dict[str, Any]], target_date: date
) -> list[dict[str, Any]]:
    ...


def _solar_next_hour_boundary(
    slots: list[dict[str, Any]], now: datetime
) -> datetime | None:
    ...
```

### Sensor property call sites

All five sensor classes derive from `_EngieBeSolarSurplusBase` at
`sensor.py:1249`; the base has `_forecasts_for_ean` which reads the raw
per-EAN forecasts. Downstream property accessors then re-call `_solar_slots`
each time:

- `EngieBeSolarSurplusSensor.extra_state_attributes` (line ~1362) calls
  `_solar_slots(forecasts)`.
- `EngieBeSolarSurplusCurrentSensor.native_value` calls
  `_solar_slot_covering(_solar_slots(forecasts), dt_util.utcnow())`.
- `EngieBeSolarSurplusNextHourSensor.native_value` calls
  `_solar_slot_covering(_solar_slots(forecasts), ...)`.
- `EngieBeSolarSurplusTodayTotalSensor.native_value` calls
  `_solar_slots_for_local_date(_solar_slots(forecasts), today)`.
- `EngieBeSolarSurplusTodayPeakSensor._today_slots_with_values` calls
  `_solar_slots_for_local_date(_solar_slots(forecasts), today)`.
- `_EngieBeSolarSurplusHourlySensorBase._next_boundary` calls
  `_solar_next_hour_boundary(_solar_slots(forecasts), now)`.

### Repo conventions

- Helpers are module-level pure functions with concise docstrings, matching
  `_solar_slots` and `_solar_slot_covering`.
- Sensor instance state is set only in `__init__` — the existing entities do
  not carry per-refresh cached derivatives, so adding one requires reading
  the coordinator on each property (which is what happens anyway).
- `PARALLEL_UPDATES = 0` at the top of `sensor.py`; entity properties are
  read synchronously by HA — no threading concerns.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Tests (solar) | `.venv/bin/pytest tests/test_sensor_solar_surplus.py tests/test_sensor_solar_surplus_schedulers.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov=custom_components.engie_be --cov-report=term --cov-fail-under=95` | pass, ≥95% |

## Scope

**In scope**:
- `custom_components/engie_be/sensor.py` — helpers and `_EngieBeSolarSurplusBase`.
- `tests/test_sensor_solar_surplus.py` — one new test verifying the
  memoization actually caches (call twice, expect identical object).

**Out of scope**:
- `_parse_solar_slot_start` — leave as is.
- Coordinator storage shape — do not touch `coordinator.data["solar_surplus"]`
  or its keys.
- `energy.py` — its own walker is a separate hot path handled by plan 003
  ordering (not part of this change).

## Git workflow

- Branch: `advisor/011-cache-solar-slots-per-refresh`.
- Commit style: `perf(sensor): cache flattened solar-surplus slots on the entity`.

## Steps

### Step 1: Add a lightweight cache on `_EngieBeSolarSurplusBase`

Locate the base class (currently at `sensor.py:1249`). The plan: add an
instance-level cache keyed by the identity of the coordinator's forecasts
list. Reading is cheap; the cache invalidates when `coordinator.data` gets
replaced (which happens on every refresh — the coordinator constructs a new
`data` dict each cycle).

Add these to `_EngieBeSolarSurplusBase`:

```python
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
        # Cache: (forecasts_id, flat_slots). Invalidates automatically on
        # coordinator refresh because ``_async_update_data`` publishes a
        # fresh ``data`` dict on every refresh, giving the ``forecasts``
        # list a new object identity.
        self._slots_cache: tuple[int, list[dict[str, Any]]] | None = None
        ...  # (existing unique_id / entity_id / translation setup stays)

    def _cached_flat_slots(self) -> list[dict[str, Any]]:
        """Return the flat slot list for this EAN, memoized per refresh."""
        forecasts = self._forecasts_for_ean()
        if not forecasts:
            self._slots_cache = None
            return []
        key = id(forecasts)
        if self._slots_cache is not None and self._slots_cache[0] == key:
            return self._slots_cache[1]
        flat = _solar_slots(forecasts)
        self._slots_cache = (key, flat)
        return flat
```

### Step 2: Redirect the five sensor properties to `_cached_flat_slots`

For each of the five sensor classes, replace calls of the form
`_solar_slots(self._forecasts_for_ean())` (and equivalents like
`_solar_slots(forecasts)` where `forecasts = self._forecasts_for_ean()`)
with `self._cached_flat_slots()`.

Concrete replacements (search-and-verify):

- **`EngieBeSolarSurplusSensor.extra_state_attributes`** — replace the loop
  over `_solar_slots(forecasts)` with iteration over
  `self._cached_flat_slots()`.
- **`EngieBeSolarSurplusCurrentSensor.native_value`** — replace
  `slot = _solar_slot_covering(_solar_slots(forecasts), dt_util.utcnow())`
  with `slot = _solar_slot_covering(self._cached_flat_slots(), dt_util.utcnow())`.
- **`EngieBeSolarSurplusNextHourSensor.native_value`** — analogous replacement
  with `dt_util.utcnow() + timedelta(hours=1)`.
- **`EngieBeSolarSurplusTodayTotalSensor.native_value`** — replace
  `_solar_slots_for_local_date(_solar_slots(forecasts), today)` with
  `_solar_slots_for_local_date(self._cached_flat_slots(), today)`.
- **`EngieBeSolarSurplusTodayPeakSensor._today_slots_with_values`** — same
  replacement pattern.
- **`_EngieBeSolarSurplusHourlySensorBase._next_boundary`** — replace
  `_solar_next_hour_boundary(_solar_slots(forecasts), dt_util.utcnow())`
  with `_solar_next_hour_boundary(self._cached_flat_slots(), dt_util.utcnow())`.

After all replacements, `_solar_slots` should be called exactly ONCE per
class — from `_cached_flat_slots`.

**Verify**: `grep -n "_solar_slots(" custom_components/engie_be/sensor.py` →
one match at the definition site plus one match inside `_cached_flat_slots`;
no other call sites remain.

### Step 3: Add a memoization test

Append to `tests/test_sensor_solar_surplus.py`:

```python
def test_cached_flat_slots_memoizes_within_refresh_cycle() -> None:
    """Repeated property reads return the same flat-slot list object."""
    coord = _make_coordinator(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    sensor = EngieBeSolarSurplusCurrentSensor(coord, _make_subentry(), _EAN)
    a = sensor._cached_flat_slots()
    b = sensor._cached_flat_slots()
    assert a is b


def test_cached_flat_slots_invalidates_when_data_swapped() -> None:
    """A new coordinator.data dict yields a fresh flat-slot list."""
    coord = _make_coordinator(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    sensor = EngieBeSolarSurplusCurrentSensor(coord, _make_subentry(), _EAN)
    first = sensor._cached_flat_slots()
    coord.data = _wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]})
    second = sensor._cached_flat_slots()
    assert first is not second
```

**Verify**: `.venv/bin/pytest tests/test_sensor_solar_surplus.py -v` → all
pass including the two new ones.

### Step 4: Full gate

**Verify**:
- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

- Two new tests in `tests/test_sensor_solar_surplus.py`:
  - `test_cached_flat_slots_memoizes_within_refresh_cycle` — asserts identity
    across two calls.
  - `test_cached_flat_slots_invalidates_when_data_swapped` — asserts identity
    breaks when a new coordinator.data dict is set (simulating a refresh).
- Model after: `test_extra_state_attributes_flattens_all_days` in the same
  file — same coordinator/sensor construction pattern.

## Done criteria

- [ ] `grep -c "_solar_slots(" custom_components/engie_be/sensor.py` returns 2 (definition + single call inside `_cached_flat_slots`).
- [ ] All 5 sensor classes call `self._cached_flat_slots()` at each read site.
- [ ] `.venv/bin/pytest tests/ -q --cov-fail-under=95` passes.
- [ ] `.venv/bin/ruff check custom_components tests` exits 0.
- [ ] No files outside "In scope" modified.
- [ ] `plans/README.md` status row flipped to DONE.

## STOP conditions

- Current-state excerpts don't match the live file — drift.
- Any existing scheduler test in `test_sensor_solar_surplus_schedulers.py`
  fails after the cache is added — the `_next_boundary` re-arm logic reads
  the cache via `_forecasts_for_ean` → `_cached_flat_slots`; a stale cache
  across `_handle_coordinator_update` would show up here.
- Coverage drops below 95%.

## Maintenance notes

- The cache is invalidated by object identity (`id(forecasts)`). This works
  because `_async_update_data` returns a **new** `data` dict on every
  successful refresh, so `coordinator.data["solar_surplus"]["data"][ean]`
  is a fresh list object each time.
- If a future refactor mutates the forecasts list in place (e.g. merging
  fetch results into the previous wrapper), the cache will serve stale
  data. In that case, either move to a monotonically-increasing
  `coordinator.last_update_success_time` key, or invalidate on
  `_handle_coordinator_update`.
- Reviewer should scrutinize: any call site in the five sensor classes that
  still invokes `_solar_slots(...)` directly instead of `_cached_flat_slots()`.
