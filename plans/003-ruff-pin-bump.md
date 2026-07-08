# Plan 003: Bump ruff pin from 0.14.14 to 0.15.x

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report — do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat d0652ec..HEAD -- requirements.txt .ruff.toml`
> If either file changed since this plan was written, compare the
> "Current state" excerpts against the live file before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 001 (doc drift — after bump the CLAUDE.md line about `ruff 0.14.14` needs updating too, so 001 needs to be done or the bump needs to be reflected there in the same session)
- **Category**: dependencies
- **Planned at**: commit `d0652ec`, 2026-07-07

## Why this matters

`requirements.txt` pins `ruff==0.14.14`, released early 2025. The 0.15 line landed months ago with lint-rule additions, formatter changes, and a chunk of speed improvements. The CI lint job at `.github/workflows/lint.yml` calls `ruff check` + `ruff format --check` at the pinned version, so lagging behind means new-signal lint findings that would help catch issues locally never fire, and any contributor with a modern `ruff` will see auto-fixes disagreeing with CI.

## Current state

### Files

- `requirements.txt` — pins ruff
- `.ruff.toml` — ruff configuration (rules, target-version)
- `.github/workflows/lint.yml` — CI job runs `ruff check` + `ruff format --check`
- `scripts/lint` — local auto-fix script

### Excerpts

`requirements.txt` (verify with `cat requirements.txt`):

```
homeassistant==2026.7.1
ruff==0.14.14
```

`.ruff.toml` header (verify with `head -20 .ruff.toml`) — contains the `select`, `target-version`, per-file ignores. Do NOT change any rule selections in this plan; only the pin bump.

### Sources of truth for the target version

Check the current published ruff version:

```
.venv/bin/python -m pip index versions ruff 2>&1 | head -5
```

Or if that fails:

```
curl -s https://pypi.org/pypi/ruff/json | python3 -c "import json, sys; print(json.load(sys.stdin)['info']['version'])"
```

Record the target version. Recent stable is `0.15.x`. Pin to the exact latest released version, not a range.

### Repo conventions to follow

- Pin exact versions in `requirements.txt` (already done for HA and the plugin).
- Ruff config lives in `.ruff.toml`, not `pyproject.toml`.
- Local auto-fix runs via `scripts/lint`; CI verifies with `--check`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Find current ruff | `curl -s https://pypi.org/pypi/ruff/json \| python3 -c "import json,sys; print(json.load(sys.stdin)['info']['version'])"` | version string e.g. `0.15.20` |
| Install pinned ruff into venv | `.venv/bin/pip install "ruff==<target>"` | success, no downgrade warnings |
| Run ruff check | `.venv/bin/ruff check custom_components/engie_be/ tests/` | `All checks passed!` OR list of new findings |
| Run ruff format check | `.venv/bin/ruff format --check .` | `62 files already formatted` or similar |
| Run auto-fix locally | `.venv/bin/ruff check --fix custom_components/engie_be/ tests/` | any auto-fixable finding gets fixed |
| Full test suite | `.venv/bin/pytest tests/ -q --tb=line` | all pass, coverage stays >= 95% |

## Scope

**In scope** (files you may modify):

- `requirements.txt` — bump the pin
- Any file the new ruff version's auto-fix touches (e.g. if a new lint rule catches an issue)
- `plans/README.md` — status update

**Out of scope** (do NOT touch):

- `.ruff.toml` rule selection. If a new ruff rule flags issues, either fix the code OR add a per-file `# noqa` — do not disable the rule globally in this plan.
- Any test file unless the new ruff version's auto-fix rewrites it.
- `manifest.json`, `hacs.json`.

## Git workflow

- Branch: `advisor/003-ruff-bump`
- Commit style: `chore(deps): bump ruff to <target-version>`
- If new lint findings appear that need code changes, commit them separately: `style: satisfy ruff <target-version> new rules`.
- Do NOT push or open a PR unless the operator instructs.

## Steps

### Step 1: Find the current stable ruff version

```
curl -s https://pypi.org/pypi/ruff/json | python3 -c "import json, sys; print(json.load(sys.stdin)['info']['version'])"
```

Record it — this is your target. If the returned version is still `0.14.x`, STOP and report (this plan is premised on `0.15.x` or higher being available).

### Step 2: Update the pin in requirements.txt

Change line 2 from `ruff==0.14.14` to `ruff==<target>`. No other lines change.

**Verify**:

```
grep ruff requirements.txt
```

Expected: shows the new pin.

### Step 3: Install the new ruff into the venv

```
.venv/bin/pip install "ruff==<target>"
```

Expected: `Successfully installed ruff-<target>` (may say "already satisfied" if you happened to already have it).

**Verify**:

```
.venv/bin/ruff --version
```

Expected: matches the new pin.

### Step 4: Run the lint checks

```
.venv/bin/ruff check custom_components/engie_be/ tests/
.venv/bin/ruff format --check .
```

Two possible outcomes:

**A. Both return clean.** Skip step 5, continue to step 6.

**B. New lint findings appear.** Either:
- Auto-fixable (`--fix` will resolve them): run `.venv/bin/ruff check --fix custom_components/engie_be/ tests/`, then re-run the check. Review the diff with `git diff` before committing.
- Not auto-fixable: read each finding and either fix the code minimally, or add a targeted `# noqa: <RULE>` with a one-line comment explaining why. STOP if the finding count exceeds ~10 — that suggests a bigger change than this plan scoped for; report back.

**Verify**:

```
.venv/bin/ruff check custom_components/engie_be/ tests/
.venv/bin/ruff format --check .
```

Both must return clean.

### Step 5: (Only if auto-fix ran) Review the diff

```
git diff --stat
git diff custom_components/engie_be/ tests/ | head -100
```

Confirm every change is a mechanical formatting/lint fix — no logic changes, no test assertions altered.

### Step 6: Run the full test suite

```
.venv/bin/pytest tests/ -q --tb=line
```

Expected: all 654+ tests pass, coverage stays >= 95% (implicit in CI's `--cov-fail-under=95`).

### Step 7: Update the mention in CLAUDE.md (if plan 001 has not landed)

If plan 001 was already completed in this session, CLAUDE.md line 11 already reflects the new ruff version — verify with `grep ruff CLAUDE.md` and confirm the version matches. If plan 001 has NOT been done, edit `CLAUDE.md:11` in place to change `ruff 0.14.14` to `ruff <target>`. Do NOT stage `CLAUDE.md` — it is intentionally untracked.

**Verify**:

```
grep "ruff 0" CLAUDE.md
```

Expected: shows the new version, not `0.14.14`.

## Test plan

- No new tests. Ruff bump is a tooling change; test suite must continue to pass.
- If auto-fix touches test files, verify each change is mechanical and does not alter behavior.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `grep ruff requirements.txt` shows the new pin.
- [ ] `.venv/bin/ruff --version` matches the new pin.
- [ ] `.venv/bin/ruff check custom_components/engie_be/ tests/` returns clean.
- [ ] `.venv/bin/ruff format --check .` returns clean.
- [ ] `.venv/bin/pytest tests/ -q --tb=line` — all pass.
- [ ] `git status` shows only `requirements.txt` and any files auto-fix touched (no unrelated changes).
- [ ] `CLAUDE.md` reflects the new ruff version.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report back (do not improvise) if:

- The published ruff version returned in step 1 is still on `0.14.x`. This plan assumes `0.15.x` or later exists.
- Step 4's new-lint-finding count exceeds ~10 items. Bulk lint changes belong in a separate plan.
- `pytest` fails after the bump. A ruff bump should not affect test behavior. If it does, something else is wrong; report the failing tests.
- CI-only files (`.github/workflows/lint.yml`) reference a version other than what `requirements.txt` installs. They currently install ruff from `requirements.txt`, so this should be consistent, but verify.

## Maintenance notes

- Ruff releases roughly every 2-4 weeks. Consider setting a Dependabot config (see plan 008 for release automation) to nudge future bumps.
- The `--target-version` in `.ruff.toml` (Python target for the auto-fixer) is separate from the ruff CLI version. Leave the Python target as-is.
- If a new ruff release ships a formatter change that touches many files, prefer a separate `chore: reformat with ruff <version>` commit over folding it into a fix commit.
