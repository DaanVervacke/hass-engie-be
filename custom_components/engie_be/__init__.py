"""Custom integration to integrate ENGIE Belgium with Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from ._relations import (
    RELATIONS_BACKFILLABLE_KEYS,
    extract_accounts,
    subentry_title,
)
from .api import (
    EngieBeApiClient,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CUSTOMER_NUMBER,
    CONF_REFRESH_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    LOGGER,
    SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
    TOKEN_REFRESH_INTERVAL_SECONDS,
)
from .coordinator import EngieBeDataUpdateCoordinator, EngieBeEpexCoordinator
from .data import EngieBeData, EngieBeSubentryData
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

    if config_entry.version == 2:  # noqa: PLR2004 - migration source version
        await _async_migrate_v2_to_v3(hass, config_entry)
        LOGGER.info(
            "Migrated config entry %s from version 2 to 3",
            config_entry.entry_id,
        )

    return True


async def _async_migrate_v2_to_v3(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Promote a v2 single-account entry to a v3 entry with one ``ConfigSubentry``.

    v2 stored ``customer_number`` directly on ``entry.data`` and held all
    entities under one ``(DOMAIN, entry_id)`` device. v3 introduces a
    ``ConfigSubentry`` per customer account: device identifiers move to
    ``(DOMAIN, subentry_id)`` and a subset of entity ``unique_id`` values
    gain a subentry-id segment so that descriptors that repeat across
    accounts (peak/EPEX/calendar) do not collide on a single login.

    Migration is best-effort but state-preserving:

    1. Read the v2 ``customer_number`` from ``entry.data``. If absent
       (corrupt entry), bump version and return; setup will fail loudly.
    2. Try a relations backfill for the optional display fields. Failures
       are tolerated; the coordinator's first refresh will retry.
    3. Create one ``ConfigSubentry`` of type
       ``customer_account``.
    4. Mutate the existing v2 device in place via ``new_identifiers`` so
       ``name_by_user``, ``area_id``, ``labels`` and history survive.
    5. Rename the 9 affected entity ``unique_id`` values via
       ``async_migrate_entries`` so the entity_id stays stable
       (preserving history, dashboards, automations).
    6. Rename the peaks-history store file on disk from
       ``engie_be.peaks_history.{entry_id}`` to
       ``engie_be.peaks_history.{subentry_id}``.
    7. Drop ``customer_number`` from ``entry.data`` and bump version.
    """
    customer_number = entry.data.get(CONF_CUSTOMER_NUMBER)
    if not customer_number:
        # Corrupt or already-partially-migrated entry: just bump version
        # so HA stops re-running this migration. Setup will fail loudly
        # if the user has no customer subentries to fall back on.
        hass.config_entries.async_update_entry(entry, version=3)
        LOGGER.warning(
            "Config entry %s missing customer_number during v2->v3 migration; "
            "bumping version without creating a subentry",
            entry.entry_id,
        )
        return

    # Build the subentry payload, optionally enriched with relations data.
    subentry_data: dict[str, Any] = {CONF_CUSTOMER_NUMBER: customer_number}
    relations_account = await _async_try_relations_backfill(
        hass,
        entry,
        customer_number,
    )
    if relations_account:
        for key in RELATIONS_BACKFILLABLE_KEYS:
            value = relations_account.get(key)
            if value:
                subentry_data[key] = value
        title = subentry_title(relations_account)
    else:
        title = customer_number

    subentry = ConfigSubentry(
        data=MappingProxyType(subentry_data),
        subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
        title=title,
        unique_id=customer_number,
    )
    hass.config_entries.async_add_subentry(entry, subentry)

    # Mutate the v2 device in place so user customisations (name_by_user,
    # area_id, labels, automation references) survive the migration.
    device_reg = dr.async_get(hass)
    v2_device = device_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    if v2_device is not None:
        device_reg.async_update_device(
            v2_device.id,
            new_identifiers={(DOMAIN, subentry.subentry_id)},
            add_config_subentry_id=subentry.subentry_id,
        )

    # Rename the unique_ids of entities whose v2 keys are now subentry-scoped.
    # Energy sensors (EAN-embedded keys) and the auth binary sensor keep their
    # v2 ids and need no rename.
    _migrate_entity_unique_ids(hass, entry.entry_id, subentry.subentry_id)

    # Rename the peaks-history store file on disk from the old per-entry
    # key to the new per-subentry key. Done via ``Store`` so the store
    # manager's cache stays consistent.
    await _async_rename_peaks_store(hass, entry.entry_id, subentry.subentry_id)

    # Drop the now-redundant top-level customer_number from entry.data and
    # bump the version. ``customer_number`` lives on the subentry from v3
    # onwards.
    new_data = {k: v for k, v in entry.data.items() if k != CONF_CUSTOMER_NUMBER}
    hass.config_entries.async_update_entry(entry, data=new_data, version=3)


async def _async_try_relations_backfill(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
    customer_number: str,
) -> dict[str, Any] | None:
    """
    Try to fetch the relations record for ``customer_number``.

    Returns the matching account dict or ``None``. Failures are swallowed
    and logged: the coordinator's first refresh retries this same backfill
    via the same shared helper, so a one-off migration-time failure is not
    fatal.
    """
    access_token = entry.data.get(CONF_ACCESS_TOKEN)
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    if not refresh_token:
        return None

    client = EngieBeApiClient(
        session=async_get_clientsession(hass),
        client_id=entry.data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
        access_token=access_token,
        refresh_token=refresh_token,
    )
    try:
        await client.async_refresh_token()
        relations = await client.async_get_customer_account_relations()
    except EngieBeApiClientError as err:
        LOGGER.debug(
            "Relations backfill skipped during v2->v3 migration of %s: %s",
            entry.entry_id,
            err,
        )
        return None

    accounts = extract_accounts(relations)
    for account in accounts:
        if account.get(CONF_CUSTOMER_NUMBER) == customer_number:
            return account
    return None


def _migrate_entity_unique_ids(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
) -> None:
    """
    Rename v2 unique_ids that became subentry-scoped in v3.

    The v2 keys listed below were previously globally unique on a single
    login because the integration only supported one customer account.
    In v3 the same descriptor repeats across customer accounts, so
    ``{entry_id}_{key}`` would collide. The subentry-id segment keeps
    them disjoint.

    Entity ids are preserved (the registry retains the old ``entity_id``
    when ``new_unique_id`` is set), so history, dashboards, automations,
    and statistics continue to work.
    """
    keys_to_rename = (
        # Captar peaks
        "captar_monthly_peak_power",
        "captar_monthly_peak_energy",
        "captar_monthly_peak_start",
        "captar_monthly_peak_end",
        # EPEX sensors
        "epex_current",
        "epex_low_today",
        "epex_high_today",
        # EPEX negative binary sensor
        "epex_negative",
        # Calendar
        "calendar",
    )
    old_to_new = {
        f"{entry_id}_{key}": f"{entry_id}_{subentry_id}_{key}"
        for key in keys_to_rename
    }

    @callback
    def _migrate(entity_entry: er.RegistryEntry) -> dict[str, Any] | None:
        new_unique_id = old_to_new.get(entity_entry.unique_id)
        if new_unique_id is None:
            return None
        return {
            "new_unique_id": new_unique_id,
            "config_subentry_id": subentry_id,
        }

    er.async_migrate_entries(hass, entry_id, _migrate)


async def _async_rename_peaks_store(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
) -> None:
    """
    Rename the persisted peaks-history file from per-entry to per-subentry key.

    Reads via a transient ``Store`` keyed on the v2 filename, writes via
    the production ``EngieBePeaksStore`` (which uses the v3 filename),
    then removes the v2 file. Failures are logged but never raise: a
    missing peaks file is benign and the store self-heals on next save.
    """
    old_key = f"{DOMAIN}.peaks_history.{entry_id}"
    old_store: Store[dict[str, Any]] = Store(hass, 1, old_key)
    try:
        old_data = await old_store.async_load()
    except Exception:  # noqa: BLE001 - migration must never abort on store IO
        LOGGER.warning(
            "Could not read v2 peaks store %s during migration; starting fresh",
            old_key,
        )
        return

    if not isinstance(old_data, dict):
        # Nothing persisted in v2 yet; nothing to rename.
        return

    peaks = old_data.get("peaks")
    if not isinstance(peaks, list) or not peaks:
        # File existed but held no peaks; just remove the empty file.
        await old_store.async_remove()
        return

    new_store = EngieBePeaksStore(hass, subentry_id)
    await new_store.async_load()  # populate _loaded so saves work
    migrated = 0
    for peak in peaks:
        if (
            isinstance(peak, dict)
            and isinstance(peak.get("year"), int)
            and isinstance(peak.get("month"), int)
        ):
            new_store.upsert(
                year=peak["year"],
                month=peak["month"],
                start=peak.get("start", ""),
                end=peak.get("end", ""),
                peak_kw=peak.get("peakKW"),
                peak_kwh=peak.get("peakKWh"),
            )
            migrated += 1

    # Remove the v2 file so the next ``Store`` cache cycle does not pick
    # it up. ``async_remove`` is idempotent (suppresses FileNotFoundError).
    if migrated:
        await new_store.async_save_now()
    await old_store.async_remove()
    LOGGER.debug(
        "Migrated %d peak(s) from v2 store %s to v3 store for subentry %s",
        migrated,
        old_key,
        subentry_id,
    )


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

    # Build per-subentry coordinators, peak stores and service-points
    # lookups, then do their initial refreshes in parallel so that a
    # user with N customer accounts does not pay sum(latency) at setup.
    subentries: list[ConfigSubentry] = [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_CUSTOMER_ACCOUNT
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

    # Refresh EPEX once at startup alongside the per-subentry customer
    # data; EPEX is shared across subentries so this is one fetch total.
    refresh_calls = [epex_coordinator.async_config_entry_first_refresh()]
    refresh_calls.extend(
        sub_data.coordinator.async_config_entry_first_refresh()
        for sub_data in entry.runtime_data.subentry_data.values()
    )
    await asyncio.gather(*refresh_calls)

    # Resolve energy type for each EAN per subentry. Service-point lookups
    # are fanned out across all subentries' EANs in a single gather so
    # multi-account customers do not pay sum(latency) for setup.
    await _async_populate_service_points(client, entry)

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
    per-subentry, since EANs belong to one customer account). Lookups
    are issued in parallel across all subentries' EANs so a multi-account
    user does not pay sum(latency) at setup. Failures degrade gracefully:
    a missing service-point falls back to the heuristic in the sensor
    layer, exactly as for single-account setups.
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
            entry.runtime_data.subentry_data[subentry_id].service_points[ean] = (
                division
            )
            LOGGER.debug("Service-point %s: division=%s", _hash_ean(ean), division)
