# Plan 014: Add an integration test that verifies HA discovers `energy.py::async_get_solar_forecast`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 6753af3..HEAD -- custom_components/engie_be/energy.py tests/test_energy.py tests/test_init.py`
> Solar-surplus is uncommitted at the "Planned at" SHA; compare "Current
> state" excerpts against the live files before proceeding.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (this touches HA's platform-discovery machinery via the
  supported public entry points, but it's untested territory)
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `6753af3`, 2026-07-08

## Why this matters

`custom_components/engie_be/energy.py` implements `async_get_solar_forecast`
following the `EnergyPlatform` Protocol from HA core. HA discovers this via
`homeassistant.components.energy.websocket_api::async_get_energy_platforms`,
which imports the integration module's `energy` submodule and picks up the
`async_get_solar_forecast` attribute. **None of this discovery plumbing is
tested.**

`tests/test_energy.py` calls `async_get_solar_forecast(hass, entry_id)`
directly — it doesn't verify that HA would actually find and call it. If a
future change (e.g. a file rename, or accidentally shadowing the attribute)
broke discovery, the Energy dashboard's "Solar production forecast" card
would silently render nothing and the entire test suite would still pass.

The safest way to close that gap: an integration test that (a) sets up a
real config entry via `MockConfigEntry`, (b) uses HA's own
`async_get_energy_platforms` public helper to discover the hook, and (c)
invokes it through that discovered reference. This exercises the same code
path HA takes at runtime.

## Current state

### Files

- `custom_components/engie_be/energy.py` — the hook module (112 lines, 100%
  unit-tested).
- `tests/test_energy.py` — 9 tests, all invoking `async_get_solar_forecast`
  directly with a `MagicMock` `hass`.
- `tests/test_init.py` — has the shared `_make_client` factory that other
  integration tests reuse; also documents the setup-and-teardown pattern
  for a fully-loaded MockConfigEntry.
- `tests/conftest.py` — provides `pytest_plugins = ["pytest_homeassistant_custom_component"]`
  which brings the `hass` and `enable_custom_integrations` fixtures.

### Current test_energy.py signature (line 51)

```python
async def test_returns_none_when_entry_missing(hass: HomeAssistant) -> None:
    """A missing config entry yields None (not an exception)."""
    hass.config_entries.async_get_entry = MagicMock(return_value=None)
    assert await async_get_solar_forecast(hass, _ENTRY_ID) is None
```

None of the 9 tests set up a real entry via `MockConfigEntry` — they patch
`hass.config_entries.async_get_entry` directly.

### HA's discovery entry point (verified from installed package)

`homeassistant.components.energy.websocket_api.async_get_energy_platforms(hass) -> dict[str, GetSolarForecastType]`
returns a dict keyed by domain, mapping to the discovered
`async_get_solar_forecast` callable.

### Repo conventions

- Integration tests in `test_init.py` set up a `MockConfigEntry` with real
  subentries, then call `hass.config_entries.async_setup(entry.entry_id)`
  inside a `patch("custom_components.engie_be.EngieBeApiClient", return_value=client)` context.
- Coordinator first-refresh is bypassed via
  `patch("custom_components.engie_be.coordinator.EngieBeDataUpdateCoordinator.async_config_entry_first_refresh", new=AsyncMock(return_value=None))`
  so tests don't need to mock every downstream API.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Target test | `.venv/bin/pytest tests/test_energy.py -v` | all pass, new tests present |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass |

## Scope

**In scope**:
- `tests/test_energy.py` — extend with two integration tests.

**Out of scope**:
- Any changes to `energy.py` — it's already 100% unit-tested and works.
- Refactoring existing `test_energy.py` mocks — leave the unit tests intact.
- Adding integration tests for other coordinator interactions — those belong
  to plans 015 and 016.

## Git workflow

- Branch: `advisor/014-energy-hook-integration-test`.
- Commit style: `test(energy): verify HA discovers the solar-forecast hook`.

## Steps

### Step 1: Study the existing MockConfigEntry setup pattern

Open `tests/test_init.py` and read `_build_entry` (near line 60) and
`_make_client` (line 100+). Note:
- The BAN, subentry data, and options structure.
- The two-`patch` context that bypasses coordinator + EPEX first-refresh.
- How `entry.runtime_data.subentry_data[<sub_id>].has_solar` can be set
  after setup (since first-refresh is patched, the flag stays at its
  initial value).

### Step 2: Add a helper in `tests/test_energy.py` that builds a real entry

At the top of the file, add imports (near the existing ones):

```python
from unittest.mock import AsyncMock, patch

from homeassistant.components.energy.websocket_api import (
    async_get_energy_platforms,
)
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
```

Add these helpers below the existing helpers (place after `_wire` — around
line 50):

```python
def _build_real_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a v5 MockConfigEntry with one business-agreement subentry."""
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
                title="Test Account",
                unique_id="B-0001",
                data={
                    CONF_BUSINESS_AGREEMENT_NUMBER: "B-0001",
                    CONF_PREMISES_NUMBER: "P-0001",
                    CONF_CONSUMPTION_ADDRESS: "Test 1, 1000 Brussels",
                },
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def _setup_entry_with_stubs(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> None:
    """Set up the entry, bypassing coordinator first-refresh side effects."""
    client = _stub_client()
    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeDataUpdateCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeEpexCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()


def _stub_client() -> MagicMock:
    """Minimal API client stub so async_setup_entry completes."""
    client = MagicMock()

    async def _refresh_and_update() -> tuple[str, str]:
        client.refresh_token = "fresh-refresh"
        return ("fresh-access", "fresh-refresh")

    client.async_refresh_token = AsyncMock(side_effect=_refresh_and_update)
    client.async_get_prices = AsyncMock(return_value={"items": []})
    client.async_get_service_point = AsyncMock(
        return_value={"division": "ELECTRICITY"},
    )
    client.async_get_monthly_peaks = AsyncMock(
        return_value={"peakOfTheMonth": None, "dailyPeaks": []},
    )
    client.async_get_happy_hour_event = AsyncMock(return_value={})
    client.async_get_happy_hours_service_enabled_flag = AsyncMock(return_value={})
    client.async_get_solar_surplus_shown_dashboard_flag = AsyncMock(return_value={})
    client.async_get_solar_surplus_forecasts = AsyncMock(return_value={"forecasts": []})
    client.async_get_energy_contracts = AsyncMock(return_value={"items": []})
    client.async_get_epex_prices = AsyncMock(return_value={"timeSeries": []})
    return client
```

### Step 3: Add the integration test

Append to `tests/test_energy.py`:

```python
async def test_hook_is_discovered_by_ha_energy_platform(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """HA's async_get_energy_platforms discovers our hook by domain."""
    entry = _build_real_entry(hass)
    await _setup_entry_with_stubs(hass, entry)

    platforms = await async_get_energy_platforms(hass)

    assert DOMAIN in platforms
    hook = platforms[DOMAIN]
    # The discovered reference must be our own function, not a bound method
    # of some other module — assert identity against the import path.
    from custom_components.engie_be.energy import async_get_solar_forecast

    assert hook is async_get_solar_forecast


async def test_hook_returns_none_for_setup_entry_without_solar(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    End-to-end: real entry, no has_solar payload → hook returns None.

    Complements the unit tests that mock hass.config_entries.async_get_entry
    directly; this one goes through the real HA lookup + our hook body.
    """
    entry = _build_real_entry(hass)
    await _setup_entry_with_stubs(hass, entry)

    result = await async_get_solar_forecast(hass, entry.entry_id)

    assert result is None
```

### Step 4: Verify each test in isolation, then together

```bash
.venv/bin/pytest tests/test_energy.py::test_hook_is_discovered_by_ha_energy_platform -v
.venv/bin/pytest tests/test_energy.py::test_hook_returns_none_for_setup_entry_without_solar -v
.venv/bin/pytest tests/test_energy.py -v
```

All must pass.

### Step 5: Full gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass

## Test plan

- Two new tests in `tests/test_energy.py`:
  - `test_hook_is_discovered_by_ha_energy_platform` — the critical
    discovery-plumbing test. Uses HA's own `async_get_energy_platforms`
    entry point, so if HA ever changes its discovery semantics this
    test will catch the drift.
  - `test_hook_returns_none_for_setup_entry_without_solar` — end-to-end
    invocation through the real entry lookup path.
- Model after: the existing MockConfigEntry patterns in `tests/test_init.py`
  (helper reused, not imported — inline for test-file locality).

## Done criteria

- [ ] `.venv/bin/pytest tests/test_energy.py -v` all pass, 11 tests total (9 existing + 2 new).
- [ ] `.venv/bin/ruff check custom_components tests` exits 0.
- [ ] The discovery test asserts `hook is async_get_solar_forecast` (identity check).
- [ ] `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` passes.
- [ ] No files outside "In scope" modified.
- [ ] `plans/README.md` status row for 014 flipped to DONE.

## STOP conditions

- `async_get_energy_platforms` is no longer importable from
  `homeassistant.components.energy.websocket_api` (HA renamed or moved
  the symbol between the pinned version and now). Report the actual
  import path and ask before proceeding.
- The MockConfigEntry setup pattern documented above fails with a
  different error than "missing mock method" — that means the shared
  setup path has drifted; investigate before working around it.
- The `enable_custom_integrations` fixture is not available (should be
  provided by `pytest_homeassistant_custom_component`).

## Maintenance notes

- If HA ever moves the discovery from `async_get_energy_platforms` to a
  different entry point, this test will fail cleanly and point at the
  right place to update.
- Reviewer should confirm that neither new test leaks mock objects to
  subsequent tests (autouse `_disable_solar_surplus_flag_probe` in
  conftest.py already handles the coordinator side).
- If `_stub_client` diverges from `test_init.py::_make_client`, consider
  extracting a shared fixture in conftest.py — deferred here to avoid
  scope creep (see plans/README.md for the "test infrastructure burden"
  observation).
