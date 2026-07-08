# Plan 012: Expose `forecastCreationDate` and `inferenceKey` on the solar-surplus level sensor

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 6753af3..HEAD -- custom_components/engie_be/sensor.py tests/test_sensor_solar_surplus.py README.md`
> The v0.13.0b0 Solar Surplus feature is uncommitted at "Planned at"; diff
> will show large changes. Compare "Current state" excerpts against the live
> file before proceeding.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 011 (both touch `EngieBeSolarSurplusSensor.extra_state_attributes` — land 011 first, or resolve the collision inline)
- **Category**: tech-debt (surface asymmetry: fetched-but-discarded fields)
- **Planned at**: commit `6753af3`, 2026-07-08

## Why this matters

The ENGIE solar-surplus payload carries two per-day metadata fields the
integration currently discards:

- `forecastCreationDate` — when ENGIE computed the forecast (ISO 8601 with
  offset).
- `inferenceKey` — describes the forecast's provenance (observed values:
  `"actuals"`, `"no_data"`; the enum surface may include more).

Without them, an automation using the surplus level can't distinguish a
fresh morning forecast from a stale placeholder. When ENGIE republishes a
day-old fallback (visible via `forecastCreationDate: 1970-01-01T01:00:00+01:00`
in the `no_data` fixture — the sentinel for missing data), users would
otherwise trust the forecast as real. Exposing both fields as attributes on
the enum sensor gives Lovelace templates and automations the ground truth
they need.

## Current state

### Files

- `custom_components/engie_be/sensor.py` — solar surplus sensors.
  `EngieBeSolarSurplusSensor` is the enum-state class whose
  `extra_state_attributes` currently returns `{ean, forecast}`.
- `tests/fixtures/solar_surplus_high.json` — fixture already carries both
  fields.
- `tests/fixtures/solar_surplus_no_data.json` — carries the sentinel
  creation date for stale data.
- `tests/test_sensor_solar_surplus.py` — tests for the enum sensor.
- `README.md` — Solar Surplus section (~line 210) documenting the sensor.

### Fixture confirms the fields exist

From `tests/fixtures/solar_surplus_high.json:2-22`:

```json
{
  "forecasts": [
    {
      "forecastDate": "2026-07-08",
      "level": "HIGH_SURPLUS",
      "details": [ ... ],
      "forecastCreationDate": "2026-07-07T22:00:00+02:00",
      "inferenceKey": "actuals"
    },
    {
      "forecastDate": "2026-07-09",
      "level": "LOW_SURPLUS",
      "details": [ ... ],
      "forecastCreationDate": "2026-07-07T22:00:00+02:00",
      "inferenceKey": "actuals"
    }
  ]
}
```

From `tests/fixtures/solar_surplus_no_data.json:6-13`:

```json
{
  "forecastDate": "2026-07-08",
  "level": "NO_DATA",
  "details": [...],
  "forecastCreationDate": "1970-01-01T01:00:00+01:00",
  "inferenceKey": "no_data"
}
```

### Current sensor attribute code

`sensor.py::EngieBeSolarSurplusSensor.extra_state_attributes` currently
returns:

```python
return {"ean": self._ean, "forecast": flat}
```

`native_value` uses `today = dt_util.now(ZoneInfo(EPEX_TZ)).date().isoformat()`
to locate today's entry in the `forecasts` list; the same lookup is what we
need for the metadata attributes.

### Repo conventions

- Attributes on sensors are lowercase snake_case dict keys. Existing sensor
  attributes: `ean`, `forecast`, `peak_start`, `slot_start`, `slot_end`.
- Extras defer to graceful degradation (`return {}` on missing data),
  matching the current `extra_state_attributes`.
- README changes must be added to `[Unreleased]` in `CHANGELOG.md` under
  `### Changed`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Tests | `.venv/bin/pytest tests/test_sensor_solar_surplus.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass |

## Scope

**In scope**:
- `custom_components/engie_be/sensor.py` — `EngieBeSolarSurplusSensor.extra_state_attributes`
- `tests/test_sensor_solar_surplus.py` — two new tests
- `README.md` — one-line addition under the Solar Surplus section
- `CHANGELOG.md` — `[Unreleased]` `### Changed` entry

**Out of scope**:
- The four numeric sensors — their attributes stay lean (only `peak_start`
  on the peak sensor).
- Any new sensor class for these fields — they are attributes on the
  existing enum sensor, not standalone timestamp/enum sensors. Refuse if
  scope creeps to "one sensor per field."
- The `strings.json`/`translations/en.json` files — attribute keys are not
  translated in HA.

## Git workflow

- Branch: `advisor/012-expose-solar-forecast-metadata`.
- Commit style: `feat(sensor): expose ENGIE forecast creation date and inference key`.

## Steps

### Step 1: Extend `extra_state_attributes` to include the today-day metadata

Locate `EngieBeSolarSurplusSensor.extra_state_attributes` in
`custom_components/engie_be/sensor.py` (around line 1339 in current
working-tree state — verify with a grep for
`def extra_state_attributes` inside `EngieBeSolarSurplusSensor`).

Refactor the today-lookup to be shared with the metadata pull:

```python
    def _today_entry(self) -> dict[str, Any] | None:
        """Return the day-entry dict for Brussels-local today, or None."""
        forecasts = self._forecasts_for_ean()
        if not forecasts:
            return None
        today = dt_util.now(ZoneInfo(EPEX_TZ)).date().isoformat()
        for day in forecasts:
            if isinstance(day, dict) and day.get("forecastDate") == today:
                return day
        # Fall back to first available day (existing behaviour of native_value)
        return next(
            (day for day in forecasts if isinstance(day, dict)),
            None,
        )
```

Update `native_value` to use `_today_entry()` instead of duplicating the
lookup. Then extend `extra_state_attributes`:

```python
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the flat hourly forecast plus today's provenance."""
        forecasts = self._forecasts_for_ean()
        if not forecasts:
            return {}
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
                level = slot.get("level")
                if not isinstance(level, str):
                    continue
                flat.append(
                    {
                        "start": slot.get("startTime"),
                        "value": slot.get("value"),
                        "level": level.lower(),
                    }
                )
        today = self._today_entry() or {}
        creation_raw = today.get("forecastCreationDate")
        inference_raw = today.get("inferenceKey")
        return {
            "ean": self._ean,
            "forecast": flat,
            "forecast_creation_date": (
                creation_raw if isinstance(creation_raw, str) else None
            ),
            "inference_key": (
                inference_raw if isinstance(inference_raw, str) else None
            ),
        }
```

Attribute keys are lowercase snake_case (`forecast_creation_date`,
`inference_key`). Do NOT parse `forecast_creation_date` into a datetime here
— attribute values are JSON-serializable and the ISO string round-trips
cleanly through the HA state machine and Lovelace templates. HA templates
can call `as_datetime()` if needed.

**Verify**: `.venv/bin/ruff check custom_components/engie_be/sensor.py` → exit 0.

### Step 2: Add tests

Append to `tests/test_sensor_solar_surplus.py`, using the existing
`_sensor()`, `_wrap()`, `_load()` helpers already in the file:

```python
def test_extra_state_attributes_include_forecast_creation_date_when_today_matches(
    freezer: FrozenDateTimeFactory,
) -> None:
    """The creation date attribute mirrors today's day-entry from the fixture."""
    freezer.move_to("2026-07-08T12:00:00+02:00")
    sensor = _sensor(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    attrs = sensor.extra_state_attributes
    assert attrs["forecast_creation_date"] == "2026-07-07T22:00:00+02:00"
    assert attrs["inference_key"] == "actuals"


def test_extra_state_attributes_surface_no_data_sentinel_creation_date(
    freezer: FrozenDateTimeFactory,
) -> None:
    """The 1970-01-01 sentinel from ENGIE for no-data days is exposed verbatim."""
    freezer.move_to("2026-07-08T12:00:00+02:00")
    sensor = _sensor(_wrap({_EAN: _load(_SOLAR_NO_DATA)["forecasts"]}))
    attrs = sensor.extra_state_attributes
    assert attrs["forecast_creation_date"] == "1970-01-01T01:00:00+01:00"
    assert attrs["inference_key"] == "no_data"


def test_extra_state_attributes_metadata_none_when_today_absent(
    freezer: FrozenDateTimeFactory,
) -> None:
    """When today's date is not in the payload, metadata falls back to first day."""
    freezer.move_to("2027-01-01T12:00:00+01:00")
    sensor = _sensor(_wrap({_EAN: _load(_SOLAR_HIGH)["forecasts"]}))
    attrs = sensor.extra_state_attributes
    # Falls back to first day in the fixture, which also has this metadata.
    assert attrs["forecast_creation_date"] == "2026-07-07T22:00:00+02:00"
    assert attrs["inference_key"] == "actuals"
```

**Verify**: `.venv/bin/pytest tests/test_sensor_solar_surplus.py -v` → all
pass including the three new ones.

### Step 3: Update `README.md`

Locate the "Solar Surplus" section (search for `## Solar Surplus` or a
neighbouring heading). Add one line under the level-sensor attribute
description. Model after the existing bullet listing `forecast`:

Find the paragraph describing the enum sensor's attributes and append:

```markdown
Two provenance attributes describe today's forecast: `forecast_creation_date`
(ISO 8601 timestamp of when ENGIE computed it — the sentinel
`1970-01-01T01:00:00+01:00` marks a placeholder for accounts without solar
data) and `inference_key` (`actuals` for real forecasts, `no_data` for the
placeholder).
```

Exact placement: immediately after the existing sentence that describes the
`forecast` attribute layout.

### Step 4: Update `CHANGELOG.md`

Under `## [Unreleased]`, add or extend a `### Changed` block (or add
`### Added` if that's the closer match to prior entries — inspect the
existing Unreleased block first):

```markdown
### Changed

- Solar surplus level sensor now exposes `forecast_creation_date` and
  `inference_key` attributes, letting automations detect stale/placeholder
  forecasts from ENGIE.
```

### Step 5: Full gate

**Verify**:
- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

Three new tests in `tests/test_sensor_solar_surplus.py`:

- `test_extra_state_attributes_include_forecast_creation_date_when_today_matches` — real forecast
- `test_extra_state_attributes_surface_no_data_sentinel_creation_date` — sentinel placeholder
- `test_extra_state_attributes_metadata_none_when_today_absent` — fallback path

Model after: `test_extra_state_attributes_flattens_all_days` above in the
file (same fixture, same sensor construction, same freezer usage).

## Done criteria

- [ ] `grep "forecast_creation_date" custom_components/engie_be/sensor.py` → at least one match.
- [ ] `grep "inference_key" custom_components/engie_be/sensor.py` → at least one match.
- [ ] `.venv/bin/pytest tests/test_sensor_solar_surplus.py -v` all pass, three new tests present.
- [ ] `.venv/bin/ruff check custom_components tests` exits 0.
- [ ] Total coverage ≥ 95%.
- [ ] `README.md` mentions `forecast_creation_date` and `inference_key`.
- [ ] `CHANGELOG.md` has an Unreleased entry.
- [ ] No files outside "In scope" modified.
- [ ] `plans/README.md` status row for 012 flipped to DONE.

## STOP conditions

- The `extra_state_attributes` code no longer matches the excerpts above
  (drift).
- The `EngieBeSolarSurplusSensor` class has been merged into another class,
  or its unique_id shape changes — that alters the attribute contract and
  needs re-planning.
- Coverage drops.

## Maintenance notes

- Attribute keys are informal contracts; renaming later requires a
  breaking-change note in CHANGELOG. Keep `forecast_creation_date` /
  `inference_key` stable.
- If ENGIE ever ships `inferenceKey` values beyond `"actuals"` / `"no_data"`,
  they surface verbatim — no code change needed. Only add a translation
  layer if the front-end asks for one.
- Reviewer should confirm the fallback-to-first-day path (line "Falls back to
  first available day…") behaves consistently for both `native_value` and
  the metadata attributes.
