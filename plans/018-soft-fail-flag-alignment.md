# Plan 018: Align TOU flag-probe soft-fail default with solar-surplus (fail-open)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report — do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be/coordinator.py tests/test_coordinator_tou.py`
> If either file changed since this plan was written, compare the
> "Current state" excerpts against the live file; on a mismatch, treat
> it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

The Solar Surplus and TOU features are meant to behave symmetrically per
the design decision the maintainer made this session ("TOU should behave
like solar-surplus"). Both share the same feature-flag helper, the same
skip-fetch-when-off semantics, and the same reload-on-flip mechanic.

They diverge in exactly one place: the soft-fail default when the
feature-flag endpoint itself is unreachable.

- **Solar Surplus** (`coordinator.py::_async_fetch_solar_flag`): returns
  `True` on transient error — "keep trying, the per-EAN fetch has its
  own soft-fail." Fail-open.
- **TOU** (`coordinator.py::_async_fetch_tou_flag`): returns `False` on
  transient error — "assume not enrolled." Fail-closed.

Concrete user impact: during a feature-flags outage (rare but observed),
solar-surplus sensors keep serving the last-known wrapper, while TOU
sensors disappear the next refresh cycle. Same integration, same account,
opposite user experience.

The fix is one line + one test flip. Pick fail-open (matches
solar-surplus, matches Home Assistant's general "keep serving stale over
going unavailable" preference for the coordinator layer) and align.

## Current state

### File

- `custom_components/engie_be/coordinator.py` — contains both methods.

### Excerpt (lines 720-757, `_async_fetch_tou_flag`)

```python
    async def _async_fetch_tou_flag(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
    ) -> bool:
        """
        Probe the ``dgo-tou-is-active`` feature flag.

        Returns ``True``/``False`` based on the flag's ``value`` field.
        Auth failures escalate to reauth. Any other failure soft-fails
        to ``False`` (the "assume not enrolled" side) so a transient
        feature-flag outage never falsely creates TOU-active entities for
        accounts that are not TOU-billed.
        """
        try:
            flags = await client.async_get_dgo_tou_is_active_flag(
                business_agreement_number,
            )
        except EngieBeApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from exception
        except EngieBeApiClientError as exception:
            LOGGER.warning(
                "Failed to fetch TOU feature flag for BAN %s, "
                "assuming disabled and continuing: %s",
                mask_identifier(business_agreement_number),
                exception,
            )
            return False
        value = flags.get("value") if isinstance(flags, dict) else None
```

### Reference — solar surplus counterpart at `coordinator.py::_async_fetch_solar_flag`

Same shape, `return True` on the `except EngieBeApiClientError` branch,
docstring: "soft-fails to ``True`` (the 'keep trying' side) so a
transient feature-flag outage does not silently strip surplus entities
from accounts that actually have them; the per-EAN fetch itself will
then soft-fail on the same outage and preserve the last-known wrapper."

### Test file

- `tests/test_coordinator_tou.py` — contains
  `test_flag_probe_error_soft_fails_to_disabled` (or similarly named,
  verify by grep). It currently asserts `is_tou_active is False` after
  a transient flag-probe error.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Target tests | `.venv/bin/pytest tests/test_coordinator_tou.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov=custom_components.engie_be --cov-fail-under=95` | pass, ≥95% |

## Scope

**In scope**:
- `custom_components/engie_be/coordinator.py` — `_async_fetch_tou_flag`
- `tests/test_coordinator_tou.py` — the one test that asserts on the
  soft-fail behavior; needs renaming + inverted assertion.

**Out of scope**:
- Solar-surplus flag path (already fail-open; leave alone).
- Happy-hours enrollment flag path — different semantics (it caches the
  last-known enrolment, not fail-open/closed). Do not touch.
- `is_tou_active` field or its downstream sensor gating.

## Steps

### Step 1: Flip the TOU flag soft-fail default

In `_async_fetch_tou_flag`, change:

- Docstring: `soft-fails to ``False`` (the "assume not enrolled" side)`
  → `soft-fails to ``True`` (the "keep trying" side) — matches
  solar-surplus discipline: the per-EAN /tou-schedules fetch has its
  own soft-fail, so a transient flag-endpoint outage should not strip
  TOU entities from customers who are legitimately TOU-billed.`
- Warning message: `"assuming disabled and continuing"` →
  `"assuming enabled and continuing"`.
- `return False` → `return True`.

Everything else stays.

**Verify**: `.venv/bin/ruff check custom_components/engie_be/coordinator.py` → exit 0.

### Step 2: Update the test

Locate the existing test in `tests/test_coordinator_tou.py` that
exercises the transient-flag-error path. Grep for `EngieBeApiClientError`
in a `tou_flag=` position:

```bash
grep -n "tou_flag=EngieBeApiClientError\|soft_fails_to_disabled\|soft.fail" tests/test_coordinator_tou.py
```

Rename and invert:

- If the test is `test_flag_probe_error_soft_fails_to_disabled` (or
  similar), rename to `test_flag_probe_error_soft_fails_to_enabled`.
- Flip the assertion from `sub_data.is_tou_active is False` to
  `sub_data.is_tou_active is True`.
- If the test currently also asserts
  `client.async_get_tou_schedules.assert_not_awaited()`, flip to
  `client.async_get_tou_schedules.assert_awaited_once()` — because
  fail-open means the fetch DOES run.
- Update the test docstring accordingly.

**Verify**: `.venv/bin/pytest tests/test_coordinator_tou.py -v` → all pass.

### Step 3: Full gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

One existing test in `tests/test_coordinator_tou.py` needs its
assertion flipped and its name updated. No new tests needed — the flip
IS the semantic change; the coverage was already correct for the
old behavior.

If time permits, add one paranoid extra test asserting the fetch DID
run (`client.async_get_tou_schedules.assert_awaited_once()`) even though
the flag probe failed — this documents the new intent explicitly.

## Done criteria

- [ ] `grep -n "return False" custom_components/engie_be/coordinator.py` under `_async_fetch_tou_flag` returns no match (only `return bool(value)` at the bottom).
- [ ] `grep "assuming enabled" custom_components/engie_be/coordinator.py` returns at least two matches (solar + TOU now say the same thing).
- [ ] Test in `test_coordinator_tou.py` for transient flag error asserts `is_tou_active is True` and `async_get_tou_schedules.assert_awaited_once()`.
- [ ] `.venv/bin/pytest tests/ -q --cov-fail-under=95` passes.
- [ ] No files outside "In scope" modified.
- [ ] `plans/README.md` status row for 018 flipped to DONE.

## STOP conditions

- The transient-flag-error test isn't in `test_coordinator_tou.py` under
  the name assumed — investigate before renaming.
- Any solar-surplus test breaks after the change — should be impossible
  (solar code untouched), but if so, report immediately.

## Maintenance notes

- Once this lands, the three flag-gated features have consistent
  soft-fail policy: fail-open on the flag probe, let the endpoint's own
  soft-fail preserve the last-known wrapper. This is documented as the
  intended discipline; any future flag-gated feature (see planned
  DIR-01) MUST follow the same rule.
- If the maintainer later wants a fail-closed feature (e.g., for a
  billing-critical flag where showing stale data would confuse
  customers), that becomes an explicit exception that must be
  documented in the docstring, not a silent policy divergence.
