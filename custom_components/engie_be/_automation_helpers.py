"""
Shared helpers for automation triggers and conditions.

Extracted here because both ``trigger.py`` and ``condition.py`` use the same
entity-filter logic verbatim.  Keep this module lean - only add code that is
genuinely duplicated across both files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def filter_by_translation_key(
    hass: HomeAssistant,
    entities: set[str],
    translation_key: str,
) -> set[str]:
    """Return entities owned by this integration with the given translation_key."""
    reg = er.async_get(hass)
    result: set[str] = set()
    for entity_id in entities:
        entry = reg.async_get(entity_id)
        if (
            entry is not None
            and entry.platform == DOMAIN
            and entry.translation_key == translation_key
        ):
            result.add(entity_id)
    return result
