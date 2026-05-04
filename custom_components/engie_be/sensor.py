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
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.util import dt as dt_util

from ._peaks import peaks_meta, peaks_payload
from .const import EPEX_TZ, KEY_EPEX, KEY_IS_DYNAMIC, LOGGER
from .data import EpexPayload
from .entity import EngieBeEntity

# Coordinator centralises updates; entities never poll individually.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EngieBeDataUpdateCoordinator
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

        for direction, icon in (
            ("offtake", "mdi:cash-minus"),
            ("injection", "mdi:cash-plus"),
        ):
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
                            icon=icon,
                            native_unit_of_measurement=unit,
                            state_class=SensorStateClass.MEASUREMENT,
                            suggested_display_precision=6,
                        ),
                        ean,
                        f"{direction}.priceValue",
                        slot_code,
                    )
                )
                # Price excluding VAT
                sensors.append(
                    (
                        SensorEntityDescription(
                            key=f"{base_key}_excl_vat",
                            translation_key=f"{base_trans}_excl_vat",
                            icon=icon,
                            native_unit_of_measurement=unit,
                            state_class=SensorStateClass.MEASUREMENT,
                            suggested_display_precision=6,
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
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = entry.runtime_data.coordinator

    # Wait for first data before building sensors
    if coordinator.data is None:
        LOGGER.warning("No data available yet, skipping sensor setup")
        return

    sensor_defs = _build_sensor_descriptions(
        coordinator.data, entry.runtime_data.service_points
    )
    entities: list[SensorEntity] = [
        EngieBeEnergySensor(
            coordinator=coordinator,
            entity_description=desc,
            ean=ean,
            value_key=value_key,
            slot_code=slot_code,
        )
        for desc, ean, value_key, slot_code in sensor_defs
    ]
    entities.extend(_build_peak_sensors(coordinator))
    entities.extend(_build_epex_sensors(coordinator))
    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Capacity-tariff (captar) peak sensors
# ---------------------------------------------------------------------------

_CAPTAR_MONTHLY_PEAK_POWER = SensorEntityDescription(
    key="captar_monthly_peak_power",
    translation_key="captar_monthly_peak_power",
    icon="mdi:flash",
    native_unit_of_measurement=UnitOfPower.KILO_WATT,
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=3,
)
_CAPTAR_MONTHLY_PEAK_ENERGY = SensorEntityDescription(
    key="captar_monthly_peak_energy",
    translation_key="captar_monthly_peak_energy",
    icon="mdi:lightning-bolt",
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    # No state_class: this is a snapshot of one 15-min peak window's energy,
    # not a measurement, total, or total_increasing. HA rejects ENERGY +
    # MEASUREMENT at runtime; TOTAL would require last_reset semantics that
    # don't fit a sliding monthly peak.
    suggested_display_precision=3,
)
_CAPTAR_MONTHLY_PEAK_START = SensorEntityDescription(
    key="captar_monthly_peak_start",
    translation_key="captar_monthly_peak_start",
    icon="mdi:clock-start",
    device_class=SensorDeviceClass.TIMESTAMP,
)
_CAPTAR_MONTHLY_PEAK_END = SensorEntityDescription(
    key="captar_monthly_peak_end",
    translation_key="captar_monthly_peak_end",
    icon="mdi:clock-end",
    device_class=SensorDeviceClass.TIMESTAMP,
)


def _build_peak_sensors(
    coordinator: EngieBeDataUpdateCoordinator,
) -> list[SensorEntity]:
    """Build the four monthly capacity-tariff peak sensors for the entry."""
    return [
        EngieBeMonthlyPeakValueSensor(
            coordinator,
            _CAPTAR_MONTHLY_PEAK_POWER,
            field="peakKW",
        ),
        EngieBeMonthlyPeakValueSensor(
            coordinator,
            _CAPTAR_MONTHLY_PEAK_ENERGY,
            field="peakKWh",
        ),
        EngieBeMonthlyPeakTimestampSensor(
            coordinator,
            _CAPTAR_MONTHLY_PEAK_START,
            field="start",
        ),
        EngieBeMonthlyPeakTimestampSensor(
            coordinator,
            _CAPTAR_MONTHLY_PEAK_END,
            field="end",
        ),
    ]


class _EngieBePeakSensorBase(EngieBeEntity, SensorEntity):
    """Common base for capacity-tariff peak sensors."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        entity_description: SensorEntityDescription,
    ) -> None:
        """Initialise the peak sensor with its coordinator and description."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )

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
        entity_description: SensorEntityDescription,
        field: str,
    ) -> None:
        """Track which numeric field of ``peakOfTheMonth`` to expose."""
        super().__init__(coordinator, entity_description)
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
        entity_description: SensorEntityDescription,
        field: str,
    ) -> None:
        """Track which timestamp field (``start`` or ``end``) to expose."""
        super().__init__(coordinator, entity_description)
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


class EngieBeEnergySensor(EngieBeEntity, SensorEntity):
    """Sensor for an ENGIE Belgium energy price."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        entity_description: SensorEntityDescription,
        ean: str,
        value_key: str,
        slot_code: str,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._ean = ean
        self._value_key = value_key
        self._slot_code = slot_code
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )

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

# EPEX wholesale prices are identical across every Belgian electricity EAN
# on a given dynamic contract, so we expose ONE shared set of sensors per
# config entry rather than duplicating per EAN. They are added at setup
# unconditionally and report ``None`` when ``coordinator.data["is_dynamic"]``
# is ``False`` -- this keeps the entity wiring stable across contract changes
# (no add/remove churn, no reload required).
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
    icon="mdi:cash-clock",
    native_unit_of_measurement=_EPEX_UNIT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=_EPEX_PRECISION,
)
_EPEX_LOW_TODAY = SensorEntityDescription(
    key="epex_low_today",
    translation_key="epex_low_today",
    icon="mdi:cash-minus",
    native_unit_of_measurement=_EPEX_UNIT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=_EPEX_PRECISION,
)
_EPEX_HIGH_TODAY = SensorEntityDescription(
    key="epex_high_today",
    translation_key="epex_high_today",
    icon="mdi:cash-plus",
    native_unit_of_measurement=_EPEX_UNIT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=_EPEX_PRECISION,
)


def _build_epex_sensors(
    coordinator: EngieBeDataUpdateCoordinator,
) -> list[SensorEntity]:
    """Build the three shared EPEX day-ahead sensors for the entry."""
    return [
        EngieBeEpexCurrentSensor(coordinator, _EPEX_CURRENT),
        EngieBeEpexExtremaSensor(coordinator, _EPEX_LOW_TODAY, mode="min"),
        EngieBeEpexExtremaSensor(coordinator, _EPEX_HIGH_TODAY, mode="max"),
    ]


def _epex_payload(coordinator: EngieBeDataUpdateCoordinator) -> EpexPayload | None:
    """Return the cached EPEX payload, or ``None`` if not on a dynamic tariff."""
    data = coordinator.data
    if not isinstance(data, dict):
        return None
    if not data.get(KEY_IS_DYNAMIC):
        return None
    payload = data.get(KEY_EPEX)
    return payload if isinstance(payload, EpexPayload) else None


def _slots_for_date(payload: EpexPayload, target: date) -> list[Any]:
    """Return slots whose Brussels-local start date matches ``target``."""
    return [slot for slot in payload.slots if slot.start.date() == target]


class _EngieBeEpexSensorBase(EngieBeEntity, SensorEntity):
    """Common base for EPEX day-ahead sensors."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        entity_description: SensorEntityDescription,
    ) -> None:
        """Bind the coordinator and entity description."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )

    @property
    def available(self) -> bool:
        """Available only on dynamic accounts with a parsed payload."""
        return super().available and _epex_payload(self.coordinator) is not None


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
        if self.coordinator.last_successful_fetch:
            attrs["last_fetched"] = self.coordinator.last_successful_fetch.isoformat()
        return attrs


class EngieBeEpexExtremaSensor(_EngieBeEpexSensorBase):
    """Lowest or highest EPEX day-ahead price for the current Brussels day."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        entity_description: SensorEntityDescription,
        mode: str,
    ) -> None:
        """``mode`` selects ``min`` or ``max`` reduction over today's slots."""
        super().__init__(coordinator, entity_description)
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
        if self.coordinator.last_successful_fetch:
            attrs["last_fetched"] = self.coordinator.last_successful_fetch.isoformat()
        return attrs


def _serialize_slot(slot: Any) -> dict[str, Any]:
    """Serialise an :class:`EpexSlot` for use in entity attributes."""
    return {
        "start": slot.start.isoformat(),
        "end": slot.end.isoformat(),
        "value": slot.value_eur_per_kwh,
        "value_eur_per_mwh": slot.value_eur_per_kwh * 1000.0,
    }
