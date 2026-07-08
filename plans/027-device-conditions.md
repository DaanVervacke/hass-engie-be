# Plan 027: Device conditions for automation UX (DIR-03)

> **Executor instructions**: Follow this plan step by step. STOP if any
> STOP condition triggers. Update `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be`

## Status

- **Priority**: P3
- **Effort**: S-M
- **Risk**: LOW
- **Depends on**: none
- **Category**: direction (UX)
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

Home Assistant supports custom `device_condition.py` platforms that
surface an integration's states as first-class dropdown options in
the automation editor. Users currently building automations like
"run the dishwasher when solar surplus is high AND tariff is offpeak"
must write template conditions like:

```yaml
condition:
  - condition: template
    value_template: "{{ states('sensor.engie_belgium_..._solar_surplus_forecast') == 'high_surplus' }}"
  - condition: template
    value_template: "{{ states('sensor.engie_belgium_..._offtake_slot') == 'offpeak' }}"
```

With `device_condition.py`, the same becomes:

- Dropdown: pick device → "Solar surplus is at level" → pick `high_surplus`
- Dropdown: pick device → "TOU offtake slot is" → pick `offpeak`

No template strings, no entity ID copying, no typos. Massive UX win
for non-technical users, and matches HA's convention for
integration-driven automation surfaces (see: `homeassistant.components.
tesla`, `hue`, etc.).

## Current state

### Existing state exposure

Three high-value automation surfaces already exist as entity states:

1. **Solar surplus level** — `sensor.engie_belgium_{...}_solar_surplus_forecast`
   with ENUM state (5 options: `no_data`, `no_surplus`,
   `minimal_surplus`, `low_surplus`, `high_surplus`)
2. **TOU offtake slot** — `sensor.engie_belgium_{...}_offtake_slot`
   with ENUM state (up to 5 options; typically `peak` / `offpeak`)
3. **TOU injection slot** — same shape
4. **EPEX price is negative** — `binary_sensor.engie_belgium_{...}_epex_negative`

Users can already trigger on entity state changes; this plan adds the
lower-friction UX layer on top.

### HA device_condition contract

A `device_condition.py` module in the integration package exports:
- `CONDITION_SCHEMA` — the voluptuous schema for the condition config
- `async_validate_condition_config` — validates against the schema
- `async_get_conditions` — enumerates available conditions for a device
- `async_condition_from_config` — returns a callable that evaluates
  the condition against `hass.states`

Reference implementations in HA core:
- `homeassistant.components.sensor.device_condition` — for enum state
  comparisons on any sensor
- `homeassistant.components.binary_sensor.device_condition` — for
  is_on / is_off

The pattern is:

```python
from homeassistant.components.device_automation.const import CONF_TYPE
from homeassistant.const import (
    ATTR_ENTITY_ID, CONF_CONDITION, CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.config_validation import CONF_ENTITY_ID
from homeassistant.helpers import config_validation as cv

CONDITION_TYPES = {
    "solar_surplus_is_at_level",
    "offtake_slot_is",
    "injection_slot_is",
    "epex_price_is_negative",
}

CONDITION_SCHEMA = ...  # voluptuous schema

async def async_get_conditions(hass, device_id):
    """Return conditions available for the device."""
    # Look up device's entities. For each engie_be entity, add matching condition.
    ...

@callback
def async_condition_from_config(hass, config):
    """Return a callable that evaluates the condition."""
    condition_type = config[CONF_TYPE]
    entity_id = config[ATTR_ENTITY_ID]
    ...  # dispatch on condition_type
```

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Target tests | `.venv/bin/pytest tests/test_device_condition.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` | pass, ≥95% |

## Scope

**In scope**:
- `custom_components/engie_be/device_condition.py` (new) — implements
  the four condition types.
- `custom_components/engie_be/strings.json` + `translations/en.json` —
  add `device_automation` section with condition-type translations.
- `tests/test_device_condition.py` (new).
- `README.md` — brief automation-UX section pointing users at the
  device conditions.
- `CHANGELOG.md` — Unreleased Added entry.

**Out of scope**:
- `device_action.py` — no write actions currently make sense
  (ENGIE endpoints are read-only for automation-relevant data;
  `import_history` is admin-flavored, not automation-driver).
- `device_trigger.py` — HA's built-in state-change trigger already
  covers this cleanly.
- The `epex_price_is_negative` condition — include only if the
  binary sensor genuinely exists (grep to verify: `grep -n
  "epex_negative\|epex_price_is_negative" custom_components/engie_be/binary_sensor.py`).
  If it doesn't have a stable entity name, drop that condition
  from the initial scope.

## Steps

### Step 1: Verify entity_id patterns

Grep for the actual entity_id shapes and translation_keys:

```bash
grep -n "translation_key\|entity_id" custom_components/engie_be/sensor.py | grep -E "solar_surplus_forecast|offtake_slot|injection_slot" | head
grep -n "translation_key\|entity_id" custom_components/engie_be/binary_sensor.py | grep epex | head
```

Confirm:
- Solar surplus enum sensor's `translation_key` (probably
  `solar_surplus_forecast`) and its ENUM `options` tuple (from
  `const.py::SOLAR_SURPLUS_LEVELS`)
- TOU sensor `translation_key`s (probably `tou_offtake_slot`,
  `tou_injection_slot`) and options (`const.py::TOU_SLOT_CODES`)
- EPEX-negative binary sensor `translation_key` and entity_id shape

Record the confirmed values in your plan-execution notes; the code
in Step 2 depends on them.

### Step 2: Write `device_condition.py`

Skeleton:

```python
"""Device conditions for the ENGIE Belgium integration.

Exposes ENGIE state as first-class dropdown conditions in the HA
automation editor so users don't have to write template conditions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_CONDITION,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    condition,
    config_validation as cv,
    entity_registry as er,
)
from homeassistant.helpers.config_validation import DEVICE_CONDITION_BASE_SCHEMA
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import DOMAIN, SOLAR_SURPLUS_LEVELS, TOU_SLOT_CODES

if TYPE_CHECKING:
    from collections.abc import Callable

CONF_LEVEL = "level"
CONF_SLOT = "slot"

_SOLAR_LEVEL_TYPE = "solar_surplus_is_at_level"
_OFFTAKE_SLOT_TYPE = "offtake_slot_is"
_INJECTION_SLOT_TYPE = "injection_slot_is"
_EPEX_NEGATIVE_TYPE = "epex_price_is_negative"

CONDITION_TYPES = {
    _SOLAR_LEVEL_TYPE,
    _OFFTAKE_SLOT_TYPE,
    _INJECTION_SLOT_TYPE,
    _EPEX_NEGATIVE_TYPE,
}

CONDITION_SCHEMA = vol.All(
    DEVICE_CONDITION_BASE_SCHEMA.extend(
        {
            vol.Required(CONF_TYPE): vol.In(CONDITION_TYPES),
            vol.Required(ATTR_ENTITY_ID): cv.entity_id,
            vol.Optional(CONF_LEVEL): vol.In(SOLAR_SURPLUS_LEVELS),
            vol.Optional(CONF_SLOT): vol.In(TOU_SLOT_CODES),
        }
    ),
)


async def async_get_conditions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """Enumerate available conditions for an engie_be device."""
    registry = er.async_get(hass)
    conditions: list[dict[str, Any]] = []
    for entry in er.async_entries_for_device(registry, device_id):
        if entry.platform != DOMAIN:
            continue
        unique_id = entry.unique_id or ""
        entity_id = entry.entity_id
        # Match on the unique_id suffix / entity_id slug to detect
        # which sensor this is. See Step 1 for the confirmed suffixes.
        if entity_id.endswith("_solar_surplus_forecast"):
            for level in SOLAR_SURPLUS_LEVELS:
                conditions.append({
                    CONF_CONDITION: "device",
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_TYPE: _SOLAR_LEVEL_TYPE,
                    ATTR_ENTITY_ID: entity_id,
                    CONF_LEVEL: level,
                })
        elif entity_id.endswith("_offtake_slot"):
            for slot in TOU_SLOT_CODES:
                conditions.append({
                    CONF_CONDITION: "device",
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_TYPE: _OFFTAKE_SLOT_TYPE,
                    ATTR_ENTITY_ID: entity_id,
                    CONF_SLOT: slot,
                })
        elif entity_id.endswith("_injection_slot"):
            for slot in TOU_SLOT_CODES:
                conditions.append({
                    CONF_CONDITION: "device",
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_TYPE: _INJECTION_SLOT_TYPE,
                    ATTR_ENTITY_ID: entity_id,
                    CONF_SLOT: slot,
                })
        elif entity_id.endswith("_epex_negative"):
            conditions.append({
                CONF_CONDITION: "device",
                CONF_DEVICE_ID: device_id,
                CONF_DOMAIN: DOMAIN,
                CONF_TYPE: _EPEX_NEGATIVE_TYPE,
                ATTR_ENTITY_ID: entity_id,
            })
    return conditions


@callback
def async_condition_from_config(
    hass: HomeAssistant, config: ConfigType
) -> condition.ConditionCheckerType:
    """Return a callable that evaluates the condition."""
    condition_type = config[CONF_TYPE]
    entity_id = config[ATTR_ENTITY_ID]
    if condition_type == _SOLAR_LEVEL_TYPE:
        expected = config[CONF_LEVEL]
        return lambda hass_, vars_=None: (
            hass_.states.get(entity_id) is not None
            and hass_.states.get(entity_id).state == expected
        )
    if condition_type in (_OFFTAKE_SLOT_TYPE, _INJECTION_SLOT_TYPE):
        expected = config[CONF_SLOT]
        return lambda hass_, vars_=None: (
            hass_.states.get(entity_id) is not None
            and hass_.states.get(entity_id).state == expected
        )
    if condition_type == _EPEX_NEGATIVE_TYPE:
        return lambda hass_, vars_=None: (
            hass_.states.get(entity_id) is not None
            and hass_.states.get(entity_id).state == "on"
        )
    msg = f"Unknown condition type: {condition_type}"
    raise ValueError(msg)
```

Adjust the entity_id suffix matches based on Step 1's grep output.
The exact `endswith` strings must match production entity IDs, not
translation keys.

### Step 3: Translations

Add to `strings.json` under a new `device_automation` block:

```json
"device_automation": {
    "condition_type": {
        "solar_surplus_is_at_level": "Solar surplus is at level",
        "offtake_slot_is": "Current offtake slot is",
        "injection_slot_is": "Current injection slot is",
        "epex_price_is_negative": "EPEX price is negative"
    }
}
```

`cp custom_components/engie_be/strings.json custom_components/engie_be/translations/en.json`.

### Step 4: Tests

Create `tests/test_device_condition.py`. Model after HA's own
`tests/components/tests/test_device_condition.py` if available;
minimum coverage:

- `test_async_get_conditions_returns_expected_types` — set up a
  MockConfigEntry with a solar-surplus + TOU + EPEX subentry, register
  the entities, call `async_get_conditions(hass, device_id)`, assert
  each condition type appears exactly once per level/slot.
- `test_async_condition_from_config_returns_true_on_match` — for each
  condition type, set the entity state to the expected value, call
  the condition, assert True.
- `test_async_condition_from_config_returns_false_on_mismatch` — same
  as above but with a mismatched state.
- `test_async_condition_from_config_returns_false_when_entity_missing`
  — condition against an entity that's not in `hass.states` → False.

### Step 5: README

Add a small "Automation UX" section after the existing "Time-of-Use"
section:

```markdown
## Automation from the UI

Every ENGIE Belgium device exposes automation conditions directly to
Home Assistant's automation editor. In **Settings → Automations & Scenes**,
under a new automation's "Conditions" step, pick your ENGIE device and
choose from:

- Solar surplus is at level (peak / medium / low / …)
- Current offtake slot is (peak / offpeak / …)
- Current injection slot is
- EPEX price is negative

No template YAML required.
```

### Step 6: CHANGELOG

`[Unreleased]` Added entry describing the four conditions.

### Step 7: Full gate + smoke check

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass
- `podman restart ha-plugin-test` → open HA UI, verify device
  conditions dropdown lists the four types under an ENGIE device.

## Test plan

- `tests/test_device_condition.py` with ≥ 8 test functions (4 types
  × get + eval).
- Reuse existing test infrastructure (MockConfigEntry, EAN fixtures).
- Do NOT create new fixtures — use whatever's already in
  `tests/fixtures/`.

## Done criteria

- [ ] `custom_components/engie_be/device_condition.py` exists.
- [ ] `grep -c "CONDITION_TYPES\|async_get_conditions\|async_condition_from_config" custom_components/engie_be/device_condition.py` returns at least 3 matches.
- [ ] `tests/test_device_condition.py` exists with ≥ 8 tests, all passing.
- [ ] Translations added under `device_automation.condition_type` block.
- [ ] `strings.json` and `translations/en.json` byte-identical.
- [ ] `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` passes.
- [ ] Devcontainer HA UI shows the four conditions in the automation editor.
- [ ] `README.md` has a section pointing at the device conditions.
- [ ] `plans/README.md` status row for 027 flipped to DONE.

## STOP conditions

- The entity ID suffix pattern differs from what this plan assumes
  (e.g., `_solar_surplus_forecast` vs `_solar_surplus`). Grep first,
  match production, do not force. Multiple suffix mismatches → STOP
  and adjust matches.
- `DEVICE_CONDITION_BASE_SCHEMA` or `condition.ConditionCheckerType`
  is not exportable from the pinned HA version. Report the actual
  imports before adjusting the schema.
- The EPEX-negative binary sensor doesn't exist as a standalone
  entity — drop the fourth condition type and note it in the plan
  completion report.

## Maintenance notes

- Device conditions are stateless; changing the SOLAR_SURPLUS_LEVELS
  or TOU_SLOT_CODES tuples automatically adds new dropdown options
  without touching this file.
- Device conditions do NOT support "per-EAN filter" out of the box in
  HA's UI. A user with two EANs will see two identical dropdown
  entries per condition type; they must pick the one whose EAN they
  care about (visible in the entity_id preview). If demand appears
  for a better UX, look at `device_automation.const.CONF_ENTITY_ID`
  filtering, but that's an HA-core-level UX concern, not something
  this integration can fix alone.
- If a fifth condition surfaces (e.g., "solar surplus is optimal"),
  add it to `CONDITION_TYPES`, add a case in
  `async_condition_from_config`, extend `async_get_conditions` — no
  other file changes.
