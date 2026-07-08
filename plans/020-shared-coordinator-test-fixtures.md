# Plan 020: Extract shared coordinator-test fixtures to conftest

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. STOP if any assertion in "STOP
> conditions" holds. Update `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- tests/conftest.py tests/test_coordinator_solar_surplus.py tests/test_coordinator_tou.py`

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt (tests)
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

`tests/test_coordinator_solar_surplus.py` (lines 56-121) and
`tests/test_coordinator_tou.py` (lines 56-121) contain byte-identical
copies of three helpers:

- `_build_entry(hass)` — builds a v5 `MockConfigEntry` with one
  business-agreement subentry.
- `_coord(hass, entry, subentry)` — instantiates
  `EngieBeDataUpdateCoordinator`.
- `_wire(entry, client, subentry, coord, *, service_points=None)` —
  attaches `runtime_data` with an `EngieBeSubentryData`.

About 50 lines duplicated per file. Any new feature (see plans 025-027)
will copy them again. Rule of three is met; extract now to prevent
compounding drift.

## Current state

### Duplicated code

Both files have the same helpers verbatim at lines 56-121. Grep to
confirm before starting:

```bash
diff <(sed -n '56,121p' tests/test_coordinator_solar_surplus.py) \
     <(sed -n '56,121p' tests/test_coordinator_tou.py)
```

Expected: no diff (or trivial whitespace).

### Test-file-specific helpers that stay

Each file also has a `_make_client` builder that's feature-specific
(mocks the feature's endpoints). Those stay in their own files —
extracting them prematurely would need conditional argument tables
that erase the readability win.

### Import blocks in the two files

Both files import the same set of names from
`homeassistant.config_entries`, `homeassistant.const`,
`custom_components.engie_be.const`, and `custom_components.engie_be.data`.
After extraction, the test files can drop most of those imports.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format tests` | no diffs |
| Lint | `.venv/bin/ruff check tests` | `All checks passed!` |
| Target tests | `.venv/bin/pytest tests/test_coordinator_solar_surplus.py tests/test_coordinator_tou.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass, ≥95% |

## Scope

**In scope**:
- `tests/conftest.py` — add three shared fixtures/helpers.
- `tests/test_coordinator_solar_surplus.py` — remove local helpers,
  import shared ones.
- `tests/test_coordinator_tou.py` — same.

**Out of scope**:
- Feature-specific `_make_client` builders in each file — leave alone.
- Any coordinator or production-source-code changes.
- Sensor-level test files.

## Steps

### Step 1: Add the shared helpers to conftest

Append to `tests/conftest.py` (after the existing autouse fixtures and
`pytest_configure`, before or alongside the `add_sensor` fixture):

```python
# --- Shared coordinator-test builders ---
#
# Extracted from tests/test_coordinator_solar_surplus.py and
# tests/test_coordinator_tou.py where they were duplicated verbatim.
# Feature-specific ``_make_client`` builders stay in each test file.


@pytest.fixture
def build_engie_entry() -> Callable[[HomeAssistant, str], MockConfigEntry]:
    """
    Return a factory that builds a v5 MockConfigEntry with one subentry.

    The BAN defaults to ``B-0001`` and can be overridden per-call for
    multi-account scenarios.
    """
    from homeassistant.config_entries import ConfigSubentryData
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.engie_be.const import (
        CONF_ACCESS_TOKEN,
        CONF_BUSINESS_AGREEMENT_NUMBER,
        CONF_CONSUMPTION_ADDRESS,
        CONF_PREMISES_NUMBER,
        CONF_REFRESH_TOKEN,
        DOMAIN,
        SUBENTRY_TYPE_BUSINESS_AGREEMENT,
    )

    def _factory(hass: HomeAssistant, ban: str = "B-0001") -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=5,
            title="user@example.com",
            unique_id="user_example_com",
            data={
                CONF_USERNAME: "user@example.com",
                CONF_PASSWORD: "hunter2",
                CONF_ACCESS_TOKEN: "stored-access",
                CONF_REFRESH_TOKEN: "stored-refresh",
            },
            options={"update_interval": 60},
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                    title="placeholder",
                    unique_id=ban,
                    data={
                        CONF_BUSINESS_AGREEMENT_NUMBER: ban,
                        CONF_PREMISES_NUMBER: f"P-{ban}",
                        CONF_CONSUMPTION_ADDRESS: "Test 1, 1000 Brussels",
                    },
                ),
            ],
        )
        entry.add_to_hass(hass)
        return entry

    return _factory


@pytest.fixture
def build_engie_coordinator():
    """Return a factory that instantiates ``EngieBeDataUpdateCoordinator``."""
    from custom_components.engie_be.coordinator import EngieBeDataUpdateCoordinator

    def _factory(hass, entry, subentry):
        return EngieBeDataUpdateCoordinator(
            hass=hass,
            config_entry=entry,
            subentry=subentry,
        )

    return _factory


@pytest.fixture
def wire_engie_runtime():
    """
    Return a factory that attaches ``runtime_data`` to an entry.

    Usage::

        wire_engie_runtime(entry, client, subentry, coord,
                           service_points={"EAN123": "ELECTRICITY"})

    The default ``service_points`` provides a single ELECTRICITY EAN
    matching the current-tree convention.
    """
    from unittest.mock import MagicMock

    from custom_components.engie_be.data import EngieBeData, EngieBeSubentryData

    def _factory(
        entry,
        client,
        subentry,
        coord,
        *,
        service_points: dict[str, str] | None = None,
    ):
        default_ean = "541448820070414088"
        sub_data = EngieBeSubentryData(
            coordinator=coord,
            service_points=(
                service_points
                if service_points is not None
                else {default_ean: "ELECTRICITY"}
            ),
        )
        entry.runtime_data = EngieBeData(
            client=client,
            epex_coordinator=MagicMock(),
            subentry_data={subentry.subentry_id: sub_data},
            authenticated=True,
            last_options=dict(entry.options),
        )

    return _factory
```

Add appropriate imports at the top of `conftest.py` if any is missing.
Use `TYPE_CHECKING` for the `HomeAssistant`, `MockConfigEntry`,
`Callable` type hints as needed to avoid runtime import churn.

**Verify**: `.venv/bin/ruff check tests/conftest.py` → exit 0.

### Step 2: Migrate `test_coordinator_solar_surplus.py`

- Delete the local `_build_entry`, `_coord`, `_wire` definitions
  (lines 56-121).
- Update every call site to use the fixture-provided factories:
  - `entry = _build_entry(hass)` → each test function that needs it
    accepts a new fixture arg `build_engie_entry: Callable`. The call
    becomes `entry = build_engie_entry(hass)`.
  - Similar for `_coord` and `_wire`.
- Clean up now-unused imports (`ConfigSubentryData`, `CONF_PASSWORD`,
  `CONF_USERNAME`, `MockConfigEntry`, `EngieBeDataUpdateCoordinator` if
  only used by the extracted helpers, `EngieBeData`, `EngieBeSubentryData`,
  and various `CONF_*` names). Keep any imports still used by the
  file's own `_make_client` or test bodies.
- Keep `pytestmark = pytest.mark.solar_surplus` at the top.

**Verify**: `.venv/bin/pytest tests/test_coordinator_solar_surplus.py -v` → all pass.

### Step 3: Migrate `test_coordinator_tou.py`

Same operations as Step 2 on the tou-specific file.

**Verify**: `.venv/bin/pytest tests/test_coordinator_tou.py -v` → all pass.

### Step 4: Full gate

- `.venv/bin/ruff format tests` → no diffs
- `.venv/bin/ruff check tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

- No new tests. Existing tests must keep passing.
- Add an assertion to `plans/README.md` (housekeeping only) noting the
  new pattern for future test files.

## Done criteria

- [ ] `diff <(sed -n '56,121p' tests/test_coordinator_solar_surplus.py) <(sed -n '56,121p' tests/test_coordinator_tou.py)` returns immediately with no output (either empty or file-not-found because those files are shorter after migration).
- [ ] `grep -c "def _build_entry\|def _coord\|def _wire" tests/test_coordinator_solar_surplus.py tests/test_coordinator_tou.py` returns 0 (or matches only the imports of the shared fixtures).
- [ ] `grep -c "build_engie_entry\|build_engie_coordinator\|wire_engie_runtime" tests/conftest.py` returns 3.
- [ ] `.venv/bin/pytest tests/ -q --cov-fail-under=95` passes.
- [ ] No files outside "In scope" modified.
- [ ] `plans/README.md` status row for 020 flipped to DONE.

## STOP conditions

- The two files' `_build_entry`/`_coord`/`_wire` sections have drifted
  and are no longer byte-identical — investigate the divergence before
  extraction. Any per-feature difference must be either parameterized
  or left in the test file.
- A test breaks after fixture migration in a way that suggests the
  factory pattern (fixture-returning-factory) changed test order or
  fixture teardown semantics — HA MockConfigEntry has known scoping
  quirks; report and re-plan if the pattern doesn't compose.

## Maintenance notes

- Any new coordinator-level test file (plan 024 for TOU schedulers,
  plans 025-027 for new features) should use these fixtures instead of
  redefining `_build_entry` / `_coord` / `_wire`. Add a note to the
  bottom of `tests/conftest.py`.
- `_make_client` stays feature-local. Only extract it if a fourth
  feature needs it AND the differences reduce to `**kwargs`.
- The default EAN in `wire_engie_runtime` (`541448820070414088`) is a
  redacted convention used in existing fixtures. Do not change it —
  many existing tests hardcode it.
