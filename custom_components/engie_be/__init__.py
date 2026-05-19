"""Custom integration to integrate ENGIE Belgium with Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from ._contracts import is_account_dynamic
from .api import (
    EngieBeApiClient,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CLIENT_ID,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    LOGGER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
    TOKEN_REFRESH_INTERVAL_SECONDS,
)
from .coordinator import EngieBeDataUpdateCoordinator, EngieBeEpexCoordinator
from .data import EngieBeData, EngieBeSubentryData
from .diagnostics import _hash_ean
from .store import EngieBePeaksStore

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from .data import EngieBeConfigEntry

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
]


async def async_migrate_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
) -> bool:
    """
    Refuse to migrate config entries from before v0.9.0.

    v0.9.0 is a hard break: the v1->v2->v3->v4 migration chain was
    removed to drop ~3000 LOC of one-shot upgrade code that had to
    survive long-tail upgrade paths. Users on any pre-v0.9.0 install
    must remove the integration from Home Assistant and re-add it
    through the UI; that re-add walks the current config flow and
    produces a fresh v5 entry. Returning ``False`` here causes HA to
    flag the entry as ``setup_error`` and surface a Repairs notice,
    which is the intended UX for this break.
    """
    LOGGER.error(
        "Cannot migrate ENGIE Belgium config entry from version %s. "
        "v0.9.0 is a hard break: remove this integration from Settings "
        "-> Devices & Services and add it again. See the v0.9.0 "
        "changelog for details.",
        entry.version,
    )
    return False


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

    epex_coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)

    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=epex_coordinator,
        last_options=dict(entry.options),
        last_subentry_ids={
            sub.subentry_id
            for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
        },
    )

    # Initial token refresh so per-subentry coordinators have a valid
    # access token to make their first authenticated request with.
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

    # Recurring token refresh (one timer per parent entry, not per
    # subentry: tokens are login-scoped, not account-scoped).
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
        except EngieBeApiClientError as err:
            entry.runtime_data.authenticated = False
            # The API client embeds HTTP status / underlying exception class
            # into the message (see api.py: "HTTP {status}: {body_preview}",
            # "Timeout communicating ... ({TimeoutError})", etc.), so logging
            # the exception type plus its message is enough to diagnose
            # transient upstream failures without enabling debug logging.
            LOGGER.warning(
                "Scheduled token refresh failed (%s: %s); will retry",
                type(err).__name__,
                err,
            )
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

    # Build per-subentry coordinators, peak stores and service-points
    # lookups, then do their initial refreshes in parallel so that a
    # user with N business agreements does not pay sum(latency) at setup.
    subentries: list[ConfigSubentry] = [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
    ]

    for subentry in subentries:
        coordinator = EngieBeDataUpdateCoordinator(
            hass=hass,
            config_entry=entry,
            subentry=subentry,
        )
        peaks_store = await _async_init_peaks_store(hass, subentry.subentry_id)
        entry.runtime_data.subentry_data[subentry.subentry_id] = EngieBeSubentryData(
            coordinator=coordinator,
            peaks_store=peaks_store,
        )

    # Refresh EPEX once at startup alongside the per-subentry data;
    # EPEX is shared across subentries so this is one fetch total.
    refresh_calls = [epex_coordinator.async_config_entry_first_refresh()]
    refresh_calls.extend(
        sub_data.coordinator.async_config_entry_first_refresh()
        for sub_data in entry.runtime_data.subentry_data.values()
    )
    await asyncio.gather(*refresh_calls)

    # Resolve energy type for each EAN per subentry. Service-point lookups
    # are fanned out across all subentries' EANs in a single gather so
    # multi-agreement customers do not pay sum(latency) for setup. Dynamic-
    # tariff detection runs in parallel with the same fan-out so the
    # ``is_dynamic`` flag (which gates EPEX entity creation) is settled
    # before platforms are forwarded.
    await asyncio.gather(
        _async_populate_service_points(client, entry),
        _async_populate_dynamic_flags(client, entry),
    )

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
    """
    Reload on options change or business-agreement subentry add/remove.

    Token rotation also fires this listener (it writes to ``entry.data``)
    but neither options nor the business-agreement subentry id set change
    on token rotation, so the no-op short-circuit holds.
    """
    options_changed = dict(entry.options) != entry.runtime_data.last_options
    current_subentry_ids = {
        sub.subentry_id
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
    }
    subentries_changed = current_subentry_ids != entry.runtime_data.last_subentry_ids
    if options_changed or subentries_changed:
        await hass.config_entries.async_reload(entry.entry_id)


def _persist_tokens(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
    access_token: str,
    refresh_token: str,
) -> None:
    """
    Persist refreshed tokens to the config entry data.

    Skips the write when both tokens already match what is stored, so
    routine coordinator refreshes that hand back the same access token
    do not dirty ``core.config_entries`` storage. ENGIE rotates the
    refresh token on every successful exchange, so in practice this
    short-circuit only fires when the OAuth helper returns a cached
    token (e.g. when the previous access token is still valid).
    """
    current_access = entry.data.get(CONF_ACCESS_TOKEN)
    current_refresh = entry.data.get(CONF_REFRESH_TOKEN)
    if current_access == access_token and current_refresh == refresh_token:
        return
    updated_data = {**entry.data}
    updated_data[CONF_ACCESS_TOKEN] = access_token
    updated_data[CONF_REFRESH_TOKEN] = refresh_token
    hass.config_entries.async_update_entry(entry, data=updated_data)


async def _async_init_peaks_store(
    hass: HomeAssistant,
    subentry_id: str,
) -> EngieBePeaksStore:
    """Build and load the persistent peaks-history store for one subentry."""
    store = EngieBePeaksStore(hass, subentry_id)
    await store.async_load()
    return store


async def _async_populate_service_points(
    client: EngieBeApiClient,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Resolve EAN-to-energy-type for every subentry in one fan-out call.

    EAN-to-division mapping is per-EAN (and therefore inherently
    per-subentry, since EANs belong to one business agreement). Lookups
    are issued in parallel across all subentries' EANs so a multi-
    agreement user does not pay sum(latency) at setup. Failures degrade
    gracefully: a missing service-point falls back to the heuristic in
    the sensor layer, exactly as for single-agreement setups.
    """
    eans_by_subentry: dict[str, list[str]] = {}
    flat_eans: list[tuple[str, str]] = []
    for subentry_id, sub_data in entry.runtime_data.subentry_data.items():
        coordinator_data = sub_data.coordinator.data or {}
        eans = [
            item.get("ean", "")
            for item in coordinator_data.get("items", [])
            if item.get("ean")
        ]
        eans_by_subentry[subentry_id] = eans
        flat_eans.extend((subentry_id, ean) for ean in eans)

    if not flat_eans:
        return

    results = await asyncio.gather(
        *(client.async_get_service_point(ean) for _, ean in flat_eans),
        return_exceptions=True,
    )

    for (subentry_id, ean), result in zip(flat_eans, results, strict=True):
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
            entry.runtime_data.subentry_data[subentry_id].service_points[ean] = division
            LOGGER.debug("Service-point %s: division=%s", _hash_ean(ean), division)


async def _async_populate_dynamic_flags(
    client: EngieBeApiClient,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Resolve the dynamic-tariff flag for every subentry in one fan-out call.

    Calls the energy-contracts endpoint once per subentry's BAN in
    parallel and writes the result to
    :attr:`EngieBeSubentryData.is_dynamic_override`. The override is
    consulted by :attr:`EngieBeDataUpdateCoordinator.is_dynamic`, which
    in turn gates EPEX entity creation in the sensor and binary-sensor
    platforms. Failures degrade gracefully: a contracts call that
    raises (network error, 5xx, schema drift) leaves the override at
    ``None`` so the legacy ``len(items) == 0`` heuristic on the prices
    payload still drives detection. Authentication failures are not
    raised here because the parent entry's first refresh has already
    surfaced any auth problem; a contracts-only auth error is treated
    as a transient failure for this account.
    """
    subentries: list[tuple[str, str]] = [
        (
            subentry.subentry_id,
            subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, ""),
        )
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
    ]
    targets = [(sid, ban) for sid, ban in subentries if ban]
    if not targets:
        return

    results = await asyncio.gather(
        *(client.async_get_energy_contracts(ban) for _, ban in targets),
        return_exceptions=True,
    )

    for (subentry_id, _ban), result in zip(targets, results, strict=True):
        sub_data = entry.runtime_data.subentry_data.get(subentry_id)
        if sub_data is None:
            continue
        if isinstance(result, EngieBeApiClientError):
            LOGGER.warning(
                "Failed to fetch energy contracts for subentry %s; "
                "falling back to legacy detection (%s: %s)",
                subentry_id,
                type(result).__name__,
                result,
            )
            continue
        if isinstance(result, BaseException):
            # Re-raise unexpected exceptions; only API errors are tolerated.
            raise result
        if not isinstance(result, dict):
            LOGGER.warning(
                "Energy contracts response for subentry %s is not a JSON "
                "object; falling back to legacy detection",
                subentry_id,
            )
            continue
        sub_data.energy_contracts_payload = result
        sub_data.is_dynamic_override = is_account_dynamic(result)
        LOGGER.debug(
            "Subentry %s dynamic-tariff flag from contracts: %s",
            subentry_id,
            sub_data.is_dynamic_override,
        )
