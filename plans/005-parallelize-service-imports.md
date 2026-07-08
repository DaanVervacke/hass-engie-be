# Plan 005: Run multi-BAN service imports in parallel via asyncio.gather

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report — do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat d0652ec..HEAD -- custom_components/engie_be/__init__.py`
> If the file changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: performance
- **Planned at**: commit `d0652ec`, 2026-07-07

## Why this matters

`_handle_import_history` (the service handler for `engie_be.import_history`) loops over the resolved (entry, subentry) targets and `await`s each import serially. A user with three business agreements pointing the service at all three devices in a single call waits for Σ(N imports) instead of max(N imports). For long backfills (2+ years of hourly data), one BAN can take minutes; three serialized is 3× that. Fanning out with `asyncio.gather` cuts total wall time to the slowest single-BAN import. Setup-time imports already run as independent background tasks (via `entry.async_create_background_task` per subentry), so this only impacts the on-demand service action.

## Current state

### File

- `custom_components/engie_be/__init__.py` — the setup + service handlers.

### Excerpt (lines ~740-763)

```python
        for entry, subentry in _resolve_targets(
            hass, device_ids, "engie_be.import_history"
        ):
            ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
            LOGGER.debug(
                "import_history: dispatching to BAN ***%s title=%r",
                ban[-4:] if ban else "????",
                subentry.title,
            )
            # User-facing end_date is inclusive; the orchestrator (and the
            # underlying ENGIE endpoint) treat it as exclusive. Bump by one
            # day so picking 2026-04-15 imports through the 15th.
            api_end_date = end_date + timedelta(days=1) if end_date else None
            await async_import_usage_history(
                hass,
                entry.runtime_data.client,
                subentry,
                start_date=start_date,
                end_date=api_end_date,
                streams=streams,
            )
```

### Existing gather patterns to model on

The setup path already uses `asyncio.gather` for service-point EAN lookups:

- `__init__.py:904-907` — grep for `*(client.async_get_service_point(ean) for _, ean in flat_eans)` to see the pattern with `return_exceptions=True`.
- The immediately-following loop at `__init__.py:909` iterates over `zip(flat_eans, results, strict=True)` and handles `EngieBeApiClientError` per item.

Use the same shape here: `asyncio.gather(*coroutines, return_exceptions=True)` followed by a `for` loop that inspects each result.

### Existing imports in `__init__.py`

`asyncio` and `timedelta` are already imported (verify with `head -15 custom_components/engie_be/__init__.py`). No new imports required.

### Related but out of scope

- `_handle_clear_import_history` (the sibling service handler at line ~758+) also loops. It calls `async_clear_usage_history`, which queues a `ClearStatisticsTask` on the recorder. The recorder is single-threaded and serializes tasks internally, so parallelizing clear operations gains nothing meaningful. **Do NOT change `_handle_clear_import_history` in this plan** unless you find evidence to the contrary.

### Repo conventions to follow

- Errors per BAN should surface via `LOGGER.exception` with the masked BAN, not aborted-flow.
- Do not swallow `EngieBeApiClientAuthenticationError` silently — that indicates a token that needs reauth.
- Match the existing `_hash_ean` / masked-BAN log format (`***%s` with `ban[-4:]`).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Tests | `.venv/bin/pytest tests/ -q --tb=line` | all pass, 654+ tests |
| Focused test | `.venv/bin/pytest tests/test_init_services.py tests/test_init.py -q --tb=short` | all pass |
| Ruff | `.venv/bin/ruff check custom_components/engie_be/__init__.py tests/` | clean |
| Format check | `.venv/bin/ruff format --check .` | clean |
| Coverage gate | `.venv/bin/pytest tests/ -q --cov=custom_components.engie_be --cov-fail-under=95` | pass |

## Scope

**In scope** (files you may modify):

- `custom_components/engie_be/__init__.py` — only the `_handle_import_history` function body.
- `tests/test_init_services.py` — add / adjust tests for multi-BAN parallel dispatch.

**Out of scope** (do NOT touch):

- `_handle_clear_import_history` — recorder task queue serializes; parallelizing gains nothing.
- `async_import_usage_history` itself in `_statistics.py` — this refactor is at the caller.
- Setup-time background-task spawn logic — already runs per subentry in parallel via `entry.async_create_background_task`.
- The `_resolve_targets` helper — its ordering is intentional.
- The `+1 day` end-date shift — leave that logic exactly as it is.

## Git workflow

- Branch: `advisor/005-parallelize-imports`
- Commit style: `perf(services): run multi-BAN import_history in parallel`
- Do NOT push or open a PR unless the operator instructs.

## Steps

### Step 1: Refactor the loop into a gather + per-result loop

Target shape:

```python
        targets = list(_resolve_targets(
            hass, device_ids, "engie_be.import_history"
        ))
        api_end_date = end_date + timedelta(days=1) if end_date else None

        async def _run_one(
            entry: EngieBeConfigEntry, subentry: ConfigSubentry
        ) -> int:
            ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
            LOGGER.debug(
                "import_history: dispatching to BAN ***%s title=%r",
                ban[-4:] if ban else "????",
                subentry.title,
            )
            return await async_import_usage_history(
                hass,
                entry.runtime_data.client,
                subentry,
                start_date=start_date,
                end_date=api_end_date,
                streams=streams,
            )

        results = await asyncio.gather(
            *(_run_one(entry, subentry) for entry, subentry in targets),
            return_exceptions=True,
        )

        for (entry, subentry), result in zip(targets, results, strict=True):
            if isinstance(result, EngieBeApiClientAuthenticationError):
                # Reauth needs to happen; re-raising here would collapse the
                # whole service call. Log per-BAN so users see which one
                # failed, and let the coordinator's token-refresh timer
                # detect and start reauth on its own schedule.
                ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
                LOGGER.warning(
                    "import_history: authentication rejected for BAN ***%s; "
                    "reauth will be triggered by the next token refresh",
                    ban[-4:] if ban else "????",
                )
                continue
            if isinstance(result, BaseException):
                # Log the traceback and continue with the other BANs;
                # matching the existing service-points fan-out pattern.
                ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
                LOGGER.exception(
                    "import_history: unexpected error for BAN ***%s",
                    ban[-4:] if ban else "????",
                    exc_info=result,
                )
                continue
            # Successful import; rows_written not currently consumed here
            # (return value already used by the setup-time guarded path).
```

Notes:
- Use `strict=True` on the `zip` (matches the existing pattern at `__init__.py:909`).
- Preserve the debug log so operator visibility is unchanged.
- The `+1 day` shift is computed once before the gather, then reused. Keep this identical to the current inline computation to avoid off-by-one regressions.
- Type-hint the inner function's parameters using `EngieBeConfigEntry` and `ConfigSubentry` (both are already imported near the top of the file).

**Verify (compile-check only)**:

```
.venv/bin/ruff check custom_components/engie_be/__init__.py
```

Expected: clean.

### Step 2: Confirm the sibling `_handle_clear_import_history` is untouched

```
git diff -U0 custom_components/engie_be/__init__.py | grep -E "^\+|^-" | grep -c "clear_import_history"
```

Expected: `0`. If `_handle_clear_import_history` shows up in the diff, revert those hunks.

### Step 3: Update tests for parallel dispatch

Find the existing service-handler test that iterates multiple subentries. Check `tests/test_init_services.py` for tests that pass multiple `device_id`s. If a test exists that verifies serial ordering (unlikely but possible), replace the ordering assertion with a "both were called with correct args, order-independent" assertion.

Add a new test `test_import_history_dispatches_in_parallel_across_bans`:

- Set up an entry with two business-agreement subentries.
- Patch `custom_components.engie_be.async_import_usage_history` with an `AsyncMock` that returns 42.
- Call the service `engie_be.import_history` with both device IDs in the `target.device_id` list.
- Assert `mock_import.await_count == 2` and both subentries appear in `mock_import.await_args_list`.
- Order-independence: use `sorted()` on the BAN args before asserting equality.

Also add `test_import_history_continues_when_one_ban_fails`:

- Same setup, but `_fake_import` raises `EngieBeApiClientError("boom")` for one specific subentry and returns 42 for the other.
- Assert the successful BAN still recorded its call, and no exception propagates out of `hass.services.async_call(...)`.
- Assert `LOGGER.exception` was called (patch `LOGGER.exception` on the module or use `caplog`).

Use `caplog` (pytest fixture) to capture log records, filtering by logger name `custom_components.engie_be`.

**Verify**:

```
.venv/bin/pytest tests/test_init_services.py -q --tb=short
```

Expected: all existing tests pass, plus the 2 new ones.

### Step 4: Confirm the setup-time path is untouched

The setup-time background-task spawn at `_async_guarded_import` is a SEPARATE code path from `_handle_import_history`. This plan does NOT change the setup-time spawn. Grep to confirm:

```
git diff -U0 custom_components/engie_be/__init__.py | grep -E "^\+|^-" | grep -c "_async_guarded_import\|async_create_background_task"
```

Expected: `0`. If those show up, you accidentally modified the setup path; revert.

### Step 5: Full test suite + lint

```
.venv/bin/pytest tests/ -q --tb=line
.venv/bin/ruff check custom_components/engie_be/ tests/
.venv/bin/ruff format --check .
```

All three expected clean.

### Step 6: Restart the dev container and smoke-test (optional but recommended)

```
podman restart ha-plugin-test && until curl -sf -o /dev/null http://localhost:8123/; do sleep 2; done
```

Fire `engie_be.import_history` from Developer Tools with multiple device IDs. The persistent-notification pattern from setup-time is not used here (this is the service path), so the smoke test just confirms no crash + the imports both complete.

## Test plan

- Two new tests in `tests/test_init_services.py`:
  1. `test_import_history_dispatches_in_parallel_across_bans` — asserts both mock calls happen and args are correct.
  2. `test_import_history_continues_when_one_ban_fails` — one BAN raises, other succeeds, no exception escapes, LOGGER.exception fired.
- Follow the pattern in `tests/test_init_services.py::test_import_history_service_bumps_end_date_by_one_day` for the mock + service-call skeleton.
- Verification: `.venv/bin/pytest tests/test_init_services.py -q` — all pass, count includes the 2 new tests.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `grep -n "asyncio.gather" custom_components/engie_be/__init__.py` returns at least 2 lines (one existing at ~904 for service-points, one new for imports).
- [ ] `_handle_clear_import_history` is unchanged (`git diff` shows no hunks in that function).
- [ ] `_async_guarded_import` is unchanged.
- [ ] `.venv/bin/pytest tests/ -q --tb=line` — all pass, count is 656+ (2 new tests).
- [ ] `.venv/bin/ruff check custom_components/engie_be/ tests/` — clean.
- [ ] `.venv/bin/ruff format --check .` — clean.
- [ ] `.venv/bin/pytest tests/ -q --cov=custom_components.engie_be --cov-fail-under=95` — passes coverage gate.
- [ ] `git status` shows only `__init__.py` and `test_init_services.py` modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report back (do not improvise) if:

- The current loop shape (lines ~740-763) doesn't match the excerpt — the file has drifted.
- The setup-time `_async_guarded_import` path relies on serial execution semantics you weren't aware of (e.g., a shared mutable state across subentries). None known at the planned-at SHA, but flag it if you find one.
- A test in `test_init_services.py` breaks after the refactor. This should be a semantics-preserving change for the happy path; a break signals the fake-import used `side_effect` in an order-dependent way.
- `_resolve_targets` has become async or has been renamed. This plan assumes it's still a sync generator returning `(entry, subentry)` pairs.
- The `+1 day` end_date shift has moved (e.g., pushed into the orchestrator). If so, adapt the plan; don't apply the shift twice.

## Maintenance notes

- If ENGIE ever rate-limits the API per-account, `asyncio.gather` fan-out will still hit that limit; consider a bounded semaphore in that case. Not needed today.
- A future feature that shares state across BAN imports (e.g., a global progress bar) would need to serialize again. Don't introduce shared state through this call path.
- The setup-time background-task spawn already parallelizes — don't unify the two paths without a clear reason. Setup and on-demand have different failure semantics (setup uses persistent notifications + Repairs; service uses immediate return + `ServiceValidationError`).
