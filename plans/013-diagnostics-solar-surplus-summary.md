# Plan 013: Summarise `solar_surplus` in diagnostics + test the shape

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 6753af3..HEAD -- custom_components/engie_be/diagnostics.py tests/test_diagnostics.py`
> Solar-surplus is uncommitted at the "Planned at" SHA. Compare "Current
> state" excerpts against the live file before proceeding.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: dx / tests
- **Planned at**: commit `6753af3`, 2026-07-08

## Why this matters

The v0.13.0b0 Solar Surplus feature stores its data under a new coordinator
key `solar_surplus` = `{"data": {ean: forecasts_list}, "fetched_at": <ISO>}`.
`diagnostics.py::_summarise_coordinator_data` already handles `peaks` and
skips other keys, so `solar_surplus` is *not currently leaked* — but it's
also invisible in support bundles. `top_level_keys` lists it by name, but
support has no way to see whether the wrapper has data, when it was fetched,
how many EANs it covers, or whether the customer is in the all-`NO_DATA`
placeholder shape.

Adding an explicit summariser closes two gaps at once: (1) support has
useful, privacy-safe data for triaging solar-surplus reports; (2) a future
refactor of `_summarise_coordinator_data` that ever falls back to raw
pass-through won't accidentally leak raw EANs from the solar wrapper.

## Current state

### Files

- `custom_components/engie_be/diagnostics.py` — 231 lines. Has
  `_summarise_coordinator_data` (line 66), `_summarise_epex` (107),
  `_hash_ean` (46) for EAN redaction.
- `tests/test_diagnostics.py` — 21 tests covering the current diagnostics.

### Current `_summarise_coordinator_data` (lines 66-104)

```python
def _summarise_coordinator_data(data: Any) -> dict[str, Any]:
    """Return a privacy-preserving summary of per-subentry coordinator data."""
    if not isinstance(data, dict):
        return {"raw_type": type(data).__name__}

    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    ean_hashes = [
        _hash_ean(item["ean"])
        for item in items
        if isinstance(item, dict) and isinstance(item.get("ean"), str) and item["ean"]
    ]
    peaks_wrapper = data.get("peaks") if isinstance(data.get("peaks"), dict) else None
    peaks_inner = (
        peaks_wrapper.get("data")
        if isinstance(peaks_wrapper, dict)
        and isinstance(peaks_wrapper.get("data"), dict)
        else None
    )
    if peaks_wrapper is not None:
        year = peaks_wrapper.get("year")
        month = peaks_wrapper.get("month")
        peaks_month = (
            f"{year:04d}-{month:02d}"
            if isinstance(year, int) and isinstance(month, int)
            else None
        )
        peaks_is_fallback = bool(peaks_wrapper.get("is_fallback", False))
    else:
        peaks_month = None
        peaks_is_fallback = None
    return {
        "item_count": len(items),
        "ean_hashes": ean_hashes,
        "top_level_keys": sorted(data.keys()),
        "peaks_present": peaks_inner is not None,
        "peaks_month": peaks_month,
        "peaks_is_fallback": peaks_is_fallback,
        "is_dynamic": bool(data.get(KEY_IS_DYNAMIC, False)),
    }
```

### Coordinator storage shape

`coordinator.data["solar_surplus"]` is either absent or:

```python
{
    "data": {
        "541448820070414088": [ /* list of day dicts, each with details[] */ ],
        # possibly more EANs
    },
    "fetched_at": "2026-07-08T10:00:00+00:00",
}
```

Each day dict has keys `forecastDate`, `level`, `details`,
`forecastCreationDate`, `inferenceKey`. Every hourly `detail` has
`startTime`, `value`, `level`.

### Repo conventions

- Diagnostic summaries expose counts, hashes, boolean presence flags, and
  ISO timestamps — never raw content.
- EANs are hashed via `_hash_ean(ean)` (8-char SHA-256 prefix).
- Tests in `test_diagnostics.py` construct diagnostics from a fully-mocked
  entry and assert specific keys are/are not present.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Tests (diagnostics) | `.venv/bin/pytest tests/test_diagnostics.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass |

## Scope

**In scope**:
- `custom_components/engie_be/diagnostics.py` — add
  `_summarise_solar_surplus` helper; hook into
  `_summarise_coordinator_data`.
- `tests/test_diagnostics.py` — three new tests.

**Out of scope**:
- Coordinator storage shape — do not touch.
- `_summarise_epex` — leave as is.
- Any other new top-level coordinator keys — this plan only handles
  `solar_surplus`.

## Git workflow

- Branch: `advisor/013-diagnostics-solar-surplus-summary`.
- Commit style: `feat(diagnostics): summarise solar_surplus wrapper`.

## Steps

### Step 1: Add `_summarise_solar_surplus` helper

Insert immediately before or after `_summarise_epex` (around line 107) so
the ordering matches the coordinator-data order:

```python
def _summarise_solar_surplus(wrapper: Any) -> dict[str, Any] | None:
    """
    Return a privacy-safe summary of the cached solar_surplus wrapper.

    Wrapper shape: ``{"data": {ean: forecasts_list}, "fetched_at": ISO}``.
    Emits per-EAN hashes, day count, hourly-slot count, and the mix of
    level values seen (as a sorted list). Never emits raw startTime,
    value, or full EAN strings.
    """
    if not isinstance(wrapper, dict):
        return None
    per_ean = wrapper.get("data")
    if not isinstance(per_ean, dict):
        return None

    per_ean_summary: dict[str, dict[str, Any]] = {}
    for ean, forecasts in per_ean.items():
        if not isinstance(forecasts, list):
            continue
        day_count = 0
        slot_count = 0
        levels: set[str] = set()
        for day in forecasts:
            if not isinstance(day, dict):
                continue
            day_count += 1
            top_level = day.get("level")
            if isinstance(top_level, str):
                levels.add(top_level)
            details = day.get("details")
            if not isinstance(details, list):
                continue
            for slot in details:
                if not isinstance(slot, dict):
                    continue
                slot_count += 1
                slot_level = slot.get("level")
                if isinstance(slot_level, str):
                    levels.add(slot_level)
        per_ean_summary[_hash_ean(ean)] = {
            "day_count": day_count,
            "slot_count": slot_count,
            "levels_present": sorted(levels),
        }

    fetched_at = wrapper.get("fetched_at")
    return {
        "ean_count": len(per_ean_summary),
        "per_ean": per_ean_summary,
        "fetched_at": fetched_at if isinstance(fetched_at, str) else None,
    }
```

### Step 2: Hook the helper into `_summarise_coordinator_data`

Extend the return dict at the bottom of `_summarise_coordinator_data`:

```python
    solar_wrapper = data.get("solar_surplus")
    return {
        "item_count": len(items),
        "ean_hashes": ean_hashes,
        "top_level_keys": sorted(data.keys()),
        "peaks_present": peaks_inner is not None,
        "peaks_month": peaks_month,
        "peaks_is_fallback": peaks_is_fallback,
        "is_dynamic": bool(data.get(KEY_IS_DYNAMIC, False)),
        "solar_surplus": _summarise_solar_surplus(solar_wrapper),
    }
```

**Verify**: `.venv/bin/ruff check custom_components/engie_be/diagnostics.py` → exit 0.

### Step 3: Add tests

Model the tests after existing ones in `tests/test_diagnostics.py`. Read
the file first to understand the fixture-building pattern (search for
`async def test_diagnostics_` and `_summarise_coordinator_data`), then
append these three tests:

```python
def test_summarise_solar_surplus_returns_none_for_missing_wrapper() -> None:
    """No wrapper → None (so top-level key is present but empty)."""
    from custom_components.engie_be.diagnostics import _summarise_solar_surplus

    assert _summarise_solar_surplus(None) is None
    assert _summarise_solar_surplus("not a dict") is None
    assert _summarise_solar_surplus({}) is None
    assert _summarise_solar_surplus({"data": "not a dict"}) is None


def test_summarise_solar_surplus_hashes_eans_and_counts_slots() -> None:
    """Wrapper with real payload yields hashed EAN keys and shape metadata."""
    from custom_components.engie_be.diagnostics import (
        _hash_ean,
        _summarise_solar_surplus,
    )

    ean = "541448820070414088"
    wrapper = {
        "data": {
            ean: [
                {
                    "forecastDate": "2026-07-08",
                    "level": "HIGH_SURPLUS",
                    "details": [
                        {"startTime": "2026-07-08T10:00:00+02:00", "value": 1.5, "level": "LOW_SURPLUS"},
                        {"startTime": "2026-07-08T11:00:00+02:00", "value": 3.2, "level": "HIGH_SURPLUS"},
                    ],
                },
                {
                    "forecastDate": "2026-07-09",
                    "level": "LOW_SURPLUS",
                    "details": [
                        {"startTime": "2026-07-09T10:00:00+02:00", "value": 2.0, "level": "LOW_SURPLUS"},
                    ],
                },
            ],
        },
        "fetched_at": "2026-07-08T10:00:00+00:00",
    }
    result = _summarise_solar_surplus(wrapper)
    assert result is not None
    assert result["ean_count"] == 1
    assert result["fetched_at"] == "2026-07-08T10:00:00+00:00"
    assert _hash_ean(ean) in result["per_ean"]
    per_ean_entry = result["per_ean"][_hash_ean(ean)]
    assert per_ean_entry["day_count"] == 2
    assert per_ean_entry["slot_count"] == 3
    assert per_ean_entry["levels_present"] == ["HIGH_SURPLUS", "LOW_SURPLUS"]
    # Raw EAN must NOT appear anywhere in the output.
    import json
    assert ean not in json.dumps(result)


def test_summarise_solar_surplus_survives_malformed_shape() -> None:
    """Non-dict days, non-list details are silently skipped."""
    from custom_components.engie_be.diagnostics import _summarise_solar_surplus

    wrapper = {
        "data": {
            "5414ZZ": [
                "not a dict",
                {"forecastDate": "2026-07-08", "details": "not a list"},
                {"forecastDate": "2026-07-09", "details": ["not a dict slot"]},
            ],
        },
        "fetched_at": None,
    }
    result = _summarise_solar_surplus(wrapper)
    assert result is not None
    assert result["ean_count"] == 1
    assert result["fetched_at"] is None
    entry = next(iter(result["per_ean"].values()))
    assert entry["day_count"] == 2  # the two dict-shaped days counted
    assert entry["slot_count"] == 0  # neither day yielded slot dicts
```

**Verify**: `.venv/bin/pytest tests/test_diagnostics.py -v` → all pass
including the three new ones.

### Step 4: Full gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

- Three new tests in `tests/test_diagnostics.py`:
  - `test_summarise_solar_surplus_returns_none_for_missing_wrapper` — None/empty/malformed wrapper shapes.
  - `test_summarise_solar_surplus_hashes_eans_and_counts_slots` — happy path; asserts EAN is hashed, not raw; counts match.
  - `test_summarise_solar_surplus_survives_malformed_shape` — degrades on partial data.
- Model after: any of the existing helper tests in `test_diagnostics.py`
  (they follow the same "import helper, call directly, assert shape"
  pattern).

## Done criteria

- [ ] `grep "solar_surplus" custom_components/engie_be/diagnostics.py` returns at least three matches (helper name, hook site, docstring).
- [ ] `.venv/bin/pytest tests/test_diagnostics.py -v` all pass, three new tests present.
- [ ] `.venv/bin/ruff check custom_components tests` exits 0.
- [ ] Total coverage ≥ 95%.
- [ ] No files outside "In scope" modified.
- [ ] `plans/README.md` status row for 013 flipped to DONE.

## STOP conditions

- The coordinator storage shape for `solar_surplus` has changed (data isn't
  a dict of EAN → list of day dicts). Investigate before summarising.
- The existing test file has been renamed or restructured such that the
  test-append point is unclear. Report before appending.
- Coverage drops.

## Maintenance notes

- If ENGIE adds a new field to the day dict (beyond `forecastDate`, `level`,
  `details`, `forecastCreationDate`, `inferenceKey`), the summariser will
  silently ignore it — that's the intended defensive behaviour. Update the
  helper only if a new field would meaningfully help support.
- If the coordinator ever adds another top-level key that reuses EANs
  (e.g. a hypothetical `solar_actuals`), copy this summariser pattern
  rather than expanding this helper — keeps each summary focused.
- Reviewer should confirm no raw EAN string appears in the summary output.
