"""Base entities for the ENGIE Belgium integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_USERNAME
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EngieBeDataUpdateCoordinator, EngieBeEpexCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

    from .data import EngieBeConfigEntry


class _EngieBeBaseEntity(CoordinatorEntity):  # type: ignore[type-arg]
    """Common attributes for every ENGIE Belgium entity."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True


class EngieBeEntity(
    _EngieBeBaseEntity,
    CoordinatorEntity[EngieBeDataUpdateCoordinator],
):
    """
    Base class for per-customer-account ENGIE entities.

    Each entity is bound to one ENGIE customer account (one
    :class:`ConfigSubentry`) and surfaces under the device representing
    that account in the device registry. ``unique_id`` strategy is the
    responsibility of subclasses, but ``DeviceInfo`` is unconditionally
    derived from the subentry so identifiers stay stable across renames
    and survive subentry deletion cleanup.
    """

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialise the per-subentry entity."""
        super().__init__(coordinator)
        self._subentry = subentry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            manufacturer="ENGIE Belgium",
            name=subentry.title,
        )


class EngieBeEpexEntity(
    _EngieBeBaseEntity,
    CoordinatorEntity[EngieBeEpexCoordinator],
):
    """
    Base class for EPEX entities attached to a customer-account device.

    EPEX day-ahead prices are polled once per parent :class:`ConfigEntry`
    by :class:`EngieBeEpexCoordinator`, but the entities themselves
    surface under each subentry's device so the user sees the EPEX
    sensors next to the supplier-price sensors for the matching account.
    Entity creation is gated upstream on the per-subentry
    ``is_dynamic`` flag, so users on fixed tariffs never see them.
    """

    def __init__(
        self,
        coordinator: EngieBeEpexCoordinator,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialise the EPEX entity bound to a subentry's device."""
        super().__init__(coordinator)
        self._subentry = subentry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            manufacturer="ENGIE Belgium",
            name=subentry.title,
        )


class EngieBeAuthEntity(
    _EngieBeBaseEntity,
    CoordinatorEntity[EngieBeDataUpdateCoordinator],
):
    """
    Base class for the per-entry login state entity.

    The auth state is account-agnostic (one login can own many ENGIE
    customer accounts) and is therefore surfaced under a dedicated
    per-entry device rather than being arbitrarily attached to one of
    the customer-account devices. The coordinator reference is required
    by :class:`CoordinatorEntity`; any per-subentry coordinator works
    because the entity does not consume coordinator data, it only
    reflects ``runtime_data.authenticated``.
    """

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator | EngieBeEpexCoordinator,
        entry: EngieBeConfigEntry,
    ) -> None:
        """Initialise the per-entry login entity."""
        super().__init__(coordinator)
        self._entry = entry
        username = entry.data.get(CONF_USERNAME, "")
        device_name = (
            f"Account ({username})"
            if username
            else "Account"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"login_{entry.entry_id}")},
            manufacturer="ENGIE Belgium",
            name=device_name,
        )
