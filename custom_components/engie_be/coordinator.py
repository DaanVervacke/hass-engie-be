"""DataUpdateCoordinators for the ENGIE Belgium integration."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from ._happy_hour import happy_hour_flag_reason, is_enrolled_from_flags
from ._relations import (
    RELATIONS_BACKFILLABLE_KEYS,
    find_agreement_for_ban,
    subentry_title,
)
from .api import (
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
    EpexNotPublishedError,
    mask_identifier,
)
from .const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    EPEX_MWH_TO_KWH,
    EPEX_SLOT_DURATION_MINUTES,
    EPEX_TZ,
    KEY_IS_DYNAMIC,
    LOGGER,
)
from .data import EpexPayload, EpexSlot

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from .api import EngieBeApiClient
    from .data import EngieBeConfigEntry

# Brussels timezone is treated as a module-level constant: it never
# changes at runtime and instantiating ``ZoneInfo`` on every refresh
# is wasteful. ``zoneinfo`` is part of the stdlib (Python 3.9+) and
# pulls DST data from the host -- this matches HA core practice.
_BRUSSELS_TZ = ZoneInfo(EPEX_TZ)


class EngieBeDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Coordinator for one ENGIE business agreement (subentry).

    Polls supplier energy prices and capacity-tariff peaks for a single
    ``businessAgreementNumber``. EPEX day-ahead prices are
    agreement-agnostic and live in :class:`EngieBeEpexCoordinator` on
    the parent entry.
    """

    config_entry: EngieBeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EngieBeConfigEntry,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialise the per-subentry coordinator."""
        update_minutes = config_entry.options.get(
            CONF_UPDATE_INTERVAL,
            DEFAULT_UPDATE_INTERVAL_MINUTES,
        )
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} agreement {subentry.title}",
            update_interval=timedelta(minutes=update_minutes),
        )
        self.subentry = subentry
        self.business_agreement_number: str = subentry.data[
            CONF_BUSINESS_AGREEMENT_NUMBER
        ]
        self.last_successful_fetch: datetime | None = None
        # One-shot backfill: when the subentry was created without all
        # relations-derived display fields, we attempt to populate them
        # from the customer-account-relations endpoint on the first
        # successful refresh. The flag is cleared after a single attempt
        # regardless of outcome to avoid hammering the API on every poll
        # for an account that simply has no data.
        self._needs_relations_backfill: bool = any(
            not subentry.data.get(key) for key in RELATIONS_BACKFILLABLE_KEYS
        )

    @property
    def is_dynamic(self) -> bool:
        """
        Return True when this account is on a dynamic (EPEX-indexed) tariff.

        The authoritative source is the energy-contracts endpoint, which
        reports the per-EAN product code (``energyProduct``) directly.
        That value is populated into ``EngieBeSubentryData.is_dynamic_override``
        during setup and takes precedence here when set, so the answer
        is correct even for mixed-fuel accounts whose supplier-energy-
        prices payload would otherwise look fixed (gas item populates
        ``items[]`` so the legacy ``len(items) == 0`` heuristic returns
        False). The legacy heuristic still backs the property when the
        contracts call failed at setup time, so the integration degrades
        gracefully on a contracts-endpoint outage.
        """
        runtime = getattr(self.config_entry, "runtime_data", None)
        subentry_data = (
            runtime.subentry_data.get(self.subentry.subentry_id)
            if runtime is not None
            else None
        )
        override = (
            subentry_data.is_dynamic_override if subentry_data is not None else None
        )
        if override is not None:
            return override
        data = self.data
        return bool(isinstance(data, dict) and data.get(KEY_IS_DYNAMIC))

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch energy prices and capacity-tariff peaks for this account."""
        client = self.config_entry.runtime_data.client
        business_agreement_number = self.business_agreement_number

        try:
            data = await client.async_get_prices(business_agreement_number)
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

        # Legacy fallback for the dynamic-tariff flag: an empty
        # ``items`` list means the supplier-energy-prices endpoint had
        # no fixed monthly rates to expose, which historically signalled
        # a dynamic account. This is per-EAN (not per-account) so it
        # misfires for mixed-fuel households whose gas EAN populates
        # ``items[]``. The authoritative answer is the energy-contracts
        # endpoint, fetched once at setup and stored on
        # ``EngieBeSubentryData.is_dynamic_override`` (see ``__init__.py``);
        # the ``is_dynamic`` property prefers that override and only
        # falls back to this value when the contracts call failed.
        items = data.get("items") if isinstance(data, dict) else None
        is_dynamic = isinstance(items, list) and len(items) == 0
        data[KEY_IS_DYNAMIC] = is_dynamic

        # One-shot backfill of relations-derived display fields. Runs
        # only when the subentry is missing at least one such field.
        # Best-effort: failures are logged and swallowed so a transient
        # relations outage never blocks price updates.
        if self._needs_relations_backfill:
            await self._async_try_backfill_subentry(client)
            self._needs_relations_backfill = False

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
            business_agreement_number,
            today.year,
            today.month,
            previous_peaks_wrapper,
        )

        if peaks_wrapper is not None:
            data["peaks"] = peaks_wrapper
            self._record_peak_history(peaks_wrapper)

        # Probe the feature-flags endpoint to learn whether this BAN is
        # enrolled in Happy Hours. The endpoint is the authoritative
        # signal: ``/happy-hour-event`` returns ``{}`` both for
        # un-enrolled BANs and for enrolled BANs without a scheduled
        # window, so the event payload alone cannot distinguish the two.
        # Soft-fail on errors: an un-readable flag is treated as "no
        # signal change" so a transient outage never silently drops or
        # creates Happy Hour entities.
        previous_enrolled = self._read_cached_enrollment()
        new_enrolled = await self._async_fetch_enrollment(
            client,
            business_agreement_number,
            previous_enrolled=previous_enrolled,
        )

        # Only poll the Happy Hour event endpoint for enrolled BANs.
        # When un-enrolled, drop any stale wrapper so the entities (if
        # they exist from a previous enrolled run that has not been
        # reloaded yet) immediately report no scheduled window.
        if new_enrolled:
            previous_happy_hour_wrapper: dict[str, Any] | None = None
            if isinstance(self.data, dict):
                existing_hh = self.data.get("happy_hour")
                if isinstance(existing_hh, dict):
                    previous_happy_hour_wrapper = existing_hh

            happy_hour_wrapper = await self._async_fetch_happy_hour(
                client,
                business_agreement_number,
                previous_happy_hour_wrapper,
            )
            if happy_hour_wrapper is not None:
                data["happy_hour"] = happy_hour_wrapper
                self._record_happy_hour_history(happy_hour_wrapper)

        # Push the enrolment outcome onto subentry runtime data and
        # schedule a config-entry reload when the state flips. The first
        # observation (previous_enrolled is None) sets the cache without
        # scheduling a reload because platforms have not yet been set up
        # against it; subsequent flips reconcile entity presence with
        # the new enrolment state.
        self._async_apply_enrollment(
            previous_enrolled=previous_enrolled,
            new_enrolled=new_enrolled,
        )

        self.last_successful_fetch = dt_util.utcnow()
        return data

    def _read_cached_enrollment(self) -> bool | None:
        """Return the previously-observed Happy Hour enrolment, or ``None``."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None:
            return None
        subentry_data = runtime.subentry_data.get(self.subentry.subentry_id)
        if subentry_data is None:
            return None
        return subentry_data.is_happy_hour_enrolled

    async def _async_fetch_enrollment(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
        *,
        previous_enrolled: bool | None,
    ) -> bool:
        """
        Fetch and interpret the feature-flags response for this BAN.

        Returns ``True``/``False`` based on
        ``happy-hours-service-enabled.value``. Auth failures escalate;
        all other failures soft-fail to the previous cached state
        (``False`` when there is no prior observation) so a transient
        outage never silently flips entities in or out.
        """
        try:
            flags = await client.async_get_feature_flags(business_agreement_number)
        except EngieBeApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from exception
        except EngieBeApiClientError as exception:
            LOGGER.warning(
                "Failed to fetch feature flags for BAN %s, "
                "keeping last-known Happy Hour enrolment (%s): %s",
                mask_identifier(business_agreement_number),
                previous_enrolled,
                exception,
            )
            return bool(previous_enrolled)

        enrolled = is_enrolled_from_flags(flags)
        reason = happy_hour_flag_reason(flags)
        LOGGER.debug(
            "BAN %s: Happy Hour enrolment from feature flags is %s (reason=%s)",
            mask_identifier(business_agreement_number),
            enrolled,
            reason,
        )
        return enrolled

    @callback
    def _async_apply_enrollment(
        self,
        *,
        previous_enrolled: bool | None,
        new_enrolled: bool,
    ) -> None:
        """
        Persist the new enrolment value and schedule a reload on a flip.

        ``previous_enrolled`` is ``None`` on the very first refresh of a
        coordinator; in that case we just set the cache so ``__init__``
        and the platform setups can read it on subsequent passes,
        without scheduling a reload (platforms have not yet been set up
        against the old value, so there is nothing to reconcile).

        On a true flip (``True`` <-> ``False``) we mark the parent entry
        for reload and queue an ``async_reload`` call. The
        ``reload_pending`` flag on ``EngieBeData`` debounces the case
        where multiple subentries flip in the same refresh tick.
        """
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None:
            return
        subentry_data = runtime.subentry_data.get(self.subentry.subentry_id)
        if subentry_data is None:
            return

        subentry_data.is_happy_hour_enrolled = new_enrolled

        if previous_enrolled is None:
            LOGGER.debug(
                "BAN %s: initial Happy Hour enrolment observed as %s; "
                "platforms will register accordingly",
                mask_identifier(self.business_agreement_number),
                new_enrolled,
            )
            return
        if previous_enrolled == new_enrolled:
            return
        if runtime.reload_pending:
            return

        runtime.reload_pending = True
        LOGGER.info(
            "Happy Hour enrolment changed for BAN %s (%s -> %s); "
            "reloading config entry to reconcile entities",
            mask_identifier(self.business_agreement_number),
            previous_enrolled,
            new_enrolled,
        )
        # ``async_create_background_task`` (not ``async_create_task``) so the
        # reload survives the teardown of the very coordinator that scheduled
        # it. ``async_reload`` will cancel our own update task; a foreground
        # ``async_create_task`` would be cancelled with us, leaving the entry
        # in a half-reloaded state. The named background task is also visible
        # in HA developer tools for diagnostics. See CFG-2 in the pre-release
        # audit.
        self.hass.async_create_background_task(
            self.hass.config_entries.async_reload(self.config_entry.entry_id),
            name=(
                "engie_be_reload_on_happy_hour_enrolment_change_"
                f"{self.config_entry.entry_id}"
            ),
        )

    async def _async_fetch_happy_hour(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
        previous_wrapper: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        Fetch the upcoming Happy Hour event for this business agreement.

        Returns a wrapper ``{"data": <payload-or-None>}``: ``data`` is the
        raw API payload when one is returned (including the empty ``{}``
        case, which means "no event scheduled"), and ``None`` only when
        the API call failed and no prior wrapper is available.

        Auth failures escalate to reauth; all other failures keep the
        last-known wrapper so sensors don't blank on a transient outage.
        """
        try:
            payload = await client.async_get_happy_hour_event(
                business_agreement_number,
            )
        except EngieBeApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from exception
        except EngieBeApiClientError as exception:
            LOGGER.warning(
                "Failed to fetch happy-hour event, keeping last-known value: %s",
                exception,
            )
            return previous_wrapper

        wrapper = {"data": payload if isinstance(payload, dict) else None}
        ban_masked = mask_identifier(business_agreement_number)
        if isinstance(payload, dict):
            tomorrow = payload.get("tomorrow")
            if isinstance(tomorrow, dict):
                LOGGER.debug(
                    "BAN %s: Happy Hour payload reports window for tomorrow "
                    "(start=%s end=%s)",
                    ban_masked,
                    tomorrow.get("startTime"),
                    tomorrow.get("endTime"),
                )
            else:
                LOGGER.debug(
                    "BAN %s: Happy Hour payload empty (no window scheduled)",
                    ban_masked,
                )
        else:
            LOGGER.debug(
                "BAN %s: Happy Hour payload was not a dict; storing None",
                ban_masked,
            )
        return wrapper

    def _record_peak_history(self, peaks_wrapper: dict[str, Any]) -> None:
        """Persist the current month's peak window if it is not a fallback."""
        if peaks_wrapper.get("is_fallback"):
            return
        runtime = getattr(self.config_entry, "runtime_data", None)
        subentry_data = (
            runtime.subentry_data.get(self.subentry.subentry_id)
            if runtime is not None
            else None
        )
        store = (
            getattr(subentry_data, "peaks_store", None)
            if subentry_data is not None
            else None
        )
        if store is None:
            return
        payload = peaks_wrapper.get("data")
        if not isinstance(payload, dict):
            return
        monthly = payload.get("peakOfTheMonth")
        if not isinstance(monthly, dict):
            return
        start = monthly.get("start")
        end = monthly.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            return
        year = peaks_wrapper.get("year")
        month = peaks_wrapper.get("month")
        if not isinstance(year, int) or not isinstance(month, int):
            return
        store.upsert(
            year=year,
            month=month,
            start=start,
            end=end,
            peak_kw=monthly.get("peakKW"),
            peak_kwh=monthly.get("peakKWh"),
        )

    def _record_happy_hour_history(self, happy_hour_wrapper: dict[str, Any]) -> None:
        """
        Persist the upcoming Happy Hour window if one is scheduled.

        Idempotent: the same ``tomorrow`` payload may be observed many
        times between the moment ENGIE announces a window and the
        moment it expires, but the store dedups on ``start``.
        """
        ban = mask_identifier(self.business_agreement_number)
        payload = happy_hour_wrapper.get("data")
        if not isinstance(payload, dict):
            LOGGER.debug(
                "BAN %s: skipping Happy Hour history record, payload is not a dict",
                ban,
            )
            return
        tomorrow = payload.get("tomorrow")
        if not isinstance(tomorrow, dict):
            LOGGER.debug(
                "BAN %s: skipping Happy Hour history record, no 'tomorrow' window",
                ban,
            )
            return
        start = tomorrow.get("startTime")
        end = tomorrow.get("endTime")
        if not isinstance(start, str) or not isinstance(end, str):
            LOGGER.debug(
                "BAN %s: skipping Happy Hour history record, "
                "start/end not strings (start=%r end=%r)",
                ban,
                start,
                end,
            )
            return
        runtime = getattr(self.config_entry, "runtime_data", None)
        subentry_data = (
            runtime.subentry_data.get(self.subentry.subentry_id)
            if runtime is not None
            else None
        )
        store = (
            getattr(subentry_data, "happy_hours_store", None)
            if subentry_data is not None
            else None
        )
        if store is None:
            LOGGER.debug(
                "BAN %s: skipping Happy Hour history record, store not available",
                ban,
            )
            return
        changed = store.upsert(start=start, end=end)
        if changed:
            LOGGER.debug(
                "BAN %s: persisted Happy Hour window to history (start=%s end=%s)",
                ban,
                start,
                end,
            )
        else:
            LOGGER.debug(
                "BAN %s: Happy Hour window already in history (start=%s)",
                ban,
                start,
            )

    async def _async_try_backfill_subentry(
        self,
        client: EngieBeApiClient,
    ) -> None:
        """
        Best-effort fill of relations-derived fields on the subentry.

        Called once per HA run for subentries that were created without
        a complete ``relations`` payload. Only missing fields are
        written; existing values are preserved so a user who
        deliberately edited a subentry isn't overwritten by upstream
        data. Any error during the relations call is logged at warning
        level and swallowed; the next refresh proceeds normally without
        the missing fields.
        """
        try:
            relations = await client.async_get_customer_account_relations()
        except EngieBeApiClientError as exception:
            LOGGER.warning(
                "Failed to backfill subentry %s from relations endpoint: %s",
                self.subentry.subentry_id,
                exception,
            )
            return

        accounts_payload = relations if isinstance(relations, dict) else {}
        match = find_agreement_for_ban(
            accounts_payload,
            self.business_agreement_number,
        )
        if match is None:
            LOGGER.debug(
                "Relations response has no entry for BAN %s; "
                "leaving subentry %s untouched",
                mask_identifier(self.business_agreement_number),
                self.subentry.subentry_id,
            )
            return

        existing = dict(self.subentry.data)
        updated = dict(existing)
        for key in RELATIONS_BACKFILLABLE_KEYS:
            if not updated.get(key) and match.get(key):
                updated[key] = match[key]

        if updated == existing:
            return

        new_title = subentry_title(updated)
        old_title = self.subentry.title
        self.hass.config_entries.async_update_subentry(
            self.config_entry,
            self.subentry,
            data=updated,
            title=new_title,
        )
        LOGGER.debug(
            "Backfilled subentry %s with relations fields: %s",
            self.subentry.subentry_id,
            sorted(set(updated) - {k for k, v in existing.items() if v}),
        )

        if new_title != old_title:
            self._async_rename_subentry_device(new_title)

    @callback
    def _async_rename_subentry_device(self, new_title: str) -> None:
        """
        Refresh the customer-account device name after a backfill.

        ``DeviceInfo`` is only consulted by HA when an entity is added or
        re-added; updating ``subentry.title`` does not by itself rename
        the device. We look the device up by its stable subentry-scoped
        identifier and update its ``name`` field directly. ``name_by_user``
        is preserved by HA's update logic, so a user-customised name is
        never clobbered.
        """
        device_reg = dr.async_get(self.hass)
        device = device_reg.async_get_device(
            identifiers={(DOMAIN, self.subentry.subentry_id)},
        )
        if device is None:
            return
        if device.name == new_title:
            return
        device_reg.async_update_device(device.id, name=new_title)

    async def _async_fetch_peaks_with_fallback(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
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
                business_agreement_number,
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
                business_agreement_number,
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


class EngieBeEpexCoordinator(DataUpdateCoordinator[EpexPayload | None]):
    """
    Coordinator for EPEX day-ahead wholesale prices.

    EPEX prices are public, login-scoped at most (the integration uses the
    public endpoint), and identical for every customer of a given parent
    :class:`ConfigEntry`. They are therefore polled once per parent entry
    rather than once per subentry, regardless of how many customer
    accounts the user owns. The coordinator is created unconditionally;
    consumers gate entity creation on per-subentry ``is_dynamic`` so a
    user with only fixed-tariff accounts simply never sees EPEX entities.
    """

    config_entry: EngieBeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EngieBeConfigEntry,
    ) -> None:
        """Initialise the entry-level EPEX coordinator."""
        update_minutes = config_entry.options.get(
            CONF_UPDATE_INTERVAL,
            DEFAULT_UPDATE_INTERVAL_MINUTES,
        )
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} EPEX",
            update_interval=timedelta(minutes=update_minutes),
        )
        self._last_update_success_time: datetime | None = None

    @property
    def last_update_success_time(self) -> datetime | None:
        """Return the UTC timestamp of the last successful EPEX fetch."""
        return self._last_update_success_time

    async def _async_update_data(self) -> EpexPayload | None:
        """
        Fetch EPEX day-ahead prices covering today + tomorrow (Brussels).

        Returns the parsed payload, or the previous (last-known) payload
        when the endpoint is reachable but tomorrow's slate is not yet
        published (HTTP 404), or when a transient communication error
        occurs. Returns ``None`` only when no previous payload exists
        either; platforms must handle this by reporting unavailable.
        """
        client = self.config_entry.runtime_data.client
        previous = self.data if isinstance(self.data, EpexPayload) else None

        # Window: [today_brussels_00:00 .. day_after_tomorrow_brussels_00:00).
        # Two full Brussels-local days expressed as a half-open interval,
        # so we always cover today + tomorrow regardless of which side of
        # the daily 13:15 publication we're polling.
        now_brussels = dt_util.now(_BRUSSELS_TZ)
        start_local = datetime.combine(
            now_brussels.date(),
            time(0, 0),
            tzinfo=_BRUSSELS_TZ,
        )
        end_local = start_local + timedelta(days=2)

        try:
            raw = await client.async_get_epex_prices(start_local, end_local)
        except EpexNotPublishedError as exception:
            LOGGER.debug(
                "EPEX endpoint reports no prices yet for window %s..%s: %s",
                start_local.isoformat(),
                end_local.isoformat(),
                exception,
            )
            return previous
        except EngieBeApiClientError as exception:
            LOGGER.warning(
                "Failed to fetch EPEX prices, keeping last-known payload: %s",
                exception,
            )
            return previous

        try:
            parsed = _parse_epex_response(raw)
        except (KeyError, TypeError, ValueError) as exception:
            LOGGER.warning(
                "Failed to parse EPEX response, keeping last-known payload: %s",
                exception,
            )
            return previous
        self._last_update_success_time = dt_util.utcnow()
        return parsed


def _parse_epex_response(raw: Any) -> EpexPayload:
    """
    Parse a raw EPEX endpoint response into an :class:`EpexPayload`.

    Slots are sorted by start time (the endpoint already returns them
    chronologically, but we don't rely on it).  Any malformed slot
    entries (missing ``period``/``value``, unparseable timestamps) are
    dropped with a debug log so a single bad row doesn't void the whole
    response.
    """
    if not isinstance(raw, dict):
        msg = f"EPEX response must be a dict, got {type(raw).__name__}"
        raise TypeError(msg)

    publication_raw = raw.get("publicationTime")
    publication: datetime | None = None
    if isinstance(publication_raw, str):
        try:
            publication = datetime.fromisoformat(publication_raw)
        except ValueError:
            LOGGER.debug(
                "Ignoring unparseable EPEX publicationTime: %r",
                publication_raw,
            )

    market_date_raw = raw.get("marketDate")
    market_date = market_date_raw if isinstance(market_date_raw, str) else None

    series = raw.get("timeSeries", [])
    if not isinstance(series, list):
        msg = f"EPEX timeSeries must be a list, got {type(series).__name__}"
        raise TypeError(msg)

    slots: list[EpexSlot] = []
    duration = timedelta(minutes=EPEX_SLOT_DURATION_MINUTES)
    for entry in series:
        if not isinstance(entry, dict):
            continue
        period_raw = entry.get("period")
        value_raw = entry.get("value")
        if not isinstance(period_raw, str) or value_raw is None:
            continue
        try:
            start_dt = datetime.fromisoformat(period_raw)
            value = float(value_raw)
        except (TypeError, ValueError):
            LOGGER.debug("Skipping malformed EPEX slot: %r", entry)
            continue
        # Normalise to Brussels-local for downstream slicing; the slot
        # is the same instant either way, but Brussels-local makes the
        # date comparisons in the sensor layer trivial and DST-safe.
        start_dt = start_dt.astimezone(_BRUSSELS_TZ)
        slots.append(
            EpexSlot(
                start=start_dt,
                end=start_dt + duration,
                value_eur_per_kwh=value / EPEX_MWH_TO_KWH,
                duration_minutes=EPEX_SLOT_DURATION_MINUTES,
            )
        )

    slots.sort(key=lambda s: s.start)
    return EpexPayload(
        slots=tuple(slots),
        publication_time=publication,
        market_date=market_date,
    )
