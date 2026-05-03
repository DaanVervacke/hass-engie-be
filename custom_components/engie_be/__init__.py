"""Custom integration to integrate ENGIE Belgium with Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import (
    EngieBeApiClient,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_REFRESH_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_CLIENT_ID,
    LOGGER,
    TOKEN_REFRESH_INTERVAL_SECONDS,
)
from .coordinator import EngieBeDataUpdateCoordinator
from .data import EngieBeData
from .diagnostics import _hash_ean
from .store import EngieBePeaksStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import EngieBeConfigEntry

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
]

_HOURS_TO_MINUTES = 60


async def async_migrate_entry(
    hass: HomeAssistant,
    config_entry: EngieBeConfigEntry,
) -> bool:
    """Migrate config entry to a new version."""
    if config_entry.version == 1:
        # v1 stored update_interval in hours; v2 stores it in minutes.
        old_interval = config_entry.options.get(CONF_UPDATE_INTERVAL)
        if old_interval is not None:
            new_options = {**config_entry.options}
            new_options[CONF_UPDATE_INTERVAL] = old_interval * _HOURS_TO_MINUTES
            hass.config_entries.async_update_entry(
                config_entry,
                options=new_options,
                version=2,
            )
        else:
            hass.config_entries.async_update_entry(config_entry, version=2)

        LOGGER.info(
            "Migrated config entry %s from version 1 to 2",
            config_entry.entry_id,
        )

    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    client = EngieBeApiClient(
        session=async_get_clientsession(hass),
        client_id=entry.data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
    )

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    peaks_store = await _async_init_peaks_store(hass, entry.entry_id)

    entry.runtime_data = EngieBeData(
        client=client,
        coordinator=coordinator,
        last_options=dict(entry.options),
        peaks_store=peaks_store,
    )

    # Do an initial token refresh so we have a valid access token
    try:
        new_access, new_refresh = await client.async_refresh_token()
    except EngieBeApiClientAuthenticationError as err:
        msg = "Stored ENGIE credentials are no longer valid"
        raise ConfigEntryAuthFailed(msg) from err
    except EngieBeApiClientError as err:
        msg = "Unable to refresh ENGIE access token; will retry"
        raise ConfigEntryNotReady(msg) from err

    _persist_tokens(hass, entry, new_access, new_refresh)
    entry.runtime_data.authenticated = True

    # Set up recurring token refresh (every 60 seconds)
    async def _refresh_token_callback(_now: object) -> None:
        """Refresh the access token periodically."""
        try:
            new_access, new_refresh = await client.async_refresh_token()
        except EngieBeApiClientAuthenticationError:
            entry.runtime_data.authenticated = False
            LOGGER.warning(
                "Scheduled token refresh rejected by ENGIE; starting reauth flow"
            )
            entry.async_start_reauth(hass)
            return
        except EngieBeApiClientError:
            entry.runtime_data.authenticated = False
            LOGGER.warning("Scheduled token refresh failed; will retry")
            return

        _persist_tokens(hass, entry, new_access, new_refresh)
        entry.runtime_data.authenticated = True
        LOGGER.debug("Token refreshed successfully")

    cancel_refresh = async_track_time_interval(
        hass,
        _refresh_token_callback,
        timedelta(seconds=TOKEN_REFRESH_INTERVAL_SECONDS),
    )
    entry.async_on_unload(cancel_refresh)

    # Fetch initial data and forward platforms
    await coordinator.async_config_entry_first_refresh()

    # Resolve energy type for each EAN via the service-points API.
    # Fetched in parallel so multi-EAN customers do not pay sum(latency).
    eans: list[str] = [
        item.get("ean", "")
        for item in (coordinator.data or {}).get("items", [])
        if item.get("ean")
    ]
    service_points: dict[str, str] = {}
    if eans:
        results = await asyncio.gather(
            *(client.async_get_service_point(ean) for ean in eans),
            return_exceptions=True,
        )
        for ean, result in zip(eans, results, strict=True):
            if isinstance(result, EngieBeApiClientError):
                LOGGER.warning(
                    "Failed to fetch service-point for EAN %s; using fallback",
                    _hash_ean(ean),
                )
                continue
            if isinstance(result, BaseException):
                # Re-raise unexpected exceptions; only API errors are tolerated.
                raise result
            division: str = result.get("division", "")
            if division:
                service_points[ean] = division
                LOGGER.debug("Service-point %s: division=%s", _hash_ean(ean), division)
    entry.runtime_data.service_points = service_points

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """Reload config entry only when options change (not on token rotation)."""
    if dict(entry.options) != entry.runtime_data.last_options:
        await hass.config_entries.async_reload(entry.entry_id)


def _persist_tokens(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
    access_token: str,
    refresh_token: str,
) -> None:
    """Persist refreshed tokens to the config entry data."""
    updated_data = {**entry.data}
    updated_data[CONF_ACCESS_TOKEN] = access_token
    updated_data[CONF_REFRESH_TOKEN] = refresh_token
    hass.config_entries.async_update_entry(entry, data=updated_data)


async def _async_init_peaks_store(
    hass: HomeAssistant,
    entry_id: str,
) -> EngieBePeaksStore:
    """Build and load the persistent peaks-history store for one entry."""
    store = EngieBePeaksStore(hass, entry_id)
    await store.async_load()
    return store
