# Plan 001: Refresh version pairings in CLAUDE.md and AGENTS.md

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat d0652ec..HEAD -- CLAUDE.md AGENTS.md`
> If either file changed since this plan was written, compare the
> "Current state" excerpts against the live file before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: docs
- **Planned at**: commit `d0652ec`, 2026-07-07

## Why this matters

`CLAUDE.md` and `AGENTS.md` are the first files every future agent (and the maintainer) reads to reason about this repo. Both claim `HA 2026.6.1`, `pytest-homeassistant-custom-component 0.13.337`, and `quality_scale: silver`. Reality after the v0.12.0 beta series: `HA 2026.7.1`, plugin `0.13.345`, and `quality_scale: gold`. Every agent that reads these files right now is being told the wrong facts about the runtime target, test-harness pin, and quality-scale claim. Landing this first unblocks every other plan in this set because those plans quote versions.

## Current state

Both files are intentionally UNTRACKED per the repo's convention (see the user's memory rule). They must be updated in place but NOT staged for commit. This plan is a pure text edit; no code changes.

### Files

- `CLAUDE.md` — repo brief for Claude sessions. Contains the drifted claims.
- `AGENTS.md` — repo brief for OpenCode/other agent sessions. Contains the drifted claims.

### Excerpts to correct

`CLAUDE.md:11` currently reads:

```
HACS-distributed Home Assistant custom integration for ENGIE Belgium. Domain `engie_be`, `iot_class: cloud_polling`, `quality_scale: silver`. Python 3.14, HA 2026.6.1, ruff 0.14.14, `select = ["ALL"]`.
```

Corrections:
- `quality_scale: silver` → `quality_scale: gold` (see `custom_components/engie_be/manifest.json:18`)
- `HA 2026.6.1` → `HA 2026.7.1` (see `requirements.txt:1`)

`CLAUDE.md:31` currently reads:

```
`pytest-homeassistant-custom-component` is pinned to a git tag matched to the HA pin (`0.13.337` ↔ HA 2026.6.1); bump in lockstep.
```

Correction: `0.13.337` ↔ HA `2026.6.1` becomes `0.13.345` ↔ HA `2026.7.1` (see `requirements.txt:8` and the comment on `requirements.txt:6`).

`AGENTS.md:8` currently reads:

```
- Runtime floor is HA `2026.6.0` in `hacs.json`; dev/test pin is `homeassistant==2026.6.1` in `requirements.txt`. Python target is 3.14 (`.ruff.toml`, devcontainer, CI).
```

Correction: `2026.6.0` → `2026.7.0` (verify against `hacs.json`), `homeassistant==2026.6.1` → `homeassistant==2026.7.1`.

`AGENTS.md:9` currently reads:

```
- `manifest.json` declares `quality_scale: silver`, `iot_class: cloud_polling`, and version `0.10.0b7`. Per-rule status lives at `custom_components/engie_be/quality_scale.yaml`.
```

Corrections:
- `quality_scale: silver` → `quality_scale: gold`
- `version 0.10.0b7` → whatever `manifest.json` currently says (read it and paste). At the planned-at SHA this is `0.12.0b11`.

`AGENTS.md:10` currently reads:

```
- `pytest-homeassistant-custom-component` is pinned to GitHub tag `0.13.337` to match the HA pin; bump it in lockstep with HA.
```

Correction: `0.13.337` → `0.13.345`.

### Sources of truth (do NOT edit — read only)

- `custom_components/engie_be/manifest.json` for `version`, `quality_scale`
- `hacs.json` for `homeassistant` floor
- `requirements.txt:1` for the HA test pin
- `requirements.txt:8` for the plugin pin

### Repo conventions to follow

- No em-dashes anywhere. Use plain hyphens.
- No AI-tell words (`robust`, `comprehensive`, `seamless`, `essentially`).
- Match the terse present-tense voice of the surrounding sentences.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Read manifest | `grep '"version"\|"quality_scale"' custom_components/engie_be/manifest.json` | version + quality_scale lines |
| Read hacs floor | `cat hacs.json` | JSON with `homeassistant: 2026.7.0` (or whatever is current) |
| Read requirements | `cat requirements.txt` | HA pin + plugin pin visible |
| Verify no drift after edit | `grep -c "2026.6.1\|0.13.337\|silver" CLAUDE.md AGENTS.md` | `0` for each file |
| Check no accidental staging | `git status --short CLAUDE.md AGENTS.md` | files listed as `??` (untracked); NOT `A` or `M ` |

## Scope

**In scope** (edit these files):

- `CLAUDE.md`
- `AGENTS.md`

**Out of scope** (do NOT touch):

- Every source file under `custom_components/engie_be/`
- `manifest.json`, `hacs.json`, `requirements.txt` — these are the sources of truth
- Every file under `tests/`
- The rest of the sections in `CLAUDE.md` and `AGENTS.md`. Only correct the drifted version numbers and the `quality_scale` label. Do NOT restructure, reword, or trim other sections.

## Git workflow

- These files are intentionally UNTRACKED. Do NOT `git add` them. Do NOT commit them. The corrections live in the working tree only.
- Do not push, do not branch, do not open a PR.

## Steps

### Step 1: Read the sources of truth

Run:

```
grep '"version"\|"quality_scale"' custom_components/engie_be/manifest.json
cat hacs.json
head -10 requirements.txt
```

Record the exact values for `version`, `quality_scale`, `homeassistant` floor in `hacs.json`, HA pin in `requirements.txt`, and the plugin pin.

**Verify**: you have five numbers or short strings noted:
1. `manifest.json` `version` (expected `0.12.0b11` at planned-at SHA, may be higher now)
2. `manifest.json` `quality_scale` (expected `gold`)
3. `hacs.json` `homeassistant` floor (expected `2026.7.0`)
4. `requirements.txt` HA pin (expected `homeassistant==2026.7.1`)
5. `requirements.txt` plugin pin tag (expected `0.13.345`)

### Step 2: Correct CLAUDE.md

Edit `CLAUDE.md`:

- Line ~11: replace `quality_scale: silver` with `quality_scale: gold`, and `HA 2026.6.1` with `HA <current HA pin>` (e.g. `HA 2026.7.1`).
- Line ~31: replace `\`0.13.337\` ↔ HA 2026.6.1` with `\`<current plugin tag>\` ↔ HA <current HA pin>` (e.g. `\`0.13.345\` ↔ HA 2026.7.1`).

Preserve backticks, punctuation, and surrounding sentences exactly.

**Verify**:

```
grep -n "0.13.337\|2026.6.1\|silver" CLAUDE.md
```

Expected: no lines returned (the three drifted tokens are gone). If any remain, the edit missed something.

### Step 3: Correct AGENTS.md

Edit `AGENTS.md`:

- Line ~8: replace `2026.6.0` → whatever `hacs.json` currently says (likely `2026.7.0`). Replace `homeassistant==2026.6.1` → whatever the requirements.txt HA pin is now (likely `homeassistant==2026.7.1`).
- Line ~9: replace `quality_scale: silver` → `quality_scale: gold`. Replace `version 0.10.0b7` → whatever `manifest.json` `version` is now (likely `0.12.0b11` or a later beta).
- Line ~10: replace `0.13.337` → whatever the plugin pin tag is now (likely `0.13.345`).

**Verify**:

```
grep -n "0.13.337\|2026.6.1\|2026.6.0\|silver\|0.10.0b7" AGENTS.md
```

Expected: no lines returned.

### Step 4: Confirm files are still untracked

```
git status --short CLAUDE.md AGENTS.md
```

Expected: both files listed with `??` prefix (untracked). If either shows `M `, it was previously tracked and this task's context is wrong — treat as a STOP condition.

## Test plan

- No new tests required. Documentation-only change to untracked files.
- Verification is the grep commands above.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `grep -c "0.13.337\|2026.6.1\|silver" CLAUDE.md` returns `0`
- [ ] `grep -c "0.13.337\|2026.6.1\|2026.6.0\|silver\|0.10.0b7" AGENTS.md` returns `0`
- [ ] `git status --short CLAUDE.md AGENTS.md` shows both as `??` (untracked)
- [ ] No source file under `custom_components/` was modified: `git diff --stat -- custom_components/` returns nothing
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- Either `CLAUDE.md` or `AGENTS.md` is missing from the working tree.
- Either file appears as tracked (`M ` in `git status`), contradicting the "intentionally untracked" convention.
- `manifest.json`, `hacs.json`, or `requirements.txt` no longer contain the expected keys and structures listed in the sources-of-truth section.

## Maintenance notes

- When you next bump HA (e.g. `2026.7.1` → `2026.8.0`) or the plugin tag, also update these two files. There is no automation because they are untracked.
- If we ever start tracking these files, add them to the release-please plan (008) so version bumps propagate automatically.
