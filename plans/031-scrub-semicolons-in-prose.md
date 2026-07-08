# Plan 031: Scrub semicolons from README and CHANGELOG prose

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report - do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 91bf9e6..HEAD -- README.md CHANGELOG.md`
> If either file has changed since this plan was written, the line
> numbers below may have shifted. Re-run the audit grep in Step 0 and
> operate on whatever lines come back, not the numbers hard-coded here.

## Status

- **Priority**: P3 (housekeeping, cosmetic-but-owned rule)
- **Effort**: S (10-12 punctuation swaps across two files, zero code touched)
- **Risk**: LOW (prose only; no code, no tests, no rendered output changes materially)
- **Depends on**: -
- **Category**: docs
- **Planned at**: commit `91bf9e6`, 2026-07-08

## Why this matters

The maintainer's rule (recorded in `~/.claude/projects/-Users-daan-vervacke-Documents-private-hass-engie-be/memory/feedback_no_semicolons.md`) is: **no `;` in any prose the user reads** - CHANGELOG, README, commits, release notes. A humanizer audit found the rule is not fully enforced. There are roughly ten semicolon clause-joiners in `README.md` + `CHANGELOG.md`. Each reads naturally as a period or, in one case, a comma inside parentheses. No content change. This is closing a gap between the maintainer's rule and the tree.

## Current state

### Files that change

- `README.md`
- `CHANGELOG.md`

### Confirmed hits at the "Planned at" SHA

Run this to enumerate them (excludes the JavaScript snippet in the README's
example block and any HTML entities like `&lt;`):

```
grep -nP ";" README.md CHANGELOG.md | grep -v "s => \[new" | grep -v "&lt;\|&gt;\|&amp;"
```

Expected hits (verified at `91bf9e6`):

- `README.md:309` - `(lower network cost); for injection it means \`on\``
- `CHANGELOG.md:122` - `wrapped in single quotes; dropped the quotes.`
- `CHANGELOG.md:150` - `earlier beta imports; only the display name changes.`
- `CHANGELOG.md:184` - `(all three energy types pre-selected; costs off).`
- `CHANGELOG.md:188` - `per dispatch; the orchestrator logs`
- `CHANGELOG.md:194` - two semicolons: `business-agreement devices; optional` and `\`_gas\`); subsequent calls`
- `CHANGELOG.md:197` - `\`usage-details\` payload; no additional API calls.`
- `CHANGELOG.md:475` - `cleaned up explicitly; this`
- `CHANGELOG.md:749` - `No behaviour changes; logging is`
- `CHANGELOG.md:767` - `Audit hygiene only; no runtime behaviour`
- `CHANGELOG.md:968` - `energy prices and sensors; allow refresh interval`

If the grep at execution time returns MORE hits (e.g. new entries have accumulated under `[Unreleased]`), fix those too - the rule applies to the whole file.

### Repo conventions to preserve

- The maintainer's rule bans `;` in prose but does NOT ban it inside fenced code blocks (JavaScript, Python, JSON). Leave code-block semicolons alone.
- HTML entities like `&lt;`, `&gt;`, `&amp;` and `&nbsp;` legitimately end with `;`. Leave them alone. The grep filter in Step 0 excludes them.
- CHANGELOG entries are historical records of shipped releases. Rewriting them is fine per the rule ("no `;` anywhere the user reads"), but treat each edit as a punctuation swap, NOT a content edit. If you find yourself rewording, back off.
- README's Limitations section uses a `**bold-header:** rest of bullet` cadence. Keep it.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Enumerate remaining `;` hits | `grep -nP ";" README.md CHANGELOG.md \| grep -v "s => \[new" \| grep -v "&lt;\\|&gt;\\|&amp;"` | Zero lines returned after Step 2 |
| Tests still pass | `.venv/bin/pytest tests/ -q --tb=line` | 873 pass |
| Ruff | `.venv/bin/ruff check custom_components/engie_be/ tests/` | clean (unrelated to this plan, but sanity) |
| Ruff format | `.venv/bin/ruff format --check .` | clean |
| Markdown lint (optional, if configured) | `pre-commit run --files README.md CHANGELOG.md` | clean if pre-commit is set up |

## Scope

**In scope**:

- `README.md` - one substitution.
- `CHANGELOG.md` - all prose semicolons.

**Out of scope**:

- Any file under `custom_components/engie_be/`. Do NOT touch docstrings, log messages, or comments.
- Test files. Semicolons in test code (rare) are code, not prose.
- Commit-message rewriting. History is history.
- Rephrasing surrounding wording. This is a punctuation swap, nothing else.
- The `plans/` directory. Those are internal handoff docs, not user-facing prose.

## Git workflow

- Branch: `advisor/031-scrub-semicolons`
- Commit style: `docs: remove semicolons from README and CHANGELOG prose`
- One commit.

## Steps

### Step 0: Enumerate current hits

```
grep -nP ";" README.md CHANGELOG.md | grep -v "s => \[new" | grep -v "&lt;\|&gt;\|&amp;"
```

Note the exact list. This is your worklist. If it differs from the "Confirmed hits" table above, use the grep output, not the table.

### Step 1: README.md

`README.md:309`. Change:

```
`on` during OFFPEAK hours (lower network cost); for injection it means `on`
during PEAK hours (best sell price).
```

to:

```
`on` during OFFPEAK hours (lower network cost). For injection it means `on`
during PEAK hours (best sell price).
```

Nothing else changes.

**Verify**:

```
grep -nP ";" README.md | grep -v "s => \[new"
```

Expected: zero matches.

### Step 2: CHANGELOG.md - apply the punctuation-swap rule per hit

For each hit, apply the rule below. Do not reword.

**Rule (in priority order)**:

1. **`; ` between two independent clauses** → `. ` (period + capitalize the next word).
2. **`;` inside parentheses joining two short parenthetical facts** → `,` (comma).
3. **`;` before a subordinate clarifier that is really a separate sentence** → `.`.

Line-by-line application (verify with your Step 0 output; numbers may drift):

- **`CHANGELOG.md:122`**: `wrapped in single quotes; dropped the quotes.` → `wrapped in single quotes. Dropped the quotes.`
- **`CHANGELOG.md:150`**: `earlier beta imports; only the display name changes.` → `earlier beta imports. Only the display name changes.`
- **`CHANGELOG.md:184`**: `(all three energy types pre-selected; costs off)` → `(all three energy types pre-selected, costs off)` (rule 2 - inside parens).
- **`CHANGELOG.md:188`**: `resolved BAN/title per dispatch; the orchestrator logs the active streams` → `resolved BAN/title per dispatch. The orchestrator logs the active streams`.
- **`CHANGELOG.md:194`** (two hits on the same long bullet):
  - `Target one or more business-agreement devices; optional \`energy_type\` field` → `Target one or more business-agreement devices. Optional \`energy_type\` field`.
  - `(\`engie_be:{BAN}_consumption\`, \`_injection\`, \`_gas\`); subsequent calls only fetch` → `(\`engie_be:{BAN}_consumption\`, \`_injection\`, \`_gas\`). Subsequent calls only fetch`.
- **`CHANGELOG.md:197`**: `sourced from the same \`usage-details\` payload; no additional API calls.` → `sourced from the same \`usage-details\` payload. No additional API calls.`
- **`CHANGELOG.md:475`**: `these are cleaned up explicitly; this` → `these are cleaned up explicitly. This`.
- **`CHANGELOG.md:749`**: `No behaviour changes; logging is` → `No behaviour changes. Logging is`.
- **`CHANGELOG.md:767`**: `Audit hygiene only; no runtime behaviour` → `Audit hygiene only. No runtime behaviour`.
- **`CHANGELOG.md:968`**: `refresh energy prices and sensors; allow refresh interval` → `refresh energy prices and sensors. Allow refresh interval`.

**Capitalization**: after every `. ` swap, capitalize the next word if it's a normal English word. Leave code identifiers (`` `energy_type` ``, `` `_injection` ``) as-is - they should stay lowercase inside backticks; if a code identifier starts a new sentence, that's fine per this repo's existing convention (see other CHANGELOG bullets that already begin with backticks).

**Verify**:

```
grep -nP ";" CHANGELOG.md | grep -v "&lt;\|&gt;\|&amp;"
```

Expected: zero matches.

### Step 3: Final verification

```
grep -nP ";" README.md CHANGELOG.md | grep -v "s => \[new" | grep -v "&lt;\|&gt;\|&amp;"
```

Expected: zero lines.

```
.venv/bin/pytest tests/ -q --tb=line
.venv/bin/ruff check custom_components/engie_be/ tests/
.venv/bin/ruff format --check .
```

Expected: unchanged from baseline (873 pass, ruff clean, format clean).

```
git diff --stat
```

Expected: only `README.md`, `CHANGELOG.md`, and `plans/README.md` (for the status update in Step 4).

### Step 4: Update `plans/README.md`

Flip row 031 to DONE. Add:

```
| 031 | Scrub semicolons from README and CHANGELOG prose | P3 | S | — | DONE |
```

(This row will already exist as TODO when the executor picks the plan up.)

## Test plan

No new tests. This is punctuation-only. The rendered README on GitHub and HACS will read identically apart from the missing `;`.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `grep -nP ";" README.md CHANGELOG.md | grep -v "s => \[new" | grep -v "&lt;\|&gt;\|&amp;"` returns zero lines.
- [ ] `.venv/bin/pytest tests/ -q --tb=line` - 873 pass (or whatever the current baseline is; must match).
- [ ] `.venv/bin/ruff check custom_components/engie_be/ tests/` - clean.
- [ ] `.venv/bin/ruff format --check .` - clean.
- [ ] `git diff --stat` shows changes only in `README.md`, `CHANGELOG.md`, and `plans/README.md`.
- [ ] No file under `custom_components/engie_be/` is modified.
- [ ] `plans/README.md` row for plan 031 marked DONE.

## STOP conditions

Stop and report back (do not improvise) if:

- A semicolon appears inside a fenced code block (```` ``` ````). Do NOT edit code snippets. If your grep hit one, filter it out and continue.
- A semicolon is inside an HTML entity (`&lt;`, `&nbsp;`, etc.). The Step 0 filter should exclude these; if one leaks through, leave it alone.
- Removing a semicolon materially changes meaning. Report and skip that line; the rule tolerates one weird edge case better than a rewrite.
- Executing the plan turns up more than ~15 hits. That would mean the file has grown since planning; the rule still applies, but flag the count so the maintainer knows the sweep covers more than they may have realized.

## Maintenance notes

- Future CHANGELOG entries and README edits: no semicolons. If a future PR introduces one, the reviewer catches it (or add a pre-commit grep hook - out of scope for this plan).
- If a maintenance tool ever adds a pre-commit or CI check for this, the grep in Step 3 is the ready-made rule.
