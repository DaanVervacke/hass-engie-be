# Plan 028: Remove `EngieBeHasOverdueInvoiceBinarySensor` and its `has_overdue` logic

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving
> on. STOP if any assertion in the STOP conditions triggers. Update
> `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be/binary_sensor.py custom_components/engie_be/_billing.py tests/test_binary_sensor_billing.py tests/test_billing_helpers.py tests/test_diagnostics.py`

## Status

- **Priority**: P2 (user-requested cleanup)
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 025 (introduced the sensor being removed) — must be DONE
- **Category**: tech-debt (removal)
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

The `EngieBeHasOverdueInvoiceBinarySensor` (added in plan 025) applied
domain-specific interpretation on top of the raw account-balance
payload — it fused two orthogonal signals (`overview.dueAmount > 0`
OR any transaction with `dueDate < today AND openAmount > 0`) into a
single `is_on` state. The maintainer prefers exposing the raw
`dueAmount` sensor and letting users build their own template
automations. Removing the binary sensor + its `has_overdue()` helper
+ its tests + its diagnostics field simplifies the surface without
losing any information (the three remaining billing sensors still
carry every field the binary sensor derived from).

## Current state

### Class to delete (`binary_sensor.py:617`)

```python
class EngieBeHasOverdueInvoiceBinarySensor(EngieBeEntity, BinarySensorEntity):
    """Binary sensor indicating whether any invoice is overdue."""
    entity_description = HAS_OVERDUE_INVOICE_DESCRIPTION
    ...
```

Plus the `HAS_OVERDUE_INVOICE_DESCRIPTION` constant that references it,
and any factory function or entry-setup registration line that
constructs it. Look for the pattern `EngieBeHasOverdueInvoice` across
`binary_sensor.py` to catch all sites.

### Helper to delete (`_billing.py`)

```python
def has_overdue(wrapper: dict[str, Any] | None, now: datetime) -> bool:
    ...
```

### Translation to delete

In `strings.json`, the `has_overdue_invoice` entry under
`entity.binary_sensor`. Then `cp` to `translations/en.json`.

### Diagnostics to update (`diagnostics.py`)

`_summarise_billing` currently emits a `has_overdue` key. Remove that
key from the returned dict — the summary should still emit
`has_data`, `fetched_at`, `status`, `transaction_count`.

### Tests to delete or edit

- `tests/test_binary_sensor_billing.py`: delete entire file (every
  test targets the sensor being removed).
- `tests/test_billing_helpers.py`: delete every test whose name
  contains `has_overdue`. Keep the rest of the file (helper tests
  for `overview_open_amount`, `overview_due_amount`, `next_due_date`).
- `tests/test_diagnostics.py`: delete or amend any assertion that
  checks `has_overdue` in the `_summarise_billing` output. If a test
  is *only* about `has_overdue`, delete it; if it also asserts on
  other keys, remove the `has_overdue` assertion but keep the rest.
- `tests/fixtures/billing_overdue.json`: delete (no longer referenced
  after the tests are gone).

### Doc changes

- `README.md`: locate the "Account balance (billing)" section (or
  whatever the title is — check with `grep -n -i 'billing\|account balance' README.md`). Strike the line that describes the
  `binary_sensor.*has_overdue_invoice`. Keep everything else.
- `CHANGELOG.md`: in the `[Unreleased] ### Added` block, locate the
  billing entry. Remove the phrase describing the overdue binary
  sensor (usually rendered as "and overdue binary sensor" or similar).
  Do NOT delete the whole entry — the three sensors remain.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| JSON sanity | `python3 -c "import json; json.load(open('custom_components/engie_be/strings.json'))"` | no error |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass, ≥95% |

## Scope

**In scope** (delete or edit only):
- `custom_components/engie_be/binary_sensor.py`
- `custom_components/engie_be/_billing.py`
- `custom_components/engie_be/diagnostics.py`
- `custom_components/engie_be/strings.json`
- `custom_components/engie_be/translations/en.json`
- `tests/test_binary_sensor_billing.py` (delete)
- `tests/test_billing_helpers.py`
- `tests/test_diagnostics.py`
- `tests/fixtures/billing_overdue.json` (delete)
- `README.md`
- `CHANGELOG.md`

**Out of scope**:
- The three remaining billing sensors and their tests.
- Any coordinator or api.py code.
- Device conditions in `device_condition.py` — the overdue binary
  sensor was NOT in the device-condition dropdown, so no cleanup
  needed there.
- Any change beyond what removing this one sensor requires.

## Steps

### Step 1: Remove the class + description + registration

Edit `custom_components/engie_be/binary_sensor.py`:
- Delete `HAS_OVERDUE_INVOICE_DESCRIPTION` (the `BinarySensorEntityDescription`).
- Delete the `EngieBeHasOverdueInvoiceBinarySensor` class.
- Locate the platform-setup path (probably a `_build_billing_binary_sensors` function or similar, or an inline construction in `async_setup_entry`). Remove the construction of the overdue sensor. If the resulting builder function is now empty, delete the function AND the call site that would have added its (now-empty) result.
- If `_billing_wrapper` in `binary_sensor.py` is now unused (grep to check), delete it.
- Clean up any imports that are now unused (`BinarySensorDeviceClass`, `HAS_OVERDUE_INVOICE_DESCRIPTION`, etc.). Ruff will help catch these on `--fix`.

**Verify**: `.venv/bin/ruff check custom_components/engie_be/binary_sensor.py --fix` → exit 0. `grep -n "HasOverdueInvoice\|HAS_OVERDUE_INVOICE\|has_overdue_invoice" custom_components/engie_be/binary_sensor.py` returns no matches.

### Step 2: Remove `has_overdue()` from `_billing.py`

Delete the function definition entirely. Clean up unused imports
(`datetime`, `date`, `time`, `timedelta`, `ZoneInfo` — check with ruff
whether they're still needed by the remaining helpers). If
`_WEEKDAY_KEYS` or other module constants are now unused, delete them
too.

**Verify**: `.venv/bin/ruff check custom_components/engie_be/_billing.py` → exit 0. `grep -c "^def has_overdue\|^def _.*overdue" custom_components/engie_be/_billing.py` returns 0.

### Step 3: Remove `has_overdue` from diagnostics summary

Edit `custom_components/engie_be/diagnostics.py`. In `_summarise_billing`, remove the `has_overdue` key from the returned dict. Do NOT remove any other key. Clean up any `has_overdue` import at the top of the file.

**Verify**: `grep -n "has_overdue" custom_components/engie_be/diagnostics.py` returns no matches.

### Step 4: Remove translation

Edit `custom_components/engie_be/strings.json`. Under `entity.binary_sensor`, delete the `has_overdue_invoice` key/value pair. Keep the surrounding JSON valid.

Copy the updated file: `cp custom_components/engie_be/strings.json custom_components/engie_be/translations/en.json`.

**Verify**: `python3 -c "import json; json.load(open('custom_components/engie_be/strings.json'))"` → no error. `grep 'has_overdue_invoice' custom_components/engie_be/strings.json custom_components/engie_be/translations/en.json` returns no matches. `diff -q custom_components/engie_be/strings.json custom_components/engie_be/translations/en.json` → no output (files identical).

### Step 5: Delete the test file and fixture

```bash
rm tests/test_binary_sensor_billing.py
rm tests/fixtures/billing_overdue.json
```

**Verify**: `ls tests/test_binary_sensor_billing.py tests/fixtures/billing_overdue.json 2>&1` → both "No such file".

### Step 6: Prune `test_billing_helpers.py`

Remove every test function whose name contains `has_overdue`. Keep everything else.

**Verify**: `grep -c "has_overdue" tests/test_billing_helpers.py` returns 0.

### Step 7: Prune `test_diagnostics.py`

Locate any test that asserts on the `has_overdue` field of the
`_summarise_billing` output. Options per test:
- Test asserts only on `has_overdue` → delete the whole test.
- Test asserts on multiple keys → remove just the `has_overdue` assertion; keep the rest.

Watch for negative assertions too (e.g. "raw amounts do not appear" — those STAY, they're privacy checks unrelated to overdue).

**Verify**: `grep -c "has_overdue" tests/test_diagnostics.py` returns 0.

### Step 8: Update `README.md`

Locate the billing section. Remove the line describing the
`binary_sensor.*has_overdue_invoice`. Leave the three sensors and
their descriptions in place.

**Verify**: `grep -i "has_overdue\|overdue" README.md` returns no matches (or only unrelated matches — e.g. legacy Happy Hours copy that never mentioned overdue).

### Step 9: Update `CHANGELOG.md`

In the `[Unreleased] ### Added` block, locate the billing entry (added by plan 025 execution). Remove the phrase describing the overdue binary sensor. Adjust wording so the entry still reads cleanly (e.g. "Account balance sensors ... plus overdue binary sensor" → "Account balance sensors ...").

**Verify**: `grep -i "overdue" CHANGELOG.md | head -5` should return no matches in the `[Unreleased]` section (matches in older `[0.11.x]` sections are unrelated legacy).

### Step 10: Full gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

No new tests. Existing tests may need trimming per Steps 6–7. The test count is expected to drop by roughly 4–7 (the deleted binary-sensor tests + any `has_overdue`-only helper/diagnostics tests). Coverage may tick slightly (fewer branches to cover) or stay flat.

## Done criteria

- [ ] `grep -rn "HasOverdueInvoice\|HAS_OVERDUE_INVOICE\|has_overdue_invoice\|has_overdue" custom_components/engie_be/` returns no matches (except in comments if any survive).
- [ ] `ls tests/test_binary_sensor_billing.py tests/fixtures/billing_overdue.json 2>&1 | grep -c 'No such file'` returns 2.
- [ ] `grep -rn "has_overdue" tests/` returns no matches.
- [ ] `.venv/bin/ruff check custom_components tests` exits 0.
- [ ] `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` passes with a smaller test count than before this plan.
- [ ] `strings.json` and `translations/en.json` are byte-identical.
- [ ] `README.md` no longer mentions `has_overdue_invoice`.
- [ ] `CHANGELOG.md`'s `[Unreleased]` billing entry no longer mentions the overdue binary sensor.
- [ ] `plans/README.md` status row for 028 flipped to DONE.

## STOP conditions

- The `EngieBeHasOverdueInvoiceBinarySensor` class or its
  registration is referenced from anywhere other than the files
  listed in Scope. Report the site — deleting outside-scope code
  is out of bounds.
- The billing platform setup (in `binary_sensor.py::async_setup_entry`)
  breaks in a way that also removes the setup path for other binary
  sensors (EPEX negative, Happy Hours active, authentication). STOP —
  the intent is to remove only the one class, not the whole billing
  binary-sensor scaffold if others exist there.
- A test file imports `has_overdue` in a way that suggests the helper
  was reused for a different purpose. Investigate before deleting.

## Maintenance notes

- Users who want overdue notifications can now build them from
  `sensor.*_account_balance_due` (numeric `dueAmount` in EUR). A
  simple template automation: `{{ states('sensor.engie_belgium_{BAN}_account_balance_due') | float(0) > 0 }}`.
- If future demand appears for a first-class overdue sensor, restore
  from `plans/025-account-balance-invoices.md` execution notes —
  the logic was straightforward.
- Reviewer should scrutinize: `_summarise_billing` output; the field
  removal must not leave any raw invoice content that was previously
  hidden behind an unrelated key.
