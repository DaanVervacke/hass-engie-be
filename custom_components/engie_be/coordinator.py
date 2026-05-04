"""DataUpdateCoordinator for the ENGIE Belgium integration."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import (
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
    EpexNotPublishedError,
)
from .const import (
    CONF_CUSTOMER_NUMBER,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    EPEX_MWH_TO_KWH,
    EPEX_SLOT_DURATION_MINUTES,
    EPEX_TZ,
    KEY_EPEX,
    KEY_IS_DYNAMIC,
    LOGGER,
)
from .data import EpexPayload, EpexSlot

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import EngieBeConfigEntry

# Brussels timezone is treated as a module-level constant: it never
# changes at runtime and instantiating ``ZoneInfo`` on every refresh
# is wasteful. ``zoneinfo`` is part of the stdlib (Python 3.9+) and
# pulls DST data from the host -- this matches HA core practice.
_BRUSSELS_TZ = ZoneInfo(EPEX_TZ)


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
        """Fetch energy prices, capacity-tariff peaks and (when applicable) EPEX."""
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

        # An empty ``items`` list is the documented signal that this
        # account is on a dynamic (EPEX-indexed) tariff: ENGIE returns
        # 200 with ``{"items":[]}`` because there are no fixed monthly
        # rates to expose. Detection is therefore at the account level,
        # not per-EAN.
        items = data.get("items") if isinstance(data, dict) else None
        is_dynamic = isinstance(items, list) and len(items) == 0
        data[KEY_IS_DYNAMIC] = is_dynamic

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
            self._record_peak_history(peaks_wrapper)

        # Fetch EPEX day-ahead prices for dynamic accounts only.  On
        # non-dynamic accounts we drop any previously cached payload so
        # sensors don't expose stale data after a contract change.
        previous_epex: EpexPayload | None = None
        if isinstance(self.data, dict):
            previous_epex_raw = self.data.get(KEY_EPEX)
            if isinstance(previous_epex_raw, EpexPayload):
                previous_epex = previous_epex_raw

        if is_dynamic:
            epex_payload = await self._async_fetch_epex(previous_epex)
            data[KEY_EPEX] = epex_payload
        else:
            data[KEY_EPEX] = None

        self.last_successful_fetch = dt_util.utcnow()
        return data

    def _record_peak_history(self, peaks_wrapper: dict[str, Any]) -> None:
        """Persist the current month's peak window if it is not a fallback."""
        if peaks_wrapper.get("is_fallback"):
            return
        runtime = getattr(self.config_entry, "runtime_data", None)
        store = getattr(runtime, "peaks_store", None) if runtime is not None else None
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

    async def _async_fetch_epex(
        self,
        previous: EpexPayload | None,
    ) -> EpexPayload | None:
        """
        Fetch EPEX day-ahead prices covering today + tomorrow (Brussels).

        Returns the parsed payload, or ``previous`` (last-known) when the
        endpoint is reachable but tomorrow's slate is not yet published
        (HTTP 404), or when a transient communication error occurs.
        Returns ``None`` only when no previous payload exists either --
        platforms must handle this by reporting unavailable.
        """
        client = self.config_entry.runtime_data.client
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
            return _parse_epex_response(raw)
        except (KeyError, TypeError, ValueError) as exception:
            LOGGER.warning(
                "Failed to parse EPEX response, keeping last-known payload: %s",
                exception,
            )
            return previous


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
