"""Base entities for the ENGIE Belgium integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.const import CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import mask_identifier
from .const import ATTRIBUTION, DOMAIN, LOGGER
from .coordinator import (
    EngieBeDataUpdateCoordinator,
    EngieBeEpexCoordinator,
    EngieBeEpexCoordinatorBase,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.config_entries import ConfigSubentry

    from .data import EngieBeConfigEntry

if TYPE_CHECKING:
    _MixinBase = CoordinatorEntity[Any]
else:
    _MixinBase = object


class _BoundaryScheduleMixin(_MixinBase):
    """
    Re-evaluate entity state at the next slot-boundary instant.

    MRO: this mixin MUST precede the entity's ``CoordinatorEntity`` base so
    ``async_added_to_hass`` and ``_handle_coordinator_update`` chain through.
    Subclasses override ``_next_boundary``. Returning ``None`` arms no timer.
    """

    _unsub_boundary: Callable[[], None] | None = None

    def _boundary_log_name(self) -> str:
        """Return a log-safe identifier ``<key>[***<last4-of-BAN>]``."""
        description = getattr(self, "entity_description", None)
        base = getattr(description, "key", None) or type(self).__name__
        entity_id: str | None = getattr(self, "entity_id", None)
        if entity_id and "engie_belgium_" in entity_id:
            tail = entity_id.split("engie_belgium_", 1)[1]
            ban = tail.split("_", 1)[0]
            if ban:
                return f"{base}[{mask_identifier(ban)}]"
        return base

    def _next_boundary(self) -> datetime | None:
        """Return the next UTC boundary datetime, or ``None`` to skip arming."""
        msg = (
            f"{type(self).__name__} must override _next_boundary to use "
            "_BoundaryScheduleMixin"
        )
        raise NotImplementedError(msg)

    async def async_added_to_hass(self) -> None:
        """Arm the next-boundary timer when the entity joins HA."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_boundary)
        self._schedule_next_boundary()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-arm the boundary timer against fresh coordinator data, then chain."""
        self._cancel_boundary()
        self._schedule_next_boundary()
        super()._handle_coordinator_update()

    @callback
    def _cancel_boundary(self) -> None:
        """Cancel any pending boundary timer and clear the handle."""
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None

    @callback
    def _schedule_next_boundary(self) -> None:
        """Arm a point-in-UTC-time callback at the next boundary."""
        target = self._next_boundary()
        if target is None or target <= dt_util.utcnow():
            LOGGER.debug(
                "%s: no future boundary to arm (next_boundary=%s)",
                self._boundary_log_name(),
                target.isoformat() if target is not None else None,
            )
            return
        self._unsub_boundary = async_track_point_in_utc_time(
            self.hass,
            self._boundary_fired,
            target,
        )
        LOGGER.debug(
            "%s: armed boundary timer for %s",
            self._boundary_log_name(),
            target.isoformat(),
        )

    @callback
    def _boundary_fired(self, _now: datetime) -> None:
        """Re-arm for the next boundary and write fresh state."""
        self._unsub_boundary = None
        LOGGER.debug(
            "%s: boundary fired at %s",
            self._boundary_log_name(),
            dt_util.utcnow().isoformat(),
        )
        self._schedule_next_boundary()
        self.async_write_ha_state()
        LOGGER.debug(
            "%s: wrote new value %r after boundary",
            self._boundary_log_name(),
            self._boundary_state_for_log(),
        )

    def _boundary_state_for_log(self) -> object:
        """Return ``is_on`` or ``native_value`` for logging."""
        if hasattr(self, "is_on"):
            return cast("Any", self).is_on
        return getattr(self, "native_value", None)


def subentry_device_info(subentry: ConfigSubentry) -> DeviceInfo:
    """Device info for the per-account device backing one subentry."""
    return DeviceInfo(
        identifiers={(DOMAIN, subentry.subentry_id)},
        manufacturer="ENGIE Belgium",
        name=subentry.title,
    )


def login_device_info(entry: EngieBeConfigEntry) -> DeviceInfo:
    """Device info for the per-login account device of one config entry."""
    username = entry.data.get(CONF_USERNAME, "")
    device_name = f"Account ({username})" if username else "Account"
    return DeviceInfo(
        identifiers={(DOMAIN, f"login_{entry.entry_id}")},
        manufacturer="ENGIE Belgium",
        name=device_name,
    )


class _EngieBeBaseEntity:
    """Pure mixin of common ENGIE entity attributes."""

    _attr_attribution: str | None = ATTRIBUTION
    _attr_has_entity_name = True


class EngieBeEntity(
    _EngieBeBaseEntity,
    CoordinatorEntity[EngieBeDataUpdateCoordinator],
):
    """Base for per-subentry ENGIE entities (one device per business agreement)."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialise the per-subentry entity."""
        super().__init__(coordinator)
        self._subentry = subentry
        self._attr_device_info = subentry_device_info(subentry)


class EngieBeEpexEntity(
    _EngieBeBaseEntity,
    CoordinatorEntity[EngieBeEpexCoordinatorBase],
):
    """EPEX entity base. Shared coordinator, entities surface per-subentry."""

    def __init__(
        self,
        coordinator: EngieBeEpexCoordinatorBase,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialise the EPEX entity bound to a subentry's device."""
        super().__init__(coordinator)
        self._subentry = subentry
        self._attr_device_info = subentry_device_info(subentry)


class EngieBeAuthEntity(
    _EngieBeBaseEntity,
    CoordinatorEntity[EngieBeDataUpdateCoordinator | EngieBeEpexCoordinator],
):
    """Per-entry login state entity, surfaced under a dedicated login device."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator | EngieBeEpexCoordinator,
        entry: EngieBeConfigEntry,
    ) -> None:
        """Initialise the per-entry login entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = login_device_info(entry)
