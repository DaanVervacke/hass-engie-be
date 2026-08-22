"""Binary sensor platform for the ENGIE Belgium integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from ._contracts import ean_with_delivery_point_suffix
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

# Coordinator centralises updates. Entities never poll individually.
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

EPEX_NEGATIVE_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key=TRANSLATION_KEY_EPEX_NEGATIVE,
    translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
)
EPEX_NEGATIVE_QUARTER_HOUR_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key=TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR,
    translation_key=TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR,
)

HAPPY_HOUR_ACTIVE_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key="happy_hours_active",
    translation_key="happy_hours_active",
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary_sensors: login-scoped auth plus per-subentry entities."""
    expose_all = entry.options.get(CONF_EXPOSE_ALL_ENTITIES, False)
    epex_coordinator = entry.runtime_data.epex_coordinator

    # CoordinatorEntity needs a coordinator reference. The auth sensor never reads it.
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

        runtime_data = entry.runtime_data.subentry_data.get(subentry.subentry_id)
        if runtime_data is None:
            LOGGER.warning(
                "No runtime data for subentry %s; skipping binary_sensor setup",
                subentry.subentry_id,
            )
            continue

        subentry_entities: list[BinarySensorEntity] = []
        if runtime_data.feature_flags.happy_hour_enrolled or expose_all:
            LOGGER.debug(
                "Subentry %s (BAN %s): enrolled in Happy Hours, "
                "registering happy_hours_active binary sensor",
                subentry.subentry_id,
                mask_identifier(runtime_data.coordinator.business_agreement_number),
            )
            subentry_entities.append(
                EngieBeHappyHourActiveSensor(
                    coordinator=runtime_data.coordinator, subentry=subentry
                ),
            )
        else:
            LOGGER.debug(
                "Subentry %s (BAN %s): not enrolled in Happy Hours, "
                "skipping happy_hours_active binary sensor",
                subentry.subentry_id,
                mask_identifier(runtime_data.coordinator.business_agreement_number),
            )
        if runtime_data.coordinator.is_dynamic or expose_all:
            subentry_entities.append(
                EngieBeEpexNegativeSensor(
                    coordinator=epex_coordinator, subentry=subentry
                )
            )
            # Add QH negative sensor if QH coordinator exists
            epex_qh_coordinator = entry.runtime_data.epex_qh_coordinator
            if epex_qh_coordinator is not None:
                subentry_entities.append(
                    EngieBeEpexNegativeSensor(
                        coordinator=epex_qh_coordinator,
                        subentry=subentry,
                        description=EPEX_NEGATIVE_QUARTER_HOUR_SENSOR_DESCRIPTION,
                        suffix="epex_negative_quarter_hour",
                    )
                )

        # TOU "is optimal" gated on TOU-active + non-trivial schedule.
        tou_entities = _build_tou_binary_sensors(
            runtime_data.coordinator, subentry, expose_all=expose_all
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
        """Auth sensor is always available. Its state reflects token validity."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True if the integration is currently authenticated."""
        return self._entry.runtime_data.authenticated


class EngieBeEpexNegativeSensor(
    _BoundaryScheduleMixin, EngieBeEpexEntity, BinarySensorEntity
):
    """
    Binary sensor ``on`` when the EPEX slot covering ``now`` has a negative price.

    ``unknown`` when the cached payload contains no covering slot.
    ``unavailable`` when no payload is cached at all.
    """

    def __init__(
        self,
        coordinator: EngieBeEpexCoordinator | EngieBeEpexQuarterHourCoordinator,
        subentry: ConfigSubentry,
        description: BinarySensorEntityDescription = EPEX_NEGATIVE_SENSOR_DESCRIPTION,
        suffix: str = "epex_negative",
    ) -> None:
        """Initialise the negative-price indicator."""
        self.entity_description = description
        super().__init__(coordinator, subentry)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{subentry.subentry_id}_{suffix}"
        )
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = f"binary_sensor.engie_belgium_{ban}_{suffix}"

    @property
    def available(self) -> bool:
        """Available only when the EPEX coordinator has a parsed payload."""
        if not super().available:
            return False
        return epex_payload(self.coordinator) is not None

    @property
    def is_on(self) -> bool | None:
        """Return True/False for a slot covering ``now``, or ``None`` when none does."""
        payload = epex_payload(self.coordinator)
        if payload is None:
            return None
        now = dt_util.utcnow()
        for slot in payload.slots:
            if slot.start <= now < slot.end:
                return slot.value_eur_per_kwh < 0
        return None

    def _next_boundary(self) -> datetime | None:
        """Return the next EPEX slot boundary in UTC, or ``None`` if payload is past."""
        payload = epex_payload(self.coordinator)
        if payload is None:
            return None
        return next_epex_slot_boundary(payload, dt_util.utcnow())


class EngieBeHappyHourActiveSensor(
    _BoundaryScheduleMixin, EngieBeEntity, BinarySensorEntity
):
    """Binary sensor ``on`` during a scheduled Happy Hours window."""

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
        """Return the next happy-hour boundary in UTC, or ``None`` if all past."""
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
    """Build TOU optimal-slot binary sensors per electricity EAN."""
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
        ean_suffix = ean_with_delivery_point_suffix(ean)
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
    """Binary sensor ``on`` when the current TOU slot equals the optimal slot code."""

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

    def _supplier_schedule(self) -> dict[str, Any] | None:
        """Return the supplier direction schedule, or None."""
        tou_data = tou_schedules_payload(self.coordinator)
        if tou_data is None:
            return None
        ean_suffix = ean_with_delivery_point_suffix(self._ean)
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
        optimal = schedule.get("optimal_slot_code")
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
