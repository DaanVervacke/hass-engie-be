# Plan 002: Fix _statistics.py module docstring drift ("three IDs" -> six)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat d0652ec..HEAD -- custom_components/engie_be/_statistics.py`
> If the file changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: docs
- **Planned at**: commit `d0652ec`, 2026-07-07

## Why this matters

The module docstring at the top of `_statistics.py` says the module writes to "three per-BAN statistic IDs (`engie_be:{ban}_consumption`, `_injection`, `_gas`)". This became outdated when the cost-stream feature landed in v0.12.0b1: there are now six streams (three energy + three cost). A contributor reading the docstring first will get a wrong mental model. Same file, same session, first thing anyone sees.

## Current state

### File

- `custom_components/engie_be/_statistics.py` — the historical-import orchestrator + pure converter.

### Excerpt to correct

Lines 1-9:

```python
"""
Historical usage import into Home Assistant long-term statistics.

Turns ENGIE usage-details payloads into hour-aligned StatisticData rows and
feeds them to ``async_add_external_statistics`` under three per-BAN
statistic IDs (``engie_be:{ban}_consumption``, ``_injection``, ``_gas``).
The Energy Dashboard picks these up automatically for the electricity and
gas source pickers.
"""
```

### Ground truth (do NOT edit — read for reference)

`_statistics.py` lines 54-67 declare six stream constants:

```python
STREAM_CONSUMPTION = "consumption"
STREAM_INJECTION = "injection"
STREAM_GAS = "gas"
STREAM_CONSUMPTION_COST = "consumption_cost"
STREAM_INJECTION_COST = "injection_cost"
STREAM_GAS_COST = "gas_cost"
```

Plus `_STREAM_SPECS` (lines 129-169) has one entry per stream. Six total. Cost streams write EUR values via `unit_class=None, unit_of_measurement="EUR"`; energy streams write kWh via `EnergyConverter.UNIT_CLASS`.

### Repo conventions to follow

- No em-dashes. Plain hyphens only.
- No AI-tell prose ("comprehensive", "seamless", "essentially", etc).
- Docstrings state facts, not marketing.
- Match the concise present-tense style of the sibling module docstrings in `_relations.py`, `_epex.py`, `_happy_hour.py`, `_peaks.py`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Tests | `.venv/bin/pytest tests/test_statistics.py -q --tb=line` | all pass |
| Full suite | `.venv/bin/pytest tests/ -q --tb=line` | all pass |
| Ruff | `.venv/bin/ruff check custom_components/engie_be/_statistics.py` | `All checks passed!` |

## Scope

**In scope** (only file you should modify):

- `custom_components/engie_be/_statistics.py` (only the module docstring at the top; lines 1-9)

**Out of scope** (do NOT touch):

- All other code in `_statistics.py`. The docstring is at the top of the file; every other line stays.
- Every other file. No cross-file edits.
- The `unit_class` / `unit_of_measurement` values on cost streams. These are settled tradeoffs (kWh streams share `EnergyConverter.UNIT_CLASS`; cost streams use `None` + "EUR" per Home Assistant statistics conventions).

## Git workflow

- Branch: `advisor/002-statistics-docstring`
- Commit style: `docs(_statistics): correct docstring to reflect six per-BAN streams`
- Do NOT push or open a PR unless the operator explicitly instructs.

## Steps

### Step 1: Rewrite the module docstring

Replace lines 1-9 with a version that accurately names the six streams. Suggested shape (adjust wording so it does not read like AI-generated slop; state facts, no filler):

```python
"""
Historical usage import into Home Assistant long-term statistics.

Turns ENGIE usage-details payloads into hour-aligned StatisticData rows
and feeds them to ``async_add_external_statistics`` under up to six
per-BAN statistic IDs: three energy streams
(``engie_be:{ban}_consumption``, ``_injection``, ``_gas`` — in kWh) and
three matching cost streams (``_consumption_cost``, ``_injection_cost``,
``_gas_cost`` — in EUR). The cost streams are opt-in via the
``include_costs`` flag on the service action and the setup flow. The
Energy dashboard picks these up automatically for the electricity and
gas source pickers.
"""
```

Notes on wording:
- "Energy dashboard" is lowercase-`d` per HA's canonical spelling (see `.github/instructions` if it exists, or the frontend translations). Do NOT use "Energy Dashboard" title case.
- Do not add an em-dash. The ` — ` (spaced en-dash-lookalike) above is a plain hyphen with spaces; if your editor auto-converts, disable that.

**Verify**:

```
head -15 custom_components/engie_be/_statistics.py
```

Expected: new docstring visible, mentioning six streams and both kWh + EUR.

### Step 2: Confirm no other stale references

Grep for the old claim to be sure nothing else in the file (or the wider docs) still says "three":

```
grep -n "three per-BAN\|three per-ban\|three statistic ID" custom_components/engie_be/_statistics.py README.md
```

Expected: no matches. If any turn up, add them to your edit or STOP and report — this plan is scoped to the module docstring only, and other drift is out of scope.

### Step 3: Run the full test suite

```
.venv/bin/pytest tests/ -q --tb=line
```

Expected: all pass. This edit is docstring-only so tests should be unaffected; if anything fails, treat as a STOP condition.

### Step 4: Lint

```
.venv/bin/ruff check custom_components/engie_be/_statistics.py
```

Expected: `All checks passed!`.

Also run:

```
.venv/bin/ruff format --check custom_components/engie_be/_statistics.py
```

Expected: file already formatted.

## Test plan

- No new tests. Docstring-only change. Existing test suite must still pass.
- Verification: pytest + ruff commands in the steps.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `head -15 custom_components/engie_be/_statistics.py` shows the new docstring naming six streams and both kWh + EUR units.
- [ ] `grep -c "three per-BAN\|three per-ban" custom_components/engie_be/_statistics.py` returns `0`.
- [ ] `.venv/bin/pytest tests/ -q --tb=line` — full suite passes.
- [ ] `.venv/bin/ruff check custom_components/engie_be/_statistics.py` returns clean.
- [ ] `.venv/bin/ruff format --check custom_components/engie_be/_statistics.py` returns clean.
- [ ] `git status --short` shows only `_statistics.py` as modified (no other files).
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report back (do not improvise) if:

- The excerpt at lines 1-9 does not match the "Current state" excerpt (the file has drifted since this plan was written).
- Any pytest failure appears. A docstring change cannot break tests; if one does, something else is wrong.
- Grep in step 2 turns up other stale "three" references outside the docstring. Those belong in a separate plan; do NOT expand scope here.

## Maintenance notes

- If a seventh stream is ever added (e.g. water, district heating), update this docstring and the sibling documentation in `README.md` under "Historical usage import".
- Watch for future contributors adding stream constants to `_STREAM_SPECS` without updating the docstring.
