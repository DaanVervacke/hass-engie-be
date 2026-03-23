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
        """Fetch energy prices from the API."""
        client = self.config_entry.runtime_data.client
        customer_number = self.config_entry.data[CONF_CUSTOMER_NUMBER]

        try:
            data = await client.async_get_prices(customer_number)
        except EngieBeApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(exception) from exception
        except EngieBeApiClientError as exception:
            raise UpdateFailed(exception) from exception

        self.last_successful_fetch = dt_util.utcnow()
        return data
