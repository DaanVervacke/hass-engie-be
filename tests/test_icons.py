"""
Guard test: every entity description, trigger and condition must have an icon.

An entity shows a sensible icon when it either has a ``device_class``
(HA supplies a default icon), an inline ``icon=`` on its description, or
a matching entry in ``icons.json``. Triggers and conditions have no
device_class fallback at all, so an ``icons.json`` entry is their only
source and a missing one renders blank in the automation editor.

This gap has recurred more than once (see plans/epex-current-icon-plan.md,
plans/trigger-condition-icon-audit-plan.md, plans/139-tou-optimal-missing-icons.md)
so this module pins coverage for every description defined at module level
in sensor.py and binary_sensor.py, and for every key registered in the
``TRIGGERS`` and ``CONDITIONS`` registries.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.sensor import SensorEntityDescription

from custom_components.engie_be import binary_sensor, sensor
from custom_components.engie_be.condition import CONDITIONS
from custom_components.engie_be.trigger import TRIGGERS

ICONS_JSON = json.loads(
    (
        Path(__file__).parent.parent / "custom_components" / "engie_be" / "icons.json"
    ).read_text(encoding="utf-8")
)


def _module_level_descriptions(
    module: object, description_type: type
) -> list[tuple[str, SensorEntityDescription | BinarySensorEntityDescription]]:
    """
    Return every module-level attribute that is an entity description.

    Home Assistant's frozen-dataclass-compat shim rebinds entity
    description classes to a dynamically generated subclass at import
    time, so ``isinstance(value, description_type)`` does not match real
    instances. Compare type names instead.
    """
    return [
        (name, value)
        for name, value in inspect.getmembers(module)
        if type(value).__name__ == description_type.__name__
    ]


def test_every_sensor_description_has_an_icon() -> None:
    """Every module-level sensor description needs a device_class or icon."""
    icons = ICONS_JSON["entity"]["sensor"]
    for name, desc in _module_level_descriptions(sensor, SensorEntityDescription):
        has_icon = (
            desc.device_class is not None
            or desc.icon is not None
            or desc.translation_key in icons
        )
        assert has_icon, (
            f"{name} ({desc.translation_key}): no device_class, inline icon, "
            "or icons.json entry"
        )


def test_every_trigger_has_an_icon() -> None:
    """Every registered trigger needs an icons.json entry to render at all."""
    icons = ICONS_JSON["triggers"]
    missing = sorted(set(TRIGGERS) - set(icons))
    assert not missing, f"triggers with no icons.json entry: {missing}"


def test_every_condition_has_an_icon() -> None:
    """Every registered condition needs an icons.json entry to render at all."""
    icons = ICONS_JSON["conditions"]
    missing = sorted(set(CONDITIONS) - set(icons))
    assert not missing, f"conditions with no icons.json entry: {missing}"


def test_no_orphan_trigger_or_condition_icons() -> None:
    """
    Icon entries must not outlive the trigger or condition they name.

    A renamed key leaves its old icon behind, which then silently stops
    applying. Catching the orphan is the only signal that happened.
    """
    orphan_triggers = sorted(set(ICONS_JSON["triggers"]) - set(TRIGGERS))
    orphan_conditions = sorted(set(ICONS_JSON["conditions"]) - set(CONDITIONS))
    assert not orphan_triggers, (
        f"icons.json triggers with no trigger: {orphan_triggers}"
    )
    assert not orphan_conditions, (
        f"icons.json conditions with no condition: {orphan_conditions}"
    )


def test_every_binary_sensor_description_has_an_icon() -> None:
    """Every module-level binary_sensor description needs a device_class or icon."""
    icons = ICONS_JSON["entity"]["binary_sensor"]
    for name, desc in _module_level_descriptions(
        binary_sensor, BinarySensorEntityDescription
    ):
        has_icon = (
            desc.device_class is not None
            or desc.icon is not None
            or desc.translation_key in icons
        )
        assert has_icon, (
            f"{name} ({desc.translation_key}): no device_class, inline icon, "
            "or icons.json entry"
        )
