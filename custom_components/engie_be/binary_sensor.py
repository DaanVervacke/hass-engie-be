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

from ._epex import epex_payload, next_epex_slot_boundary
from ._happy_hour import happy_hour_window, is_happy_hour_active
from ._tou import current_slot as tou_current_slot
from ._tou import has_multiple_slot_codes, schedule_for_ean, tou_schedules_payload
from .api import mask_identifier
from .const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_EXPOSE_ALL_ENTITIES,
    LOGGER,
    SIGNAL_AUTHENTICATION_STATE_CHANGED,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
    TRANSLATION_KEY_EPEX_NEGATIVE,
    TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR,
)
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

    from .coordinator import (
        EngieBeDataUpdateCoordinator,
        EngieBeEpexCoordinator,
        EngieBeEpexQuarterHourCoordinator,
    )
    from .data import EngieBeConfigEntry

AUTHENTICATION_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key="authentication",
    translation_key="authentication",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)

# EPEX "negative price right now" indicator.
#
# Created per dynamic-tariff customer account so users can wire
# ``numeric_state``-free automations such as "run the dishwasher when
# wholesale is paying me".  Reports ``unavailable`` during outages so
# downstream automations don't fire on stale data.  Fixed-tariff
# accounts never get the entity at all.
EPEX_NEGATIVE_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key=TRANSLATION_KEY_EPEX_NEGATIVE,
    translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
)
EPEX_NEGATIVE_QUARTER_HOUR_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key=TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR,
    translation_key=TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR,
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
    expose_all = entry.options.get(CONF_EXPOSE_ALL_ENTITIES, False)
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
        if sub_data.feature_flags.happy_hour_enrolled or expose_all:
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
        if sub_data.coordinator.is_dynamic or expose_all:
            subentry_entities.append(
                EngieBeEpexNegativeSensor(
                    coordinator=epex_coordinator, subentry=subentry
                )
            )
            # Add QH negative sensor if QH coordinator exists
            epex_qh_coordinator = entry.runtime_data.epex_qh_coordinator
            if epex_qh_coordinator is not None:
                subentry_entities.append(
                    EngieBeEpexQuarterHourNegativeSensor(
                        coordinator=epex_qh_coordinator, subentry=subentry
                    )
                )

        # TOU "is optimal slot" binary sensors: created when the supplier
        # contract is TOU-active OR when the per-EAN schedule has more than
        # one distinct slot code (i.e. not a flat all-OFFPEAK schedule).
        # The gate avoids spamming "is optimal" on flat-rate accounts
        # where every hour is OFFPEAK and the answer is always True.
        tou_entities = _build_tou_binary_sensors(
            sub_data.coordinator, subentry, expose_all=expose_all
        )
        subentry_entities.extend(tou_entities)

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
        return epex_payload(self.coordinator) is not None

    @property
    def is_on(self) -> bool | None:
        """
        Return ``True`` when the slot covering ``now`` has a negative price.

        Returns ``None`` (rendered as ``unknown``) when no slot covers
        the current instant -- distinct from the unavailable case
        handled in ``available``.
        """
        payload = epex_payload(self.coordinator)
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
        payload = epex_payload(self.coordinator)
        if payload is None:
            return None
        return next_epex_slot_boundary(payload, dt_util.utcnow())


class EngieBeEpexQuarterHourNegativeSensor(
    _BoundaryScheduleMixin, EngieBeEpexEntity, BinarySensorEntity
):
    """
    Binary sensor that turns ``on`` when the current QH EPEX slot is negative.

    The wholesale leg of the user's bill is a credit (not a cost) during
    these slots; final delivered price still includes positive grid fees,
    taxes, and supplier margin, so this sensor flags the wholesale signal
    only.

    State semantics:

    * ``on`` / ``off`` -- a slot covers ``now`` and its price is
      negative (``< 0``) or non-negative (``>= 0``) respectively.
      Zero is treated as non-negative.
    * ``unknown`` (``is_on=None`` while available) -- payload present
      but no slot covers ``now`` (e.g. a multi-hour outage where the
      cached payload no longer covers the present instant).
    * ``unavailable`` -- no payload cached yet, or the QH coordinator is not set

    The ``_BoundaryScheduleMixin`` arms a point-in-UTC-time callback at
    the next slot boundary so the entity flips at the exact second the
    market moves between negative and non-negative slots.
    """

    entity_description = EPEX_NEGATIVE_QUARTER_HOUR_SENSOR_DESCRIPTION

    def __init__(
        self,
        coordinator: EngieBeEpexQuarterHourCoordinator,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialise the QH negative-price indicator."""
        super().__init__(coordinator, subentry)
        sub = subentry.subentry_id
        entry = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry}_{sub}_epex_negative_quarter_hour"
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = (
                f"binary_sensor.engie_belgium_{ban}_epex_negative_quarter_hour"
            )

    @property
    def available(self) -> bool:
        """Available only when the QH EPEX coordinator has a parsed payload."""
        if not super().available:
            return False
        return epex_payload(self.coordinator) is not None

    @property
    def is_on(self) -> bool | None:
        """
        Return ``True`` when the slot covering ``now`` has a negative price.

        Returns ``None`` (rendered as ``unknown``) when no slot covers
        the current instant -- distinct from the unavailable case
        handled in ``available``.
        """
        payload = epex_payload(self.coordinator)
        if payload is None:
            return None
        now = dt_util.utcnow()
        for slot in payload.slots:
            if slot.start <= now < slot.end:
                return slot.value_eur_per_kwh < 0
        return None

    def _next_boundary(self) -> datetime | None:
        """Return the next EPEX slot boundary in UTC, or ``None``."""
        payload = epex_payload(self.coordinator)
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


# ---------------------------------------------------------------------------
# TOU "is optimal slot" binary sensors
# ---------------------------------------------------------------------------

TOU_OFFTAKE_IS_OPTIMAL_DESCRIPTION = BinarySensorEntityDescription(
    key="tou_offtake_is_optimal",
    translation_key="tou_offtake_is_optimal",
)

TOU_INJECTION_IS_OPTIMAL_DESCRIPTION = BinarySensorEntityDescription(
    key="tou_injection_is_optimal",
    translation_key="tou_injection_is_optimal",
)


def _build_tou_binary_sensors(
    coordinator: EngieBeDataUpdateCoordinator,
    subentry: ConfigSubentry,
    *,
    expose_all: bool = False,
) -> list[BinarySensorEntity]:
    """
    Build TOU optimal-slot binary sensors for every electricity EAN.

    Gated on the ``dgo-tou-is-active`` feature flag mirroring the solar-
    surplus pattern: when the flag is off the coordinator skips the fetch
    entirely, so the wrapper is absent and there is nothing to key
    against. Only ``is_tou_active is True`` accounts get the binary sensors.
    """
    from .data import EngieBeSubentryData  # noqa: PLC0415, TC001 - avoid import cycle

    runtime = getattr(coordinator.config_entry, "runtime_data", None)
    sub_data: EngieBeSubentryData | None = (
        runtime.subentry_data.get(subentry.subentry_id) if runtime is not None else None
    )
    if sub_data is None:
        return []
    if sub_data.feature_flags.tou_active is not True and not expose_all:
        return []
    service_points = sub_data.service_points

    entities: list[BinarySensorEntity] = []
    for ean, division in service_points.items():
        if division != "ELECTRICITY":
            continue
        ean_suffix = f"{ean}_ID1"
        tou_data = tou_schedules_payload(coordinator)
        item = schedule_for_ean(tou_data, ean_suffix) if tou_data is not None else None
        offtake_sched = (
            item.get("supplierSchedule", {}).get("offtake", {})
            if isinstance(item, dict)
            else {}
        )
        injection_sched = (
            item.get("supplierSchedule", {}).get("injection", {})
            if isinstance(item, dict)
            else {}
        )
        # Suppress binary sensors on trivial (all-OFFPEAK) schedules where
        # the answer would always be True and add no automation value.
        show_offtake = expose_all or has_multiple_slot_codes(offtake_sched)
        show_injection = expose_all or has_multiple_slot_codes(injection_sched)
        if show_offtake:
            entities.append(
                EngieBeTouIsOptimalSensor(
                    coordinator=coordinator,
                    subentry=subentry,
                    entity_description=TOU_OFFTAKE_IS_OPTIMAL_DESCRIPTION,
                    ean=ean,
                    direction="offtake",
                )
            )
        if show_injection:
            entities.append(
                EngieBeTouIsOptimalSensor(
                    coordinator=coordinator,
                    subentry=subentry,
                    entity_description=TOU_INJECTION_IS_OPTIMAL_DESCRIPTION,
                    ean=ean,
                    direction="injection",
                )
            )
    return entities


class EngieBeTouIsOptimalSensor(
    _BoundaryScheduleMixin, EngieBeEntity, BinarySensorEntity
):
    """
    Binary sensor indicating whether the current TOU slot is the optimal one.

    ``on`` when the current slot code equals the schedule's
    ``optimalTimeslotCode``. Uses ``_BoundaryScheduleMixin`` so state
    flips at the exact slot boundary rather than the next coordinator
    refresh.

    Created only when the schedule is non-trivial (has more than one
    distinct slot code) or when the supplier contract is TOU-active.
    """

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: BinarySensorEntityDescription,
        ean: str,
        direction: str,
    ) -> None:
        """Initialise the is-optimal indicator."""
        super().__init__(coordinator, subentry)
        self.entity_description = entity_description
        self._ean = ean
        self._direction = direction
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{subentry.subentry_id}_{ean}_{entity_description.key}"
        )
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = (
                f"binary_sensor.engie_belgium_{ban}_{ean}_{entity_description.key}"
            )
        self._attr_translation_placeholders = {"ean": ean}

    def _supplier_schedule(self) -> dict | None:
        """Return the supplier direction schedule, or None."""
        tou_data = tou_schedules_payload(self.coordinator)
        if tou_data is None:
            return None
        ean_suffix = f"{self._ean}_ID1"
        item = schedule_for_ean(tou_data, ean_suffix)
        if not isinstance(item, dict):
            return None
        schedule = item.get("supplierSchedule")
        if not isinstance(schedule, dict):
            return None
        direction_sched = schedule.get(self._direction)
        return direction_sched if isinstance(direction_sched, dict) else None

    @property
    def is_on(self) -> bool | None:
        """Return True when the current slot is the optimal slot."""
        schedule = self._supplier_schedule()
        if schedule is None:
            return None
        optimal = schedule.get("optimalTimeslotCode")
        if not isinstance(optimal, str):
            return None
        code, _ = tou_current_slot(schedule, dt_util.utcnow())
        if code is None:
            return None
        return code == optimal.lower()

    def _next_boundary(self) -> datetime | None:
        """Return the next slot transition in UTC, or None."""
        schedule = self._supplier_schedule()
        if schedule is None:
            return None
        from datetime import UTC  # noqa: PLC0415

        now = dt_util.utcnow()
        _, next_trans = tou_current_slot(schedule, now)
        if next_trans is None:
            return None
        return next_trans.astimezone(UTC)
