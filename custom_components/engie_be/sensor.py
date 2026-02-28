"""Sensor platform for the Engie Belgium integration."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

from .const import (
    ELECTRICITY_EAN_PREFIX,
    GAS_EAN_PREFIX,
    LOGGER,
)
from .entity import EngieBeEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EngieBeDataUpdateCoordinator
    from .data import EngieBeConfigEntry


def _detect_energy_type(ean: str) -> str:
    """Detect the energy type from the EAN prefix."""
    if ean.startswith(GAS_EAN_PREFIX):
        return "Gas"
    if ean.startswith(ELECTRICITY_EAN_PREFIX):
        return "Electricity"
    return ean


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


def _build_sensor_descriptions(
    data: dict[str, Any],
) -> list[tuple[SensorEntityDescription, str, str]]:
    """
    Build sensor descriptions from the API response.

    Returns a list of (description, ean, value_key) tuples where
    value_key is a dotted path like ``offtake.priceValue``.
    """
    sensors: list[tuple[SensorEntityDescription, str, str]] = []

    for item in data.get("items", []):
        ean: str = item.get("ean", "unknown")
        energy_type = _detect_energy_type(ean)
        # Strip trailing _ID* suffix for display
        # e.g. "541448...267_ID1" -> cleaner key
        ean_short = ean.split("_", maxsplit=1)[0] if "_" in ean else ean

        current_price = _find_current_price(item.get("prices", []))
        if current_price is None:
            continue

        configs = current_price.get("proportionalPriceConfigurations", {})

        # Offtake sensors (always present)
        offtake_list = configs.get("offtake", [])
        if offtake_list:
            sensors.append(
                (
                    SensorEntityDescription(
                        key=f"{ean_short}_offtake",
                        translation_key=f"{energy_type.lower()}_offtake",
                        icon="mdi:cash-minus",
                        native_unit_of_measurement="EUR/kWh",
                        state_class=SensorStateClass.MEASUREMENT,
                        suggested_display_precision=6,
                    ),
                    ean,
                    "offtake.priceValue",
                )
            )
            sensors.append(
                (
                    SensorEntityDescription(
                        key=f"{ean_short}_offtake_excl_vat",
                        translation_key=f"{energy_type.lower()}_offtake_excl_vat",
                        icon="mdi:cash-minus",
                        native_unit_of_measurement="EUR/kWh",
                        state_class=SensorStateClass.MEASUREMENT,
                        suggested_display_precision=6,
                    ),
                    ean,
                    "offtake.priceValueExclVAT",
                )
            )

        # Injection sensors (only if data present)
        injection_list = configs.get("injection", [])
        if injection_list:
            sensors.append(
                (
                    SensorEntityDescription(
                        key=f"{ean_short}_injection",
                        translation_key=f"{energy_type.lower()}_injection",
                        icon="mdi:cash-plus",
                        native_unit_of_measurement="EUR/kWh",
                        state_class=SensorStateClass.MEASUREMENT,
                        suggested_display_precision=6,
                    ),
                    ean,
                    "injection.priceValue",
                )
            )
            sensors.append(
                (
                    SensorEntityDescription(
                        key=f"{ean_short}_injection_excl_vat",
                        translation_key=f"{energy_type.lower()}_injection_excl_vat",
                        icon="mdi:cash-plus",
                        native_unit_of_measurement="EUR/kWh",
                        state_class=SensorStateClass.MEASUREMENT,
                        suggested_display_precision=6,
                    ),
                    ean,
                    "injection.priceValueExclVAT",
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

    sensor_defs = _build_sensor_descriptions(coordinator.data)
    async_add_entities(
        EngieBeEnergySensor(
            coordinator=coordinator,
            entity_description=desc,
            ean=ean,
            value_key=value_key,
        )
        for desc, ean, value_key in sensor_defs
    )


class EngieBeEnergySensor(EngieBeEntity, SensorEntity):
    """Sensor for an Engie Belgium energy price."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        entity_description: SensorEntityDescription,
        ean: str,
        value_key: str,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._ean = ean
        self._value_key = value_key
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
        price_entry = self._get_current_price_entry()
        if price_entry:
            attrs["from"] = price_entry.get("from")
            attrs["to"] = price_entry.get("to")
            attrs["vat_tariff"] = price_entry.get("vatTariff")
            # Add the time-of-use slot code
            direction, _ = self._value_key.split(".")
            configs = price_entry.get("proportionalPriceConfigurations", {})
            direction_list = configs.get(direction, [])
            if direction_list:
                attrs["time_of_use_slot_code"] = direction_list[0].get(
                    "timeOfUseSlotCode"
                )
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
        direction_list = configs.get(direction, [])
        if not direction_list:
            return None

        value = direction_list[0].get(field_name)
        if value is None:
            return None
        return float(value)
