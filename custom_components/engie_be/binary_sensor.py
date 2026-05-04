"""Binary sensor platform for the ENGIE Belgium integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.util import dt as dt_util

from .const import KEY_EPEX, KEY_IS_DYNAMIC
from .data import EpexPayload
from .entity import EngieBeEntity

# Coordinator centralises updates; entities never poll individually.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EngieBeDataUpdateCoordinator
    from .data import EngieBeConfigEntry

AUTHENTICATION_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key="authentication",
    translation_key="authentication",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    icon="mdi:shield-check",
)

# EPEX "negative price right now" indicator.
#
# Exposed for every config entry so users can wire ``numeric_state``-free
# automations such as "run the dishwasher when wholesale is paying me".
# Reports ``unavailable`` on non-dynamic accounts and during outages so
# downstream automations don't fire on stale data.
EPEX_NEGATIVE_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key="epex_negative",
    translation_key="epex_negative",
    icon="mdi:cash-minus",
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up the binary sensor platform.

    The auth sensor is always added.  The EPEX negative-price indicator
    is only added on dynamic (EPEX-indexed) accounts: a fixed-tariff
    customer never has a wholesale price to flag, so the entity would
    be permanently ``unavailable`` and only add UI noise.  This mirrors
    how a contract switch is handled elsewhere in the integration: the
    coordinator detects ``is_dynamic`` at first refresh, and a contract
    change requires a config-entry reload to (re)create the entity.
    """
    coordinator = entry.runtime_data.coordinator
    entities: list[BinarySensorEntity] = [
        EngieBeAuthSensor(coordinator=coordinator, entry=entry),
    ]
    data = coordinator.data
    if isinstance(data, dict) and data.get(KEY_IS_DYNAMIC):
        entities.append(EngieBeEpexNegativeSensor(coordinator=coordinator))
    async_add_entities(entities)


class EngieBeAuthSensor(EngieBeEntity, BinarySensorEntity):
    """Binary sensor indicating whether the integration is authenticated."""

    entity_description = AUTHENTICATION_SENSOR_DESCRIPTION

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        entry: EngieBeConfigEntry,
    ) -> None:
        """Initialise the authentication binary sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_authentication"

    @property
    def available(self) -> bool:
        """Auth sensor is always available; its state reflects token validity."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True if the integration is currently authenticated."""
        return self._entry.runtime_data.authenticated


def _epex_payload(
    coordinator: EngieBeDataUpdateCoordinator,
) -> EpexPayload | None:
    """
    Return the cached EPEX payload, or ``None`` if not on a dynamic tariff.

    Mirrors the helper in ``sensor.py`` deliberately rather than importing
    across platform modules; HA platform modules are loaded independently
    and a cross-platform import would couple their lifecycles.
    """
    data = coordinator.data
    if not isinstance(data, dict):
        return None
    if not data.get(KEY_IS_DYNAMIC):
        return None
    payload = data.get(KEY_EPEX)
    return payload if isinstance(payload, EpexPayload) else None


class EngieBeEpexNegativeSensor(EngieBeEntity, BinarySensorEntity):
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

    def __init__(self, coordinator: EngieBeDataUpdateCoordinator) -> None:
        """Initialise the negative-price indicator."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_epex_negative"

    @property
    def available(self) -> bool:
        """
        Available only on dynamic accounts with a parsed payload.

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
