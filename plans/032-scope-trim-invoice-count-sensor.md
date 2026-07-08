# Plan 032: Scope-trim `EngieBeOpenInvoiceCountSensor` from plan 025

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report - do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat main..HEAD -- custom_components/engie_be/sensor.py custom_components/engie_be/strings.json plans/025-account-balance-invoices.md plans/README.md`
> If any of these changed materially since this plan was written, re-read
> the "Current state" excerpts against the live files before proceeding.

## Status

- **Priority**: P2 (spec-hygiene, blocks v0.13.0 GA sign-off)
- **Effort**: S (documentation-only, zero LoC change to `custom_components/`)
- **Risk**: NONE (no runtime code touched)
- **Depends on**: -
- **Category**: scope-trim / docs

## Why this matters

Plan 025 (`account-balance-invoices`) originally promised three sensor
classes at lines 111-113:

> `custom_components/engie_be/sensor.py` - three sensor classes:
> balance (MONETARY EUR), next-invoice-due (TIMESTAMP), invoice-count
> (numeric).

and detailed `EngieBeOpenInvoiceCountSensor` at plan 025 line 324:

> `EngieBeOpenInvoiceCountSensor` - Count of invoices in "open" or
> "overdue" state.

Only two of those three shipped in v0.13.0 (`outstanding_balance` +
`next_invoice_due`). Plan 029 added `overdue_amount` as a third billing
sensor, replacing the invoice-count in practice. The has-overdue binary
sensor from plan 025 was explicitly removed by plan 028.

Net result: `EngieBeOpenInvoiceCountSensor` was quietly dropped. The
`overdue_amount` scalar (EUR) covers the same intent (highlight that
something is past due) with a more useful state value, and reduces the
per-BAN entity count. Two-axis code-review flagged this as a spec gap.

This plan formalises the trim so a future audit does not resurface it.

## Current state

### `custom_components/engie_be/sensor.py`

Three billing sensor constants exist:

```
_BILLING_OUTSTANDING_BALANCE
_BILLING_OVERDUE_AMOUNT
_BILLING_NEXT_INVOICE_DUE
```

No `_BILLING_OPEN_INVOICE_COUNT`, no `EngieBeOpenInvoiceCountSensor`,
no `open_invoice_count` translation key in `strings.json`.

### `plans/README.md`

Row 025 is marked `DONE`. Rows 028 and 029 (which followed) do not
mention the invoice-count trim.

### `plans/025-account-balance-invoices.md`

Still contains the original scope with three sensors. No supersession
note.

### `README.md`

The "Billing" section (lines ~341-360) documents the three shipped
sensors correctly. No stale reference to invoice-count.

### `CHANGELOG.md`

`[Unreleased]` mentions the three shipped billing sensors. No stale
invoice-count entry.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Confirm code has no invoice-count | `grep -rn "invoice_count\|InvoiceCount" custom_components/engie_be/ tests/` | Zero matches |
| Confirm strings has no orphan | `grep -n "invoice_count" custom_components/engie_be/strings.json custom_components/engie_be/translations/` | Zero matches |
| Tests | `.venv/bin/pytest tests/ -q --tb=line` | Pass count unchanged |
| Ruff | `.venv/bin/ruff check custom_components/engie_be/ tests/` | Clean |

## Scope

**In scope**:

- `plans/025-account-balance-invoices.md` - add a supersession note at
  the top of the file describing what was dropped and why.
- `plans/README.md` - add row 032 with status DONE, keep row 025 as DONE.

**Out of scope**:

- Any change to `custom_components/engie_be/` code. The trim already
  landed silently. This plan only documents the outcome.
- Any change to `README.md` or `CHANGELOG.md` (they already reflect the
  shipped surface correctly).
- Reintroducing the sensor. That decision is closed.

## Git workflow

- Branch: `advisor/032-scope-trim-invoice-count`
- Commit style: `docs(plans): document scope-trim of invoice-count sensor from plan 025`
- One commit.

## Steps

### Step 1: Confirm the code has already dropped the sensor

Run the grep commands from the table above. All four must return zero
matches. If any match surfaces, STOP - the trim has not fully landed and
the assumption behind this plan is wrong.

### Step 2: Add a supersession note to `plans/025-account-balance-invoices.md`

Insert a block directly after the plan's `## Status` section (before
the "Why this matters" section):

```markdown
## Superseded scope (2026-07-09)

The original scope listed **three** billing sensors (balance,
next-invoice-due, invoice-count) plus one has-overdue binary sensor.
Only balance and next-invoice-due shipped in this plan. The invoice-count
sensor was dropped and the has-overdue binary was replaced:

- **has-overdue binary sensor**: removed by plan 028, superseded by the
  `overdue_amount` scalar sensor added in plan 029. The scalar carries
  strictly more information (EUR value, not just presence) and covers
  the same automation triggers via a numeric threshold.
- **`EngieBeOpenInvoiceCountSensor`**: dropped. `overdue_amount > 0` is
  the load-bearing automation signal. A separate count sensor adds an
  entity per BAN for a value that end-users do not surface in
  automations. Revisit only if a user asks for the count as a distinct
  signal (for example a dashboard tile).

The rest of this plan (API client, coordinator wiring, diagnostics,
tests, README section) shipped as written. See plans 028 and 029 for
the follow-up removals and renames.
```

**Verify**:

```
grep -n "Superseded scope" plans/025-account-balance-invoices.md
```

Expected: one match.

### Step 3: Add row 032 to `plans/README.md`

Insert after the row for plan 031:

```
| 032 | Scope-trim `EngieBeOpenInvoiceCountSensor` from plan 025 | P2 | S | 025, 028, 029 | DONE |
```

Note: the `Depends on` column intentionally lists 025 (source), 028
(binary removal), and 029 (rename). Use `-` characters, not em-dashes,
per repo convention.

**Verify**:

```
grep -n "^| 032 " plans/README.md
```

Expected: one match with status DONE.

### Step 4: Final verification

```
.venv/bin/pytest tests/ -q --tb=line
```

Expected: pass count unchanged from baseline. This plan does not touch
runtime code, so any test delta signals an unrelated regression.

```
grep -rn "invoice_count\|InvoiceCount\|OpenInvoiceCount" custom_components/engie_be/ tests/
```

Expected: zero matches.

```
git diff --stat
```

Expected: exactly two files changed - `plans/025-account-balance-invoices.md`
and `plans/README.md`.

## Test plan

No new tests. This is a documentation-only plan.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `plans/025-account-balance-invoices.md` contains a `## Superseded scope` section.
- [ ] `plans/README.md` has a row 032 marked DONE with `Depends on` = `025, 028, 029`.
- [ ] `grep -rn "invoice_count\|InvoiceCount" custom_components/engie_be/ tests/` returns zero matches.
- [ ] Pytest pass count unchanged from baseline.
- [ ] `git diff --stat` touches only `plans/025-account-balance-invoices.md` and `plans/README.md`.
- [ ] No em-dashes or semicolons in the added prose.

## STOP conditions

Stop and report back (do not improvise) if:

- Any grep for `invoice_count` / `InvoiceCount` / `OpenInvoiceCount`
  returns a hit anywhere under `custom_components/engie_be/` or `tests/`.
  This plan assumes the code trim already landed silently. A residual
  reference means the assumption is wrong and the code path needs to be
  fixed before documenting the outcome.
- Pytest count changes. Not a docs-only change - investigate before proceeding.
- The user changes their mind and asks for the sensor to ship after all.
  In that case discard this plan and open a new one that adds the sensor,
  strings, README row, and tests.

## Maintenance notes

- If a future user requests a count-of-open-invoices signal, prefer
  adding it as an attribute on the existing `outstanding_balance` sensor
  rather than a fourth entity.
- The scope-trim rationale (fewer entities per BAN, `overdue_amount`
  carries the signal) is the load-bearing argument. Update this note if
  that reasoning ever changes.
