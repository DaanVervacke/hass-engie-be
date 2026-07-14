"""DataUpdateCoordinators for the ENGIE Belgium integration."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from ._contracts import ean_with_delivery_point_suffix
from ._happy_hour import happy_hour_flag_reason, is_enrolled_from_flag
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
    BRUSSELS_TZ,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_EXPOSE_ALL_ENTITIES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    EPEX_DEFAULT_SLOT_DURATION_MINUTES,
    EPEX_MWH_TO_KWH,
    KEY_IS_DYNAMIC,
    LOGGER,
    EpexGranularity,
)
from .data import EpexPayload, EpexSlot

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from .api import EngieBeApiClient
    from .data import EngieBeConfigEntry

# Per-flag metadata for the two thin coordinator helpers.
# Maps the leaf field name on FeatureFlagState to (log_prefix, task_name_suffix).
# Strings must stay verbatim to keep log lines byte-identical with the
# six methods they replace.
_FEATURE_FLAG_METADATA: dict[str, tuple[str, str]] = {
    "happy_hour_enrolled": ("Happy Hours enrolment", "happy_hour_enrolment_change"),
    "solar": ("solar-surplus availability", "solar_surplus_change"),
    "tou_active": ("TOU-active state", "tou_change"),
}


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
        # Per-EAN solar-surplus outage tracking: transient errors soft-fail
        # to the last-known wrapper, so the coordinator's built-in
        # once-per-outage logging does not fire. Track it manually to
        # match the ``log-when-unavailable`` rule (warn on outage entry,
        # debug while it persists, info on recovery).
        self._solar_surplus_unavailable: set[str] = set()
        # TOU schedules outage tracking: same once-per-outage discipline.
        self._tou_unavailable: bool = False
        # Billing endpoint outage tracking: same once-per-outage discipline.
        self._billing_unavailable: bool = False

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

    async def _async_update_data(self) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
        """Fetch energy prices and capacity-tariff peaks for this account."""
        client = self.config_entry.runtime_data.client
        business_agreement_number = self.business_agreement_number
        expose_all = self.config_entry.options.get(CONF_EXPOSE_ALL_ENTITIES, False)

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
        if isinstance(data, dict):
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
        today = dt_util.now(BRUSSELS_TZ)

        previous_peaks_wrapper: dict[str, Any] | None = None
        if isinstance(self.data, dict):
            existing = self.data.get("peaks")
            if isinstance(existing, dict):
                previous_peaks_wrapper = existing

        # Probe the feature-flags endpoint to learn whether this BAN is
        # enrolled in Happy Hours. The endpoint is the authoritative
        # signal: ``/happy-hour-event`` returns ``{}`` both for
        # un-enrolled BANs and for enrolled BANs without a scheduled
        # window, so the event payload alone cannot distinguish the two.
        # Soft-fail on errors: an un-readable flag is treated as "no
        # signal change" so a transient outage never silently drops or
        # creates Happy Hours entities.
        previous_enrolled = self._read_cached_enrollment()
        previous_has_solar = self._read_cached_flag("solar")
        previous_is_tou_active = self._read_cached_flag("tou_active")

        # Fetch account-balance / billing data. The endpoint is per-BAN
        # with no feature flag; fetch unconditionally on every refresh.
        # Soft-fail to previous wrapper on transient errors so sensors
        # stay populated. Auth errors escalate to reauth.
        previous_billing_wrapper: dict[str, Any] | None = None
        if isinstance(self.data, dict):
            existing_billing = self.data.get("billing")
            if isinstance(existing_billing, dict):
                previous_billing_wrapper = existing_billing

        # These five calls have no data dependency on each other's result;
        # run them concurrently instead of five sequential round trips.
        # Each already soft-fails its own EngieBeApiClientError internally
        # (returning a fallback value) and re-raises
        # EngieBeApiClientAuthenticationError, so gather() without
        # return_exceptions=True preserves current abort-on-auth-failure
        # behaviour: an auth error propagates immediately and cancels the
        # remaining in-flight tasks.
        (
            peaks_wrapper,
            new_enrolled,
            solar_shown,
            tou_active,
            billing_wrapper,
        ) = await asyncio.gather(
            self._async_fetch_peaks_with_fallback(
                client,
                business_agreement_number,
                today.year,
                today.month,
                previous_peaks_wrapper,
            ),
            self._async_fetch_enrollment(
                client,
                business_agreement_number,
                previous_enrolled=previous_enrolled,
            ),
            self._async_fetch_solar_flag(client, business_agreement_number),
            self._async_fetch_tou_flag(client, business_agreement_number),
            self._async_fetch_billing(
                client,
                business_agreement_number,
                previous_billing_wrapper,
            ),
        )

        if expose_all:
            new_enrolled = True
            solar_shown = True
            tou_active = True

        if peaks_wrapper is not None:
            data["peaks"] = peaks_wrapper
            self._record_peak_history(peaks_wrapper)

        # Only poll the Happy Hours event endpoint for enrolled BANs.
        # When un-enrolled, drop any stale wrapper so the entities (if
        # they exist from a previous enrolled run that has not been
        # reloaded yet) immediately report no scheduled window.
        previous_happy_hour_wrapper: dict[str, Any] | None = None
        if isinstance(self.data, dict):
            existing_hh = self.data.get("happy_hour")
            if isinstance(existing_hh, dict):
                previous_happy_hour_wrapper = existing_hh

        # The Happy Hours month report is fetched for enrolled BANs so the
        # current-month summary sensors (consumption, eligible hours,
        # reward) have fresh data on every coordinator refresh. Soft-fail:
        # a transient API error keeps the last-known wrapper so existing
        # sensors stay populated. Only fetched when enrolled.
        previous_month_report_wrapper: dict[str, Any] | None = None
        if isinstance(self.data, dict):
            existing_mr = self.data.get("happy_hour_month_report")
            if isinstance(existing_mr, dict):
                previous_month_report_wrapper = existing_mr

        # Solar-surplus forecasts. Two gates apply: the Smart App feature
        # flag (``solar-surplus-shown-dashboard``) mirrors ENGIE's own
        # contract for whether this account qualifies for the feature, and
        # the per-EAN forecast payload carries ``level = NO_DATA``
        # everywhere for accounts without a solar installation. We honour
        # the flag first (skips the per-EAN fan-out entirely when off) and
        # fall back to the data-driven signal for accounts where the flag
        # is on but no forecast data is available yet.
        previous_solar_wrapper: dict[str, Any] | None = None
        if isinstance(self.data, dict):
            existing_solar = self.data.get("solar_surplus")
            if isinstance(existing_solar, dict):
                previous_solar_wrapper = existing_solar

        # TOU schedules. The flag gates the supplier-side TOU meaning but
        # the endpoint returns data regardless (the network schedule
        # always applies to digital-meter customers). Fetch unconditionally
        # and surface the flag separately.
        previous_tou_wrapper: dict[str, Any] | None = None
        if isinstance(self.data, dict):
            existing_tou = self.data.get("tou_schedules")
            if isinstance(existing_tou, dict):
                previous_tou_wrapper = existing_tou

        # These four calls have no data dependency on each other's result
        # (only on new_enrolled/solar_shown/tou_active from the first
        # gather); run them concurrently instead of four sequential round
        # trips. Gated-off fetches are replaced with a no-op coroutine so
        # the tuple unpacking stays positional. Each fetch already
        # soft-fails its own EngieBeApiClientError internally and
        # re-raises EngieBeApiClientAuthenticationError, so gather()
        # without return_exceptions=True preserves current
        # abort-on-auth-failure behaviour.
        async def _noop() -> None:
            return None

        (
            happy_hour_wrapper,
            month_report_wrapper,
            solar_wrapper,
            tou_wrapper,
        ) = await asyncio.gather(
            self._async_fetch_happy_hour(
                client,
                business_agreement_number,
                previous_happy_hour_wrapper,
            )
            if new_enrolled
            else _noop(),
            self._async_fetch_month_report(
                client,
                business_agreement_number,
                today.year,
                today.month,
                previous_month_report_wrapper,
            )
            if new_enrolled
            else _noop(),
            self._async_fetch_solar_surplus(
                client,
                business_agreement_number,
                previous_solar_wrapper,
            )
            if solar_shown
            else _noop(),
            self._async_fetch_tou_schedules(
                client,
                business_agreement_number,
                previous_tou_wrapper,
            )
            if tou_active
            else _noop(),
        )

        if new_enrolled and happy_hour_wrapper is not None:
            data["happy_hour"] = happy_hour_wrapper
            self._record_happy_hour_history(happy_hour_wrapper)

        if new_enrolled and month_report_wrapper is not None:
            data["happy_hour_month_report"] = month_report_wrapper

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

        if solar_shown and solar_wrapper is not None:
            data["solar_surplus"] = solar_wrapper
        new_has_solar = (
            _derive_has_solar(
                solar_wrapper if solar_wrapper is not None else previous_solar_wrapper,
            )
            if solar_shown
            else False
        )
        self._async_apply_flag("solar", previous=previous_has_solar, new=new_has_solar)

        if tou_active and tou_wrapper is not None:
            data["tou_schedules"] = tou_wrapper
        # Flag off → drop any stale wrapper so entities from a prior
        # enabled run report unavailable until the next reload. Mirrors
        # the solar-surplus behaviour when its own flag is off.
        self._async_apply_flag(
            "tou_active", previous=previous_is_tou_active, new=tou_active
        )

        if expose_all:
            LOGGER.debug(
                "BAN %s: expose_all_entities is ON, forcing flags: "
                "enrolled=%s, solar=%s, tou=%s",
                mask_identifier(business_agreement_number),
                new_enrolled,
                solar_shown,
                tou_active,
            )

        if billing_wrapper is not None:
            data["billing"] = billing_wrapper

        self.last_successful_fetch = dt_util.utcnow()
        return data

    def _read_cached_enrollment(self) -> bool | None:
        """Return the previously-observed Happy Hours enrolment, or ``None``."""
        return self._read_cached_flag("happy_hour_enrolled")

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
            flags = await client.async_get_happy_hours_service_enabled_flag(
                business_agreement_number
            )
        except EngieBeApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from exception
        except EngieBeApiClientError as exception:
            LOGGER.warning(
                "Failed to fetch feature flags for BAN %s, "
                "keeping last-known Happy Hours enrolment (%s): %s",
                mask_identifier(business_agreement_number),
                previous_enrolled,
                exception,
            )
            return bool(previous_enrolled)

        enrolled = is_enrolled_from_flag(flags)
        reason = happy_hour_flag_reason(flags)
        LOGGER.debug(
            "BAN %s: Happy Hours enrolment from feature flags is %s (reason=%s)",
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
        """Persist the new enrolment value and schedule a reload on a flip."""
        self._async_apply_flag(
            "happy_hour_enrolled",
            previous=previous_enrolled,
            new=new_enrolled,
        )

    async def _async_probe_boolean_flag(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
        *,
        api_method: str,
        log_prefix: str,
    ) -> bool:
        """
        Probe a boolean feature flag with the shared soft-fail discipline.

        Auth failures escalate via ``ConfigEntryAuthFailed``. Any other
        API error logs a warning at ``log_prefix`` and soft-fails to
        ``True`` (the "keep trying" side) - matching the fail-open
        discipline where a transient flag-endpoint outage should not
        strip entities from accounts that are legitimately enrolled.

        Returns the ``value`` field of the flag response coerced to bool.
        """
        try:
            flags = await getattr(client, api_method)(business_agreement_number)
        except EngieBeApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from exception
        except EngieBeApiClientError as exception:
            LOGGER.warning(
                "Failed to fetch %s feature flag for BAN %s, "
                "assuming enabled and continuing: %s",
                log_prefix,
                mask_identifier(business_agreement_number),
                exception,
            )
            return True
        value = flags.get("value") if isinstance(flags, dict) else None
        LOGGER.debug(
            "BAN %s: %s feature flag is %s",
            mask_identifier(business_agreement_number),
            log_prefix,
            value,
        )
        return bool(value)

    async def _async_fetch_solar_flag(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
    ) -> bool:
        """Probe the ``solar-surplus-shown-dashboard`` feature flag."""
        return await self._async_probe_boolean_flag(
            client,
            business_agreement_number,
            api_method="async_get_solar_surplus_shown_dashboard_flag",
            log_prefix="solar-surplus",
        )

    def _note_solar_unavailable(
        self,
        ean: str,
        ban_masked: str,
        exception: Exception,
    ) -> None:
        """Log a solar-surplus outage at most once per EAN per outage."""
        if ean in self._solar_surplus_unavailable:
            LOGGER.debug(
                "BAN %s EAN %s: solar-surplus still unavailable: %s",
                ban_masked,
                mask_identifier(ean),
                exception,
            )
            return
        LOGGER.warning(
            "Failed to fetch solar-surplus forecasts for BAN %s EAN %s, "
            "keeping last-known value: %s",
            ban_masked,
            mask_identifier(ean),
            exception,
        )
        self._solar_surplus_unavailable.add(ean)

    def _note_solar_recovered(self, ean: str, ban_masked: str) -> None:
        """Log recovery once after a solar-surplus outage cleared."""
        if ean not in self._solar_surplus_unavailable:
            return
        LOGGER.info(
            "BAN %s EAN %s: solar-surplus fetch recovered",
            ban_masked,
            mask_identifier(ean),
        )
        self._solar_surplus_unavailable.discard(ean)

    async def _async_fetch_solar_surplus(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
        previous_wrapper: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        Fetch solar-surplus forecasts for every electricity EAN.

        Returns a wrapper ``{"data": {ean: forecasts_list}, "fetched_at":
        <ISO-UTC>}``. ``forecasts_list`` is the raw ``forecasts`` array
        from the API (one entry per day, each with an hourly ``details``
        list). Missing / failed per-EAN calls carry their previous
        wrapper value forward.

        Auth failures escalate to reauth. All other errors soft-fail:
        the previous wrapper is preserved so entities don't blank on a
        transient outage.
        """
        runtime = getattr(self.config_entry, "runtime_data", None)
        subentry_data = (
            runtime.subentry_data.get(self.subentry.subentry_id)
            if runtime is not None
            else None
        )
        service_points = (
            subentry_data.service_points if subentry_data is not None else {}
        )
        electricity_eans = [
            ean for ean, division in service_points.items() if division == "ELECTRICITY"
        ]
        if not electricity_eans:
            return previous_wrapper

        ban_masked = mask_identifier(business_agreement_number)
        previous_per_ean = (
            previous_wrapper.get("data")
            if isinstance(previous_wrapper, dict)
            and isinstance(previous_wrapper.get("data"), dict)
            else {}
        )

        async def _fetch_one(ean: str) -> tuple[str, Any, bool]:
            try:
                payload = await client.async_get_solar_surplus_forecasts(
                    business_agreement_number,
                    # ENGIE delivery-point IDs observed in the wild are
                    # always ``{EAN}_ID1``. Multi-panel installations may
                    # expose ``_ID2``/``_ID3`` but no service-points
                    # endpoint currently surfaces them; extend this
                    # mapping when a real multi-ID sample appears.
                    ean_with_delivery_point_suffix(ean),
                )
            except EngieBeApiClientAuthenticationError:
                raise
            except EngieBeApiClientError as exception:
                self._note_solar_unavailable(ean, ban_masked, exception)
                return ean, previous_per_ean.get(ean), False
            forecasts = payload.get("forecasts") if isinstance(payload, dict) else None
            fresh = isinstance(forecasts, list)
            if fresh:
                self._note_solar_recovered(ean, ban_masked)
            return ean, forecasts if fresh else previous_per_ean.get(ean), fresh

        results = await asyncio.gather(
            *(_fetch_one(ean) for ean in electricity_eans),
            return_exceptions=True,
        )

        per_ean: dict[str, Any] = {}
        any_fresh = False
        for result in results:
            if isinstance(result, EngieBeApiClientAuthenticationError):
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="auth_failed",
                ) from result
            if isinstance(result, BaseException):
                LOGGER.debug(
                    "Unexpected solar-surplus fetch exception, "
                    "skipping this EAN this cycle: %s",
                    result,
                )
                continue
            ean, forecasts, fresh = result
            if forecasts is not None:
                per_ean[ean] = forecasts
            if fresh:
                any_fresh = True

        if not per_ean:
            return previous_wrapper
        if not any_fresh:
            # Every EAN carried its previous value forward; keep the
            # previous wrapper intact so ``fetched_at`` still reflects the
            # last time real data was seen.
            return previous_wrapper

        return {"data": per_ean, "fetched_at": dt_util.utcnow().isoformat()}

    @callback
    def _async_apply_flag_state(  # noqa: PLR0913
        self,
        *,
        field_name: str,
        previous: bool | None,
        new: bool,
        log_prefix: str,
        task_name_suffix: str,
        target: object | None = None,
    ) -> None:
        """
        Persist a boolean flag state and schedule a reload on a flip.

        Shared implementation of the first-observation, no-change, and
        flip branches used by Happy Hours enrolment, solar-surplus
        availability, and TOU activation. ``field_name`` is the
        attribute to mutate. When ``target`` is provided the attribute is
        written on that object instead of ``EngieBeSubentryData``
        directly (used by the nested ``feature_flags`` bundle).

        The ``reload_pending`` check MUST fire before the flag is set to
        ``True`` so two simultaneous flips in the same refresh tick do
        not both queue a reload.
        """
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None:
            return
        subentry_data = runtime.subentry_data.get(self.subentry.subentry_id)
        if subentry_data is None:
            return

        write_target = target if target is not None else subentry_data
        setattr(write_target, field_name, new)

        if previous is None:
            LOGGER.debug(
                "BAN %s: initial %s state observed as %s; "
                "platforms will register accordingly",
                mask_identifier(self.business_agreement_number),
                log_prefix,
                new,
            )
            return
        if previous == new:
            return
        if runtime.reload_pending:
            return

        runtime.reload_pending = True
        LOGGER.info(
            "%s changed for BAN %s (%s -> %s); "
            "reloading config entry to reconcile entities",
            log_prefix,
            mask_identifier(self.business_agreement_number),
            previous,
            new,
        )
        self.hass.async_create_background_task(
            self.hass.config_entries.async_reload(self.config_entry.entry_id),
            name=f"engie_be_reload_on_{task_name_suffix}_{self.config_entry.entry_id}",
        )

    def _subentry_data(self) -> object | None:
        """Return the runtime subentry data for this subentry, or ``None``."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None:
            return None
        return runtime.subentry_data.get(self.subentry.subentry_id)

    def _read_cached_flag(self, name: str) -> bool | None:
        """Return the previously-observed value for ``name``, or ``None``."""
        subentry_data = self._subentry_data()
        if subentry_data is None:
            return None
        return getattr(subentry_data.feature_flags, name)

    @callback
    def _async_apply_flag(
        self,
        name: str,
        *,
        previous: bool | None,
        new: bool | None,
    ) -> None:
        """Persist ``name`` on ``feature_flags`` and schedule a reload on a flip."""
        if new is None:
            return
        subentry_data = self._subentry_data()
        if subentry_data is None:
            return
        log_prefix, task_suffix = _FEATURE_FLAG_METADATA[name]
        self._async_apply_flag_state(
            field_name=name,
            previous=previous,
            new=new,
            log_prefix=log_prefix,
            task_name_suffix=task_suffix,
            target=subentry_data.feature_flags,
        )

    async def _async_fetch_tou_flag(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
    ) -> bool:
        """Probe the ``dgo-tou-is-active`` feature flag."""
        return await self._async_probe_boolean_flag(
            client,
            business_agreement_number,
            api_method="async_get_dgo_tou_is_active_flag",
            log_prefix="TOU",
        )

    async def _async_fetch_tou_schedules(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
        previous_wrapper: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        Fetch the TOU schedules for this business agreement.

        Returns a wrapper ``{"data": <payload>, "fetched_at": <ISO-UTC>}``
        on success, or ``previous_wrapper`` on transient failure. Auth
        errors escalate to reauth.
        """
        ban_masked = mask_identifier(business_agreement_number)
        try:
            payload = await client.async_get_tou_schedules(business_agreement_number)
        except EngieBeApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from exception
        except EngieBeApiClientError as exception:
            self._note_tou_unavailable(ban_masked, exception)
            return previous_wrapper
        self._note_tou_recovered(ban_masked)
        return {"data": payload, "fetched_at": dt_util.utcnow().isoformat()}

    def _note_tou_unavailable(
        self,
        ban_masked: str,
        exception: Exception,
    ) -> None:
        """Log a TOU schedules outage at most once per outage."""
        if self._tou_unavailable:
            LOGGER.debug(
                "BAN %s: TOU schedules still unavailable: %s",
                ban_masked,
                exception,
            )
            return
        LOGGER.warning(
            "Failed to fetch TOU schedules for BAN %s, keeping last-known value: %s",
            ban_masked,
            exception,
        )
        self._tou_unavailable = True

    def _note_tou_recovered(self, ban_masked: str) -> None:
        """Log recovery once after a TOU schedules outage cleared."""
        if not self._tou_unavailable:
            return
        LOGGER.info(
            "BAN %s: TOU schedules fetch recovered",
            ban_masked,
        )
        self._tou_unavailable = False

    async def _async_fetch_billing(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
        previous_wrapper: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        Fetch the account-balance payload for this business agreement.

        Returns a wrapper ``{"data": <payload>, "fetched_at": <ISO-UTC>}``
        on success. On transient failure the previous wrapper is returned
        unchanged (so sensors keep their last-known values). Auth errors
        escalate to reauth.
        """
        ban_masked = mask_identifier(business_agreement_number)
        try:
            payload = await client.async_get_account_balance(business_agreement_number)
        except EngieBeApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from exception
        except EngieBeApiClientError as exception:
            self._note_billing_unavailable(ban_masked, exception)
            return previous_wrapper
        self._note_billing_recovered(ban_masked)
        return {"data": payload, "fetched_at": dt_util.utcnow().isoformat()}

    def _note_billing_unavailable(
        self,
        ban_masked: str,
        exception: Exception,
    ) -> None:
        """Log a billing endpoint outage at most once per outage."""
        if self._billing_unavailable:
            LOGGER.debug(
                "BAN %s: billing endpoint still unavailable: %s",
                ban_masked,
                exception,
            )
            return
        LOGGER.warning(
            "Failed to fetch account balance for BAN %s, keeping last-known value: %s",
            ban_masked,
            exception,
        )
        self._billing_unavailable = True

    def _note_billing_recovered(self, ban_masked: str) -> None:
        """Log recovery once after a billing endpoint outage cleared."""
        if not self._billing_unavailable:
            return
        LOGGER.info(
            "BAN %s: account-balance fetch recovered",
            ban_masked,
        )
        self._billing_unavailable = False

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
            scheduled = {
                key: payload[key]
                for key in ("today", "tomorrow")
                if isinstance(payload.get(key), dict)
            }
            if scheduled:
                for key, window in scheduled.items():
                    LOGGER.debug(
                        "BAN %s: Happy Hours payload reports window for %s "
                        "(start=%s end=%s)",
                        ban_masked,
                        key,
                        window.get("startTime"),
                        window.get("endTime"),
                    )
            else:
                LOGGER.debug(
                    "BAN %s: Happy Hours payload empty (no window scheduled)",
                    ban_masked,
                )
        else:
            LOGGER.debug(
                "BAN %s: Happy Hours payload was not a dict; storing None",
                ban_masked,
            )
        return wrapper

    async def _async_fetch_month_report(
        self,
        client: EngieBeApiClient,
        business_agreement_number: str,
        year: int,
        month: int,
        previous_wrapper: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        Fetch the Happy Hours month report for this business agreement.

        Returns a wrapper ``{"data": <payload-or-None>, "year": year,
        "month": month, "is_fallback": bool}``.

        On a successful fetch, if the current month carries a ``happyHour``
        dict the wrapper is built from that (``is_fallback=False``).  If
        the current month's ``happyHour`` is absent (common in the first
        day or two of a new month before ENGIE has accrued any eligible
        hours), the ``history`` array in the same response is scanned for
        the most-recent entry that *does* carry a ``happyHour`` dict.  When
        found, that entry's data is wrapped with ``is_fallback=True`` and
        ``year``/``month`` taken from the history entry's ``yearMonth``
        field, so sensors show the previous month's numbers instead of going
        unknown.

        Note: ``data`` is the full API response on the success path but the
        minimal shape ``{"month": {"happyHour": ...}}`` on the fallback
        path. Both forms support sensor lookups walking
        ``("month", "happyHour", ...)``; readers must not depend on other
        top-level fields being present in fallback mode.

        Auth failures escalate to reauth; all other failures keep the
        last-known wrapper so sensors don't blank on a transient outage.
        """
        try:
            payload = await client.async_get_month_report(
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
                "Failed to fetch Happy Hours month report for BAN %s, "
                "keeping last-known value: %s",
                mask_identifier(business_agreement_number),
                exception,
            )
            if previous_wrapper is None:
                return None
            # Preserve the stored data as-is but mark it as stale.
            # Keep the ORIGINAL year/month so users can see which period
            # the numbers belong to rather than the current month header.
            return {
                **previous_wrapper,
                "is_fallback": True,
            }

        ban_masked = mask_identifier(business_agreement_number)

        if not isinstance(payload, dict):
            LOGGER.debug(
                "BAN %s: fetched Happy Hours month report for %d-%02d "
                "(non-dict response; data=None)",
                ban_masked,
                year,
                month,
            )
            return {
                "data": None,
                "year": year,
                "month": month,
                "is_fallback": False,
            }

        month_block = payload.get("month")
        current_happy_hour = (
            month_block.get("happyHour") if isinstance(month_block, dict) else None
        )

        if isinstance(current_happy_hour, dict):
            LOGGER.debug(
                "BAN %s: fetched Happy Hours month report for %d-%02d",
                ban_masked,
                year,
                month,
            )
            return {
                "data": payload,
                "year": year,
                "month": month,
                "is_fallback": False,
            }

        # Current month has no happyHour data yet (typical early in a new
        # month).  Scan history for the most-recent entry with a happyHour
        # block and use it as a fallback so users still see meaningful values.
        history = payload.get("history")
        fallback_wrapper = _find_history_fallback(history, ban_masked)
        if fallback_wrapper is not None:
            LOGGER.debug(
                "BAN %s: current month %d-%02d has no Happy Hours data yet; "
                "using historical fallback %d-%02d",
                ban_masked,
                year,
                month,
                fallback_wrapper["year"],
                fallback_wrapper["month"],
            )
            return fallback_wrapper

        # No history with happyHour data either - first-ever month or
        # empty history.  Return None so the key stays absent from
        # coordinator.data and sensors report unknown.
        LOGGER.debug(
            "BAN %s: no Happy Hours data in current month %d-%02d "
            "or history; returning no wrapper",
            ban_masked,
            year,
            month,
        )
        return None

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
        Persist every scheduled Happy Hours window if one is announced.

        ENGIE publishes the upcoming window under a ``tomorrow`` key the
        day before and re-publishes the same window under a ``today`` key
        once midnight passes; both keys are recorded so a window seen only
        after a post-midnight restart still reaches the history store.

        Idempotent: the same window may be observed many times (and under
        both keys) between announcement and expiry, but the store dedups
        on ``start``.
        """
        ban = mask_identifier(self.business_agreement_number)
        payload = happy_hour_wrapper.get("data")
        if not isinstance(payload, dict):
            LOGGER.debug(
                "BAN %s: skipping Happy Hours history record, payload is not a dict",
                ban,
            )
            return
        windows = [
            (key, payload[key])
            for key in ("today", "tomorrow")
            if isinstance(payload.get(key), dict)
        ]
        if not windows:
            LOGGER.debug(
                "BAN %s: skipping Happy Hours history record, no scheduled window",
                ban,
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
                "BAN %s: skipping Happy Hours history record, store not available",
                ban,
            )
            return
        for key, window in windows:
            start = window.get("startTime")
            end = window.get("endTime")
            if not isinstance(start, str) or not isinstance(end, str):
                LOGGER.debug(
                    "BAN %s: skipping Happy Hour %s window, "
                    "start/end not strings (start=%r end=%r)",
                    ban,
                    key,
                    start,
                    end,
                )
                continue
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


def _find_history_fallback(
    history: Any,
    ban_masked: str,
) -> dict[str, Any] | None:
    """
    Scan a month-report ``history`` array for the most-recent happyHour entry.

    Returns a ready-to-store wrapper
    ``{"data": ..., "year": int, "month": int, "is_fallback": True}``
    where ``data`` is shaped like a minimal month-report response
    (``{"month": {"happyHour": ...}}``) so sensor paths that walk
    ``("month", "happyHour", ...)`` still resolve correctly.

    Returns ``None`` when *history* is not a list, is empty, or contains no
    entry with a valid ``yearMonth`` string and a dict ``happyHour`` block.

    Entries are compared by their ``yearMonth`` string (``"YYYY-MM"`` format)
    using ordinary lexicographic ordering, which is correct for ISO year-month
    strings.  The entry with the lexicographically greatest *parseable*
    ``yearMonth`` that carries a ``happyHour`` dict is selected.  Entries
    with malformed ``yearMonth`` strings are skipped with a debug log.
    """
    if not isinstance(history, list):
        return None

    best_year_month: str | None = None
    best_year: int | None = None
    best_month: int | None = None
    best_happy_hour: dict[str, Any] | None = None

    for entry in history:
        if not isinstance(entry, dict):
            continue
        year_month_raw = entry.get("yearMonth")
        if not isinstance(year_month_raw, str):
            continue
        happy_hour = entry.get("happyHour")
        if not isinstance(happy_hour, dict):
            continue
        # Parse yearMonth here so unparseable entries are dropped in-loop
        # rather than discovered only after selection.
        try:
            fb_year_str, fb_month_str = year_month_raw.split("-", 1)
            fb_year = int(fb_year_str)
            fb_month = int(fb_month_str)
        except (ValueError, AttributeError):
            LOGGER.debug(
                "BAN %s: skipping history entry with unparseable yearMonth %r",
                ban_masked,
                year_month_raw,
            )
            continue
        if best_year_month is None or year_month_raw > best_year_month:
            best_year_month = year_month_raw
            best_year = fb_year
            best_month = fb_month
            best_happy_hour = happy_hour

    if best_year is None or best_month is None or best_happy_hour is None:
        return None

    return {
        "data": {"month": {"happyHour": best_happy_hour}},
        "year": best_year,
        "month": best_month,
        "is_fallback": True,
    }


def _derive_has_solar(wrapper: dict[str, Any] | None) -> bool | None:
    """
    Infer whether the customer has a solar installation from a wrapper.

    Returns ``True`` when any hourly slot across any EAN and any day
    carries a level other than ``NO_DATA``, ``False`` when the wrapper
    is present but every slot is ``NO_DATA`` (the shape ENGIE returns
    for customers without solar), and ``None`` when no wrapper is
    available so callers know to preserve the last-known value.
    """
    if not isinstance(wrapper, dict):
        return None
    per_ean = wrapper.get("data")
    if not isinstance(per_ean, dict):
        return None
    for forecasts in per_ean.values():
        if not isinstance(forecasts, list):
            continue
        for day in forecasts:
            if not isinstance(day, dict):
                continue
            details = day.get("details")
            if not isinstance(details, list):
                continue
            for slot in details:
                if not isinstance(slot, dict):
                    continue
                level = slot.get("level")
                if isinstance(level, str) and level.upper() != "NO_DATA":
                    return True
    return False


class _EngieBeEpexCoordinatorBase(DataUpdateCoordinator[EpexPayload | None]):
    """
    Common base class for EPEX day-ahead coordinators.

    Provides shared functionality for fetching and parsing EPEX market data.
    Subclasses implement the specific granularity (hourly or quarter-hourly).
    """

    config_entry: EngieBeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EngieBeConfigEntry,
        coordinator_name: str,
    ) -> None:
        """Initialise the EPEX coordinator with shared configuration."""
        update_minutes = config_entry.options.get(
            CONF_UPDATE_INTERVAL,
            DEFAULT_UPDATE_INTERVAL_MINUTES,
        )
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {coordinator_name}",
            update_interval=timedelta(minutes=update_minutes),
        )
        self._last_update_success_time: datetime | None = None
        self._unavailable_logged = False

    @property
    def last_update_success_time(self) -> datetime | None:
        """Return the UTC timestamp of the last successful EPEX fetch."""
        return self._last_update_success_time

    def _note_unavailable(self, message: str, exception: Exception) -> None:
        """
        Log an EPEX fetch/parse failure at most once per outage.

        The EPEX coordinator intentionally keeps serving the last-known
        payload instead of raising :class:`UpdateFailed`, so it cannot rely
        on the coordinator's built-in once-per-outage logging. This mirrors
        the manual ``_unavailable_logged`` pattern from the quality-scale
        ``log-when-unavailable`` rule: warn on the transition into the
        failure state, then stay quiet (debug) for as long as it persists.
        """
        if self._unavailable_logged:
            LOGGER.debug(message, exception)
        else:
            LOGGER.warning(message, exception)
            self._unavailable_logged = True

    async def _async_update_data(self) -> EpexPayload | None:
        """
        Fetch EPEX day-ahead prices.

        Returns the parsed payload, or the previous (last-known) payload
        when the endpoint is reachable but tomorrow's slate is not yet
        published (HTTP 404), or when a transient communication error
        occurs. Returns ``None`` only when no previous payload exists
        either; platforms must handle this by reporting unavailable.
        """
        msg = "Subclasses must implement _async_update_data"
        raise NotImplementedError(msg)


class EngieBeEpexCoordinator(_EngieBeEpexCoordinatorBase):
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

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EngieBeConfigEntry,
    ) -> None:
        """Initialise the entry-level EPEX coordinator."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            coordinator_name="EPEX",
        )

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
        now_brussels = dt_util.now(BRUSSELS_TZ)
        start_local = datetime.combine(
            now_brussels.date(),
            time(0, 0),
            tzinfo=BRUSSELS_TZ,
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
            self._note_unavailable(
                "Failed to fetch EPEX prices, keeping last-known payload: %s",
                exception,
            )
            return previous

        try:
            parsed = _parse_epex_response(
                raw, slot_duration_minutes=EpexGranularity.HOURLY.value
            )
        except (KeyError, TypeError, ValueError) as exception:
            self._note_unavailable(
                "Failed to parse EPEX response, keeping last-known payload: %s",
                exception,
            )
            return previous
        self._last_update_success_time = dt_util.utcnow()
        if self._unavailable_logged:
            LOGGER.info("EPEX prices fetch recovered; resuming fresh updates")
            self._unavailable_logged = False
        return parsed


class EngieBeEpexQuarterHourCoordinator(_EngieBeEpexCoordinatorBase):
    """
    Coordinator for EPEX day-ahead wholesale prices with quarter-hourly granularity.

    EPEX prices are public, login-scoped at most (the integration uses the
    public endpoint), and identical for every customer of a given parent
    :class:`ConfigEntry`. They are therefore polled once per parent entry
    rather than once per subentry, regardless of how many customer
    accounts the user owns. The coordinator is created unconditionally;
    consumers gate entity creation on per-subentry ``is_dynamic`` so a
    user with only fixed-tariff accounts simply never sees EPEX entities.

    This coordinator fetches 15-minute slots rather than
    hourly slots. The granularity is specified via the
    ``granularity="QUARTER_HOURLY"`` parameter when calling the API, and the
    parsed payload carries a ``slot_duration`` of 15 minutes to distinguish
    it from hourly data.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EngieBeConfigEntry,
    ) -> None:
        """Initialise the entry-level EPEX quarter-hourly coordinator."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            coordinator_name="EPEX QH",
        )

    async def _async_update_data(self) -> EpexPayload | None:
        """
        Fetch EPEX day-ahead prices with quarter-hourly granularity.

        Covering today + tomorrow (Brussels).

        Returns the parsed payload, or the previous (last-known) payload
        when the endpoint is reachable but tomorrow's slate is not yet
        published (HTTP 404), or when a transient communication error
        occurs. Returns ``None`` only when no previous payload exists
        either; platforms must handle this by reporting unavailable.
        """
        client = self.config_entry.runtime_data.client
        previous = self.data if isinstance(self.data, EpexPayload) else None

        # Window: [today_brussels_00:00 .. day_after_tomorrow_brussels_00:00).
        now_brussels = dt_util.now(BRUSSELS_TZ)
        start_local = datetime.combine(
            now_brussels.date(),
            time(0, 0),
            tzinfo=BRUSSELS_TZ,
        )
        end_local = start_local + timedelta(days=2)

        try:
            raw = await client.async_get_epex_prices(
                start_local, end_local, granularity="QUARTER_HOURLY"
            )
        except EpexNotPublishedError as exception:
            LOGGER.debug(
                "EPEX QH endpoint reports no prices yet for window %s..%s: %s",
                start_local.isoformat(),
                end_local.isoformat(),
                exception,
            )
            return previous
        except EngieBeApiClientError as exception:
            self._note_unavailable(
                "Failed to fetch EPEX QH prices, keeping last-known payload: %s",
                exception,
            )
            return previous

        try:
            parsed = _parse_epex_response(
                raw, slot_duration_minutes=EpexGranularity.QUARTER_HOURLY.value
            )
        except (KeyError, TypeError, ValueError) as exception:
            self._note_unavailable(
                "Failed to parse EPEX QH response, keeping last-known payload: %s",
                exception,
            )
            return previous
        self._last_update_success_time = dt_util.utcnow()
        if self._unavailable_logged:
            LOGGER.info("EPEX QH prices fetch recovered; resuming fresh updates")
            self._unavailable_logged = False
        return parsed


def _parse_epex_response(
    raw: Any, slot_duration_minutes: int = EPEX_DEFAULT_SLOT_DURATION_MINUTES
) -> EpexPayload:
    """
    Parse a raw EPEX endpoint response into an :class:`EpexPayload`.

    Slots are sorted by start time (the endpoint already returns them
    chronologically, but we don't rely on it).  Any malformed slot
    entries (missing ``period``/``value``, unparseable timestamps) are
    dropped with a debug log so a single bad row doesn't void the whole
    response.

    Args:
        raw: The raw API response dictionary.
        slot_duration_minutes: Duration of each slot in minutes.
            Use EpexGranularity.HOURLY.value (60) or
            EpexGranularity.QUARTER_HOURLY.value (15).

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
    slot_duration = timedelta(minutes=slot_duration_minutes)
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
        start_dt = start_dt.astimezone(BRUSSELS_TZ)
        slots.append(
            EpexSlot(
                start=start_dt,
                end=start_dt + slot_duration,
                value_eur_per_kwh=value / EPEX_MWH_TO_KWH,
            )
        )

    slots.sort(key=lambda s: s.start)
    return EpexPayload(
        slots=tuple(slots),
        publication_time=publication,
        market_date=market_date,
        slot_duration=slot_duration,
    )
