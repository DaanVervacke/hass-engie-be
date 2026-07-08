# Plan 029: Rename `account_balance` → `outstanding_balance` and `account_balance_due` → `overdue_amount`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. STOP if any STOP condition
> triggers. Update `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be tests README.md CHANGELOG.md`

## Status

- **Priority**: P2 (bug — semantic error in user-facing entity names)
- **Effort**: S
- **Risk**: LOW (pre-release; no installed user has these entity IDs)
- **Depends on**: 025 (created the sensors), 028 (removed the overdue binary sensor). Both DONE.
- **Category**: bug
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

The two billing sensors added by plan 025 use "account balance"
terminology. In finance, "account balance" implies the amount held in
the account (positive = money you have). ENGIE's `overview.openAmount`
is the opposite: it's how much the customer OWES ENGIE. Users looking
at `sensor.engie_belgium_{BAN}_account_balance = 80.60` reasonably
assume "I have €80.60" when the actual meaning is "I owe €80.60."

Rename before v0.13.0 ships:

- `account_balance` (state = `overview.openAmount`, EUR) → `outstanding_balance`
- `account_balance_due` (state = `overview.dueAmount`, EUR) → `overdue_amount`

Also update the user-facing names:

- "Account balance" → "Outstanding balance"
- "Account balance past due" → "Overdue amount"

Entity IDs, unique IDs, translation keys, class names, and internal
descriptor constants all move in lockstep so the codebase stays
self-consistent. **`_billing.py` helper names stay** — they mirror
the wire field names (`overview_open_amount`, `overview_due_amount`),
which is informative and correct.

## Current state

### `sensor.py`

Around line 1748 (`# Billing (account balance) sensors` heading), then:

```python
_BILLING_ACCOUNT_BALANCE = SensorEntityDescription(
    key="account_balance",
    translation_key="account_balance",
    native_unit_of_measurement=CURRENCY_EURO,
    device_class=SensorDeviceClass.MONETARY,
    state_class=SensorStateClass.TOTAL,
    suggested_display_precision=2,
)

_BILLING_ACCOUNT_BALANCE_DUE = SensorEntityDescription(
    key="account_balance_due",
    translation_key="account_balance_due",
    ...
)
```

And two class definitions:

```python
class EngieBeAccountBalanceSensor(_EngieBeBillingBase):
    """Total open account balance in EUR (positive = customer owes ENGIE)."""
    ...
    return overview_open_amount(_billing_wrapper(self.coordinator))


class EngieBeAccountBalanceDueSensor(_EngieBeBillingBase):
    """Amount that is currently past its due date in EUR."""
    ...
    return overview_due_amount(_billing_wrapper(self.coordinator))
```

Plus a `_build_billing_sensors` factory that constructs both classes
along with `EngieBeNextInvoiceDueSensor` (the third billing sensor,
untouched by this plan).

### `strings.json`

Two entries under `entity.sensor`:

```json
"account_balance": {"name": "Account balance"},
"account_balance_due": {"name": "Account balance past due"},
```

(Exact wording may differ — verify by grep.)

### `translations/en.json`

Byte-identical to `strings.json` (per repo convention).

### Test files

- `tests/test_sensor_billing.py` — asserts on class names, entity IDs, and translation keys. Every assertion must be updated.
- `tests/test_diagnostics.py` — likely no impact (billing diagnostics summariser only exposes structural metadata: `has_data`, `fetched_at`, `status`, `transaction_count`; not the entity names or the raw amount fields). Verify with grep.
- `tests/test_init.py::_make_client` — no impact (the mock stubs the client method, not the entities).

### `README.md`

Billing section describes both sensors by name and entity ID.

### `CHANGELOG.md`

`[Unreleased] ### Added` block mentions the billing sensors.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| JSON sanity | `python3 -c "import json; json.load(open('custom_components/engie_be/strings.json'))"` | no error |
| Byte-identical i18n | `diff -q custom_components/engie_be/strings.json custom_components/engie_be/translations/en.json` | no output |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass, ≥95% |

## Scope

**In scope**:
- `custom_components/engie_be/sensor.py` — rename 2 descriptor constants, 2 class names, 2 SensorEntityDescription `key`/`translation_key` values, and any references from `_build_billing_sensors`.
- `custom_components/engie_be/strings.json` — rename 2 translation keys + update the 2 `name` strings.
- `custom_components/engie_be/translations/en.json` — byte-copy of `strings.json`.
- `tests/test_sensor_billing.py` — update all class-name imports, entity-id assertions, translation-key expectations.
- `README.md` — update billing section wording + entity-id references.
- `CHANGELOG.md` — update `[Unreleased]` billing entry wording.

**Out of scope**:
- `custom_components/engie_be/_billing.py` helper functions
  (`overview_open_amount`, `overview_due_amount`) — names stay. They
  mirror wire field names.
- `EngieBeNextInvoiceDueSensor` — untouched.
- `diagnostics.py::_summarise_billing` — no rename needed (the field
  names in the summary are structural: `has_data`, `fetched_at`,
  `status`, `transaction_count`).
- Any coordinator or API-client code.

## Steps

### Step 1: Rename in `sensor.py`

Symmetric rename. Use these exact substitutions in
`custom_components/engie_be/sensor.py`:

- Constant `_BILLING_ACCOUNT_BALANCE` → `_BILLING_OUTSTANDING_BALANCE`
- Constant `_BILLING_ACCOUNT_BALANCE_DUE` → `_BILLING_OVERDUE_AMOUNT`
- Class `EngieBeAccountBalanceSensor` → `EngieBeOutstandingBalanceSensor`
- Class `EngieBeAccountBalanceDueSensor` → `EngieBeOverdueAmountSensor`
- Inside the two SensorEntityDescription blocks:
  - `key="account_balance"` → `key="outstanding_balance"`
  - `key="account_balance_due"` → `key="overdue_amount"`
  - `translation_key="account_balance"` → `translation_key="outstanding_balance"`
  - `translation_key="account_balance_due"` → `translation_key="overdue_amount"`

Update the section header comment (line ~1748) from
`# Billing (account balance) sensors` to `# Billing (outstanding balance + overdue amount) sensors`.

Update the docstring on `EngieBeOutstandingBalanceSensor` from
"Total open account balance in EUR" to "Outstanding balance owed to
ENGIE in EUR (positive = customer owes ENGIE, negative = credit)".

Update the docstring on `EngieBeOverdueAmountSensor` from "Amount that
is currently past its due date in EUR" to "Overdue amount in EUR
(portion of the outstanding balance past its due date)".

Fix the factory reference in `_build_billing_sensors` to use the new
class names.

**Verify**:
- `grep -n "AccountBalance\|account_balance" custom_components/engie_be/sensor.py` returns no matches.
- `grep -c "OutstandingBalance\|outstanding_balance\|OverdueAmount\|overdue_amount" custom_components/engie_be/sensor.py` returns at least 8 matches.
- `.venv/bin/ruff check custom_components/engie_be/sensor.py` exits 0.

### Step 2: Rename in `strings.json`

Under `entity.sensor`:

- `"account_balance": {"name": "Account balance"}` → `"outstanding_balance": {"name": "Outstanding balance"}`
- `"account_balance_due": {"name": "Account balance past due"}` → `"overdue_amount": {"name": "Overdue amount"}`

Preserve JSON validity. Do NOT reorder or touch other keys.

**Verify**:
- `python3 -c "import json; d = json.load(open('custom_components/engie_be/strings.json')); print('outstanding_balance' in d['entity']['sensor'], 'overdue_amount' in d['entity']['sensor'], 'account_balance' in d['entity']['sensor'])"` prints `True True False`.

### Step 3: Sync `translations/en.json`

```bash
cp custom_components/engie_be/strings.json custom_components/engie_be/translations/en.json
```

**Verify**:
- `diff -q custom_components/engie_be/strings.json custom_components/engie_be/translations/en.json` → no output.

### Step 4: Update `tests/test_sensor_billing.py`

Locate every reference. Update:

- Class imports: `EngieBeAccountBalanceSensor` → `EngieBeOutstandingBalanceSensor`, `EngieBeAccountBalanceDueSensor` → `EngieBeOverdueAmountSensor`
- Any `entity_id` assertion containing `_account_balance` → `_outstanding_balance`; `_account_balance_due` → `_overdue_amount`
- Any `unique_id` assertion using the same slug — same rename
- Any test-function name that includes `account_balance` — rename for consistency (`test_account_balance_positive` → `test_outstanding_balance_positive`, etc.)

**Verify**:
- `grep -n "AccountBalance\|account_balance" tests/test_sensor_billing.py` returns no matches.
- `.venv/bin/pytest tests/test_sensor_billing.py -v` all pass.

### Step 5: Check `test_diagnostics.py` for stragglers

Almost certainly no impact, but confirm:

```bash
grep -n "account_balance\|AccountBalance" tests/test_diagnostics.py
```

If any matches exist, apply the same rename.

### Step 6: Update `README.md`

Locate the billing section. Update the entity list + any prose that
describes the sensors. Concrete substitutions:

- `sensor.engie_belgium_{BAN}_account_balance` → `sensor.engie_belgium_{BAN}_outstanding_balance`
- `sensor.engie_belgium_{BAN}_account_balance_due` → `sensor.engie_belgium_{BAN}_overdue_amount`
- "Account balance" (in the descriptive text) → "Outstanding balance"
- Whichever wording currently describes the second sensor → "Overdue amount"

If the section header says "Account balance" or similar, update to
something like "Outstanding balance and overdue amount" or simply
"Billing".

**Verify**:
- `grep -i "account_balance\|account balance" README.md` returns no matches inside the billing section (older CHANGELOG-style text elsewhere unrelated).

### Step 7: Update `CHANGELOG.md`

In the `[Unreleased] ### Added` block, locate the billing entry (added
by plan 025). Update the wording to reflect the new names:

- "Account balance" → "Outstanding balance"
- Any past-due description → "overdue amount"

If the entry lists specific entity IDs, update those too.

**Verify**:
- `grep -n "account_balance\|Account balance" CHANGELOG.md | head` — matches, if any, should only appear in historical `[0.12.x]` sections, NOT in `[Unreleased]`. Older sections are frozen; do not edit them.

### Step 8: Full gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass
- `diff -q custom_components/engie_be/strings.json custom_components/engie_be/translations/en.json` → no output

## Test plan

No new tests. Every existing billing test in `tests/test_sensor_billing.py` should keep passing after the rename, with test names + assertions updated in lockstep. Test count stays the same (~10-12 billing tests before the rename, same count after).

## Done criteria

- [ ] `grep -rn "EngieBeAccountBalance\|account_balance\|_ACCOUNT_BALANCE" custom_components/engie_be/ tests/ README.md CHANGELOG.md` returns no matches (except in `[0.12.x]` CHANGELOG sections).
- [ ] `grep -rn "OutstandingBalance\|outstanding_balance\|OverdueAmount\|overdue_amount" custom_components/engie_be/sensor.py` returns ≥ 8 matches.
- [ ] `strings.json` and `translations/en.json` are byte-identical.
- [ ] `.venv/bin/pytest tests/ -q --cov-fail-under=95` passes with the same or higher test count than before this plan.
- [ ] `README.md` billing section uses "Outstanding balance" and "Overdue amount" terminology.
- [ ] `CHANGELOG.md` `[Unreleased]` billing entry uses the new terminology.
- [ ] `plans/README.md` status row for 029 flipped to DONE.

## STOP conditions

- Any billing sensor code references `_billing.py::overview_open_amount`
  or `overview_due_amount` with a NAME check (e.g., `if fn.__name__ ==
  "overview_open_amount"`). The helpers stay named; only the sensor-level
  wrappers rename. If a name check exists, adjust accordingly.
- `tests/test_sensor_billing.py` uses a shared fixture / helper for
  BOTH renamed classes that would double the rename churn. Fine to
  refactor the helper along with the rename; that's still in-scope.
- A CHANGELOG entry for `[0.12.x]` or earlier mentions "account
  balance" — do NOT rewrite historical entries. Frozen releases stay
  frozen.

## Maintenance notes

- Any future billing-related sensor (e.g., installment plan amount,
  pending online payment) should follow the "domain-noun" naming
  established here (`outstanding_balance`, `overdue_amount`), not
  the "account_balance_X" pattern.
- If ENGIE ever introduces a sensor that IS a true credit balance
  (money owed by ENGIE to the customer), THAT is where the name
  `account_balance` (or preferably `account_credit`) would fit —
  positive numbers meaning "money you have." Not applicable today.
- Reviewer should sanity-check that the entity ID slugs on the sensor
  device in the HA UI now read as `Outstanding balance` and
  `Overdue amount` for a fresh install — the friendly names come
  from strings.json, entity IDs come from the `key=` values.
