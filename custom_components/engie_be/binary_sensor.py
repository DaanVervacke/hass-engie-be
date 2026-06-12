"""Binary sensor platform for the ENGIE Belgium integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from ._epex import next_epex_slot_boundary
from ._happy_hour import happy_hour_window, is_happy_hour_active
from .api import mask_identifier
from .const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    LOGGER,
    SIGNAL_AUTHENTICATION_STATE_CHANGED,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from .data import EpexPayload
from .entity import (
    EngieBeAuthEntity,
    EngieBeEntity,
    EngieBeEpexEntity,
    _BoundaryScheduleMixin,
)

# Coordinator centralises updates; entities never poll individually.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import EngieBeDataUpdateCoordinator, EngieBeEpexCoordinator
    from .data import EngieBeConfigEntry

AUTHENTICATION_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key="authentication",
    translation_key="authentication",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
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

# Happy Hours active indicator.
#
# Created per Happy Hours-enrolled business agreement. The happy-hour
# endpoint is account scoped and not gated on dynamic tariff. The entity
# is available when created: ``on`` while the current moment falls inside
# a scheduled window, ``off`` otherwise (including when no event is
# scheduled). The companion timestamp sensors expose the "scheduled vs
# not scheduled" distinction.
HAPPY_HOUR_ACTIVE_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key="happy_hours_active",
    translation_key="happy_hours_active",
    icon="mdi:sun-clock",
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
    auth_backing_coordinator: EngieBeDataUpdateCoordinator | EngieBeEpexCoordinator = (
        epex_coordinator
    )
    for sub_data in entry.runtime_data.subentry_data.values():
        auth_backing_coordinator = sub_data.coordinator
        break

    async_add_entities(
        [EngieBeAuthSensor(coordinator=auth_backing_coordinator, entry=entry)]
    )

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_BUSINESS_AGREEMENT:
            continue

        sub_data = entry.runtime_data.subentry_data.get(subentry.subentry_id)
        if sub_data is None:
            LOGGER.warning(
                "No runtime data for subentry %s; skipping binary_sensor setup",
                subentry.subentry_id,
            )
            continue

        subentry_entities: list[BinarySensorEntity] = []
        # Only surface the Happy Hours active binary sensor when this
        # BAN is enrolled in the Happy Hours service. Enrolment is
        # detected from the feature-flags endpoint during the
        # coordinator's first refresh; the parent entry is reloaded
        # automatically when enrolment flips so entities track the
        # service status.
        if sub_data.is_happy_hour_enrolled:
            LOGGER.debug(
                "Subentry %s (BAN %s): enrolled in Happy Hours, "
                "registering happy_hours_active binary sensor",
                subentry.subentry_id,
                mask_identifier(sub_data.coordinator.business_agreement_number),
            )
            subentry_entities.append(
                EngieBeHappyHourActiveSensor(
                    coordinator=sub_data.coordinator, subentry=subentry
                ),
            )
        else:
            LOGGER.debug(
                "Subentry %s (BAN %s): not enrolled in Happy Hours, "
                "skipping happy_hours_active binary sensor",
                subentry.subentry_id,
                mask_identifier(sub_data.coordinator.business_agreement_number),
            )
        if sub_data.coordinator.is_dynamic:
            subentry_entities.append(
                EngieBeEpexNegativeSensor(
                    coordinator=epex_coordinator, subentry=subentry
                )
            )

        if not subentry_entities:
            continue

        async_add_entities(
            subentry_entities,
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

    async def async_added_to_hass(self) -> None:
        """Subscribe to login-scoped auth-state changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_AUTHENTICATION_STATE_CHANGED.format(
                    entry_id=self._entry.entry_id,
                ),
                self.async_write_ha_state,
            )
        )

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


class EngieBeEpexNegativeSensor(
    _BoundaryScheduleMixin, EngieBeEpexEntity, BinarySensorEntity
):
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

    The ``_BoundaryScheduleMixin`` arms a point-in-UTC-time callback at
    the next slot boundary so the entity flips at the exact second the
    market moves between negative and non-negative slots, rather than
    waiting up to a full coordinator refresh interval.
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
            f"{coordinator.config_entry.entry_id}_{subentry.subentry_id}_epex_negative"
        )
        # BAN-prefixed entity_id keeps the slug stable and collision-free
        # across multiple dynamic-tariff business agreements on one login.
        # Only effective on first registration; entity registry overrides
        # on subsequent boots.
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = f"binary_sensor.engie_belgium_{ban}_epex_negative"

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

    def _next_boundary(self) -> datetime | None:
        """
        Return the next EPEX slot boundary in UTC, or ``None``.

        Delegates to :func:`next_epex_slot_boundary` so the helper is
        shared with the EPEX current-price and next-hour sensors. When
        the cached payload is fully in the past (multi-hour outage),
        returns ``None``; the next coordinator update re-arms via
        :meth:`_handle_coordinator_update` once a fresh payload lands.
        """
        payload = _epex_payload(self.coordinator)
        if payload is None:
            return None
        return next_epex_slot_boundary(payload, dt_util.utcnow())


class EngieBeHappyHourActiveSensor(
    _BoundaryScheduleMixin, EngieBeEntity, BinarySensorEntity
):
    """
    Binary sensor that turns ``on`` during a scheduled Happy Hour window.

    Backed by the per-subentry data coordinator (NOT the EPEX
    coordinator): the happy-hour endpoint is account-scoped and the
    response is folded into the same payload as supplier prices and
    captar peaks.

    State semantics:

    * ``on``: the current instant falls inside a scheduled window.
    * ``off``: no scheduled window, or ``now`` is outside the window.

    The sensor is always available once the coordinator has data;
    ``off`` covers both "no event scheduled" and "scheduled but not
    active right now". Automations that need the distinction can
    consult the ``happy_hours_next_start`` / ``happy_hours_next_end``
    timestamp sensors (which are ``unknown`` when no window is
    scheduled).

    The ``_BoundaryScheduleMixin`` arms a point-in-UTC-time callback
    at the next window boundary so the entity flips on and off at the
    exact second the window starts and ends, rather than waiting up to
    a full coordinator refresh interval.
    """

    entity_description = HAPPY_HOUR_ACTIVE_SENSOR_DESCRIPTION

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialise the happy-hour active indicator."""
        super().__init__(coordinator, subentry)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{subentry.subentry_id}_happy_hours_active"
        )
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = f"binary_sensor.engie_belgium_{ban}_happy_hours_active"

    @property
    def is_on(self) -> bool:
        """Return True iff the current moment is inside a scheduled window."""
        return is_happy_hour_active(self.coordinator, dt_util.utcnow())

    def _next_boundary(self) -> datetime | None:
        """
        Return the next happy-hour boundary in UTC, or ``None``.

        Picks ``start`` while the window is still in the future, ``end``
        while we are inside it, and ``None`` once both endpoints are in
        the past (the next coordinator refresh either replaces the
        cached payload with the next day's window or with ``{}``).
        """
        window = happy_hour_window(self.coordinator)
        if window is None:
            return None
        start, end = window
        now = dt_util.utcnow()
        if now < start:
            return start
        if now < end:
            return end
        return None
