"""Custom integration to integrate ENGIE Belgium with Home Assistant."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_USERNAME, Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import slugify

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
    DOMAIN,
    LOGGER,
    TOKEN_REFRESH_INTERVAL_SECONDS,
)
from .coordinator import EngieBeDataUpdateCoordinator
from .data import EngieBeData
from .diagnostics import _hash_ean
from .store import EngieBePeaksStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from .data import EngieBeConfigEntry

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
]

_HOURS_TO_MINUTES = 60

# hass.data layout:
#   hass.data[DOMAIN]["clients"][login_key] -> _SharedClient
_DATA_CLIENTS = "clients"


@dataclass
class _SharedClient:
    """
    One ENGIE login's API client, shared across its config entries.

    The ENGIE Auth0 access token authorises a *login*, not a single
    customer-account. When a user has multiple customer accounts under
    one login they show up as separate config entries, but they must
    all reuse the same EngieBeApiClient (and the same 60s refresh
    task), otherwise we'd run N concurrent refresh loops racing on the
    same refresh-token rotation and burning through ENGIE's rate limit.

    ``entry_ids`` is the live set of config entries currently using the
    client; the last one to unload tears the refresh task down. The
    ``lock`` serialises token rotations so a simultaneous reload of a
    sibling entry can't observe a half-written token pair.
    """

    client: EngieBeApiClient
    entry_ids: set[str] = field(default_factory=set)
    cancel_refresh: Callable[[], None] | None = field(default=None)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _shared_client_key(entry: EngieBeConfigEntry) -> str:
    """
    Compute the shared-client registry key for an entry.

    Derived from the ENGIE login email rather than ``entry.unique_id``
    because the unique_id format changes between schema versions
    (post v2->v3 it gains a per-customer suffix), but the login does
    not, so the key stays stable across migrations.
    """
    return slugify(entry.data[CONF_USERNAME])


def _get_clients_registry(hass: HomeAssistant) -> dict[str, _SharedClient]:
    """Return the shared-client registry, creating it on first access."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(_DATA_CLIENTS, {})


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
    shared = await _async_acquire_shared_client(hass, entry)
    client = shared.client

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    peaks_store = await _async_init_peaks_store(hass, entry.entry_id)

    entry.runtime_data = EngieBeData(
        client=client,
        coordinator=coordinator,
        last_options=dict(entry.options),
        peaks_store=peaks_store,
    )

    # Initial token refresh. With a shared client, a sibling may have
    # already refreshed in the same setup batch, so we serialise via
    # the shared lock to avoid two refreshes racing on the same
    # refresh_token (which ENGIE invalidates on rotation).
    try:
        async with shared.lock:
            new_access, new_refresh = await client.async_refresh_token()
        _persist_tokens_for_login(hass, shared, new_access, new_refresh)
    except EngieBeApiClientAuthenticationError as err:
        # Roll back the registry slot we just took so a leaked refresh
        # task isn't left running for an entry that never finished setup.
        _release_shared_client(hass, entry)
        msg = "Stored ENGIE credentials are no longer valid"
        raise ConfigEntryAuthFailed(msg) from err
    except EngieBeApiClientError as err:
        _release_shared_client(hass, entry)
        msg = "Unable to refresh ENGIE access token; will retry"
        raise ConfigEntryNotReady(msg) from err

    entry.runtime_data.authenticated = True
    # First successful refresh for this login arms the periodic task.
    # Doing this here (rather than in _async_acquire_shared_client)
    # means a setup that fails the initial refresh doesn't leave a
    # 60s callback hammering ENGIE with a known-bad token.
    _ensure_refresh_task_started(hass, shared, _shared_client_key(entry))

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
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Always release the shared client, even if platform unload failed,
    # so we don't leak a refresh task or pin a stale client in the
    # registry. HA will decide whether to retry the unload itself.
    _release_shared_client(hass, entry)
    return unload_ok


async def async_reload_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """Reload config entry only when options change (not on token rotation)."""
    if dict(entry.options) != entry.runtime_data.last_options:
        await hass.config_entries.async_reload(entry.entry_id)


# ---------------------------------------------------------------------------
# Shared-client registry
# ---------------------------------------------------------------------------


async def _async_acquire_shared_client(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> _SharedClient:
    """
    Get or create the shared client for ``entry``'s login.

    The first config entry to set up creates the API client and starts
    the periodic refresh task. Subsequent entries with the same login
    just register themselves on the existing record so the refresh
    task knows to fan token rotations and reauth flows out to them too.
    """
    registry = _get_clients_registry(hass)
    key = _shared_client_key(entry)
    shared = registry.get(key)

    if shared is None:
        client = EngieBeApiClient(
            session=async_get_clientsession(hass),
            client_id=entry.data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
            access_token=entry.data.get(CONF_ACCESS_TOKEN),
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        )
        shared = _SharedClient(client=client)
        registry[key] = shared
        LOGGER.debug("Created shared ENGIE client for login %s", key)

    shared.entry_ids.add(entry.entry_id)
    return shared


def _ensure_refresh_task_started(
    hass: HomeAssistant,
    shared: _SharedClient,
    key: str,
) -> None:
    """
    Arm the periodic token-refresh task if it isn't already running.

    Idempotent: a sibling entry that sets up after the task is already
    armed simply does nothing here. Called only after the entry's
    initial token refresh succeeds, so we never schedule a recurring
    refresh against credentials that ENGIE has already rejected.
    """
    if shared.cancel_refresh is not None:
        return
    shared.cancel_refresh = async_track_time_interval(
        hass,
        _make_refresh_callback(hass, key),
        timedelta(seconds=TOKEN_REFRESH_INTERVAL_SECONDS),
    )


def _release_shared_client(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Drop ``entry`` from the shared client; tear down if it was the last.

    Called from ``async_unload_entry``. Tolerates entries that were
    never registered (e.g. a setup that failed before acquisition) so
    HA can call us unconditionally.
    """
    registry = _get_clients_registry(hass)
    key = _shared_client_key(entry)
    shared = registry.get(key)
    if shared is None:
        return

    shared.entry_ids.discard(entry.entry_id)
    if shared.entry_ids:
        return

    # Last entry for this login is going away.
    if shared.cancel_refresh is not None:
        shared.cancel_refresh()
        shared.cancel_refresh = None
    registry.pop(key, None)
    LOGGER.debug("Released shared ENGIE client for login %s", key)


def _make_refresh_callback(
    hass: HomeAssistant,
    key: str,
) -> Callable[[object], asyncio.Future[None]]:
    """
    Build the periodic refresh callback for one login.

    The callback closes over the registry key rather than the entry
    itself so it can fan token rotations out to every config entry
    currently sharing this login, not just the one that happened to
    set up first.
    """

    async def _refresh_token_callback(_now: object) -> None:
        """Refresh the access token and propagate to all sibling entries."""
        registry = _get_clients_registry(hass)
        shared = registry.get(key)
        if shared is None:
            # Registry entry vanished between schedule and dispatch
            # (e.g. all entries unloaded). Nothing to do.
            return

        try:
            async with shared.lock:
                new_access, new_refresh = await shared.client.async_refresh_token()
        except EngieBeApiClientAuthenticationError:
            _mark_unauthenticated(hass, shared)
            LOGGER.warning(
                "Scheduled token refresh rejected by ENGIE for login %s; "
                "starting reauth flow on %d entry(ies)",
                key,
                len(shared.entry_ids),
            )
            _start_reauth_for_login(hass, shared)
            return
        except EngieBeApiClientError:
            _mark_unauthenticated(hass, shared)
            LOGGER.warning(
                "Scheduled token refresh failed for login %s; will retry",
                key,
            )
            return

        _persist_tokens_for_login(hass, shared, new_access, new_refresh)
        _mark_authenticated(hass, shared)
        LOGGER.debug("Token refreshed for login %s", key)

    return _refresh_token_callback


def _persist_tokens_for_login(
    hass: HomeAssistant,
    shared: _SharedClient,
    access_token: str,
    refresh_token: str,
) -> None:
    """
    Write rotated tokens to every config entry sharing this login.

    A token rotation that only updated one entry would silently
    drop the new refresh_token from any sibling entry that later
    reloads on its own (it would re-read the now-revoked stored
    token from disk and start a reauth loop).

    Whenever fresh tokens land on a sibling, any reauth flow that
    was previously started for that sibling becomes moot, so we
    abort it here. This is what makes the multi-account UX work:
    the user clicks reauth on one entry, ``async_oauth_create_entry``
    (or our reauth step) writes tokens back, and the dialogs sitting
    on the user's other entries disappear on their own instead of
    asking for credentials they've already provided.
    """
    for entry_id in tuple(shared.entry_ids):
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            continue
        _persist_tokens(hass, entry, access_token, refresh_token)
        _dismiss_reauth_flows(hass, entry_id)


def _dismiss_reauth_flows(hass: HomeAssistant, entry_id: str) -> None:
    """
    Abort any in-progress reauth flow for ``entry_id``.

    Looks up reauth flows by handler+context (HA's flow manager
    indexes by handler, then we filter by source=reauth and
    matching entry_id). ``async_abort`` raises ``UnknownFlow`` if
    the flow is gone between lookup and abort, which is a benign
    race we swallow.
    """
    flow_manager = hass.config_entries.flow
    in_progress = flow_manager.async_progress_by_handler(
        DOMAIN,
        match_context={"source": SOURCE_REAUTH, "entry_id": entry_id},
    )
    for flow in in_progress:
        flow_id = flow.get("flow_id")
        if not flow_id:
            continue
        try:
            flow_manager.async_abort(flow_id)
        except Exception:  # noqa: BLE001 - benign race; flow already gone
            LOGGER.debug(
                "Reauth flow %s for entry %s already gone; skipping abort",
                flow_id,
                entry_id,
            )


def _mark_authenticated(hass: HomeAssistant, shared: _SharedClient) -> None:
    """Flip ``runtime_data.authenticated`` to True on every sibling entry."""
    _set_authenticated(hass, shared, value=True)


def _mark_unauthenticated(hass: HomeAssistant, shared: _SharedClient) -> None:
    """Flip ``runtime_data.authenticated`` to False on every sibling entry."""
    _set_authenticated(hass, shared, value=False)


def _set_authenticated(
    hass: HomeAssistant,
    shared: _SharedClient,
    *,
    value: bool,
) -> None:
    """Apply ``value`` to ``runtime_data.authenticated`` on every sibling."""
    for entry_id in tuple(shared.entry_ids):
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            continue
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None:
            continue
        runtime.authenticated = value


def _start_reauth_for_login(
    hass: HomeAssistant,
    shared: _SharedClient,
) -> None:
    """
    Trigger reauth on every config entry sharing this login.

    HA dedupes per entry, so each sibling sees at most one in-flight
    reauth flow even if the refresh task fires repeatedly. As soon as
    *any* one of those flows succeeds and rotated tokens are written
    back to every sibling via ``_persist_tokens_for_login``, the
    matching helper ``_dismiss_reauth_flows`` aborts the still-pending
    sibling flows: the user provides credentials once, every account
    recovers, and no leftover dialogs ask for the same login again.
    """
    for entry_id in tuple(shared.entry_ids):
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            continue
        entry.async_start_reauth(hass)


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
