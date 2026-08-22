"""Custom integration to integrate ENGIE Belgium with Home Assistant."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import persistent_notification as pn
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID, Platform
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from ._contracts import bare_ean, is_account_dynamic, service_points_by_ean
from ._statistics import (
    _filter_by_contract,
    _last_stats,
    async_clear_usage_history,
    async_import_usage_history,
    streams_for_energy_types,
)
from .api import (
    EngieBeApiClient,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
from .const import (
    ATTR_END_DATE,
    ATTR_ENERGY_TYPE,
    ATTR_INCLUDE_COSTS,
    ATTR_START_DATE,
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_EXPOSE_ALL_ENTITIES,
    CONF_IMPORT_END_DATE,
    CONF_IMPORT_ENERGY_TYPES,
    CONF_IMPORT_HISTORY,
    CONF_IMPORT_INCLUDE_COSTS,
    CONF_IMPORT_START_DATE,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    ENERGY_TYPE_OPTIONS,
    HISTORY_BACKFILL_STALE_DAYS,
    LOGGER,
    SERVICE_CLEAR_IMPORT_HISTORY,
    SERVICE_IMPORT_HISTORY,
    SIGNAL_AUTHENTICATION_STATE_CHANGED,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
    TOKEN_REFRESH_INTERVAL_SECONDS,
)
from .coordinator import (
    EngieBeDataUpdateCoordinator,
    EngieBeEpexCoordinator,
    EngieBeEpexQuarterHourCoordinator,
)
from .data import EngieBeData, EngieBeSubentryData
from .diagnostics import _hash_ean
from .store import EngieBeHappyHoursStore, EngieBePeaksStore

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.device_registry import DeviceEntry
    from homeassistant.helpers.typing import ConfigType

    from .data import EngieBeConfigEntry

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:  # noqa: ARG001
    """Register domain-level services so they survive per-entry setup failures."""
    _async_register_services(hass)
    return True


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> bool:
    """Refuse to migrate config entries from before v0.9.0."""
    LOGGER.error(
        "Cannot migrate ENGIE Belgium config entry from version %s. "
        "v0.9.0 is a breaking schema change: remove this integration from "
        "Settings -> Devices & Services and add it again. See the v0.9.0 "
        "changelog for details.",
        entry.version,
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"pre_v5_entry_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="pre_v5_entry",
        translation_placeholders={"version": str(entry.version)},
    )
    return False


async def async_setup_entry(  # noqa: PLR0915 - orchestrator, splitting hurts readability
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    client = EngieBeApiClient(
        session=async_get_clientsession(hass),
        client_id=DEFAULT_CLIENT_ID,
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
    )

    epex_coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)
    epex_qh_coordinator = EngieBeEpexQuarterHourCoordinator(
        hass=hass, config_entry=entry
    )

    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=epex_coordinator,
        epex_qh_coordinator=epex_qh_coordinator,
        last_options=dict(entry.options),
        last_subentry_ids={
            sub.subentry_id
            for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
        },
    )

    # Register the update listener without ``async_on_unload`` so it survives a
    # failed setup and still fires on reauth completion.
    # ponytail: relying on the public ``update_listeners`` field is
    # intentional; the returned unlisten callable is discarded because the
    # listener must outlive individual setup attempts.
    if async_reload_entry not in entry.update_listeners:
        entry.add_update_listener(async_reload_entry)

    try:
        new_access, new_refresh = await client.async_refresh_token()
    except EngieBeApiClientAuthenticationError as err:
        msg = "Stored ENGIE credentials are no longer valid"
        raise ConfigEntryAuthFailed(msg) from err
    except EngieBeApiClientError as err:
        msg = "Unable to refresh ENGIE access token; will retry"
        raise ConfigEntryNotReady(msg) from err

    _persist_tokens(hass, entry, new_access, new_refresh)
    _set_authenticated(hass, entry, authenticated=True)

    async def _refresh_token_callback(_now: object) -> None:
        """Refresh the access token periodically."""
        try:
            new_access, new_refresh = await client.async_refresh_token()
        except EngieBeApiClientAuthenticationError:
            _set_authenticated(hass, entry, authenticated=False)
            LOGGER.warning(
                "Scheduled token refresh rejected by ENGIE; starting reauth flow"
            )
            # Cancel the timer before starting reauth so it stops firing 403s
            # until the entry reloads. Double-cancel is safe.
            runtime = entry.runtime_data
            if runtime.cancel_token_refresh is not None:
                runtime.cancel_token_refresh()
                runtime.cancel_token_refresh = None
            entry.async_start_reauth(hass)
            return
        except EngieBeApiClientError as err:
            _set_authenticated(hass, entry, authenticated=False)
            LOGGER.warning(
                "Scheduled token refresh failed (%s: %s); will retry",
                type(err).__name__,
                err,
            )
            return

        _persist_tokens(hass, entry, new_access, new_refresh)
        _set_authenticated(hass, entry, authenticated=True)
        LOGGER.debug("Token refreshed successfully")

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
        happy_hours_store = await _async_init_happy_hours_store(
            hass, subentry.subentry_id
        )
        entry.runtime_data.subentry_data[subentry.subentry_id] = EngieBeSubentryData(
            coordinator=coordinator,
            peaks_store=peaks_store,
            happy_hours_store=happy_hours_store,
        )

    # ``return_exceptions=True`` so a coordinator failure does not cancel
    # siblings mid-flight. After all tasks settle, prefer re-raising
    # ``ConfigEntryAuthFailed``, then ``ConfigEntryNotReady``, else the first.
    refresh_calls = [
        epex_coordinator.async_config_entry_first_refresh(),
        epex_qh_coordinator.async_config_entry_first_refresh(),
    ]
    refresh_calls.extend(
        sub_data.coordinator.async_config_entry_first_refresh()
        for sub_data in entry.runtime_data.subentry_data.values()
    )
    results = await asyncio.gather(*refresh_calls, return_exceptions=True)
    exceptions = [r for r in results if isinstance(r, BaseException)]
    if exceptions:
        for exc in exceptions:
            if isinstance(exc, ConfigEntryAuthFailed):
                raise exc
        for exc in exceptions:
            if isinstance(exc, ConfigEntryNotReady):
                raise exc
        raise exceptions[0]

    # Fan out service-point lookups and dynamic-tariff detection so the
    # ``is_dynamic`` flag is settled before platforms are forwarded.
    await asyncio.gather(
        _async_populate_service_points(client, entry),
        _async_populate_dynamic_flags(client, entry),
    )
    _merge_service_points_from_contracts(entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_reenable_expose_all_entities(hass, entry)

    # Spawn setup-time background import tasks for subentries with
    # ``import_history = True``. The closure snapshots client/subentry so a
    # reload's runtime_data teardown does not leave it holding stale state.
    for subentry in subentries:
        subentry_data: Mapping[str, Any] = subentry.data or {}
        if not subentry_data.get(CONF_IMPORT_HISTORY, False):
            continue
        raw_energy_types: list[str] = subentry_data.get(
            CONF_IMPORT_ENERGY_TYPES, list(ENERGY_TYPE_OPTIONS)
        )
        include_costs: bool = subentry_data.get(CONF_IMPORT_INCLUDE_COSTS, False)
        streams = streams_for_energy_types(
            raw_energy_types, include_costs=include_costs
        )
        ban = subentry_data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
        raw_start = subentry_data.get(CONF_IMPORT_START_DATE)
        raw_end = subentry_data.get(CONF_IMPORT_END_DATE)
        start_date: date | None = date.fromisoformat(raw_start) if raw_start else None
        # User-facing end_date is inclusive, orchestrator wants exclusive.
        end_date: date | None = (
            date.fromisoformat(raw_end) + timedelta(days=1) if raw_end else None
        )
        snap_client = client
        snap_subentry = subentry
        snap_sub_data = entry.runtime_data.subentry_data.get(subentry.subentry_id)
        snap_contracts_payload = (
            snap_sub_data.energy_contracts_payload if snap_sub_data else None
        )
        entry.async_create_background_task(
            hass,
            _async_guarded_import(
                hass,
                snap_client,
                snap_subentry,
                streams=streams,
                start_date=start_date,
                end_date=end_date,
                contracts_payload=snap_contracts_payload,
            ),
            name=f"engie_be import {ban[-4:] if ban else '????'}",
        )
        LOGGER.debug(
            "Scheduled setup-time import for BAN ***%s "
            "(streams=%s include_costs=%s start=%s end=%s)",
            ban[-4:] if ban else "????",
            sorted(streams),
            include_costs,
            start_date,
            end_date,
        )

    # Register the recurring token refresh only after every step that can raise
    # ``ConfigEntryNotReady``, otherwise it leaks on a half-set-up entry.
    cancel_refresh = async_track_time_interval(
        hass,
        _refresh_token_callback,
        timedelta(seconds=TOKEN_REFRESH_INTERVAL_SECONDS),
    )
    entry.runtime_data.cancel_token_refresh = cancel_refresh
    entry.async_on_unload(cancel_refresh)

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Allow removing devices with no matching active business-agreement subentry."""
    active_subentry_ids: set[str] = {
        sub.subentry_id
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
    }
    return not any(
        (DOMAIN, subentry_id) in device_entry.identifiers
        for subentry_id in active_subentry_ids
    )


async def async_remove_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """Delete imported statistics and per-subentry stores on entry removal."""
    for subentry_id in entry.subentries:
        await EngieBePeaksStore(hass, subentry_id).async_remove()
        await EngieBeHappyHoursStore(hass, subentry_id).async_remove()

    streams = streams_for_energy_types(None, include_costs=True)
    for subentry in entry.subentries.values():
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
        if not ban:
            continue
        try:
            await async_clear_usage_history(hass, ban, streams=streams)
        except KeyError:
            LOGGER.warning(
                "Could not clear imported statistics for BAN ***%s: the "
                "recorder is not available. Any imported history remains "
                "in the database.",
                ban[-4:],
            )
            return


async def async_reload_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Reload on options change, subentry add/remove, or external token rotation.

    Routine token rotation is a no-op because the live client's refresh
    token equals the stored one when the listener runs. Reauth writes
    tokens externally so the mismatch is the trigger. Multi-pick
    subentry adds are batched via ``pending_subentry_target``.
    """
    runtime = entry.runtime_data
    options_changed = dict(entry.options) != runtime.last_options
    current_subentry_ids = {
        sub.subentry_id
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
    }
    subentries_changed = current_subentry_ids != runtime.last_subentry_ids
    tokens_externally_updated = (
        entry.data.get(CONF_REFRESH_TOKEN) != runtime.client.refresh_token
    )

    target = runtime.pending_subentry_target
    if target is not None and not options_changed:
        # Multi-add in progress: suppress until the target BAN set is reached,
        # then reload once. Superset rather than equality so a concurrent
        # removal cannot wedge the gate open.
        if _business_agreement_numbers(entry) >= target:
            runtime.pending_subentry_target = None
            await hass.config_entries.async_reload(entry.entry_id)
        return

    if options_changed or subentries_changed or tokens_externally_updated:
        await hass.config_entries.async_reload(entry.entry_id)


def _business_agreement_numbers(entry: EngieBeConfigEntry) -> set[str]:
    """Return the BANs currently attached as business-agreement subentries."""
    bans: set[str] = set()
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_TYPE_BUSINESS_AGREEMENT:
            continue
        ban = sub.unique_id or sub.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            bans.add(ban)
    return bans


_ENERGY_TYPE_LIST = vol.All(cv.ensure_list, [vol.In(ENERGY_TYPE_OPTIONS)])

_IMPORT_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_ENERGY_TYPE): _ENERGY_TYPE_LIST,
        vol.Optional(ATTR_START_DATE): cv.date,
        vol.Optional(ATTR_END_DATE): cv.date,
        vol.Optional(ATTR_INCLUDE_COSTS, default=False): cv.boolean,
    },
)

_CLEAR_IMPORT_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_ENERGY_TYPE): _ENERGY_TYPE_LIST,
        vol.Optional(ATTR_INCLUDE_COSTS, default=True): cv.boolean,
    },
)


def _resolve_targets(
    hass: HomeAssistant,
    device_ids: list[str],
    service_name: str,
) -> list[tuple[EngieBeConfigEntry, ConfigSubentry]]:
    """Resolve service ``device_id`` targets to (entry, subentry) pairs."""
    device_reg = dr.async_get(hass)
    resolved: list[tuple[EngieBeConfigEntry, ConfigSubentry]] = []
    LOGGER.debug(
        "%s: resolving %d device_id(s): %s",
        service_name,
        len(device_ids),
        device_ids,
    )
    for device_id in device_ids:
        device = device_reg.async_get(device_id)
        if device is None:
            LOGGER.warning("Unknown device %s for %s", device_id, service_name)
            continue
        subentry_id: str | None = None
        for domain, ident in device.identifiers:
            if domain != DOMAIN or ident.startswith("login_"):
                continue
            subentry_id = ident
            break
        if subentry_id is None:
            LOGGER.warning(
                "Device %s is not a business-agreement device; skipping",
                device_id,
            )
            continue
        found = False
        for entry in hass.config_entries.async_entries(DOMAIN):
            subentry = entry.subentries.get(subentry_id)
            if subentry is None:
                continue
            # Gate on entry state, not runtime_data: a failed setup leaves a
            # live-looking client behind, so LOADED is the only safe signal.
            if entry.state is not ConfigEntryState.LOADED:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="service_entry_reloading",
                    translation_placeholders={"entry_id": entry.entry_id},
                )
            LOGGER.debug(
                "%s: device %s -> entry_id=%s subentry_id=%s title=%r",
                service_name,
                device_id,
                entry.entry_id,
                subentry_id,
                subentry.title,
            )
            resolved.append((entry, subentry))
            found = True
            break
        if not found:
            LOGGER.warning(
                "No live config entry owns subentry %s; skipping", subentry_id
            )
    if device_ids and not resolved:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="service_no_valid_target",
        )
    return resolved


async def _async_guarded_import(  # noqa: PLR0913
    hass: HomeAssistant,
    client: EngieBeApiClient,
    subentry: ConfigSubentry,
    *,
    streams: frozenset[str],
    start_date: date | None = None,
    end_date: date | None = None,
    contracts_payload: dict[str, Any] | None = None,
) -> None:
    """
    Run a setup-time historical import, freshness-checked and error-swallowing.

    Skips when every contracted stream has a statistic newer than
    HISTORY_BACKFILL_STALE_DAYS. On failure, raises a non-fixable
    Repairs issue rather than failing entry setup. ``end_date`` must
    already be shifted to exclusive by the caller.
    """
    ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
    masked = ban[-4:] if ban else "????"
    address = subentry.title or f"BAN ***{masked}"
    notification_id = f"engie_be_import_{subentry.subentry_id}"
    issue_id = f"setup_import_failed_{subentry.subentry_id}"
    try:
        contracted_streams = _filter_by_contract(streams, contracts_payload)
        existing = (
            await _last_stats(hass, ban, contracted_streams)
            if contracted_streams
            else {}
        )
        if contracted_streams and existing.keys() >= contracted_streams:
            ages = {
                stream: dt_util.utcnow()
                - dt_util.utc_from_timestamp(float(existing[stream]["start"]))
                for stream in contracted_streams
            }
            oldest = max(ages.values())
            if oldest <= timedelta(days=HISTORY_BACKFILL_STALE_DAYS):
                LOGGER.debug(
                    "Setup-time import skipped for BAN ***%s: all %d contracted "
                    "stream(s) present and current, oldest as of %s ago "
                    "(streams: %s)",
                    masked,
                    len(contracted_streams),
                    oldest,
                    sorted(contracted_streams),
                )
                ir.async_delete_issue(hass, DOMAIN, issue_id)
                return
            LOGGER.debug(
                "Setup-time import retrying for BAN ***%s: oldest contracted "
                "stream's stat is %s old (>%d days), treating as an "
                "interrupted backfill rather than a completed one",
                masked,
                oldest,
                HISTORY_BACKFILL_STALE_DAYS,
            )
        elif contracted_streams:
            LOGGER.debug(
                "Setup-time import retrying for BAN ***%s: %d of %d contracted "
                "stream(s) have never been imported (%s)",
                masked,
                len(contracted_streams - existing.keys()),
                len(contracted_streams),
                sorted(contracted_streams - existing.keys()),
            )
        is_retry = bool(existing)
        message = (
            f"Resuming historical usage import for {address}."
            if is_retry
            else (
                f"Importing historical usage for {address}. "
                "This can take a few minutes for a multi-year window."
            )
        )
        pn.async_create(
            hass,
            message,
            title="ENGIE Belgium: importing historical data",
            notification_id=notification_id,
        )
        rows_written = await async_import_usage_history(
            hass,
            client,
            subentry,
            streams=streams,
            start_date=start_date,
            end_date=end_date,
            contracts_payload=contracts_payload,
        )
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        if rows_written == 0:
            message = (
                f"No historical usage was imported for {address}. "
                "This can happen when the selected energy types have no "
                "matching contract on this business agreement, or when "
                "ENGIE has no data for the requested window."
            )
            title = "ENGIE Belgium: historical import produced no data"
        else:
            message = (
                f"Historical usage for {address} imported into long-term statistics."
            )
            title = "ENGIE Belgium: historical import complete"
        pn.async_create(
            hass,
            message,
            title=title,
            notification_id=notification_id,
        )
    except asyncio.CancelledError:
        # Dismiss the running notification so unload does not orphan it.
        pn.async_dismiss(hass, notification_id)
        raise
    except Exception:  # noqa: BLE001
        LOGGER.exception(
            "Setup-time historical import failed for BAN ***%s; "
            "use the import_history service action to retry",
            masked,
        )
        pn.async_dismiss(hass, notification_id)
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="setup_import_failed",
            translation_placeholders={"title": subentry.title or ban},
        )


@dataclass(frozen=True, slots=True)
class _ImportHistoryCall:
    """Extracted, validated payload of an ``engie_be.import_history`` service call."""

    device_ids: list[str]
    energy_type: list[str] | None
    include_costs: bool
    start_date: date | None
    end_date: date | None

    @classmethod
    def from_service_call(cls, call: ServiceCall) -> _ImportHistoryCall:
        """Read fields off ``call.data``, preserving the handler's original defaults."""
        data = call.data
        return cls(
            device_ids=list(data.get(ATTR_DEVICE_ID) or []),
            energy_type=data.get(ATTR_ENERGY_TYPE),
            include_costs=bool(data.get(ATTR_INCLUDE_COSTS, False)),
            start_date=data.get(ATTR_START_DATE),
            end_date=data.get(ATTR_END_DATE),
        )


@dataclass(frozen=True, slots=True)
class _ClearImportHistoryCall:
    """Extracted, validated payload of an ``engie_be.clear_import_history`` call."""

    device_ids: list[str]
    energy_type: list[str] | None
    include_costs: bool

    @classmethod
    def from_service_call(cls, call: ServiceCall) -> _ClearImportHistoryCall:
        """Read fields off ``call.data`` (``include_costs`` defaults to True)."""
        data = call.data
        return cls(
            device_ids=list(data.get(ATTR_DEVICE_ID) or []),
            energy_type=data.get(ATTR_ENERGY_TYPE),
            include_costs=bool(data.get(ATTR_INCLUDE_COSTS, True)),
        )


def _async_register_services(hass: HomeAssistant) -> None:  # noqa: PLR0915 - two service handlers, branches are all irreducible
    """Register the domain-level services."""

    async def _handle_import_history(call: ServiceCall) -> None:
        payload = _ImportHistoryCall.from_service_call(call)
        if not payload.device_ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="service_no_target_device",
            )
        if payload.energy_type is not None and not payload.energy_type:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="service_no_energy_type_selected",
            )
        streams = streams_for_energy_types(
            payload.energy_type,
            include_costs=payload.include_costs,
        )
        LOGGER.debug(
            "import_history called: device_ids=%s energy_type=%s"
            " include_costs=%s start=%s end=%s",
            payload.device_ids,
            payload.energy_type,
            payload.include_costs,
            payload.start_date,
            payload.end_date,
        )
        targets = list(
            _resolve_targets(hass, payload.device_ids, "engie_be.import_history")
        )
        # User-facing end_date is inclusive, ENGIE endpoint wants exclusive.
        api_end_date = (
            payload.end_date + timedelta(days=1) if payload.end_date else None
        )

        async def _run_one(
            entry: EngieBeConfigEntry,
            subentry: ConfigSubentry,
        ) -> int:
            ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
            LOGGER.debug(
                "import_history: dispatching to BAN ***%s title=%r",
                ban[-4:] if ban else "????",
                subentry.title,
            )
            sub_data = entry.runtime_data.subentry_data.get(subentry.subentry_id)
            cached_contracts = sub_data.energy_contracts_payload if sub_data else None
            return await async_import_usage_history(
                hass,
                entry.runtime_data.client,
                subentry,
                start_date=payload.start_date,
                end_date=api_end_date,
                streams=streams,
                contracts_payload=cached_contracts,
            )

        results = await asyncio.gather(
            *(_run_one(entry, subentry) for entry, subentry in targets),
            return_exceptions=True,
        )

        failed: list[str] = []  # masked BANs, for the aggregate message

        def _flag_failed(subentry: ConfigSubentry, masked: str) -> None:
            """Raise the service-failure Repairs issue and record the BAN."""
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"service_import_failed_{subentry.subentry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="service_import_failed",
                translation_placeholders={
                    "title": subentry.title or f"BAN ***{masked}"
                },
            )
            failed.append(f"BAN ***{masked}")

        for (_entry, subentry), result in zip(targets, results, strict=True):
            ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
            masked = ban[-4:] if ban else "????"

            if isinstance(result, asyncio.CancelledError):
                raise result

            if isinstance(result, ServiceValidationError):
                # User-input error, propagate without traceback or Repairs card.
                raise result

            if isinstance(result, EngieBeApiClientAuthenticationError):
                LOGGER.warning(
                    "import_history: authentication rejected for BAN ***%s; "
                    "reauth will be triggered by the next token refresh",
                    masked,
                )
                _flag_failed(subentry, masked)
                continue
            if isinstance(result, BaseException):
                LOGGER.exception(
                    "import_history: unexpected error for BAN ***%s",
                    masked,
                    exc_info=result,
                )
                _flag_failed(subentry, masked)
                continue

            # Clear both this call's issue and the setup-time backfill's
            # issue: the latter tells the user to retry via this service.
            ir.async_delete_issue(
                hass, DOMAIN, f"service_import_failed_{subentry.subentry_id}"
            )
            ir.async_delete_issue(
                hass, DOMAIN, f"setup_import_failed_{subentry.subentry_id}"
            )

        if failed:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="import_history_failed",
                translation_placeholders={
                    "error": (
                        f"{len(failed)} of {len(targets)} target(s) failed "
                        f"({', '.join(failed)}); see Settings -> Repairs and the "
                        "log for details."
                    ),
                },
            )

    async def _handle_clear_import_history(call: ServiceCall) -> None:
        payload = _ClearImportHistoryCall.from_service_call(call)
        if not payload.device_ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="service_no_target_device",
            )
        if payload.energy_type is not None and not payload.energy_type:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="service_no_energy_type_selected",
            )
        streams = streams_for_energy_types(
            payload.energy_type,
            include_costs=payload.include_costs,
        )
        LOGGER.debug(
            "clear_import_history called: device_ids=%s"
            " energy_type=%s include_costs=%s",
            payload.device_ids,
            payload.energy_type,
            payload.include_costs,
        )
        targets = list(
            _resolve_targets(hass, payload.device_ids, "engie_be.clear_import_history")
        )

        async def _clear_one(subentry: ConfigSubentry) -> None:
            ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
            if not ban:
                return
            LOGGER.debug(
                "clear_import_history: dispatching to BAN ***%s title=%r",
                ban[-4:],
                subentry.title,
            )
            await async_clear_usage_history(hass, ban, streams=streams)

        results = await asyncio.gather(
            *(_clear_one(subentry) for _entry, subentry in targets),
            return_exceptions=True,
        )

        failed: list[str] = []  # masked BANs, for the aggregate message
        for (_entry, subentry), result in zip(targets, results, strict=True):
            ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
            masked = ban[-4:] if ban else "????"
            issue_id = f"service_clear_failed_{subentry.subentry_id}"

            if isinstance(result, asyncio.CancelledError):
                raise result

            if isinstance(result, BaseException):
                LOGGER.exception(
                    "clear_import_history: unexpected error for BAN ***%s",
                    masked,
                    exc_info=result,
                )
                ir.async_create_issue(
                    hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="service_clear_failed",
                    translation_placeholders={
                        "title": subentry.title or f"BAN ***{masked}"
                    },
                )
                failed.append(f"BAN ***{masked}")
                continue

            ir.async_delete_issue(hass, DOMAIN, issue_id)

        if failed:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="clear_history_failed",
                translation_placeholders={
                    "error": (
                        f"{len(failed)} of {len(targets)} target(s) failed "
                        f"({', '.join(failed)}); see Settings -> Repairs and the "
                        "log for details."
                    ),
                },
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_HISTORY,
        _handle_import_history,
        schema=_IMPORT_HISTORY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_IMPORT_HISTORY,
        _handle_clear_import_history,
        schema=_CLEAR_IMPORT_HISTORY_SCHEMA,
    )


def _persist_tokens(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
    access_token: str,
    refresh_token: str,
) -> None:
    """Persist refreshed tokens to the config entry data, skipping no-op writes."""
    current_access = entry.data.get(CONF_ACCESS_TOKEN)
    current_refresh = entry.data.get(CONF_REFRESH_TOKEN)
    if current_access == access_token and current_refresh == refresh_token:
        return
    updated_data = {**entry.data}
    updated_data[CONF_ACCESS_TOKEN] = access_token
    updated_data[CONF_REFRESH_TOKEN] = refresh_token
    hass.config_entries.async_update_entry(entry, data=updated_data)


def _set_authenticated(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
    *,
    authenticated: bool,
) -> None:
    """Update login auth state and notify the auth binary sensor on changes."""
    if entry.runtime_data.authenticated == authenticated:
        return
    entry.runtime_data.authenticated = authenticated
    async_dispatcher_send(
        hass,
        SIGNAL_AUTHENTICATION_STATE_CHANGED.format(entry_id=entry.entry_id),
    )


async def _async_init_peaks_store(
    hass: HomeAssistant,
    subentry_id: str,
) -> EngieBePeaksStore:
    """Build and load the persistent peaks-history store for one subentry."""
    store = EngieBePeaksStore(hass, subentry_id)
    await store.async_load()
    return store


async def _async_init_happy_hours_store(
    hass: HomeAssistant,
    subentry_id: str,
) -> EngieBeHappyHoursStore:
    """Build and load the persistent Happy Hours history store for one subentry."""
    store = EngieBeHappyHoursStore(hass, subentry_id)
    await store.async_load()
    return store


async def _async_populate_service_points(
    client: EngieBeApiClient,
    entry: EngieBeConfigEntry,
) -> None:
    """Resolve EAN-to-energy-type for every subentry in one fan-out call."""
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
            raise result
        division: str = result.get("division", "")
        if division:
            # Prices API returns EAN with a delivery-point suffix, store bare.
            ean_short = bare_ean(ean)
            entry.runtime_data.subentry_data[subentry_id].service_points[ean_short] = (
                division
            )
            LOGGER.debug("Service-point %s: division=%s", _hash_ean(ean), division)


def _merge_service_points_from_contracts(entry: EngieBeConfigEntry) -> None:
    """
    Fill service_points gaps using the energy-contracts payload.

    Dynamic-tariff accounts have no prices-endpoint items, so their EANs
    would never be learned otherwise. Never overwrites an existing entry.
    """
    for sub_data in entry.runtime_data.subentry_data.values():
        contract_points = service_points_by_ean(sub_data.energy_contracts_payload)
        for ean, division in contract_points.items():
            ean_short = bare_ean(ean)
            sub_data.service_points.setdefault(ean_short, division)


def _async_reenable_expose_all_entities(
    hass: HomeAssistant, entry: EngieBeConfigEntry
) -> None:
    """Re-enable INTEGRATION-disabled entities when expose_all_entities is on."""
    if not entry.options.get(CONF_EXPOSE_ALL_ENTITIES, False):
        return
    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(entity_entry.entity_id, disabled_by=None)


async def _async_populate_dynamic_flags(
    client: EngieBeApiClient,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Resolve the dynamic-tariff flag for every subentry in one fan-out call.

    Leaves ``is_dynamic_override`` at ``None`` on failure so the legacy
    ``len(items) == 0`` heuristic on the prices payload still drives detection.
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
        *(
            client.async_get_energy_contracts(ban, include_inactive=True)
            for _, ban in targets
        ),
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
