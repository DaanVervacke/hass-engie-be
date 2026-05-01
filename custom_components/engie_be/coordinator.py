"""DataUpdateCoordinator for the ENGIE Belgium integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import (
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
from .const import (
    CONF_CUSTOMER_NUMBER,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    LOGGER,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import EngieBeConfigEntry


class EngieBeDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to poll energy prices from ENGIE Belgium."""

    config_entry: EngieBeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EngieBeConfigEntry,
    ) -> None:
        """Initialise the coordinator."""
        update_minutes = config_entry.options.get(
            CONF_UPDATE_INTERVAL,
            DEFAULT_UPDATE_INTERVAL_MINUTES,
        )
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name="ENGIE Belgium",
            update_interval=timedelta(minutes=update_minutes),
        )
        self.last_successful_fetch: datetime | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch energy prices and capacity-tariff peaks from the API."""
        client = self.config_entry.runtime_data.client
        customer_number = self.config_entry.data[CONF_CUSTOMER_NUMBER]

        try:
            data = await client.async_get_prices(customer_number)
        except EngieBeApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from exception
        except EngieBeApiClientError as exception:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
            ) from exception

        # Fetch current-month captar peaks. Failures here must not block
        # price updates; we keep the last-known peaks payload so existing
        # peak sensors remain populated until the next successful poll.
        today = dt_util.now()
        previous_peaks: dict[str, Any] | None = None
        if isinstance(self.data, dict):
            previous_peaks = self.data.get("peaks")
        try:
            peaks: dict[str, Any] | None = await client.async_get_monthly_peaks(
                customer_number,
                today.year,
                today.month,
            )
        except EngieBeApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from exception
        except EngieBeApiClientError as exception:
            LOGGER.warning(
                "Failed to fetch monthly peaks, keeping last-known values: %s",
                exception,
            )
            peaks = previous_peaks

        if peaks is not None:
            data["peaks"] = peaks

        self.last_successful_fetch = dt_util.utcnow()
        return data
