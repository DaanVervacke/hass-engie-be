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
from .coordinator import EngieBeDataUpdateCoordinator, EngieBeEpexCoordinator

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
    Mixin that re-evaluates entity state at the next "boundary" instant.

    Many ENGIE entities (Happy Hours active, EPEX-negative, EPEX
    current-price, EPEX next-hour) derive their state from a time
    window plus the current instant. Without help, they only refresh
    when their coordinator does, which can be up to a full refresh
    interval (default 60 minutes) late. This mixin schedules a single
    point-in-UTC-time callback at the next state-change instant and
    re-arms itself on fire, mirroring the pattern used by Home
    Assistant core's ``binary_sensor.tod`` integration.

    Concrete subclasses supply ``_next_boundary`` to describe when the
    next state change will occur. Returning ``None`` means "no future
    boundary is known yet" and arms no timer; the next coordinator
    update is expected to call ``_handle_coordinator_update`` and
    recompute.

    MRO requirement: this mixin MUST come before the entity's
    coordinator base (``EngieBeEntity``, ``EngieBeEpexEntity``, etc.)
    so that ``async_added_to_hass`` and ``_handle_coordinator_update``
    chain through ``super()`` to the ``CoordinatorEntity``
    implementation.
    """

    _unsub_boundary: Callable[[], None] | None = None

    def _boundary_log_name(self) -> str:
        """
        Return a log-safe, descriptive identifier for this entity.

        Combines the entity-description ``key`` (BAN-free and stable,
        e.g. ``happy_hours_active``) with the business-agreement number
        (BAN) masked to its last four digits. Boundary entities carry
        the BAN in their ``entity_id`` slug (e.g.
        ``binary_sensor.engie_belgium_<BAN>_happy_hours_active``), so it
        is masked before it reaches the log to keep shared debug bundles
        free of account numbers. Produces values like
        ``happy_hours_active[***6420]``; degrades to the description key
        alone, then the class name, when those parts are unavailable
        (e.g. before ``entity_id`` is assigned early in ``__init__``).
        """
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
        """
        Return the next UTC datetime at which this entity's state changes.

        Subclasses must override. Returning ``None`` skips arming the
        timer (e.g. payload not yet available, or every relevant
        window already in the past).
        """
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
        """
        Re-arm the boundary timer whenever the coordinator data changes.

        Cancels any pending timer, recomputes the next boundary against
        the freshly-published payload, and only then chains to the
        ``CoordinatorEntity`` implementation which performs the state
        write.
        """
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
        """
        Compute the next boundary and arm a point-in-UTC-time callback.

        No-op when ``_next_boundary`` returns ``None`` or returns a
        timestamp not strictly in the future (defensive: handles the
        case where the boundary helper races against ``dt_util.utcnow``
        and returns a value that has already elapsed).
        """
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
        """
        Re-arm for the boundary after this one and write fresh state.

        The HA scheduler delivers the current time as the callback's
        argument; we discard it because every consumer in this
        codebase reads ``dt_util.utcnow`` directly. Clearing
        ``_unsub_boundary`` first ensures the handle does not outlive
        the timer that has just fired.
        """
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
        """
        Return a cheap, log-safe snapshot of this entity's current value.

        Reads ``is_on`` for binary sensors and ``native_value`` for
        sensors. Deliberately avoids the HA ``state`` property, which on
        ``SensorEntity`` resolves the unit-of-measurement translation key
        and raises ``ValueError`` until the entity is fully added to a
        platform -- a state we do not want a debug log to depend on.
        Returns ``None`` when neither attribute is present.
        """
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
    """
    Common attributes shared by every ENGIE Belgium entity.

    Pure mixin: holds class-level attributes only and does not inherit
    from ``CoordinatorEntity``. Each concrete subclass inherits
    ``CoordinatorEntity[<concrete coordinator>]`` exactly once so the
    generic parameter is preserved end-to-end without forcing a
    type-arg suppression.
    """

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
        self._attr_device_info = subentry_device_info(subentry)


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
        self._attr_device_info = subentry_device_info(subentry)


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
        self._attr_device_info = login_device_info(entry)
