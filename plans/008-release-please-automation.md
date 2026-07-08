# Plan 008: Automate releases with release-please

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report — do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- CHANGELOG.md custom_components/engie_be/manifest.json .github/workflows/`
> If any of these changed since this refresh, compare the "Current state"
> excerpts against the live files before proceeding; on a mismatch, treat
> it as a STOP condition. **Note**: this plan was reconciled at HEAD
> `85011b7` on 2026-07-08 to reflect the current version (`0.13.0b1`)
> and the accumulated CHANGELOG entries for the v0.13.0 feature set.

## Status

- **Priority**: P2 (high value but larger blast radius)
- **Effort**: M
- **Risk**: MED
- **Depends on**: 001 (doc drift so future release notes stay accurate), 003 (ruff bump is on the manual path this replaces)
- **Category**: dx
- **Planned at**: commit `d0652ec`, 2026-07-07
- **Refreshed at**: commit `85011b7`, 2026-07-08 (version + CHANGELOG excerpts updated post-v0.12.0 release + v0.13.0 feature drops)

## Why this matters

Between 2026-07-06 and 2026-07-08 the maintainer shipped 11 beta releases
(v0.12.0b1 through v0.12.0b11), tagged v0.12.0 GA, and is now on
v0.13.0b1 with substantial uncommitted feature work (solar surplus,
TOU, calendar events, device conditions, account balance — see the
current `[Unreleased]` block in `CHANGELOG.md`). Each release required
manual `manifest.json` version bump, manual `CHANGELOG.md` entry, manual
`git tag`, manual push, manual `gh release create` with hand-written
notes. Every one of those steps is a place typos or drift can leak in.
Release-please + Conventional Commits automates the version bump,
changelog generation, tag creation, and GitHub Release drafting. The
maintainer reviews and merges a release-PR; everything else is
mechanical. The pattern fits the observed rhythm well, and the sheer
volume of `[Unreleased]` entries queued up for v0.13.0 makes this a
particularly good moment to introduce it — release-please can cut the
first automated release directly from the current unreleased pile.

## Current state

### Files that will change or be added

- `custom_components/engie_be/manifest.json` — release-please writes the version here.
- `CHANGELOG.md` — release-please writes new sections here.
- `.github/workflows/release-please.yml` — NEW. Runs on push to `main`.
- `release-please-config.json` — NEW. Schema config.
- `.release-please-manifest.json` — NEW. Tracks current version.

### Excerpts / references

`manifest.json` at the refreshed SHA (`0.13.0b1`):

```
{
  "domain": "engie_be",
  "name": "ENGIE Belgium",
  ...
  "version": "0.13.0b1"
}
```

(The specific version WILL have moved by the time this plan runs — the
executor should read the live `manifest.json` and use whatever value is
there. That's what `.release-please-manifest.json` gets seeded with in
Step 1.)

Release-please can be configured to update the `version` key in a JSON file via the `extra-files` mechanism (documented in the release-please schema). The manifest is the only file we need to sync at release time.

`CHANGELOG.md` header:

```
# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

...
```

Existing entries follow `## [X.Y.Zbn] - YYYY-MM-DD` with sub-sections `### Added / ### Changed / ### Fixed / ### Docs / ### Tests / ### Chore`. Compare with the release-please default output shape and configure `changelog-sections` to match.

Commit history (`git log --oneline -20`) shows the maintainer's current commit style: `release: v0.12.0`, `refactor(entity): ...`, `fix(docs): ...`, `feat(sensor): ...`, `test(coordinator): ...`. Already conventional-commits-like — good.

### The current pre-release cadence

`v0.13.0b1` at the refresh SHA (`85011b7`). The v0.12.0 GA has already
shipped. The current pattern is beta series → GA → new beta series, so
release-please should:

- Continue the current `bN` beta line (v0.13.0b1, b2, ...) until GA
- Increment to v0.13.0 on a `feat!:` / breaking change or when the
  maintainer manually opts in
- Start v0.14.0bN when new features land after GA

Release-please supports pre-release versioning via the
`versioning-strategy` and `include-v-in-tag` options. For a pre-1.0
project on a `bN` cadence, use `prerelease` mode with
`prerelease-type: b` or configure `versioning: prerelease`.

### Conventional Commits shape the tool expects

- `feat: ...` → minor bump (or patch, pre-1.0)
- `fix: ...` → patch bump
- `docs: ...` → no release
- `chore: ...` → no release
- `feat!: ...` or `BREAKING CHANGE:` → major bump (or minor, pre-1.0)

The repo's commit log is largely conformant already. Any deviations from that shape after release-please lands will just not trigger a release — soft failure, no data loss.

### Repo conventions to preserve

- CHANGELOG entries are terse, factual, no AI-tell prose.
- Version tags are prefixed `v` (e.g. `v0.13.0b1`, `v0.12.0`).
- Only the maintainer merges the release-PR. Bots don't auto-merge here.
- The commit that lands a release is titled `release: v<version>` per current pattern.
- HACS reads `manifest.json` `version` for update detection.
- The user's rule bans em-dashes and AI-tell characters everywhere. Configure release-please's changelog template to use plain hyphens.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Verify config | `cat release-please-config.json \| python3 -m json.tool` | valid JSON, no errors |
| Verify manifest | `cat .release-please-manifest.json \| python3 -m json.tool` | valid JSON, no errors |
| Verify workflow | `.github/workflows/release-please.yml` linted by yamllint if installed, or just visual read | valid YAML |
| Tests still pass | `.venv/bin/pytest tests/ -q --tb=line` | pass |
| Ruff still passes | `.venv/bin/ruff check custom_components/engie_be/ tests/` | clean |
| Dry-run against a test branch (optional) | See step 6 | release-please writes a PR |

## Scope

**In scope**:

- Add `.github/workflows/release-please.yml`
- Add `release-please-config.json`
- Add `.release-please-manifest.json`
- Update `CHANGELOG.md`: replace the top-of-file "How to release" note (if present) with a one-line pointer to the release-please PR flow; add a section header release-please will target.
- Do NOT edit any code under `custom_components/engie_be/`.

**Out of scope**:

- Migrating the existing beta series to a different versioning scheme. Continue `v0.12.0bN` under release-please.
- Auto-publishing releases on tag push. Release-please writes a PR; the maintainer merges it manually. Do NOT enable auto-merge.
- Signing commits or tags. If the maintainer wants GPG-signed releases, that's a separate config on the GitHub workflow.
- Adding release-drafter, semantic-release, or other alternatives. Release-please is chosen.
- Publishing to PyPI. HACS distributes the integration via the git repo directly.

## Git workflow

- Branch: `advisor/008-release-please`
- Commit style: `chore(ci): add release-please automation`
- The FIRST run of release-please after landing will create a bootstrapping PR. That's expected. Do NOT merge it yourself — hand it back to the maintainer.

## Steps

### Step 1: Add `.release-please-manifest.json`

Path: `.release-please-manifest.json` (at repo root).

Content (seed with the current manifest version, whatever it is at
land-time; the value below matches the refresh-SHA state):

```json
{
  ".": "0.13.0b1"
}
```

The version MUST match `custom_components/engie_be/manifest.json` at
the SHA where this workflow lands. Read the current live value with
`.venv/bin/python -c "import json; print(json.load(open('custom_components/engie_be/manifest.json'))['version'])"`
and use exactly that string. The `.` key means "the whole repo".

**Verify**:

```
python3 -c "import json; print(json.load(open('.release-please-manifest.json')))"
```

Expected: `{'.': '<live-manifest-version>'}` matching what you read
from `manifest.json`.

### Step 2: Add `release-please-config.json`

Path: `release-please-config.json` (at repo root).

Content (adjust the version-in-manifest jsonpath if release-please's schema requires a different form; verify with `https://github.com/googleapis/release-please/blob/main/docs/customizing.md`):

```json
{
  "release-type": "python",
  "prerelease": true,
  "prerelease-type": "b",
  "include-v-in-tag": true,
  "packages": {
    ".": {
      "package-name": "hass-engie-be",
      "changelog-path": "CHANGELOG.md",
      "extra-files": [
        {
          "type": "json",
          "path": "custom_components/engie_be/manifest.json",
          "jsonpath": "$.version"
        }
      ],
      "changelog-sections": [
        {"type": "feat", "section": "Added"},
        {"type": "fix", "section": "Fixed"},
        {"type": "perf", "section": "Fixed"},
        {"type": "docs", "section": "Docs"},
        {"type": "refactor", "section": "Changed"},
        {"type": "test", "section": "Tests"},
        {"type": "chore", "section": "Chore", "hidden": true}
      ]
    }
  }
}
```

Notes:
- `release-type: python` is the closest release-please preset for a Python repo. It doesn't require setup.py/pyproject bumps; we only extract the manifest.
- `prerelease: true` + `prerelease-type: "b"` continue the `bN` line.
- `extra-files` points the JSON version bumper at `manifest.json:$.version`.
- `changelog-sections` mirrors the existing `Added / Fixed / Changed / Docs / Tests` shape.
- `chore` is hidden by default so the CHANGELOG isn't cluttered with tooling bumps.

**Verify**:

```
python3 -c "import json; json.load(open('release-please-config.json'))"
```

Expected: parses cleanly, no exception.

### Step 3: Add the workflow at `.github/workflows/release-please.yml`

Content:

```yaml
name: Release Please

on:
  push:
    branches:
      - main

permissions: {}

jobs:
  release-please:
    name: release-please
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    steps:
      - uses: googleapis/release-please-action@v4
        with:
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

Notes:
- `permissions: {}` on the workflow, then bump inside the job. Minimum required for release-please to write PRs and commit-back tags.
- Pin the action to a major version (`@v4`) rather than a SHA to keep upgrades smooth. If the repo elsewhere pins actions by SHA (see `.github/workflows/test.yml`), match that style and pin release-please by SHA too.
- Runs only on push to `main`. Do NOT run on PRs or tags.

**Verify**:

```
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release-please.yml'))"
```

Expected: parses without error.

### Step 4: Update CHANGELOG.md to be release-please-friendly

Release-please inserts new entries directly above the newest existing section. Verify the top of `CHANGELOG.md` still has the `## [Unreleased]` header — release-please replaces this on release. If a bulleted release note is currently under `## [Unreleased]`, LEAVE IT. Release-please will fold it into the next tagged release automatically (via the config's `changelog-sections`).

If there is a pinned instruction ("bump manifest.json manually", "how to release") anywhere in `CHANGELOG.md` or `CONTRIBUTING.md`, update it to point at the new flow: "Commit with Conventional Commits. Release-please opens a PR on push to main; merging the PR tags the release and drafts the GitHub Release."

**Verify**:

```
head -10 CHANGELOG.md
```

Expected: `## [Unreleased]` section still exists at the top; no leftover manual-release instructions above it.

### Step 5: Handle the manifest-and-CHANGELOG version drift on first run

The first release-please run after this lands will notice `manifest.json` and `.release-please-manifest.json` are in sync at whatever version is current at land-time (currently `0.13.0b1` at refresh-SHA), so no release-PR fires until the next `feat:`/`fix:`/`perf:` commit lands on `main`. That's expected. Alternative: if the v0.13.0 feature drops are ready to release when this lands, the maintainer can trigger the first release-PR by pushing a small `feat:` commit or by manually running the workflow with a `--force` input, but that's a follow-up decision.

Do NOT try to trigger a release-PR during this plan. Once this plan's own commit lands (as a `chore:` commit), release-please will pick up any subsequent feature/fix commits and open a PR proposing the next version.

### Step 6: (Optional) Local sanity check via dry-run

If `npx` is available and network permissions allow, you can dry-run release-please against a scratch branch:

```
npx release-please@latest release-pr \
  --repo-url=DaanVervacke/hass-engie-be \
  --config-file=release-please-config.json \
  --manifest-file=.release-please-manifest.json \
  --dry-run
```

Expected: prints the proposed changelog + version bump for the current commit log, without pushing anything. If dry-run isn't supported or `npx` isn't available, skip this step.

### Step 7: Final verification

```
.venv/bin/pytest tests/ -q --tb=line
.venv/bin/ruff check custom_components/engie_be/ tests/
.venv/bin/ruff format --check .
```

All expected clean. This plan doesn't touch source code, so the test suite should be entirely unaffected.

Confirm the new files:

```
ls -la .github/workflows/release-please.yml release-please-config.json .release-please-manifest.json
```

Expected: all three present, owner readable.

### Step 8: Update CLAUDE.md / AGENTS.md release process notes

If `CLAUDE.md` or `AGENTS.md` (both untracked) has a "Release process" section describing manual version bumps, update it to describe the release-please flow instead. Do NOT stage those files — they stay untracked. See plan 001 for the untracked-file convention.

## Test plan

- No new tests. This plan adds workflow / config files, not source code.
- Verification: existing test suite must pass. Manual verification of the workflow happens on the next push to `main` after this lands — release-please either opens a PR (if there are release-worthy commits since the last tag) or silently no-ops (if not).

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `release-please-config.json` exists at repo root and parses as JSON.
- [ ] `.release-please-manifest.json` exists at repo root and its `.` key matches `custom_components/engie_be/manifest.json` `version`.
- [ ] `.github/workflows/release-please.yml` exists and parses as YAML.
- [ ] `.venv/bin/pytest tests/ -q --tb=line` — all pass.
- [ ] `.venv/bin/ruff check custom_components/engie_be/ tests/` — clean.
- [ ] `.venv/bin/ruff format --check .` — clean.
- [ ] `CHANGELOG.md` still has `## [Unreleased]` at the top and no leftover manual-release instructions.
- [ ] `git status` shows only the 3 new files (plus optional CHANGELOG.md tweak from step 4) and `plans/README.md`.
- [ ] No file under `custom_components/engie_be/` is modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report back (do not improvise) if:

- `manifest.json` `version` has drifted materially from the refresh-SHA value (`0.13.0b1`) between when this plan was refreshed and when it's executed. Update `.release-please-manifest.json` to match the live manifest value; if the delta jumps a minor version (e.g. `0.13.x` → `0.14.x`) without a landed release commit, STOP and confirm with the maintainer.
- The release-please schema has changed materially (v4 → v5, config-file field renamed, etc.). Read the current release-please docs at https://github.com/googleapis/release-please and adapt.
- `permissions: {}` on the workflow trips a security policy. Some org-level policies require `permissions:` at both workflow and job level; keep both.
- Running the workflow on `main` produces an unexpected PR shape (e.g., the version bump proposes `v0.13.0` when we're mid-beta on `v0.12.0`). STOP, examine the release-please output; do NOT merge that PR without maintainer review.
- The maintainer's memory explicitly says "no release automation, I want to bump manually". Verify via CLAUDE.md / AGENTS.md before merging.

## Maintenance notes

- Every subsequent commit to `main` should follow Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `perf:`, `refactor:`, `test:`). Existing commits are already close.
- Release-please opens a rolling PR; new `feat:`/`fix:` commits get folded into it automatically. Merging it triggers the release.
- The GitHub release description is auto-generated. Custom release notes (like the ones handwritten for v0.12.0b1-b11) still need manual editing on the GitHub Release page if the auto-generated version isn't sufficient.
- If you ever need to override a version (e.g., "release this as v1.0.0 not v0.13.0"), append `Release-As: 1.0.0` to a commit body. Release-please recognizes that footer.
- `chore(deps): ...` for Dependabot bumps stays hidden from the changelog by default — that's desired.
- Do NOT enable `include-component-in-tag` or `bootstrap-sha` unless the repo grows into a monorepo.
