"""Common fixtures for ENGIE Belgium tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.binary_sensor import BinarySensorEntity

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def _disable_relations_backfill(
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """
    Stub the relations-backfill side effect for all coordinator tests by default.

    The coordinator now calls ``client.async_get_customer_account_relations``
    once per refresh as a best-effort fill of subentry display fields.
    Pre-existing tests build their client with a bare ``MagicMock``, so the
    new ``await`` raises ``TypeError: 'MagicMock' object can't be awaited``
    even though the behaviour under test has nothing to do with backfill.

    This autouse fixture replaces the backfill method with a no-op for the
    duration of every test. Tests that explicitly exercise backfill
    behaviour can opt out with the ``backfill`` marker, e.g.::

        @pytest.mark.backfill
        async def test_backfill_does_something(...):
            ...

    Centralising the stub here avoids re-stubbing the relations method in
    every test client builder across four test files.
    """
    if "backfill" in request.keywords:
        yield
        return

    target = (
        "custom_components.engie_be.coordinator."
        "EngieBeDataUpdateCoordinator._async_try_backfill_subentry"
    )
    with patch(target, return_value=None):
        yield


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``backfill`` marker so opting out is documented."""
    config.addinivalue_line(
        "markers",
        "backfill: test exercises relations-backfill behaviour; do not stub it",
    )


@pytest.fixture
def add_sensor() -> Callable[[HomeAssistant, Entity], Awaitable[None]]:
    """
    Bind an entity to ``hass`` and drive ``async_added_to_hass``.

    Used by the boundary-scheduler tests across both binary_sensor and
    sensor platforms. Avoids spinning up a full config entry / platform
    pipeline: the scheduler behaviour under test depends only on
    ``self.hass`` being a real HomeAssistant instance, not on the entity
    being registered with a platform. The platform stub carries just
    enough attributes for ``async_write_ha_state`` to render a
    translation key.

    The fallback ``entity_id`` slug is derived from the entity's class
    name so binary sensors land under ``binary_sensor.`` and sensors
    under ``sensor.`` without per-call wiring.
    """

    async def _add(hass: HomeAssistant, entity: Entity) -> None:
        entity.hass = hass
        domain = "binary_sensor" if isinstance(entity, BinarySensorEntity) else "sensor"
        platform = MagicMock()
        platform.platform_name = "engie_be"
        platform.domain = domain
        entity.platform = platform
        if entity.entity_id is None:
            entity.entity_id = f"{domain}.test_{type(entity).__name__.lower()}"
        await entity.async_added_to_hass()

    return _add
