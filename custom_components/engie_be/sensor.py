"""Sensor platform for the ENGIE Belgium integration."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CURRENCY_EURO,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.util import dt as dt_util

from ._epex import next_epex_slot_boundary
from ._happy_hour import happy_hour_window
from ._peaks import peaks_meta, peaks_payload
from .api import mask_identifier
from .const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    EPEX_TZ,
    LOGGER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from .data import EpexPayload
from .entity import EngieBeEntity, EngieBeEpexEntity, _BoundaryScheduleMixin

# Coordinator centralises updates; entities never poll individually.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import EngieBeDataUpdateCoordinator, EngieBeEpexCoordinator
    from .data import EngieBeConfigEntry


# Mapping from service-point division to display name.
_DIVISION_MAP: dict[str, str] = {
    "ELECTRICITY": "Electricity",
    "GAS": "Gas",
}


def _detect_energy_type(ean: str, service_points: dict[str, str]) -> str:
    """Detect the energy type from the service-points division lookup."""
    division = service_points.get(ean, "")
    return _DIVISION_MAP.get(division, "Energy")


def _find_current_price(prices: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the price entry whose date range covers today, or the last entry."""
    today = datetime.now(tz=UTC).date()
    for price in prices:
        from_date = date.fromisoformat(price["from"])
        to_date = date.fromisoformat(price["to"])
        if from_date <= today < to_date:
            return price
    # Fall back to the last entry if no exact match
    return prices[-1] if prices else None


# Mapping from normalised rate code to (key_suffix, translation_suffix).
# TOTAL_HOURS uses empty strings to preserve backward compatibility.
# A ``None`` value means "skip this entry entirely" (e.g. blended rates).
_SLOT_CODE_MAP: dict[str, tuple[str, str] | None] = {
    "TOTAL_HOURS": ("", ""),
    "PEAK": ("_peak", "_peak"),
    "OFFPEAK": ("_offpeak", "_offpeak"),
    "SUPEROFFPEAK": ("_superoffpeak", "_superoffpeak"),
    "EN": None,  # blended/total rate - skipped
}

# Direction keywords used to split prefixed slot codes.
_DIRECTION_KEYWORDS = ("OFFTAKE_", "INJECTION_")


def _normalize_slot_code(raw_code: str) -> str:
    """
    Normalise a raw ``timeOfUseSlotCode`` to its rate portion.

    Bare codes (``TOTAL_HOURS``, ``PEAK``, ``OFFPEAK``) are returned as-is.
    Prefixed codes (e.g. ``S_TOU1_OFFTAKE_PEAK``) are stripped down to the
    part after the last direction keyword (``OFFTAKE_`` / ``INJECTION_``).
    """
    for keyword in _DIRECTION_KEYWORDS:
        idx = raw_code.rfind(keyword)
        if idx != -1:
            return raw_code[idx + len(keyword) :]
    return raw_code


def _slot_suffixes(slot_code: str) -> tuple[str, str] | None:
    """
    Return (key_suffix, translation_suffix) for a time-of-use slot code.

    Returns ``None`` when the code should be skipped entirely.
    """
    normalised = _normalize_slot_code(slot_code)
    if normalised in _SLOT_CODE_MAP:
        return _SLOT_CODE_MAP[normalised]
    # Unknown codes: use lowercased normalised code as suffix
    lower = normalised.lower()
    return (f"_{lower}", f"_{lower}")


def _build_sensor_descriptions(
    data: dict[str, Any],
    service_points: dict[str, str],
) -> list[tuple[SensorEntityDescription, str, str, str]]:
    """
    Build sensor descriptions from the API response.

    Returns a list of ``(description, ean, value_key, slot_code)`` tuples where
    *value_key* is a dotted path like ``offtake.priceValue`` and *slot_code* is
    the ``timeOfUseSlotCode`` (e.g. ``TOTAL_HOURS``, ``PEAK``, ``OFFPEAK``).
    """
    sensors: list[tuple[SensorEntityDescription, str, str, str]] = []

    for item in data.get("items", []):
        ean: str = item.get("ean", "unknown")
        energy_type = _detect_energy_type(ean, service_points)
        # Strip trailing _ID* suffix for display
        # e.g. "541448...267_ID1" -> cleaner key
        ean_short = ean.split("_", maxsplit=1)[0] if "_" in ean else ean

        current_price = _find_current_price(item.get("prices", []))
        if current_price is None:
            continue

        configs = current_price.get("proportionalPriceConfigurations", {})

        unit = "EUR/kWh"

        for direction in ("offtake", "injection"):
            direction_list: list[dict[str, Any]] = configs.get(direction, [])
            if not direction_list:
                continue

            for slot_entry in direction_list:
                slot_code: str = slot_entry.get("timeOfUseSlotCode", "TOTAL_HOURS")
                suffixes = _slot_suffixes(slot_code)
                if suffixes is None:
                    continue
                key_suffix, trans_suffix = suffixes

                base_key = f"{ean_short}_{direction}{key_suffix}"
                base_trans = f"{energy_type.lower()}_{direction}{trans_suffix}"

                # Price including VAT
                sensors.append(
                    (
                        SensorEntityDescription(
                            key=base_key,
                            translation_key=base_trans,
                            native_unit_of_measurement=unit,
                            state_class=SensorStateClass.MEASUREMENT,
                            suggested_display_precision=6,
                        ),
                        ean,
                        f"{direction}.priceValue",
                        slot_code,
                    )
                )
                # Price excluding VAT — disabled by default; available for
                # users who need the pre-VAT value for accounting purposes.
                sensors.append(
                    (
                        SensorEntityDescription(
                            key=f"{base_key}_excl_vat",
                            translation_key=f"{base_trans}_excl_vat",
                            native_unit_of_measurement=unit,
                            state_class=SensorStateClass.MEASUREMENT,
                            suggested_display_precision=6,
                            entity_registry_enabled_default=False,
                        ),
                        ean,
                        f"{direction}.priceValueExclVAT",
                        slot_code,
                    )
                )

    return sensors


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """
    Set up the sensor platform.

    Builds entities once per :class:`ConfigSubentry` of type
    ``business_agreement``. Energy-price and peak sensors come from the
    per-subentry coordinator; EPEX sensors come from the entry-level
    EPEX coordinator and are gated on the per-subentry ``is_dynamic``
    flag so users on a fixed tariff never see them.
    """
    epex_coordinator = entry.runtime_data.epex_coordinator

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_BUSINESS_AGREEMENT:
            continue

        sub_data = entry.runtime_data.subentry_data.get(subentry.subentry_id)
        if sub_data is None:
            LOGGER.warning(
                "No runtime data for subentry %s; skipping sensor setup",
                subentry.subentry_id,
            )
            continue

        coordinator = sub_data.coordinator
        if coordinator.data is None:
            LOGGER.warning(
                "No data available yet for subentry %s; skipping sensor setup",
                subentry.subentry_id,
            )
            continue

        sensor_defs = _build_sensor_descriptions(
            coordinator.data,
            sub_data.service_points,
        )
        entities: list[SensorEntity] = [
            EngieBeEnergySensor(
                coordinator=coordinator,
                subentry=subentry,
                entity_description=desc,
                ean=ean,
                value_key=value_key,
                slot_code=slot_code,
            )
            for desc, ean, value_key, slot_code in sensor_defs
        ]
        entities.extend(_build_peak_sensors(coordinator, subentry))
        # Only surface Happy Hours timestamp sensors when this BAN is
        # enrolled in the Happy Hours service. Enrolment is detected
        # from the feature-flags endpoint during the coordinator's
        # first refresh; the parent entry is reloaded automatically
        # when enrolment flips so entities track the service status.
        if sub_data.is_happy_hour_enrolled:
            happy_hour_sensors = _build_happy_hour_sensors(coordinator, subentry)
            LOGGER.debug(
                "Subentry %s (BAN %s): enrolled in Happy Hours, "
                "registering %d Happy Hours timestamp sensors",
                subentry.subentry_id,
                mask_identifier(coordinator.business_agreement_number),
                len(happy_hour_sensors),
            )
            entities.extend(happy_hour_sensors)
            entities.extend(
                _build_happy_hour_month_report_sensors(coordinator, subentry)
            )
        else:
            LOGGER.debug(
                "Subentry %s (BAN %s): not enrolled in Happy Hours, "
                "skipping Happy Hours timestamp sensors",
                subentry.subentry_id,
                mask_identifier(coordinator.business_agreement_number),
            )
        if coordinator.is_dynamic:
            entities.extend(_build_epex_sensors(epex_coordinator, subentry))

        async_add_entities(entities, config_subentry_id=subentry.subentry_id)


# ---------------------------------------------------------------------------
# Capacity-tariff (captar) peak sensors
# ---------------------------------------------------------------------------

_CAPTAR_MONTHLY_PEAK_POWER = SensorEntityDescription(
    key="captar_monthly_peak_power",
    translation_key="captar_monthly_peak_power",
    native_unit_of_measurement=UnitOfPower.KILO_WATT,
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=3,
)
_CAPTAR_MONTHLY_PEAK_ENERGY = SensorEntityDescription(
    key="captar_monthly_peak_energy",
    translation_key="captar_monthly_peak_energy",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    # No state_class: this is a snapshot of one 15-min peak window's energy,
    # not a measurement, total, or total_increasing. HA rejects ENERGY +
    # MEASUREMENT at runtime; TOTAL would require last_reset semantics that
    # don't fit a sliding monthly peak.
    suggested_display_precision=3,
    # Disabled by default: this is a raw measurement that most users don't
    # need; the peak power (kW) is the value used for the capacity tariff
    # calculation and is always enabled.
    entity_registry_enabled_default=False,
)
_CAPTAR_MONTHLY_PEAK_START = SensorEntityDescription(
    key="captar_monthly_peak_start",
    translation_key="captar_monthly_peak_start",
    device_class=SensorDeviceClass.TIMESTAMP,
    # Diagnostic + disabled by default: timestamp detail is contextual
    # information about the peak power value; users can enable if they
    # want the raw timestamps.
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)
_CAPTAR_MONTHLY_PEAK_END = SensorEntityDescription(
    key="captar_monthly_peak_end",
    translation_key="captar_monthly_peak_end",
    device_class=SensorDeviceClass.TIMESTAMP,
    # See captar_monthly_peak_start.
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)


def _build_peak_sensors(
    coordinator: EngieBeDataUpdateCoordinator,
    subentry: ConfigSubentry,
) -> list[SensorEntity]:
    """Build the four monthly capacity-tariff peak sensors for one subentry."""
    return [
        EngieBeMonthlyPeakValueSensor(
            coordinator,
            subentry,
            _CAPTAR_MONTHLY_PEAK_POWER,
            field="peakKW",
        ),
        EngieBeMonthlyPeakValueSensor(
            coordinator,
            subentry,
            _CAPTAR_MONTHLY_PEAK_ENERGY,
            field="peakKWh",
        ),
        EngieBeMonthlyPeakTimestampSensor(
            coordinator,
            subentry,
            _CAPTAR_MONTHLY_PEAK_START,
            field="start",
        ),
        EngieBeMonthlyPeakTimestampSensor(
            coordinator,
            subentry,
            _CAPTAR_MONTHLY_PEAK_END,
            field="end",
        ),
    ]


class _EngieBePeakSensorBase(EngieBeEntity, SensorEntity):
    """Common base for capacity-tariff peak sensors."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
    ) -> None:
        """Initialise the peak sensor with its coordinator and description."""
        super().__init__(coordinator, subentry)
        self.entity_description = entity_description
        # Unique IDs are now subentry-scoped: peak descriptions repeat
        # across customer accounts, so plain ``{entry_id}_{key}`` would
        # collide between subentries on the same login.
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{subentry.subentry_id}_{entity_description.key}"
        )
        # Force a BAN-prefixed entity_id so two business agreements
        # on the same login never collide on the translated friendly
        # name. ``_attr_suggested_object_id`` is not honoured by
        # ``Entity.suggested_object_id`` (which reads ``self.name``);
        # setting ``self.entity_id`` directly is the supported escape
        # hatch. Only effective on first registration; entity
        # registry overrides on subsequent boots.
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = f"sensor.engie_belgium_{ban}_{entity_description.key}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return last-fetched timestamp plus active peak month metadata."""
        attrs: dict[str, Any] = {}
        if self.coordinator.last_successful_fetch:
            attrs["last_fetched"] = self.coordinator.last_successful_fetch.isoformat()
        meta = peaks_meta(self.coordinator)
        if meta is not None:
            attrs["peak_month"] = f"{meta['year']:04d}-{meta['month']:02d}"
            attrs["peak_is_fallback"] = meta["is_fallback"]
        return attrs


class EngieBeMonthlyPeakValueSensor(_EngieBePeakSensorBase):
    """Numeric monthly capacity-tariff peak value (kW or kWh)."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
        field: str,
    ) -> None:
        """Track which numeric field of ``peakOfTheMonth`` to expose."""
        super().__init__(coordinator, subentry, entity_description)
        self._field = field

    @property
    def native_value(self) -> float | None:
        """Return the configured numeric field of the monthly peak."""
        peaks = peaks_payload(self.coordinator)
        if peaks is None:
            return None
        monthly = peaks.get("peakOfTheMonth")
        if not isinstance(monthly, dict):
            return None
        value = monthly.get(self._field)
        if value is None:
            return None
        try:
            return float(value)
        except TypeError:
            return None
        except ValueError:
            return None


class EngieBeMonthlyPeakTimestampSensor(_EngieBePeakSensorBase):
    """Start or end timestamp of the monthly capacity-tariff peak window."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
        field: str,
    ) -> None:
        """Track which timestamp field (``start`` or ``end``) to expose."""
        super().__init__(coordinator, subentry, entity_description)
        self._field = field

    @property
    def native_value(self) -> datetime | None:
        """Return the parsed ISO 8601 timestamp, or ``None`` if unavailable."""
        peaks = peaks_payload(self.coordinator)
        if peaks is None:
            return None
        monthly = peaks.get("peakOfTheMonth")
        if not isinstance(monthly, dict):
            return None
        raw = monthly.get(self._field)
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# Happy Hours sensors
# ---------------------------------------------------------------------------

_HAPPY_HOUR_NEXT_START = SensorEntityDescription(
    key="happy_hours_next_start",
    translation_key="happy_hours_next_start",
    device_class=SensorDeviceClass.TIMESTAMP,
)
_HAPPY_HOUR_NEXT_END = SensorEntityDescription(
    key="happy_hours_next_end",
    translation_key="happy_hours_next_end",
    device_class=SensorDeviceClass.TIMESTAMP,
)


def _build_happy_hour_sensors(
    coordinator: EngieBeDataUpdateCoordinator,
    subentry: ConfigSubentry,
) -> list[SensorEntity]:
    """Build the start/end timestamp sensors for the next Happy Hour window."""
    return [
        EngieBeHappyHourTimestampSensor(
            coordinator,
            subentry,
            _HAPPY_HOUR_NEXT_START,
            field="start",
        ),
        EngieBeHappyHourTimestampSensor(
            coordinator,
            subentry,
            _HAPPY_HOUR_NEXT_END,
            field="end",
        ),
    ]


class EngieBeHappyHourTimestampSensor(EngieBeEntity, SensorEntity):
    """Start or end of the next upcoming Happy Hour window."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
        field: str,
    ) -> None:
        """Initialise the sensor, recording which window endpoint to expose."""
        super().__init__(coordinator, subentry)
        self.entity_description = entity_description
        # Field is either "start" or "end": which end of the
        # ``happy_hour_window`` tuple to expose.
        self._field = field
        # Subentry-scoped unique ID, matching the peak-sensor convention.
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{subentry.subentry_id}_{entity_description.key}"
        )
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = f"sensor.engie_belgium_{ban}_{entity_description.key}"

    @property
    def native_value(self) -> datetime | None:
        """Return the start or end of the next happy-hour window, if any."""
        window = happy_hour_window(self.coordinator)
        if window is None:
            return None
        start, end = window
        return start if self._field == "start" else end


# ---------------------------------------------------------------------------
# Happy Hours month-report sensors
# ---------------------------------------------------------------------------

_HAPPY_HOUR_MONTH_CONSUMPTION = SensorEntityDescription(
    key="happy_hours_month_consumption",
    translation_key="happy_hours_month_consumption",
    icon="mdi:lightning-bolt",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL,
    suggested_display_precision=3,
)
_HAPPY_HOUR_MONTH_ELIGIBLE_HOURS = SensorEntityDescription(
    key="happy_hours_month_eligible_hours",
    translation_key="happy_hours_month_eligible_hours",
    icon="mdi:clock-check",
    # Count of discrete Happy Hours windows — no standard unit applies.
    native_unit_of_measurement=None,
    state_class=SensorStateClass.TOTAL_INCREASING,
    suggested_display_precision=0,
)
_HAPPY_HOUR_MONTH_REWARD = SensorEntityDescription(
    key="happy_hours_month_reward",
    translation_key="happy_hours_month_reward",
    icon="mdi:cash-plus",
    native_unit_of_measurement=CURRENCY_EURO,
    device_class=SensorDeviceClass.MONETARY,
    state_class=SensorStateClass.TOTAL,
    suggested_display_precision=2,
)


def _month_report_wrapper(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """Return the raw happy_hour_month_report wrapper dict, or None."""
    if not isinstance(coordinator.data, dict):
        return None
    wrapper = coordinator.data.get("happy_hour_month_report")
    return wrapper if isinstance(wrapper, dict) else None


def _month_report_payload(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """Unwrap the happy_hour_month_report coordinator key, or return None."""
    wrapper = _month_report_wrapper(coordinator)
    if wrapper is None:
        return None
    payload = wrapper.get("data")
    return payload if isinstance(payload, dict) else None


def _build_happy_hour_month_report_sensors(
    coordinator: EngieBeDataUpdateCoordinator,
    subentry: ConfigSubentry,
) -> list[SensorEntity]:
    """Build the three Happy Hours month-report sensors for one subentry."""
    return [
        EngieBeHappyHourMonthSensor(
            coordinator,
            subentry,
            _HAPPY_HOUR_MONTH_CONSUMPTION,
            path=("month", "happyHour", "consumptionKWh"),
        ),
        EngieBeHappyHourMonthSensor(
            coordinator,
            subentry,
            _HAPPY_HOUR_MONTH_ELIGIBLE_HOURS,
            path=("month", "happyHour", "numberOfEligibleHappyHours"),
        ),
        EngieBeHappyHourMonthRewardSensor(
            coordinator,
            subentry,
        ),
    ]


class EngieBeHappyHourMonthSensor(EngieBeEntity, SensorEntity):
    """A single field from the Happy Hours month report."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
        path: tuple[str, ...],
    ) -> None:
        """Initialise the sensor, recording the dotted path into the payload."""
        super().__init__(coordinator, subentry)
        self.entity_description = entity_description
        self._path = path
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{subentry.subentry_id}_{entity_description.key}"
        )
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = f"sensor.engie_belgium_{ban}_{entity_description.key}"

    def _resolve_path(self, data: dict[str, Any], path: tuple[str, ...]) -> Any:
        """Walk *path* through nested dicts, returning the leaf or None."""
        node: Any = data
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node

    @property
    def native_value(self) -> float | None:
        """Return the numeric value at the configured path."""
        payload = _month_report_payload(self.coordinator)
        if payload is None:
            return None
        raw = self._resolve_path(payload, self._path)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose report month metadata and last-fetched timestamp."""
        attrs: dict[str, Any] = {}
        if self.coordinator.last_successful_fetch:
            attrs["last_fetched"] = self.coordinator.last_successful_fetch.isoformat()
        wrapper = _month_report_wrapper(self.coordinator)
        if wrapper is not None:
            year = wrapper.get("year")
            month = wrapper.get("month")
            if isinstance(year, int) and isinstance(month, int):
                attrs["report_month"] = f"{year:04d}-{month:02d}"
            is_fallback = wrapper.get("is_fallback")
            if is_fallback is not None:
                attrs["report_is_fallback"] = bool(is_fallback)
        return attrs


class EngieBeHappyHourMonthRewardSensor(EngieBeHappyHourMonthSensor):
    """Reward sensor that also exposes the ``isCalculationOngoing`` flag."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialise with the fixed reward description and path."""
        super().__init__(
            coordinator,
            subentry,
            _HAPPY_HOUR_MONTH_REWARD,
            path=("month", "happyHour", "rewardEuros"),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Extend base attributes with the calculation-in-progress flag."""
        attrs = super().extra_state_attributes
        payload = _month_report_payload(self.coordinator)
        if payload is not None:
            flag = self._resolve_path(
                payload, ("month", "happyHour", "isCalculationOngoing")
            )
            if flag is not None:
                attrs["is_calculation_ongoing"] = bool(flag)
        return attrs


class EngieBeEnergySensor(EngieBeEntity, SensorEntity):
    """Sensor for an ENGIE Belgium energy price."""

    def __init__(  # noqa: PLR0913 - sensor identity needs coord, subentry, descriptor, EAN, and slot/value keys
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
        ean: str,
        value_key: str,
        slot_code: str,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, subentry)
        self.entity_description = entity_description
        self._ean = ean
        self._value_key = value_key
        self._slot_code = slot_code
        # Subentry-scoped unique IDs match every other v3 customer-account
        # entity (peaks, calendar, EPEX). Energy descriptors already embed
        # the EAN, but keeping the subentry segment in the unique_id keeps
        # the schema uniform across platforms and avoids collisions if two
        # subentries on the same login ever share an EAN (e.g. address
        # corrections at ENGIE that reuse the meter ID under a new CAN).
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{subentry.subentry_id}_{entity_description.key}"
        )
        # Force a BAN-prefixed entity_id so price sensors for
        # different business agreements on one login don't collide
        # on their translated friendly name. The energy descriptor
        # key already embeds EAN + direction, so the final slug is
        # ``engie_belgium_{ban}_{ean}_{direction}[_excl_vat]`` which
        # is globally unique. ``_attr_suggested_object_id`` is not
        # honoured by ``Entity.suggested_object_id``; setting
        # ``self.entity_id`` directly is the supported escape hatch.
        # Only effective on first registration.
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = f"sensor.engie_belgium_{ban}_{entity_description.key}"

    @property
    def native_value(self) -> float | None:
        """Return the current price value."""
        return self._get_price_value()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {"ean": self._ean}
        if self.coordinator.last_successful_fetch:
            attrs["last_fetched"] = self.coordinator.last_successful_fetch.isoformat()
        price_entry = self._get_current_price_entry()
        if price_entry:
            attrs["from"] = price_entry.get("from")
            attrs["to"] = price_entry.get("to")
            attrs["vat_tariff"] = price_entry.get("vatTariff")
            attrs["time_of_use_slot_code"] = self._slot_code
        return attrs

    def _get_current_price_entry(self) -> dict[str, Any] | None:
        """Find the current price entry for this sensor's EAN."""
        if not self.coordinator.data:
            return None
        for item in self.coordinator.data.get("items", []):
            if item.get("ean") == self._ean:
                return _find_current_price(item.get("prices", []))
        return None

    def _get_price_value(self) -> float | None:
        """Extract the specific price value from the current entry."""
        price_entry = self._get_current_price_entry()
        if not price_entry:
            return None

        direction, field_name = self._value_key.split(".")
        configs = price_entry.get("proportionalPriceConfigurations", {})
        direction_list: list[dict[str, Any]] = configs.get(direction, [])
        if not direction_list:
            return None

        # Find the entry matching this sensor's time-of-use slot code
        for slot_entry in direction_list:
            if slot_entry.get("timeOfUseSlotCode") == self._slot_code:
                value = slot_entry.get(field_name)
                if value is None:
                    return None
                return float(value)

        return None


# ---------------------------------------------------------------------------
# EPEX day-ahead price sensors (dynamic / EPEX-indexed contracts only)
# ---------------------------------------------------------------------------

# EPEX wholesale prices are identical for every Belgian electricity EAN on
# a given dynamic contract, so a single :class:`EngieBeEpexCoordinator`
# fetches them once per parent ConfigEntry. The entities themselves are
# attached to each customer-account device that is on a dynamic tariff
# (gated upstream by ``coordinator.is_dynamic``) so the user sees them
# alongside the other sensors for that account.
#
# Unit follows the existing convention in this integration: the string
# ``"EUR/kWh"`` rather than ``SensorDeviceClass.MONETARY``+``"EUR"``,
# because Home Assistant's MONETARY device class requires a bare ISO
# 4217 currency code as the unit and would reject the per-kWh form.
# Precision is 4 (vs. 6 for retail prices) because wholesale fluctuates
# at the cent level and extra digits are noise.

_EPEX_UNIT = "EUR/kWh"
_EPEX_PRECISION = 4
_BRUSSELS_TZ = ZoneInfo(EPEX_TZ)

_EPEX_CURRENT = SensorEntityDescription(
    key="epex_current",
    translation_key="epex_current",
    native_unit_of_measurement=_EPEX_UNIT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=_EPEX_PRECISION,
)
_EPEX_LOW_TODAY = SensorEntityDescription(
    key="epex_low_today",
    translation_key="epex_low_today",
    native_unit_of_measurement=_EPEX_UNIT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=_EPEX_PRECISION,
)
_EPEX_HIGH_TODAY = SensorEntityDescription(
    key="epex_high_today",
    translation_key="epex_high_today",
    native_unit_of_measurement=_EPEX_UNIT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=_EPEX_PRECISION,
)
_EPEX_NEXT_HOUR = SensorEntityDescription(
    key="epex_next_hour",
    translation_key="epex_next_hour",
    native_unit_of_measurement=_EPEX_UNIT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=_EPEX_PRECISION,
)


def _build_epex_sensors(
    epex_coordinator: EngieBeEpexCoordinator,
    subentry: ConfigSubentry,
) -> list[SensorEntity]:
    """Build the four shared EPEX day-ahead sensors for one subentry."""
    return [
        EngieBeEpexCurrentSensor(epex_coordinator, subentry, _EPEX_CURRENT),
        EngieBeEpexExtremaSensor(
            epex_coordinator, subentry, _EPEX_LOW_TODAY, mode="min"
        ),
        EngieBeEpexExtremaSensor(
            epex_coordinator, subentry, _EPEX_HIGH_TODAY, mode="max"
        ),
        EngieBeEpexNextHourSensor(epex_coordinator, subentry, _EPEX_NEXT_HOUR),
    ]


def _epex_payload(coordinator: EngieBeEpexCoordinator) -> EpexPayload | None:
    """Return the cached EPEX payload, or ``None`` if not yet fetched."""
    payload = coordinator.data
    return payload if isinstance(payload, EpexPayload) else None


def _slots_for_date(payload: EpexPayload, target: date) -> list[Any]:
    """Return slots whose Brussels-local start date matches ``target``."""
    return [slot for slot in payload.slots if slot.start.date() == target]


class _EngieBeEpexSensorBase(_BoundaryScheduleMixin, EngieBeEpexEntity, SensorEntity):
    """
    Common base for EPEX day-ahead sensors.

    The ``_BoundaryScheduleMixin`` arms a point-in-UTC-time callback at
    the next EPEX slot boundary so both the current-price and
    next-hour-price sensors update at the exact second the market moves
    between slots, rather than waiting up to a full coordinator refresh
    interval. The same boundary serves both: at each hourly transition
    the current slot becomes the previous-hour slot AND the next-hour
    slot shifts, so a single callback covers every dependent value.
    """

    def __init__(
        self,
        coordinator: EngieBeEpexCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
    ) -> None:
        """Bind the coordinator, subentry and entity description."""
        super().__init__(coordinator, subentry)
        self.entity_description = entity_description
        # Subentry-scoped unique IDs because the same EPEX descriptor
        # repeats across every dynamic-tariff customer account.
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{subentry.subentry_id}_{entity_description.key}"
        )
        # Force a BAN-prefixed entity_id so EPEX sensors stay
        # distinct per business agreement on multi-agreement
        # dynamic-tariff logins. ``_attr_suggested_object_id`` is
        # not honoured by ``Entity.suggested_object_id``; setting
        # ``self.entity_id`` directly is the supported escape hatch.
        # Only effective on first registration.
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = f"sensor.engie_belgium_{ban}_{entity_description.key}"

    @property
    def available(self) -> bool:
        """Available only when a parsed payload exists for the entry."""
        return super().available and _epex_payload(self.coordinator) is not None

    def _next_boundary(self) -> datetime | None:
        """
        Return the next EPEX slot boundary in UTC, or ``None``.

        Shared by the current-price and next-hour-price sensors: at
        every hourly transition the current slot rolls over AND the
        next-hour slot shifts, so a single callback at the next slot
        boundary covers both dependent values.
        """
        payload = _epex_payload(self.coordinator)
        if payload is None:
            return None
        return next_epex_slot_boundary(payload, dt_util.utcnow())


class EngieBeEpexCurrentSensor(_EngieBeEpexSensorBase):
    """Current EPEX day-ahead price for the slot covering ``now``."""

    @property
    def native_value(self) -> float | None:
        """Return the EUR/kWh price of the slot covering the current instant."""
        payload = _epex_payload(self.coordinator)
        if payload is None:
            return None
        now = dt_util.utcnow()
        for slot in payload.slots:
            if slot.start <= now < slot.end:
                return slot.value_eur_per_kwh
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """
        Expose the today/tomorrow slot arrays plus publication metadata.

        Hour arrays are emitted as ``{start, end, value}`` dicts using
        Brussels-local ISO 8601 timestamps so dashboard cards
        (ApexCharts, etc.) can plot them without timezone gymnastics.
        Raw EUR/MWh is included alongside EUR/kWh for users who prefer
        wholesale-market units.
        """
        payload = _epex_payload(self.coordinator)
        if payload is None:
            return {}

        today_brussels = dt_util.now(_BRUSSELS_TZ).date()
        tomorrow_brussels = today_brussels + timedelta(days=1)

        attrs: dict[str, Any] = {
            "today": [
                _serialize_slot(s) for s in _slots_for_date(payload, today_brussels)
            ],
            "tomorrow": [
                _serialize_slot(s) for s in _slots_for_date(payload, tomorrow_brussels)
            ],
            "slot_duration_minutes": (
                payload.slots[0].duration_minutes if payload.slots else None
            ),
        }
        if payload.publication_time is not None:
            attrs["publication_time"] = payload.publication_time.isoformat()
        if payload.market_date is not None:
            attrs["market_date"] = payload.market_date
        if self.coordinator.last_update_success_time is not None:
            attrs["last_fetched"] = (
                self.coordinator.last_update_success_time.isoformat()
            )
        return attrs


class EngieBeEpexNextHourSensor(_EngieBeEpexSensorBase):
    """EPEX day-ahead price for the slot starting one hour from now."""

    @property
    def native_value(self) -> float | None:
        """Return the EUR/kWh price of the slot covering ``now + 1h``."""
        payload = _epex_payload(self.coordinator)
        if payload is None:
            return None
        target = dt_util.utcnow() + timedelta(hours=1)
        for slot in payload.slots:
            if slot.start <= target < slot.end:
                return slot.value_eur_per_kwh
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """
        Expose the start/end of the slot whose price is being reported.

        Intentionally narrower than :class:`EngieBeEpexCurrentSensor`'s
        attribute set: this is a point lookup for one specific future
        slot, not a today/tomorrow slate browser, so the per-day arrays
        and market metadata are omitted to keep the entity focused.
        """
        payload = _epex_payload(self.coordinator)
        if payload is None:
            return {}
        target = dt_util.utcnow() + timedelta(hours=1)
        for slot in payload.slots:
            if slot.start <= target < slot.end:
                attrs: dict[str, Any] = {
                    "slot_start": slot.start.isoformat(),
                    "slot_end": slot.end.isoformat(),
                    "slot_duration_minutes": slot.duration_minutes,
                }
                if self.coordinator.last_update_success_time is not None:
                    attrs["last_fetched"] = (
                        self.coordinator.last_update_success_time.isoformat()
                    )
                return attrs
        return {}


class EngieBeEpexExtremaSensor(_EngieBeEpexSensorBase):
    """Lowest or highest EPEX day-ahead price for the current Brussels day."""

    def __init__(
        self,
        coordinator: EngieBeEpexCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
        mode: str,
    ) -> None:
        """``mode`` selects ``min`` or ``max`` reduction over today's slots."""
        super().__init__(coordinator, subentry, entity_description)
        if mode not in ("min", "max"):
            msg = f"mode must be 'min' or 'max', got {mode!r}"
            raise ValueError(msg)
        self._mode = mode

    def _selected_slot(self) -> Any | None:
        payload = _epex_payload(self.coordinator)
        if payload is None:
            return None
        today = dt_util.now(_BRUSSELS_TZ).date()
        slots = _slots_for_date(payload, today)
        if not slots:
            return None
        reducer = min if self._mode == "min" else max
        return reducer(slots, key=lambda s: s.value_eur_per_kwh)

    @property
    def native_value(self) -> float | None:
        """Return the EUR/kWh value of the chosen slot, if any."""
        slot = self._selected_slot()
        return slot.value_eur_per_kwh if slot is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the start/end of the slot that produced this extremum."""
        slot = self._selected_slot()
        if slot is None:
            return {}
        attrs = {
            "slot_start": slot.start.isoformat(),
            "slot_end": slot.end.isoformat(),
            "slot_duration_minutes": slot.duration_minutes,
        }
        if self.coordinator.last_update_success_time is not None:
            attrs["last_fetched"] = (
                self.coordinator.last_update_success_time.isoformat()
            )
        return attrs


def _serialize_slot(slot: Any) -> dict[str, Any]:
    """Serialise an :class:`EpexSlot` for use in entity attributes."""
    return {
        "start": slot.start.isoformat(),
        "end": slot.end.isoformat(),
        "value": slot.value_eur_per_kwh,
        "value_eur_per_mwh": slot.value_eur_per_kwh * 1000.0,
    }
