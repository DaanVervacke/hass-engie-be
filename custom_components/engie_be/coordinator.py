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
        # When the current month has no ``peakOfTheMonth`` yet (typical in
        # the first day or two of a new month before ENGIE has recorded a
        # 15-minute interval), we fall back to the previous month so users
        # still see a meaningful value.
        today = dt_util.now()
        previous_peaks_wrapper: dict[str, Any] | None = None
        if isinstance(self.data, dict):
            existing = self.data.get("peaks")
            if isinstance(existing, dict):
                previous_peaks_wrapper = existing

        peaks_wrapper = await self._async_fetch_peaks_with_fallback(
            client,
            customer_number,
            today.year,
            today.month,
            previous_peaks_wrapper,
        )

        if peaks_wrapper is not None:
            data["peaks"] = peaks_wrapper

        self.last_successful_fetch = dt_util.utcnow()
        return data

    async def _async_fetch_peaks_with_fallback(
        self,
        client: Any,
        customer_number: str,
        year: int,
        month: int,
        previous_wrapper: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        Fetch current-month peaks, falling back to the previous month.

        Returns a wrapper dict ``{"data", "year", "month", "is_fallback"}``
        so consumers know which month the displayed value reflects.
        Returns ``None`` when no data could be obtained at all.
        """
        try:
            current = await client.async_get_monthly_peaks(
                customer_number,
                year,
                month,
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
            return previous_wrapper

        if isinstance(current, dict) and isinstance(
            current.get("peakOfTheMonth"),
            dict,
        ):
            return {
                "data": current,
                "year": year,
                "month": month,
                "is_fallback": False,
            }

        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        try:
            previous = await client.async_get_monthly_peaks(
                customer_number,
                prev_year,
                prev_month,
            )
        except EngieBeApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from exception
        except EngieBeApiClientError as exception:
            LOGGER.warning(
                "Current month has no peak yet and fallback to %d-%02d failed: %s",
                prev_year,
                prev_month,
                exception,
            )
            return {
                "data": current if isinstance(current, dict) else None,
                "year": year,
                "month": month,
                "is_fallback": False,
            }

        if isinstance(previous, dict) and isinstance(
            previous.get("peakOfTheMonth"),
            dict,
        ):
            LOGGER.debug(
                "Current month %d-%02d has no peak yet; using fallback %d-%02d",
                year,
                month,
                prev_year,
                prev_month,
            )
            return {
                "data": previous,
                "year": prev_year,
                "month": prev_month,
                "is_fallback": True,
            }

        return {
            "data": current if isinstance(current, dict) else None,
            "year": year,
            "month": month,
            "is_fallback": False,
        }
