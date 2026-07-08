# Plan 006: Reuse the cached contracts payload in async_import_usage_history

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report — do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat d0652ec..HEAD -- custom_components/engie_be/_statistics.py custom_components/engie_be/__init__.py custom_components/engie_be/data.py`
> If any of these changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S-M
- **Risk**: LOW
- **Depends on**: none (but interacts with 005 — either landing order works)
- **Category**: performance
- **Planned at**: commit `d0652ec`, 2026-07-07

## Why this matters

`async_import_usage_history` in `_statistics.py` fetches the full contracts payload at the top of every invocation (line ~426), used for:
1. Division-based stream filtering (dropping streams the BAN has no contract for).
2. `earliest_contract_start_date` lookup for auto-mode window selection.

Meanwhile, `_async_populate_dynamic_flags` in `__init__.py:985` already fetches the same payload during setup and caches it on `EngieBeSubentryData.energy_contracts_payload` (see `data.py:95`). Every service-triggered import re-fetches contracts that are already in memory. On a multi-BAN service call, that's N extra HTTP round-trips before the actual usage-details work starts.

The fix: pass the cached payload through when it's available, and only fetch fresh when the cache is stale or missing. Cheap network + latency win; no functional change.

## Current state

### Files in scope

- `custom_components/engie_be/_statistics.py`
- `custom_components/engie_be/__init__.py`
- `custom_components/engie_be/data.py` (read-only reference for the cache field)

### Excerpts

`data.py:95` (the cache field):

```python
energy_contracts_payload: dict[str, Any] | None = field(default=None)
```

`__init__.py:985` (setup populates the cache):

```python
sub_data.energy_contracts_payload = result
```

`_statistics.py:371` (the orchestrator signature):

```python
async def async_import_usage_history(  # noqa: PLR0912, PLR0913, PLR0915 - orchestrator params + branches are all irreducible
    hass: HomeAssistant,
    client: EngieBeApiClient,
    subentry: ConfigSubentry,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    streams: frozenset[str] | None = None,
) -> int:
```

`_statistics.py:420-437` (the redundant fetch site):

```python
    # Fetch contracts once upfront - used both to filter streams by division
    # and (in auto mode) to determine the earliest contract start date.
    # include_inactive=True so a user who switched gas providers but kept ENGIE
    # electricity still gets the gas history imported for the inactive contract.
    contracts_payload: dict[str, Any] | None = None
    try:
        contracts_payload = await client.async_get_energy_contracts(
            business_agreement_number,
            include_inactive=True,
        )
    except EngieBeApiClientError as err:
        LOGGER.debug(
            "BAN ***%s: could not fetch energy contracts (%s); "
            "skipping division filter (fail-open)",
            masked_ban,
            err,
        )
```

### Sites that call `async_import_usage_history`

Grep confirms two: the setup-time `_async_guarded_import` in `__init__.py` and the service handler `_handle_import_history` (also `__init__.py`). Both have access to `entry.runtime_data.subentry_data[subentry.subentry_id].energy_contracts_payload`.

### Setup-cache freshness

`_async_populate_dynamic_flags` fetches contracts **without** `include_inactive=True` (default is `False`, so the setup cache uses `filter=ONLY_ACTIVE_ENERGY_CONTRACTS`). Verify with `grep -n "async_get_energy_contracts" custom_components/engie_be/__init__.py` — the setup call passes only the BAN, no `include_inactive`.

**Critical**: `async_import_usage_history` explicitly uses `include_inactive=True` because a BAN with an expired gas contract should still get the gas history imported. If we blindly pass the setup cache (which is active-only), we lose the historical-gas-on-inactive-contract case.

**Options**:

- **A. Change setup to fetch with `include_inactive=True`.** Then the cache is always "safe" to reuse. Small change in `__init__.py:_async_populate_dynamic_flags`; setup does slightly more work.
- **B. Add a separate cache field for the `include_inactive=True` payload.** More complex; two caches to manage.
- **C. Keep the fetch in the orchestrator; skip this plan.** Reject — that's the current state.

**Choose Option A.** The active vs. all difference is a few extra items in the response payload; the setup cost is negligible; the orchestrator becomes strictly cheaper on every subsequent call. `is_account_dynamic` — the current consumer of the cache during setup — is division-agnostic, so it doesn't care whether inactive contracts are included.

### Repo conventions

- `_statistics.py` is HA-free by convention (no `hass.data` access from inside it). Passing the cached payload as a kwarg preserves that.
- `include_inactive=True` semantics are already documented in `_statistics.py:420-424` comment.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Tests | `.venv/bin/pytest tests/ -q --tb=line` | all pass |
| Focused stats tests | `.venv/bin/pytest tests/test_statistics.py -q --tb=short` | all pass |
| Focused init tests | `.venv/bin/pytest tests/test_init.py tests/test_init_services.py -q --tb=short` | all pass |
| Ruff | `.venv/bin/ruff check custom_components/engie_be/ tests/` | clean |
| Format check | `.venv/bin/ruff format --check .` | clean |
| Coverage gate | `.venv/bin/pytest tests/ -q --cov=custom_components.engie_be --cov-fail-under=95` | pass |

## Scope

**In scope**:

- `custom_components/engie_be/_statistics.py` — add `contracts_payload` kwarg to `async_import_usage_history`, use it when passed.
- `custom_components/engie_be/__init__.py` — pass the cached payload from both call sites; also update `_async_populate_dynamic_flags` to fetch with `include_inactive=True`.
- `tests/test_statistics.py` — add tests for the new kwarg path.
- `tests/test_init.py` and/or `test_init_services.py` — assert the cache is threaded through.

**Out of scope**:

- Introducing a second cache field for one-vs-all contracts. Option A collapses to one cache.
- Changing the `is_account_dynamic` logic in `_contracts.py`. The `include_inactive=True` payload still exposes all division info correctly.
- Sneaking a cache invalidation strategy in. The cache lives for the lifetime of the entry; a full reload refreshes it. That's fine for now.
- Any refactor of `_async_populate_dynamic_flags` beyond the one-line `include_inactive=True` addition.

## Git workflow

- Branch: `advisor/006-reuse-cached-contracts`
- Commit style: `perf(_statistics): reuse cached energy-contracts payload`
- Do NOT push or open a PR unless the operator instructs.

## Steps

### Step 1: Update the setup-time fetch to include inactive contracts

Find the `async_get_energy_contracts` call inside `_async_populate_dynamic_flags` in `__init__.py`. Change from:

```python
result = await client.async_get_energy_contracts(ban)
```

to:

```python
result = await client.async_get_energy_contracts(ban, include_inactive=True)
```

Preserve everything else in the surrounding block (retry logic, the `if isinstance(result, dict)` guard, the cache-populate line at 985).

**Verify**:

```
grep -A2 "async_get_energy_contracts" custom_components/engie_be/__init__.py | head -20
```

Expected: the setup call now passes `include_inactive=True`.

### Step 2: Add `contracts_payload` kwarg to `async_import_usage_history`

Update the signature in `_statistics.py:371`:

```python
async def async_import_usage_history(  # noqa: PLR0912, PLR0913, PLR0915 - orchestrator params + branches are all irreducible
    hass: HomeAssistant,
    client: EngieBeApiClient,
    subentry: ConfigSubentry,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    streams: frozenset[str] | None = None,
    contracts_payload: dict[str, Any] | None = None,
) -> int:
```

Update the docstring block to explain: "``contracts_payload`` may be passed in by callers who have already fetched contracts for this BAN (with ``include_inactive=True``). When provided, the orchestrator skips its own fetch. When ``None``, the orchestrator fetches fresh."

### Step 3: Use the passed-in payload if present

Modify the fetch block at `_statistics.py:420-437`:

```python
    # Reuse a caller-provided contracts payload when available (setup and
    # service-action call sites already have this cached on
    # ``EngieBeSubentryData.energy_contracts_payload``). Fall back to a
    # fresh fetch when nothing was passed in.
    if contracts_payload is None:
        try:
            contracts_payload = await client.async_get_energy_contracts(
                business_agreement_number,
                include_inactive=True,
            )
        except EngieBeApiClientError as err:
            LOGGER.debug(
                "BAN ***%s: could not fetch energy contracts (%s); "
                "skipping division filter (fail-open)",
                masked_ban,
                err,
            )
```

**Verify**:

```
grep -n "contracts_payload is None\|await client.async_get_energy_contracts" custom_components/engie_be/_statistics.py
```

Expected: the orchestrator has both branches — reuse when passed in, fetch when not.

### Step 4: Thread the cache through from both call sites

**Site 1: setup-time (`_async_guarded_import` in `__init__.py`)**

Locate the `await async_import_usage_history(...)` call inside `_async_guarded_import`. Before the call, resolve the cached payload:

```python
subentry_data = entry.runtime_data.subentry_data.get(subentry.subentry_id)
contracts_payload = (
    subentry_data.energy_contracts_payload if subentry_data else None
)
```

Then pass `contracts_payload=contracts_payload` to the `async_import_usage_history` kwargs.

**Site 2: service handler (`_handle_import_history`)**

Same pattern. Inside the loop (or gather block from plan 005), resolve the cached payload per subentry before dispatching.

If plan 005 has landed and the code now uses `asyncio.gather` via an inner `_run_one` function, resolve `contracts_payload` inside `_run_one` too and pass it as a kwarg.

### Step 5: Update existing tests that mock `async_get_energy_contracts`

Many orchestrator tests mock the client's `async_get_energy_contracts` to return specific payloads. Those tests continue to work because when `contracts_payload=None` (default), the orchestrator still fetches. Grep to see the affected tests:

```
grep -n "async_get_energy_contracts" tests/test_statistics.py
```

For each test, decide:

- If the test is asserting the orchestrator handles a specific division/inactive scenario → keep the mock; do NOT pass `contracts_payload` in the test call. The default path (fetch) is still tested.
- If the test is asserting the orchestrator's behavior when the cache is fresh → this is a new path; add one or two new tests explicitly passing a `contracts_payload=` kwarg.

### Step 6: Add tests for the new pass-through path

Add to `tests/test_statistics.py`:

**Test A: `test_orchestrator_reuses_passed_in_contracts_payload`**

- Set up client with `async_get_energy_contracts` as an `AsyncMock` (default).
- Call `async_import_usage_history(..., contracts_payload={"items": [{"division": "ELECTRICITY", "status": "ACTIVE"}, ...]})` with a dual-fuel payload.
- Assert `client.async_get_energy_contracts.await_count == 0` (fetch was skipped).
- Assert `async_add_external_statistics` was called with the expected stream IDs.

**Test B: `test_orchestrator_still_fetches_when_no_payload_passed`**

- Same setup but do NOT pass `contracts_payload`.
- Assert `client.async_get_energy_contracts.await_count == 1`.

Also add to `tests/test_init.py`:

**Test C: `test_setup_import_reuses_cached_contracts_payload`**

- Follow the pattern of `test_setup_spawns_background_task_when_import_history_true`.
- Prime `entry.runtime_data.subentry_data[<id>].energy_contracts_payload` with a dual-fuel payload.
- Patch `async_import_usage_history` with an `AsyncMock` to capture kwargs.
- After setup, assert the mock was called with `contracts_payload={...}` (the dict primed above).

### Step 7: Full test suite + lint

```
.venv/bin/pytest tests/ -q --tb=line
.venv/bin/ruff check custom_components/engie_be/ tests/
.venv/bin/ruff format --check .
```

All three expected clean.

### Step 8: Restart the dev container and verify (optional but recommended)

```
podman restart ha-plugin-test && until curl -sf -o /dev/null http://localhost:8123/; do sleep 2; done
```

Then either:
- Wait for the next natural setup-time import (unlikely without config change), OR
- Manually clear stats for a BAN and trigger `engie_be.import_history`; watch `podman logs ha-plugin-test | grep async_get_energy_contracts` to confirm the ALL_ENERGY_CONTRACTS fetch fires ONCE at setup and NOT again during the import.

## Test plan

- **New tests (3 total):**
  - `test_orchestrator_reuses_passed_in_contracts_payload` — cache reuse.
  - `test_orchestrator_still_fetches_when_no_payload_passed` — no-cache-still-works.
  - `test_setup_import_reuses_cached_contracts_payload` — the setup call site threads the cache through.
- **Existing tests:** all `test_statistics.py` orchestrator tests should continue to pass unchanged; they exercise the `contracts_payload=None` path (default), which still fetches.
- Verification: `.venv/bin/pytest tests/ -q --tb=line` — all pass.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `_async_populate_dynamic_flags` in `__init__.py` calls `async_get_energy_contracts` with `include_inactive=True`.
- [ ] `async_import_usage_history` in `_statistics.py` has `contracts_payload: dict[str, Any] | None = None` in its kwargs.
- [ ] Grep shows the orchestrator only fetches when `contracts_payload is None`.
- [ ] Both call sites (`_async_guarded_import` and `_handle_import_history`) pass `contracts_payload=` when the cache is available.
- [ ] `.venv/bin/pytest tests/ -q --tb=line` — all pass, count is 657+ (3 new tests).
- [ ] `.venv/bin/ruff check custom_components/engie_be/ tests/` — clean.
- [ ] `.venv/bin/ruff format --check .` — clean.
- [ ] `.venv/bin/pytest tests/ -q --cov=custom_components.engie_be --cov-fail-under=95` — passes coverage.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report back (do not improvise) if:

- `EngieBeSubentryData.energy_contracts_payload` has been renamed or removed. That field is the whole premise of the plan.
- `_async_populate_dynamic_flags` no longer exists or has a materially different shape.
- Interaction with plan 005 (parallel service imports): if plan 005 has landed and the loop is now `asyncio.gather`, the cache lookup must happen inside `_run_one` (per-BAN), not outside. If unsure how to interleave the two, STOP and report — do not guess.
- Tests fail with a coverage drop below 95%. The new code paths are trivially covered by tests A and B; a drop indicates something else regressed.
- The setup cache is somehow empty on a live install for a BAN that has active contracts (would suggest a setup-order bug). Report the observation.

## Maintenance notes

- The cache is populated once at setup and never invalidated. If ENGIE ever adds/removes contracts on a running install without a reload, the orchestrator's filter would use stale data. Acceptable: contracts don't change under a running HA instance frequently. If it becomes a problem, add a lightweight refresh in the coordinator.
- Diagnostics (`diagnostics.py:173`) already surfaces `energy_contracts_payload`. No change needed there.
- If a future feature needs a different filter (e.g., only ACTIVE contracts for something), do NOT reuse this cache — add a new field.
