"""
Guard test: every module-level entity description must have an icon.

An entity shows a sensible icon when it either has a ``device_class``
(HA supplies a default icon), an inline ``icon=`` on its description, or
a matching entry in ``icons.json``. This gap has recurred more than once
(see plans/epex-current-icon-plan.md, plans/trigger-condition-icon-audit-plan.md,
plans/139-tou-optimal-missing-icons.md) so this test pins coverage for
every description defined at module level in sensor.py and binary_sensor.py.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.sensor import SensorEntityDescription

from custom_components.engie_be import binary_sensor, sensor

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
