"""Base entity for the Engie Belgium integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EngieBeDataUpdateCoordinator


class EngieBeEntity(CoordinatorEntity[EngieBeDataUpdateCoordinator]):
    """Base class for Engie Belgium entities."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: EngieBeDataUpdateCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            manufacturer="Engie Belgium",
            name="Engie Belgium",
        )
