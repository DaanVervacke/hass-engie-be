"""
Guard test: every surface naming a TOU slot code must agree with const.py.

The vocabulary is duplicated seven times: const.py, strings.json,
translations/en.json, icons.json, _tou_calendar.py, triggers.yaml and
conditions.yaml. Nothing compared them, so widening TOU_SLOT_CODES for the
billing-endpoint migration left the automation editor's picker offering five
of the codes its own schema accepts. A YAML automation could use total_hours;
the UI could not select it.

That is the same shape of bug as the one that started the migration: a
hardcoded list lagging the data it describes.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from custom_components.engie_be._tou import _SLOT_CODE_ALIASES
from custom_components.engie_be._tou_calendar import _SLOT_LABELS
from custom_components.engie_be.const import TOU_SLOT_CODES

_COMPONENT = Path(__file__).parent.parent / "custom_components" / "engie_be"


def _yaml_slot_options(name: str) -> list[str]:
    """Return the .tou_slot_options anchor from a trigger/condition YAML file."""
    data = yaml.safe_load((_COMPONENT / name).read_text(encoding="utf-8"))
    return data[".tou_slot_options"]


def _translations(name: str) -> dict:
    """Load strings.json or translations/en.json."""
    return json.loads((_COMPONENT / name).read_text(encoding="utf-8"))


def test_trigger_picker_offers_every_slot_code() -> None:
    """The automation editor must offer every code the schema accepts."""
    assert sorted(_yaml_slot_options("triggers.yaml")) == sorted(TOU_SLOT_CODES)


def test_condition_picker_offers_every_slot_code() -> None:
    """Same for conditions: schema and picker must not diverge."""
    assert sorted(_yaml_slot_options("conditions.yaml")) == sorted(TOU_SLOT_CODES)


def test_calendar_labels_cover_every_slot_code() -> None:
    """A code without a label renders its raw wire form as an event title."""
    assert sorted(_SLOT_LABELS) == sorted(TOU_SLOT_CODES)


def test_sensor_states_and_selector_cover_every_slot_code() -> None:
    """Both slot sensors and the selector must declare every code."""
    for name in ("strings.json", "translations/en.json"):
        data = _translations(name)
        for key in ("tou_offtake_slot", "tou_injection_slot"):
            states = data["entity"]["sensor"][key]["state"]
            assert sorted(states) == sorted(TOU_SLOT_CODES), f"{name}:{key}"
        options = data["selector"]["tou_slot_code"]["options"]
        assert sorted(options) == sorted(TOU_SLOT_CODES), f"{name}:selector"


def test_every_alias_target_is_a_declared_state() -> None:
    """An alias pointing at a code we do not declare would still go unknown."""
    for source, target in _SLOT_CODE_ALIASES.items():
        assert target.lower() in TOU_SLOT_CODES, source
        assert source.lower() not in TOU_SLOT_CODES, source
