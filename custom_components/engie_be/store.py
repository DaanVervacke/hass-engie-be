"""
Persistent storage of historical captar peak windows and Happy Hours windows.

ENGIE's peaks endpoint only returns the *current* month's
``peakOfTheMonth``; once a new month rolls over, the previous month's
peak is no longer available from the API. The Happy Hours endpoint only
ever returns ``tomorrow``'s scheduled window (or ``{}``); windows that
have already happened are not retrievable. Both stores persist every
window we observe so the calendar entity can keep surfacing the full
history across restarts and so newly-installed integrations gradually
build up their own local archive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_STORE_VERSION = 1
_SAVE_DELAY_SECONDS = 30


class EngieBePeaksStore:
    """Wrapper around ``Store`` for persisted peak history."""

    def __init__(self, hass: HomeAssistant, subentry_id: str) -> None:
        """Initialise the store for one customer-account subentry."""
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORE_VERSION,
            f"{DOMAIN}.peaks_history.{subentry_id}",
        )
        self._subentry_id = subentry_id
        self._peaks: list[dict[str, Any]] = []
        self._loaded: bool = False

    async def async_load(self) -> None:
        """Load persisted peaks into memory."""
        data = await self._store.async_load()
        if isinstance(data, dict):
            raw = data.get("peaks")
            if isinstance(raw, list):
                self._peaks = [p for p in raw if _is_valid_peak(p)]
        self._loaded = True
        LOGGER.debug(
            "Subentry %s: loaded %d historical peaks from store",
            self._subentry_id,
            len(self._peaks),
        )

    @property
    def peaks(self) -> list[dict[str, Any]]:
        """Return historical peaks sorted by (year, month) ascending."""
        return sorted(self._peaks, key=lambda p: (p["year"], p["month"]))

    def upsert(  # noqa: PLR0913 - explicit args mirror the persisted schema
        self,
        year: int,
        month: int,
        start: str,
        end: str,
        peak_kw: Any,
        peak_kwh: Any,
    ) -> bool:
        """
        Insert or update a peak entry, returning ``True`` if anything changed.

        Entries are keyed by ``(year, month)``. An existing entry is
        overwritten when any field differs (the peak window can shift
        within a month as larger 15-minute peaks are recorded).
        """
        new_entry = {
            "year": year,
            "month": month,
            "start": start,
            "end": end,
            "peakKW": peak_kw,
            "peakKWh": peak_kwh,
        }
        for index, existing in enumerate(self._peaks):
            if existing.get("year") == year and existing.get("month") == month:
                if existing == new_entry:
                    return False
                self._peaks[index] = new_entry
                self._schedule_save()
                return True
        self._peaks.append(new_entry)
        self._schedule_save()
        return True

    def _schedule_save(self) -> None:
        """Coalesce frequent updates into one disk write."""
        self._store.async_delay_save(self._data_to_save, _SAVE_DELAY_SECONDS)

    def _data_to_save(self) -> dict[str, Any]:
        """Return the payload persisted by ``Store``."""
        return {"peaks": self.peaks}

    def summary(self) -> dict[str, Any]:
        """Return a small summary suitable for diagnostics."""
        peaks = self.peaks
        if not peaks:
            return {"count": 0, "oldest": None, "newest": None, "latest_peakKW": None}
        oldest, newest = peaks[0], peaks[-1]
        return {
            "count": len(peaks),
            "oldest": f"{oldest['year']:04d}-{oldest['month']:02d}",
            "newest": f"{newest['year']:04d}-{newest['month']:02d}",
            "latest_peakKW": newest.get("peakKW"),
        }


def _is_valid_peak(peak: Any) -> bool:
    """Return True if ``peak`` has the minimum shape we require."""
    return (
        isinstance(peak, dict)
        and isinstance(peak.get("year"), int)
        and isinstance(peak.get("month"), int)
        and isinstance(peak.get("start"), str)
        and isinstance(peak.get("end"), str)
    )


class EngieBeHappyHoursStore:
    """
    Wrapper around ``Store`` for persisted Happy Hours window history.

    Entries are keyed by ``start`` (the ISO-formatted window start with
    explicit timezone offset, as supplied by ENGIE). The API publishes a
    window under a ``tomorrow`` key and re-publishes the same window under
    a ``today`` key once midnight passes; both are recorded, so dedup by
    ``start`` is enough to make repeated upserts idempotent across the
    many refreshes (and both keys) that happen between the moment ENGIE
    announces a window and the moment it expires.

    The store can only ever contain windows the integration observed
    while running. Windows that happened before the user installed the
    integration, or before this store landed, are permanently lost
    because ENGIE does not expose Happy Hours history.
    """

    def __init__(self, hass: HomeAssistant, subentry_id: str) -> None:
        """Initialise the store for one customer-account subentry."""
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORE_VERSION,
            f"{DOMAIN}.happy_hours_history.{subentry_id}",
        )
        self._subentry_id = subentry_id
        self._windows: list[dict[str, Any]] = []
        self._loaded: bool = False

    async def async_load(self) -> None:
        """Load persisted Happy Hours windows into memory."""
        data = await self._store.async_load()
        if isinstance(data, dict):
            raw = data.get("windows")
            if isinstance(raw, list):
                self._windows = [w for w in raw if _is_valid_happy_hour(w)]
        self._loaded = True
        LOGGER.debug(
            "Subentry %s: loaded %d historical Happy Hours windows from store",
            self._subentry_id,
            len(self._windows),
        )

    @property
    def windows(self) -> list[dict[str, Any]]:
        """Return historical windows sorted by ``start`` ascending."""
        return sorted(self._windows, key=lambda w: w["start"])

    def upsert(self, start: str, end: str) -> bool:
        """
        Insert or update a window entry, returning ``True`` if anything changed.

        Entries are keyed by ``start``. An existing entry is overwritten
        only when ``end`` differs (start is the dedup key by definition).
        """
        new_entry = {"start": start, "end": end}
        for index, existing in enumerate(self._windows):
            if existing.get("start") == start:
                if existing == new_entry:
                    return False
                self._windows[index] = new_entry
                self._schedule_save()
                return True
        self._windows.append(new_entry)
        self._schedule_save()
        return True

    def _schedule_save(self) -> None:
        """Coalesce frequent updates into one disk write."""
        self._store.async_delay_save(self._data_to_save, _SAVE_DELAY_SECONDS)

    def _data_to_save(self) -> dict[str, Any]:
        """Return the payload persisted by ``Store``."""
        return {"windows": self.windows}

    def summary(self) -> dict[str, Any]:
        """Return a small summary suitable for diagnostics."""
        if not self._windows:
            return {"count": 0, "oldest": None, "newest": None}
        sorted_windows = self.windows
        return {
            "count": len(sorted_windows),
            "oldest": sorted_windows[0]["start"],
            "newest": sorted_windows[-1]["start"],
        }


def _is_valid_happy_hour(window: Any) -> bool:
    """Return True if ``window`` has the minimum shape we require."""
    return (
        isinstance(window, dict)
        and isinstance(window.get("start"), str)
        and isinstance(window.get("end"), str)
    )
