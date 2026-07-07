"""Custom integration to integrate ENGIE Belgium with Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID, Platform
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from ._contracts import is_account_dynamic
from ._statistics import (
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
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    ENERGY_TYPE_OPTIONS,
    LOGGER,
    SERVICE_CLEAR_IMPORT_HISTORY,
    SERVICE_IMPORT_HISTORY,
    SIGNAL_AUTHENTICATION_STATE_CHANGED,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
    TOKEN_REFRESH_INTERVAL_SECONDS,
)
from .coordinator import EngieBeDataUpdateCoordinator, EngieBeEpexCoordinator
from .data import EngieBeData, EngieBeSubentryData
from .diagnostics import _hash_ean
from .store import EngieBeHappyHoursStore, EngieBePeaksStore

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

    from .data import EngieBeConfigEntry

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
]


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> bool:
    """
    Refuse to migrate config entries from before v0.9.0.

    v0.9.0 is a breaking schema change: the v1->v2->v3->v4 migration chain
    was removed to drop ~3000 LOC of one-shot upgrade code that had to
    survive long-tail upgrade paths. Users on any pre-v0.9.0 install
    must remove the integration from Home Assistant and re-add it
    through the UI; that re-add walks the current config flow and
    produces a fresh v5 entry. Returning ``False`` here causes HA to
    flag the entry as ``setup_error``; alongside that, we raise a
    translated, non-fixable Repairs issue so the user sees an
    actionable card in Settings -> Repairs.
    """
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

    # Register the update listener BEFORE any step that can raise
    # ``ConfigEntryAuthFailed`` AND without wrapping in ``async_on_unload``.
    # When ``async_setup_entry`` raises, HA invokes every ``async_on_unload``
    # callback (see ``config_entries.py`` ``_async_process_on_unload`` in the
    # setup-failure finally-branch), which would immediately unregister the
    # listener, so reauth completion (via ``async_update_and_abort``) would
    # fire no listener and no reload would happen. Registering directly on
    # ``entry.update_listeners`` survives the failed setup. The list is
    # created once at entry construction and never reset, so a membership
    # check keeps this idempotent across setup retries.
    # ponytail: relying on the public ``update_listeners`` field is
    # intentional; the returned unlisten callable is discarded because the
    # listener must outlive individual setup attempts.
    if async_reload_entry not in entry.update_listeners:
        entry.add_update_listener(async_reload_entry)

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
    _set_authenticated(hass, entry, authenticated=True)

    # Recurring token refresh (one timer per parent entry, not per
    # subentry: tokens are login-scoped, not account-scoped).
    async def _refresh_token_callback(_now: object) -> None:
        """Refresh the access token periodically."""
        try:
            new_access, new_refresh = await client.async_refresh_token()
        except EngieBeApiClientAuthenticationError:
            _set_authenticated(hass, entry, authenticated=False)
            LOGGER.warning(
                "Scheduled token refresh rejected by ENGIE; starting reauth flow"
            )
            # Cancel the timer before starting reauth so it does not keep
            # firing 403s every 60s until the user completes the reauth
            # flow and the entry reloads. The on_unload path remains armed
            # as belt-and-braces; calling the cancel callable twice is safe
            # because async_track_time_interval returns an idempotent remove
            # listener closure.
            runtime = entry.runtime_data
            if runtime.cancel_token_refresh is not None:
                runtime.cancel_token_refresh()
                runtime.cancel_token_refresh = None
            entry.async_start_reauth(hass)
            return
        except EngieBeApiClientError as err:
            _set_authenticated(hass, entry, authenticated=False)
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
        _set_authenticated(hass, entry, authenticated=True)
        LOGGER.debug("Token refreshed successfully")

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
        happy_hours_store = await _async_init_happy_hours_store(
            hass, subentry.subentry_id
        )
        entry.runtime_data.subentry_data[subentry.subentry_id] = EngieBeSubentryData(
            coordinator=coordinator,
            peaks_store=peaks_store,
            happy_hours_store=happy_hours_store,
        )

    # Refresh EPEX once at startup alongside the per-subentry data;
    # EPEX is shared across subentries so this is one fetch total.
    #
    # ``return_exceptions=True`` so a failure in one coordinator does not
    # cancel siblings mid-flight, leaking in-flight aiohttp requests and
    # committing partial state (peaks history upsert, enrolment cache)
    # for some subentries but not others. After all tasks settle we
    # re-raise the most-actionable exception:
    #
    #   1. ``ConfigEntryAuthFailed`` -> HA triggers reauth flow
    #   2. ``ConfigEntryNotReady``   -> HA retries setup later
    #   3. anything else             -> first one wins (propagates)
    #
    # See ``.opencode/audit-v0.10.0b1-prerelease.md`` CFG-1.
    refresh_calls = [epex_coordinator.async_config_entry_first_refresh()]
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

    _async_register_services(hass)

    # Recurring token refresh registered AFTER every step that can raise
    # ``ConfigEntryNotReady`` (initial token refresh at the top of this
    # function, plus per-subentry ``async_config_entry_first_refresh``
    # which wraps ``UpdateFailed`` from ``coordinator.py:_async_update_data``
    # into ``ConfigEntryNotReady``). Registering the timer earlier would leak
    # it on a half-set-up entry: if a later setup step raises, the timer
    # keeps firing, rotating refresh tokens that never get persisted (because
    # the ``_persist_tokens`` write inside the callback targets the same
    # half-set-up entry), and the next real setup attempt fails reauth.
    # See ``.opencode/audit-v0.10.0b1-prerelease.md`` Blocker B1a.
    # Note: the update listener (registered above, before the first await)
    # is intentionally placed earlier; it is safe on a half-set-up entry.
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
    """
    Allow removal of stale devices from the device registry.

    Each device in this integration is either the login device (one per
    parent :class:`ConfigEntry`) or a customer-account device (one per
    ``ConfigSubentry``).  When a subentry is deleted the device is
    normally cleaned up automatically by HA (all entities carry
    ``config_subentry_id``, so HA removes the device once it has no
    entities).  This function handles the edge case where a device
    lingers with no corresponding live subentry, for example after a
    failed teardown or a manual store edit.

    A device is removable when no currently-active business-agreement
    subentry's ``subentry_id`` matches any of the device's identifiers.
    The login device (``login_{entry_id}``) has no matching subentry and
    is therefore also considered removable if the user requests it; HA
    will recreate it on the next successful setup.
    """
    active_subentry_ids: set[str] = {
        sub.subentry_id
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
    }
    return not any(
        (DOMAIN, subentry_id) in device_entry.identifiers
        for subentry_id in active_subentry_ids
    )


async def async_reload_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Reload on options change, business-agreement subentry add/remove, or reauth.

    Token rotation also fires this listener (it writes to ``entry.data``
    via ``_persist_tokens``). During rotation the live client updates its
    in-memory ``refresh_token`` *before* ``_persist_tokens`` is called, so
    ``entry.data[CONF_REFRESH_TOKEN]`` and ``runtime.client.refresh_token``
    are equal when the listener runs, so no reload happens.

    Reauthentication writes new tokens to ``entry.data`` externally (via
    ``async_update_and_abort`` in the config flow) without touching the
    live client object. When the listener fires after a reauth, the stored
    refresh token differs from the live client's in-memory token. That
    mismatch is the signal that an external write occurred and a reload is
    needed to wire the new credentials into the running client.

    A multi-pick subentry add writes N subentries via N separate
    ``async_add_subentry`` calls, each scheduling this listener. The
    subentry picker sets ``runtime_data.pending_subentry_target`` to the
    final expected set of business-agreement numbers (BANs) so intermediate
    listener runs (whose current BAN set does not yet cover the target)
    suppress their reload; the run that first observes the full target
    clears the gate and reloads once.
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
        # A multi-add is in progress. Suppress until the full target BAN set
        # is present, then reload exactly once. ``>=`` (superset) rather than
        # strict equality so an unrelated concurrent removal cannot wedge the
        # gate open forever; reaching or passing the target clears it.
        if _business_agreement_numbers(entry) >= target:
            runtime.pending_subentry_target = None
            await hass.config_entries.async_reload(entry.entry_id)
        return

    if options_changed or subentries_changed or tokens_externally_updated:
        await hass.config_entries.async_reload(entry.entry_id)


def _business_agreement_numbers(entry: EngieBeConfigEntry) -> set[str]:
    """
    Return the set of BANs currently attached as business-agreement subentries.

    A subentry's BAN is its ``unique_id`` (set by the picker), falling back
    to the stored ``CONF_BUSINESS_AGREEMENT_NUMBER`` when unset. Used by
    the ``pending_subentry_target`` reload gate, which keys on BANs rather
    than subentry ids because the first pick's ``subentry_id`` is generated
    by the framework finish path and is not predictable up front.
    """
    bans: set[str] = set()
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_TYPE_BUSINESS_AGREEMENT:
            continue
        ban = sub.unique_id or (sub.data or {}).get(CONF_BUSINESS_AGREEMENT_NUMBER)
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
        vol.Optional(ATTR_INCLUDE_COSTS, default=False): cv.boolean,
    },
)


def _resolve_targets(
    hass: HomeAssistant,
    device_ids: list[str],
    service_name: str,
) -> list[tuple[EngieBeConfigEntry, ConfigSubentry]]:
    """
    Resolve service ``device_id`` targets to (entry, subentry) pairs.

    Skips (with a warning) any device that is not a business-agreement
    device or whose owning entry is not currently loaded. Setup order
    guarantees that any entry returned here has ``runtime_data.client``
    populated: ``_async_register_services`` runs only after
    ``async_setup_entry`` has forwarded platforms, which itself runs
    after the parent ``EngieBeData`` (holding the client) is assigned to
    ``entry.runtime_data``. Callers can dereference the client without a
    further None check.
    """
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
            # Guard against the narrow reload window where ``runtime_data``
            # can be transiently unset between the old teardown and the new
            # setup. Callers dereference ``.client`` right after, so a stale
            # entry would AttributeError. Raise a translated
            # ``ServiceValidationError`` so the user sees an actionable
            # message and can retry once the reload settles.
            runtime = getattr(entry, "runtime_data", None)
            if runtime is None or getattr(runtime, "client", None) is None:
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


def _async_register_services(hass: HomeAssistant) -> None:
    """
    Register domain-level services once per HA startup.

    Services outlive individual config entries: multiple entries share
    the same registration and the handler routes to the entry that owns
    the targeted device. Guarded so a second entry setup does not raise
    ``ServiceRegistrationError``.
    """
    if hass.services.has_service(DOMAIN, SERVICE_IMPORT_HISTORY):
        return

    async def _handle_import_history(call: object) -> None:
        # ``call`` is ``ServiceCall``; typed loosely to keep the import
        # surface small for this file.
        device_ids: list[str] = call.data.get(ATTR_DEVICE_ID) or []  # type: ignore[attr-defined]
        if not device_ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="service_no_target_device",
            )
        raw_energy_types = call.data.get(ATTR_ENERGY_TYPE)  # type: ignore[attr-defined]
        if raw_energy_types is not None and not raw_energy_types:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="service_no_energy_type_selected",
            )
        include_costs: bool = call.data.get(ATTR_INCLUDE_COSTS, False)  # type: ignore[attr-defined]
        streams = streams_for_energy_types(
            raw_energy_types,
            include_costs=include_costs,
        )
        start_date = call.data.get(ATTR_START_DATE)  # type: ignore[attr-defined]
        end_date = call.data.get(ATTR_END_DATE)  # type: ignore[attr-defined]
        LOGGER.debug(
            "import_history called: device_ids=%s energy_type=%s"
            " include_costs=%s start=%s end=%s",
            device_ids,
            raw_energy_types,
            include_costs,
            start_date,
            end_date,
        )
        for entry, subentry in _resolve_targets(
            hass, device_ids, "engie_be.import_history"
        ):
            ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
            LOGGER.debug(
                "import_history: dispatching to BAN ***%s title=%r",
                ban[-4:] if ban else "????",
                subentry.title,
            )
            # User-facing end_date is inclusive; the orchestrator (and the
            # underlying ENGIE endpoint) treat it as exclusive. Bump by one
            # day so picking 2026-04-15 imports through the 15th.
            api_end_date = end_date + timedelta(days=1) if end_date else None
            await async_import_usage_history(
                hass,
                entry.runtime_data.client,
                subentry,
                start_date=start_date,
                end_date=api_end_date,
                streams=streams,
            )

    async def _handle_clear_import_history(call: object) -> None:
        device_ids: list[str] = call.data.get(ATTR_DEVICE_ID) or []  # type: ignore[attr-defined]
        if not device_ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="service_no_target_device",
            )
        raw_energy_types = call.data.get(ATTR_ENERGY_TYPE)  # type: ignore[attr-defined]
        if raw_energy_types is not None and not raw_energy_types:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="service_no_energy_type_selected",
            )
        include_costs: bool = call.data.get(ATTR_INCLUDE_COSTS, False)  # type: ignore[attr-defined]
        streams = streams_for_energy_types(
            raw_energy_types,
            include_costs=include_costs,
        )
        LOGGER.debug(
            "clear_import_history called: device_ids=%s"
            " energy_type=%s include_costs=%s",
            device_ids,
            raw_energy_types,
            include_costs,
        )
        for _entry, subentry in _resolve_targets(
            hass, device_ids, "engie_be.clear_import_history"
        ):
            ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
            if ban:
                LOGGER.debug(
                    "clear_import_history: dispatching to BAN ***%s title=%r",
                    ban[-4:],
                    subentry.title,
                )
                await async_clear_usage_history(hass, ban, streams=streams)

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
