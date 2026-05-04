"""Binary sensor platform for the ENGIE Belgium integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.util import dt as dt_util

from .const import LOGGER, SUBENTRY_TYPE_CUSTOMER_ACCOUNT
from .data import EpexPayload
from .entity import EngieBeAuthEntity, EngieBeEpexEntity

# Coordinator centralises updates; entities never poll individually.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import EngieBeDataUpdateCoordinator, EngieBeEpexCoordinator
    from .data import EngieBeConfigEntry

AUTHENTICATION_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key="authentication",
    translation_key="authentication",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    icon="mdi:shield-check",
)

# EPEX "negative price right now" indicator.
#
# Created per dynamic-tariff customer account so users can wire
# ``numeric_state``-free automations such as "run the dishwasher when
# wholesale is paying me".  Reports ``unavailable`` during outages so
# downstream automations don't fire on stale data.  Fixed-tariff
# accounts never get the entity at all.
EPEX_NEGATIVE_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key="epex_negative",
    translation_key="epex_negative",
    icon="mdi:cash-minus",
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """
    Set up the binary sensor platform.

    The auth sensor is created **once per parent config entry** because
    authentication is login-scoped, not account-scoped: a single ENGIE
    login holds one Auth0 session shared across all customer accounts.
    It attaches to a dedicated "login" device (no ``config_subentry_id``)
    rather than to any one customer-account device.

    The EPEX negative-price indicator is created **once per dynamic
    customer account** and attached to that account's device.  A
    fixed-tariff account never sees one: the coordinator detects
    ``is_dynamic`` at first refresh, and a contract change requires a
    config-entry reload to (re)create the entity.
    """
    epex_coordinator = entry.runtime_data.epex_coordinator

    # Pick any per-subentry coordinator to back the auth sensor's
    # CoordinatorEntity machinery.  The auth sensor doesn't consume
    # coordinator data -- it reflects ``runtime_data.authenticated`` --
    # but ``CoordinatorEntity`` requires a coordinator reference.  Fall
    # back to the EPEX coordinator if no customer-account subentries
    # exist yet (e.g. a future state where all accounts were removed).
    auth_backing_coordinator: (
        EngieBeDataUpdateCoordinator | EngieBeEpexCoordinator
    ) = epex_coordinator
    for sub_data in entry.runtime_data.subentry_data.values():
        auth_backing_coordinator = sub_data.coordinator
        break

    async_add_entities(
        [EngieBeAuthSensor(coordinator=auth_backing_coordinator, entry=entry)]
    )

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_CUSTOMER_ACCOUNT:
            continue

        sub_data = entry.runtime_data.subentry_data.get(subentry.subentry_id)
        if sub_data is None:
            LOGGER.warning(
                "No runtime data for subentry %s; skipping binary_sensor setup",
                subentry.subentry_id,
            )
            continue

        if not sub_data.coordinator.is_dynamic:
            continue

        async_add_entities(
            [
                EngieBeEpexNegativeSensor(
                    coordinator=epex_coordinator, subentry=subentry
                )
            ],
            config_subentry_id=subentry.subentry_id,
        )


class EngieBeAuthSensor(EngieBeAuthEntity, BinarySensorEntity):
    """Binary sensor indicating whether the integration is authenticated."""

    entity_description = AUTHENTICATION_SENSOR_DESCRIPTION

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator | EngieBeEpexCoordinator,
        entry: EngieBeConfigEntry,
    ) -> None:
        """Initialise the authentication binary sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_authentication"

    @property
    def available(self) -> bool:
        """Auth sensor is always available; its state reflects token validity."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True if the integration is currently authenticated."""
        return self._entry.runtime_data.authenticated


def _epex_payload(coordinator: EngieBeEpexCoordinator) -> EpexPayload | None:
    """Return the cached EPEX payload, or ``None`` if not yet available."""
    payload = coordinator.data
    return payload if isinstance(payload, EpexPayload) else None


class EngieBeEpexNegativeSensor(EngieBeEpexEntity, BinarySensorEntity):
    """
    Binary sensor that turns ``on`` when the current EPEX slot is negative.

    The wholesale leg of the user's bill is a credit (not a cost) during
    these slots; final delivered price still includes positive grid fees,
    taxes, and supplier margin, so this sensor flags the wholesale signal
    only.  No device class is set because none of the built-in classes
    (POWER, BATTERY_CHARGING, ...) describe a price-sign indicator.

    State semantics:

    * ``on`` / ``off`` -- a slot covers ``now`` and its price is
      negative (``< 0``) or non-negative (``>= 0``) respectively.
      Zero is treated as non-negative.
    * ``unknown`` (``is_on=None`` while available) -- payload present
      but no slot covers ``now`` (e.g. a multi-hour outage where the
      cached payload no longer covers the present instant).  Returning
      ``off`` here would falsely imply a non-negative price.
    * ``unavailable`` -- no payload cached yet (first poll 404), or
      the account silently flipped off the dynamic tariff between
      polls without a config-entry reload (defensive only; the entity
      isn't created at all on accounts that are non-dynamic at setup).
    """

    entity_description = EPEX_NEGATIVE_SENSOR_DESCRIPTION

    def __init__(
        self,
        coordinator: EngieBeEpexCoordinator,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialise the negative-price indicator."""
        super().__init__(coordinator, subentry)
        # Subentry-scoped unique ID: the same EPEX-negative descriptor
        # repeats across every dynamic-tariff customer account on a
        # single login.
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{subentry.subentry_id}_epex_negative"
        )

    @property
    def available(self) -> bool:
        """
        Available only when the EPEX coordinator has a parsed payload.

        Per HA's integration-quality-scale guidance: an entity is
        ``unavailable`` when data cannot be fetched, but ``unknown``
        when the fetch succeeded yet a specific datum is missing.
        Here, "no payload" is the unavailable case; "payload present
        but no slot covers ``now``" is handled by ``is_on`` returning
        ``None`` (which surfaces as ``unknown``).
        """
        if not super().available:
            return False
        return _epex_payload(self.coordinator) is not None

    @property
    def is_on(self) -> bool | None:
        """
        Return ``True`` when the slot covering ``now`` has a negative price.

        Returns ``None`` (rendered as ``unknown``) when no slot covers
        the current instant -- distinct from the unavailable case
        handled in ``available``.
        """
        payload = _epex_payload(self.coordinator)
        if payload is None:
            return None
        now = dt_util.utcnow()
        for slot in payload.slots:
            if slot.start <= now < slot.end:
                return slot.value_eur_per_kwh < 0
        return None
