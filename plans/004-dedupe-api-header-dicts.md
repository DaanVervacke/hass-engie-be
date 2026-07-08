# Plan 004: Extract a helper for the repeated API header dict in api.py

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat d0652ec..HEAD -- custom_components/engie_be/api.py`
> If the file changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `d0652ec`, 2026-07-07

## Why this matters

`api.py` has ~8 endpoint methods that each construct the same header dict:

```python
{
    "User-Agent": USER_AGENT_NATIVE,  # or USER_AGENT_BROWSER
    "Accept": "application/json, application/problem+json",
    "authorization": f"Bearer {self.access_token}",
    "x-trace-id": str(uuid.uuid4()),
}
```

Any header change (adding a version header, switching x-trace-id format, updating Accept) requires touching all 8 sites, with real risk of inconsistency. This is a mechanical refactor that shrinks the file, kills the risk, and needs no test rewrites because tests mock `_api_wrapper`, not the header dict.

## Current state

### File

- `custom_components/engie_be/api.py` — ~1871 LoC HTTP client with per-endpoint methods.

### Sites to refactor (verified via `grep -n "x-trace-id\|User-Agent.*USER_AGENT" custom_components/engie_be/api.py`)

Header blocks live around lines: `817`, `848`, `882`, `919`, `960`, `998`, `1039`, `1085`. Each is a 5-line dict literal at the top of an `async def async_get_*` method.

Representative example around line 880-890:

```python
url = f"{ACCOUNTS_BASE_URL}/customer-account-relations"
headers = {
    "User-Agent": USER_AGENT_NATIVE,
    "Accept": "application/json, application/problem+json",
    "authorization": f"Bearer {self.access_token}",
    "x-trace-id": str(uuid.uuid4()),
}
return await self._api_wrapper(
    session=self._session,
    method="GET",
    url=url,
    headers=headers,
    params={"withBusinessAgreements": "SMART_APP"},
    json_response=True,
)
```

### Constants already available

At the top of `api.py` (verify with `grep -n "^USER_AGENT\|^ACCEPT\|import uuid" custom_components/engie_be/api.py`):

- `USER_AGENT_BROWSER` — used by web-endpoint sites
- `USER_AGENT_NATIVE` — used by native-app endpoint sites
- `uuid` — imported at the top

### Which sites use which user agent

- `USER_AGENT_BROWSER`: verify with `grep -n "USER_AGENT_BROWSER" custom_components/engie_be/api.py` (typically web-flow paths like `www.engie.be`).
- `USER_AGENT_NATIVE`: the rest.

Do NOT swap user agents. Preserve which site uses which.

### Repo conventions to follow

- Private helper methods on the class use a leading underscore.
- Type hints on every method signature.
- No em-dashes, no AI-tell prose in comments.
- Match the existing docstring style in `api.py`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Tests | `.venv/bin/pytest tests/ -q --tb=line` | all pass |
| Ruff | `.venv/bin/ruff check custom_components/engie_be/api.py` | `All checks passed!` |
| Format check | `.venv/bin/ruff format --check custom_components/engie_be/api.py` | already formatted |
| Line count | `wc -l custom_components/engie_be/api.py` | before: ~1871; after: fewer |
| Verify no header-dict remnants | `grep -c "\"x-trace-id\": str(uuid.uuid4())" custom_components/engie_be/api.py` | `1` (only inside the helper) |

## Scope

**In scope** (only file you may modify):

- `custom_components/engie_be/api.py`

**Out of scope**:

- `tests/test_api*.py` — the test suite mocks `_api_wrapper` and never asserts on the header dict directly. If you find yourself editing tests, STOP.
- Any file outside `custom_components/engie_be/api.py`.
- Changing which endpoints use `USER_AGENT_BROWSER` vs `USER_AGENT_NATIVE`. This is a mechanical dedup — preserve semantics exactly.
- The auth flow methods (around `async_start_authentication`, `async_refresh_token`) — they use different header shapes and different lifecycle. Leave alone.

## Git workflow

- Branch: `advisor/004-api-headers-dedup`
- Commit style: `refactor(api): extract _authenticated_headers helper`
- Do NOT push or open a PR unless the operator instructs.

## Steps

### Step 1: Add the helper method

Place a private method on the `EngieBeApiClient` class, near the other private helpers (near `_api_wrapper`). Target shape:

```python
def _authenticated_headers(
    self,
    user_agent: str = USER_AGENT_NATIVE,
) -> dict[str, str]:
    """
    Return the standard authenticated JSON header dict.

    Used by every ENGIE endpoint that requires a Bearer token. The
    ``x-trace-id`` is fresh per call so support requests can correlate
    a single HTTP round-trip. Auth flow methods use custom header dicts
    and do not go through this helper.
    """
    return {
        "User-Agent": user_agent,
        "Accept": "application/json, application/problem+json",
        "authorization": f"Bearer {self.access_token}",
        "x-trace-id": str(uuid.uuid4()),
    }
```

**Verify**:

```
grep -n "_authenticated_headers" custom_components/engie_be/api.py
```

Expected: the method definition appears once, near `_api_wrapper`.

### Step 2: Replace each header-dict site

For each of the 8 sites listed in "Current state", replace:

```python
headers = {
    "User-Agent": USER_AGENT_NATIVE,  # or USER_AGENT_BROWSER
    "Accept": "application/json, application/problem+json",
    "authorization": f"Bearer {self.access_token}",
    "x-trace-id": str(uuid.uuid4()),
}
```

with:

```python
headers = self._authenticated_headers()
# OR (for USER_AGENT_BROWSER sites)
headers = self._authenticated_headers(user_agent=USER_AGENT_BROWSER)
```

Work one method at a time. After each edit, run `.venv/bin/ruff check custom_components/engie_be/api.py` — a clean pass after each site is a signal you did not accidentally break syntax.

**Do NOT** change the `headers` variable name. Downstream code uses `headers=headers` in the `_api_wrapper` call; keeping the name identical keeps the diff minimal.

**Verify (after each site)**:

```
.venv/bin/ruff check custom_components/engie_be/api.py
```

Expected: clean.

**Verify (once all sites are done)**:

```
grep -c "\"x-trace-id\": str(uuid.uuid4())" custom_components/engie_be/api.py
```

Expected: `1` — the string now only appears in the helper.

```
grep -c "\"Accept\": \"application/json, application/problem" custom_components/engie_be/api.py
```

Expected: `1`.

If either is > 1, at least one site was missed. Grep for the sites and find the missed one.

### Step 3: Sanity-check that no non-authenticated endpoint uses the helper

Verify with `grep -n "async_start_authentication\|async_refresh_token\|async_get_epex_prices" custom_components/engie_be/api.py` — read those methods' header dicts to confirm they still look right. Auth-flow endpoints often have a different shape (no bearer token, custom `x-trace-id` handling, form-encoded bodies). Do NOT touch those; they're intentionally out of scope. Confirm they still compile and pass tests.

### Step 4: Run the full test suite

```
.venv/bin/pytest tests/ -q --tb=line
```

Expected: all 654+ tests pass. This is a semantics-preserving refactor; any test failure signals a regression.

### Step 5: Lint + format

```
.venv/bin/ruff check custom_components/engie_be/ tests/
.venv/bin/ruff format --check .
```

Expected: both clean.

## Test plan

- **No new tests.** This is a mechanical semantics-preserving refactor. The test suite already mocks `_api_wrapper` and covers all API endpoint code paths.
- **Verification**: the existing test suite (`tests/test_api*.py`, especially `test_api_getters.py`, `test_api_peaks.py`, `test_api_contracts.py`, `test_api_epex.py`) must pass unchanged.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `_authenticated_headers` method exists in `api.py` (grep confirms).
- [ ] `grep -c "\"x-trace-id\": str(uuid.uuid4())" custom_components/engie_be/api.py` returns `1`.
- [ ] `grep -c "\"Accept\": \"application/json, application/problem" custom_components/engie_be/api.py` returns `1`.
- [ ] `.venv/bin/pytest tests/ -q --tb=line` — all pass.
- [ ] `.venv/bin/ruff check custom_components/engie_be/ tests/` — clean.
- [ ] `.venv/bin/ruff format --check .` — clean.
- [ ] `wc -l custom_components/engie_be/api.py` — line count decreased by roughly 24 (8 sites * 3-4 lines removed per site, minus ~12 lines added for the helper).
- [ ] No file other than `api.py` (and `plans/README.md`) is modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report back (do not improvise) if:

- The current header-dict shape at any of the sites listed in "Current state" no longer matches the excerpt (e.g. an extra header like `X-API-Version` was added since the plan was written). Report so the helper signature can be extended.
- The number of sites you find is not 8. If it's more, someone added new endpoints; extend the plan. If it's fewer, an earlier commit already partially deduped; extend cautiously.
- A test fails after the refactor. This is a semantics-preserving change; failure means the refactor was not actually semantics-preserving.
- You need to change which `USER_AGENT_*` a site uses. That is not this plan's scope.

## Maintenance notes

- Any future endpoint that needs a Bearer token should call `self._authenticated_headers()` instead of building its own dict.
- If ENGIE ever requires a per-endpoint header (e.g. `X-Locale` for one specific endpoint), extend the helper with an `extra: dict[str, str] | None` kwarg rather than reverting to inline dicts.
- The auth-flow endpoints intentionally do not use this helper. If someone later "cleans up" and migrates them, expect the OAuth token exchange to break because those methods build a different auth shape.
