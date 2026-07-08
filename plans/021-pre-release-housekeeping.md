# Plan 021: Pre-release housekeeping bundle (3 items)

> **Executor instructions**: Follow this plan step by step. Each of the
> three items is independently verifiable. If ONE item hits a STOP
> condition, complete the other two and report the blocker on the third.
> Update `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be/quality_scale.yaml tests/test_sensor_solar_surplus.py CHANGELOG.md`

## Status

- **Priority**: P3
- **Effort**: S (bundle of three trivial items)
- **Risk**: LOW
- **Depends on**: none
- **Category**: dx
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

Three unrelated but trivial defects sit in the tree ahead of the
v0.13.0 release. Bundling them into one plan and one commit is cheaper
than three separate reviews:

1. **`quality_scale.yaml` misrepresents Platinum status.** Lists
   `inject-websession: todo` at line 66. Prior audit (see
   `plans/README.md::2026-07-08 re-audit note`) settled that the
   requirement is met — the main `EngieBeApiClient` accepts an
   injected session; the auth-flow scratch session is intentionally
   isolated. Only the marker is stale.
2. **`test_sensor_solar_surplus.py` missing `pytestmark`.** The three
   other solar/tou test files declare
   `pytestmark = pytest.mark.<name>` to opt into the real
   coordinator behavior; this one does not. Cosmetic inconsistency
   (the sensor unit tests don't hit the coordinator path in
   practice), but the marker is the documented convention.
3. **CHANGELOG has `[#NN]` PR-number placeholders.** Six occurrences
   plus the reference-link stub. Release notes are unreadable until
   filled in with the merged PR number.

None of these blocks the release, but doing them before tagging keeps
the release commit clean.

## Current state

### Item 1: `custom_components/engie_be/quality_scale.yaml`, line 66

```yaml
  # Platinum
  async-dependency:
    status: exempt
    comment: API client is vendored in api.py rather than published as a separate async PyPI package; not currently planned to extract.
  inject-websession: todo
  strict-typing: todo
```

### Item 2: `tests/test_sensor_solar_surplus.py`

Grep confirms no `pytestmark` at module level. Compare against
`tests/test_sensor_tou.py:34` (`pytestmark = pytest.mark.tou`) and
`tests/test_coordinator_solar_surplus.py:48`
(`pytestmark = pytest.mark.solar_surplus`).

### Item 3: `CHANGELOG.md`

Lines 12-25 contain six `[#NN]` link references. Line 27 is the stub:
`[#NN]: https://github.com/DaanVervacke/hass-engie-be/pull/NN`.

Because the executor may not know the merged PR number yet, this plan
has two paths:

- **If the PR is merged**: substitute the actual PR number in all six
  places and in the reference stub.
- **If the PR is still open**: replace `[#NN]` and the stub with a
  placeholder that reads better in draft form (e.g. leave the `[#NN]`
  markers but delete the broken reference stub so GitHub doesn't
  render blank links), and add a note to the top of the `[Unreleased]`
  section: `> PR-number links will be filled in on release-tagging.`

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Target tests | `.venv/bin/pytest tests/test_sensor_solar_surplus.py -v` | all pass |
| Yaml sanity | `.venv/bin/python -c "import yaml; yaml.safe_load(open('custom_components/engie_be/quality_scale.yaml'))"` | no error |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass, ≥95% |
| GH PR number (if merged) | `gh pr view --json number,url` | JSON with number field |

## Scope

**In scope**:
- `custom_components/engie_be/quality_scale.yaml`
- `tests/test_sensor_solar_surplus.py`
- `CHANGELOG.md`

**Out of scope**:
- `strict-typing: todo` at line 67 of quality_scale.yaml — Platinum
  candidate deferred separately; do NOT flip.
- Any source code, sensor logic, or test-body changes.
- Other CHANGELOG entries (only the placeholders under
  `[Unreleased]` are addressed).

## Steps

### Step 1: Flip `inject-websession: todo` → `exempt` with rationale

Replace the single line in `custom_components/engie_be/quality_scale.yaml`
(line 66):

Old:

```yaml
  inject-websession: todo
```

New:

```yaml
  inject-websession:
    status: exempt
    comment: Main EngieBeApiClient accepts an injected session (see api.py:578-586); the pre-token OAuth/MFA flow creates an isolated aiohttp.ClientSession at api.py:620 to prevent cookie leakage across concurrent logins. The requirement is met for the client that HA integrations extend; the auth-flow scratch session is intentional isolation.
```

The block form matches the existing `async-dependency` entry two lines
above.

**Verify**:
- `.venv/bin/python -c "import yaml; yaml.safe_load(open('custom_components/engie_be/quality_scale.yaml'))"` → no error
- `grep 'inject-websession' custom_components/engie_be/quality_scale.yaml` returns exactly one match, and the following line contains `status: exempt`.

### Step 2: Add `pytestmark` to `test_sensor_solar_surplus.py`

Locate the module-level import block. Immediately after the last
import (before the first constant or helper), add:

```python
pytestmark = pytest.mark.solar_surplus
```

This must be at module scope, not inside a class or function. The
`pytest` import is already present in that file.

**Verify**:
- `grep -n "pytestmark = pytest.mark.solar_surplus" tests/test_sensor_solar_surplus.py` returns exactly one match at module top.
- `.venv/bin/pytest tests/test_sensor_solar_surplus.py -v` → all tests still pass.

### Step 3: Handle CHANGELOG placeholders

First determine whether the PR is merged:

```bash
gh pr view --json number,state 2>/dev/null || echo "no PR context"
```

**Path A — PR is merged (`state == "MERGED"`)**:

Extract the PR number:

```bash
PR=$(gh pr view --json number --jq '.number')
```

Substitute in `CHANGELOG.md`:

```bash
sed -i.bak "s/\[#NN\]/[#${PR}]/g; s|/pull/NN|/pull/${PR}|" CHANGELOG.md
rm CHANGELOG.md.bak
```

Verify: `grep '#NN' CHANGELOG.md` returns no matches.

**Path B — PR is still open or context unknown**:

Do NOT substitute placeholder text. Instead, delete the broken
reference-link stub at line 27:

```
[#NN]: https://github.com/DaanVervacke/hass-engie-be/pull/NN
```

(this line renders as a broken external link in every markdown viewer).

Add a maintainer note at the top of the `[Unreleased]` block:

```markdown
## [Unreleased]

> PR-number links will be substituted at release-tagging.

### Added
```

Verify: `grep '#NN' CHANGELOG.md` returns only the `[#NN]` inline
markers (the stub is gone), and the top of `[Unreleased]` carries the
note.

**Recommend Path A** if the PR is merged. Do NOT execute both.

### Step 4: Full gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Done criteria

- [ ] `grep 'inject-websession: todo' custom_components/engie_be/quality_scale.yaml` returns no match.
- [ ] `grep 'inject-websession' custom_components/engie_be/quality_scale.yaml` returns exactly one line, followed by `status: exempt`.
- [ ] `grep -c 'pytestmark = pytest.mark.solar_surplus' tests/test_sensor_solar_surplus.py` returns 1.
- [ ] Either (Path A) `grep '#NN' CHANGELOG.md` returns zero matches, or (Path B) the reference-link stub is deleted AND a note about PR-number substitution appears at the top of the `[Unreleased]` section.
- [ ] `.venv/bin/pytest tests/ -q --cov-fail-under=95` passes.
- [ ] No files outside "In scope" modified.
- [ ] `plans/README.md` status row for 021 flipped to DONE.

## STOP conditions

- `quality_scale.yaml` no longer contains an `inject-websession` key
  at all — someone flipped it to `done` or removed it. Investigate
  before writing new content.
- `test_sensor_solar_surplus.py` structure has changed so much that
  the `pytestmark` insertion point is ambiguous — grep for
  `SUBENTRY_TYPE_BUSINESS_AGREEMENT` and place `pytestmark`
  immediately after it fails to find a stable anchor — report before
  guessing.
- `CHANGELOG.md` no longer has an `[Unreleased]` heading (someone
  already tagged v0.13.0) — verify with `git log` before proceeding.

## Maintenance notes

- Once the PR merges and CHANGELOG placeholders are substituted, the
  release-please plan 008 (still TODO) would automate this on future
  releases. Consider landing 008 before the next feature cycle to
  eliminate the manual step.
- `inject-websession: exempt` should be revisited if the API client
  ever becomes an external PyPI package (the exemption's rationale
  would then no longer hold).
