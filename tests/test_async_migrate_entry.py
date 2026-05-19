"""
Tests for :func:`custom_components.engie_be.async_migrate_entry`.

v0.9.0 is a breaking schema change: the v1->v2->v3->v4 migration chain
was deleted to drop ~3000 LOC of one-shot upgrade code. Users on any
pre-v0.9.0 install must remove and re-add the integration. The
migration hook now returns ``False`` for every prior version, which
causes Home Assistant to flag the entry as ``setup_error`` and
surface a Repairs notice.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be import async_migrate_entry
from custom_components.engie_be.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.parametrize("legacy_version", [1, 2, 3, 4])
async def test_async_migrate_entry_refuses_every_legacy_version(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    legacy_version: int,
) -> None:
    """Every pre-v5 version is rejected with an error-level log line."""
    entry: MockConfigEntry = MockConfigEntry(
        domain=DOMAIN,
        version=legacy_version,
        data={},
        options={},
    )
    entry.add_to_hass(hass)

    with caplog.at_level(logging.ERROR, logger="custom_components.engie_be"):
        result = await async_migrate_entry(hass, entry)

    assert result is False
    assert any(
        "Cannot migrate ENGIE Belgium config entry" in record.message
        and str(legacy_version) in record.message
        for record in caplog.records
    )
