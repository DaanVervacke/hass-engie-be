# Plan 025: Account balance + open-invoices sensors (DIR-01)

> **Executor instructions**: This plan has a **SPIKE step 1** that
> captures the real endpoint payload before implementation. Do NOT
> skip the spike — endpoint schema details in this plan are inferred
> from the APK and MUST be confirmed against a live response before
> writing production code. STOP if the spike reveals a different shape.
> Update `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be`

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (new endpoint surface; billing data is sensitive → diagnostics-redaction discipline is critical)
- **Depends on**: 018 (soft-fail alignment) — the fourth flag-gated
  feature should follow the aligned convention. 022 (feature-flag
  scaffold extraction) is RECOMMENDED but not required.
- **Category**: direction (feature)
- **Planned at**: commit `85011b7`, 2026-07-08

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

## Why this matters

Every ENGIE customer has monthly invoices and an outstanding account
balance. Currently, users must open engie.be or the Smart App to
check. The integration already surfaces energy prices, historical
usage, and cost statistics; the natural forward-looking counterpart
is "what's my current balance and next invoice due date."

Concrete user value:

- `sensor.engie_belgium_{ban}_account_balance` (EUR, MONETARY device
  class) — headline figure users check most frequently
- `sensor.engie_belgium_{ban}_next_invoice_due` (TIMESTAMP device
  class) — enables "notify me 7 days before invoice due" automations
- `binary_sensor.engie_belgium_{ban}_has_overdue_invoice` — critical
  automation trigger for reminders

The endpoints exist and are grounded in the APK reverse-engineering
(prior audit finding DIR-01):

- `/api/engie/ms/billing/customer/v1/business-agreements/{ban}/account-balance`
- `/api/engie/ms/billing/customer/v1/business-agreements/{ban}/invoices`

## Current state

### APK-derived model names (from libapp.so)

```
AccountBalanceModel
AccountBalanceOverviewModel
AccountBalanceDetailsModel
AccountBalanceRecoveryModel
InvoiceModel
GetInvoicesResponseModel
PaymentConfigurationModel
CollectionAgencyModel  (indicates overdue collection state)
```

**Feature flag**: needs discovery. The prior APK survey did not
identify a specific `invoices-shown-dashboard`-style flag; assume the
endpoint is always available for authenticated customers and gate
sensor creation on "the endpoint returned data" rather than a flag.

### The integration already has

- Shared `_async_query_boolean_feature_flag` helper (if a flag is
  discovered during the spike, use this).
- The pattern for coordinator wrapper storage:
  `{"data": <payload>, "fetched_at": <ISO>}`, matches solar and TOU.
- Diagnostics `_summarise_*` helper pattern (`_summarise_solar_surplus`
  in `diagnostics.py`) — extend for `_summarise_billing`.
- `HAPPY_HOUR_BASE_URL` covers `energy-insights/customer/v1`. This
  feature needs a NEW base URL for `billing/customer/v1`; add a
  constant.

### Reference implementations to mirror

- `custom_components/engie_be/api.py::async_get_solar_surplus_forecasts` —
  API method style (single-line summary + multi-paragraph body,
  `_authenticated_headers()`, `_api_wrapper` invocation).
- `custom_components/engie_be/coordinator.py::_async_fetch_solar_surplus` —
  soft-fail-to-previous discipline.
- `tests/test_api_solar_surplus.py` — API test structure.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Target tests | `.venv/bin/pytest tests/test_api_billing.py tests/test_coordinator_billing.py tests/test_sensor_billing.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass, ≥95% |
| Spike: probe live endpoints | see Step 1 | JSON payload captured |

## Scope

**In scope**:
- `custom_components/engie_be/const.py` — new `BILLING_BASE_URL` constant.
- `custom_components/engie_be/api.py` — `async_get_account_balance(ban)`
  and `async_get_invoices(ban)`.
- `custom_components/engie_be/data.py` — new `has_billing_data: bool | None`
  field on `EngieBeSubentryData` (if a flag is discovered; otherwise
  gate on data presence).
- `custom_components/engie_be/coordinator.py` — fetch both endpoints per
  refresh, store under `coordinator.data["billing"]` (a merged wrapper
  containing both `account_balance` and `invoices` sub-objects). Soft-
  fail-to-previous discipline. Auth escalate.
- `custom_components/engie_be/sensor.py` — three sensor classes:
  balance (MONETARY EUR), next-invoice-due (TIMESTAMP), invoice-count
  (numeric).
- `custom_components/engie_be/binary_sensor.py` — one class:
  has-overdue-invoice.
- `custom_components/engie_be/diagnostics.py` — `_summarise_billing`
  helper that redacts amounts (keeps count + status only) and hashes
  invoice IDs.
- `custom_components/engie_be/strings.json` + `translations/en.json` —
  entity names.
- `tests/fixtures/billing_typical.json` (new, sanitized from spike output),
  `tests/fixtures/billing_overdue.json` (synthetic edge case).
- `tests/test_api_billing.py`, `tests/test_coordinator_billing.py`,
  `tests/test_sensor_billing.py`, `tests/test_binary_sensor_billing.py`
  (new).
- `tests/test_diagnostics.py` — extend with billing summariser tests.
- `tests/conftest.py` — add mock methods to `_make_client` in
  `test_init.py`.
- `README.md` — add billing section.
- `CHANGELOG.md` — Unreleased entry.

**Out of scope**:
- Invoice detail line items (per-kWh breakdown) — deferred.
- SEPA mandate management — deferred (the APK has it under a separate
  `package:mandate/`; requires user redirects).
- Auto-pay / online payment endpoints — deferred; write actions require
  more careful design.
- Multi-account aggregation — account balance is per-BAN and stays
  per-BAN in HA.

## Steps

### Step 1: SPIKE — capture the real payload

**Do this first. Do not skip.** Endpoint schema in the sections below
is inferred from Flutter model names; the wire format needs
confirmation.

Extract a fresh access token from the running HA container:

```bash
TOKEN=$(podman exec ha-plugin-test cat /config/.storage/core.config_entries \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(next(e for e in d['data']['entries'] if e['domain']=='engie_be')['data']['access_token'])")
BAN=002209795515  # replace with a real BAN on the account

curl -s "https://api.engie.be/engie/ms/billing/customer/v1/business-agreements/$BAN/account-balance" \
  -H "authorization: Bearer $TOKEN" \
  -H "user-agent: engie-smart-release android 4.21.0.926" \
  -H "accept: application/json" | tee /tmp/engie-account-balance.json | python3 -m json.tool

curl -s "https://api.engie.be/engie/ms/billing/customer/v1/business-agreements/$BAN/invoices" \
  -H "authorization: Bearer $TOKEN" \
  -H "user-agent: engie-smart-release android 4.21.0.926" \
  -H "accept: application/json" | tee /tmp/engie-invoices.json | python3 -m json.tool
```

If HTTP 404 or 403 → the endpoint URL is wrong. Try variants:
`/billing/customer/v2/...`, `/billing-overview/customer/v1/...`. Do
NOT proceed to Step 2 until both endpoints return a 200 with parseable
JSON.

**Record the actual payload shape in your plan-execution notes.**
Include: top-level keys, currency field, timestamp format for due
dates, how "overdue" is encoded, how "paid" vs "open" is encoded,
whether amounts are strings or floats, whether the response contains
PII (e.g., customer name, address) that must be redacted.

**Sanitize and save fixtures**:

```bash
python3 << 'PY'
import json, re, sys, pathlib
for src, dst in [
    ("/tmp/engie-account-balance.json", "tests/fixtures/billing_typical.json"),
]:
    data = json.loads(pathlib.Path(src).read_text())
    # Redact any string that looks like a BAN, EAN, invoice number,
    # customer ID, or address. Below is a starting set; expand based
    # on the actual payload.
    text = json.dumps(data, indent=2)
    text = re.sub(r'\b\d{12}\b', '000000000000', text)          # 12-digit BANs
    text = re.sub(r'\b54144\d{13}\b', '5414400000000000000', text)  # EANs
    pathlib.Path(dst).write_text(text)
    print(f"Wrote {dst}")
PY
```

STOP condition: if the payload contains sensitive PII (customer name,
IBAN, address, email) that cannot be surgically redacted without
losing schema fidelity, DO NOT store the raw payload. Hand-author a
minimal synthetic fixture matching the field structure.

### Step 2: Add the endpoint constant + API methods

`const.py`:

```python
# Billing customer service (invoices, account balance).
BILLING_BASE_URL = "https://api.engie.be/engie/ms/billing/customer/v1"
```

`api.py` (after `async_get_solar_surplus_shown_dashboard_flag`):

```python
    async def async_get_account_balance(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """
        Fetch the current account balance for a business agreement.

        Returns the parsed JSON response. Shape confirmed via spike
        capture on <YYYY-MM-DD> against BAN <redacted>:
        <PASTE ACTUAL SHAPE FROM STEP 1 HERE>

        Amounts are in EUR. Sensitive per-invoice details are gated
        behind the /invoices endpoint; this endpoint returns only the
        aggregate balance.
        """
        ban = business_agreement_number.replace(" ", "")
        url = f"{BILLING_BASE_URL}/business-agreements/{ban}/account-balance"
        headers = self._authenticated_headers()
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            json_response=True,
        )

    async def async_get_invoices(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """
        Fetch the list of invoices for a business agreement.

        Returns the parsed JSON response. Shape confirmed via spike:
        <PASTE ACTUAL SHAPE FROM STEP 1 HERE>
        """
        ban = business_agreement_number.replace(" ", "")
        url = f"{BILLING_BASE_URL}/business-agreements/{ban}/invoices"
        headers = self._authenticated_headers()
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            json_response=True,
        )
```

### Step 3: Coordinator wiring

Add a new block in `_async_update_data` after the TOU block (the
"noqa: PLR0912, PLR0915" markers already exist).

Fetch both endpoints unconditionally per refresh (no feature flag
found in the APK survey). Merge into a single wrapper:

```python
        # Fetch billing data (account balance + invoices).
        previous_billing_wrapper: dict[str, Any] | None = None
        if isinstance(self.data, dict):
            existing = self.data.get("billing")
            if isinstance(existing, dict):
                previous_billing_wrapper = existing

        billing_wrapper = await self._async_fetch_billing(
            client,
            business_agreement_number,
            previous_billing_wrapper,
        )
        if billing_wrapper is not None:
            data["billing"] = billing_wrapper
```

Add the helper `_async_fetch_billing` following the shape of
`_async_fetch_solar_surplus`. Auth errors escalate; transient errors
soft-fail to previous wrapper. Merge both endpoint responses:

```python
{
    "data": {
        "account_balance": <balance payload>,
        "invoices": <invoices payload>,
    },
    "fetched_at": <ISO-UTC>,
}
```

If either endpoint fails but the other succeeds, keep the successful
one and reuse the previous value for the failing one — do NOT fail
the whole wrapper on a partial outage.

### Step 4: Sensor + binary sensor classes

Non-EAN entities go on the subentry device directly (no per-EAN slug),
following the `EngieBeHappyHourMonthSensor` pattern (see `sensor.py`
around line ~700).

Sensors:

- `EngieBeAccountBalanceSensor(EngieBeEntity, SensorEntity)`
  - `device_class = MONETARY`, `native_unit_of_measurement = "EUR"`
  - `state_class = SensorStateClass.TOTAL` (balance can go negative
    on credit)
  - Reads `coordinator.data["billing"]["data"]["account_balance"][<currency-amount-field>]`
    — exact field name comes from Step 1's spike.
- `EngieBeNextInvoiceDueSensor(EngieBeEntity, SensorEntity)`
  - `device_class = TIMESTAMP`
  - Reads the next-due invoice from the invoices list, filtered to
    unpaid.
- `EngieBeOpenInvoiceCountSensor(EngieBeEntity, SensorEntity)`
  - `device_class = None`, `state_class = MEASUREMENT`
  - Count of invoices in "open" or "overdue" state.

Binary sensor:

- `EngieBeHasOverdueInvoiceBinarySensor(EngieBeEntity, BinarySensorEntity)`
  - `device_class = PROBLEM`
  - `is_on = True` if any invoice is overdue (past due date and not
    paid).

Gate all four on `coordinator.data.get("billing")` being present.

### Step 5: Diagnostics summariser

Add `_summarise_billing(wrapper)` in `diagnostics.py`. Return:

```python
{
    "has_data": True/False,
    "fetched_at": wrapper.get("fetched_at"),
    "invoice_count": int,
    "open_invoice_count": int,
    "overdue_invoice_count": int,
    "invoice_ids_hashed": [_hash_ean(i["id"]) for i in invoices],
    # NEVER include: actual balance amount, invoice PDF URLs,
    # customer address, IBAN, or bank details.
}
```

Follow the exact pattern of `_summarise_solar_surplus`. Add tests to
`test_diagnostics.py` asserting no raw amount, no raw invoice ID.

### Step 6: Tests

Create four test files following the solar-surplus / TOU pattern:

- `test_api_billing.py` — URL builder + BAN whitespace stripping
- `test_coordinator_billing.py` — happy path, partial-failure
  soft-fail, auth escalation
- `test_sensor_billing.py` — value extraction for each sensor
- `test_binary_sensor_billing.py` — overdue detection

Use `pytest.mark.billing` marker if consistent with the plan-020 test-
fixture cleanup. Register the marker in `conftest.py`.

### Step 7: Strings + translations

Entity names in `strings.json`, then `cp` to `translations/en.json`.
Model after existing entries.

### Step 8: README + CHANGELOG

README: add "Billing" section describing the four entities.

CHANGELOG: `[Unreleased]` Added entry.

### Step 9: Devcontainer smoke test

```bash
podman restart ha-plugin-test
sleep 12
podman logs --since 15s ha-plugin-test 2>&1 | grep -iE "billing|invoice|account.balance|ERROR" | tail -20
```

Expected: real HTTP GETs to the billing endpoints, no errors, sensor
entities appearing in the entity registry.

### Step 10: Full gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

- Spike + fixture capture (Step 1)
- 3 API request-shape tests
- 5 coordinator tests (happy path, partial failure, full failure with
  previous, auth on balance, auth on invoices)
- 4 sensor unit tests (one per class × value/None)
- 3 binary sensor tests (has-overdue True/False/absent)
- 2 diagnostics tests (privacy assertions)

## Done criteria

- [ ] `tests/fixtures/billing_typical.json` exists and contains no
      unredacted BAN, EAN, IBAN, or customer name.
- [ ] `grep 'account-balance\|invoices' custom_components/engie_be/api.py` returns 2 matches.
- [ ] `grep 'BILLING_BASE_URL' custom_components/engie_be/const.py` returns 1 match.
- [ ] `grep 'EngieBeAccountBalanceSensor\|EngieBeNextInvoiceDueSensor\|EngieBeOpenInvoiceCountSensor' custom_components/engie_be/sensor.py` returns at least 3 matches.
- [ ] `.venv/bin/pytest tests/ -q --cov-fail-under=95` passes.
- [ ] Devcontainer smoke test shows both endpoints hit successfully.
- [ ] `README.md` has a "Billing" section.
- [ ] `CHANGELOG.md` has an Unreleased Added entry.
- [ ] `plans/README.md` status row for 025 flipped to DONE.

## STOP conditions

- **Spike returns unexpected shape**: the payload doesn't match the
  inferred structure (no `balance` field, no `invoices` list at root,
  or requires different auth headers). STOP and update the plan with
  the real shape before proceeding.
- **Endpoint returns 404 for the test BAN**: it might be gated on
  contract type (some Belgian tariffs might not have this endpoint).
  Test with a different BAN or investigate whether a feature flag
  exists.
- **Payload contains PII that cannot be sanitized**: DO NOT commit the
  fixture. Hand-author a synthetic fixture matching the shape.
- **Ambiguity between "open" and "overdue" invoice states**: request
  clarification before implementing the overdue binary sensor. A
  wrong "overdue: True" alert is worse than not having the sensor.
- **Endpoint requires a scope/authorization the current OAuth token
  doesn't have**: STOP and report — this becomes a config-flow scope
  extension, not a plan-025 task.

## Maintenance notes

- Currency: assume EUR for Belgium; the model does not need to be
  multi-currency. Hard-code `EUR` in the sensor unit.
- Time zones: invoice due dates come as ISO 8601 with offset.
  `SensorDeviceClass.TIMESTAMP` handles this natively.
- Refresh cadence: invoices change monthly at most. The default 60-min
  coordinator interval is generous. Do NOT add a separate lower-freq
  poll for billing — the coordinator does one refresh per interval
  regardless.
- Sensitive data: reviewer should scrutinize every log line, exception
  message, and diagnostics field for accidental amount / invoice-ID
  leakage.
- Future extension: if user demand appears, add per-invoice sensors
  behind an options-flow toggle. Do NOT do it by default — 12
  invoices/year × N accounts × 3 fields = entity spam.
