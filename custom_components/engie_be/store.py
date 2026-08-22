"""Persistent history for captar peaks and Happy Hours windows."""

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
        self._peaks_sorted: list[dict[str, Any]] | None = None
        self._loaded: bool = False

    async def async_load(self) -> None:
        """Load persisted peaks into memory."""
        data = await self._store.async_load()
        if isinstance(data, dict):
            raw = data.get("peaks")
            if isinstance(raw, list):
                self._peaks = [p for p in raw if _is_valid_peak(p)]
                self._peaks_sorted = None
        self._loaded = True
        LOGGER.debug(
            "Subentry %s: loaded %d historical peaks from store",
            self._subentry_id,
            len(self._peaks),
        )

    @property
    def peaks(self) -> list[dict[str, Any]]:
        """Return historical peaks sorted by (year, month) ascending."""
        if self._peaks_sorted is None:
            self._peaks_sorted = sorted(
                self._peaks, key=lambda p: (p["year"], p["month"])
            )
        return self._peaks_sorted

    def upsert(  # noqa: PLR0913, PLR0917 - explicit args mirror the persisted schema
        self,
        year: int,
        month: int,
        start: str,
        end: str,
        peak_kw: Any,
        peak_kwh: Any,
    ) -> bool:
        """Insert or update a peak keyed by ``(year, month)``. Return True on change."""
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
                self._peaks_sorted = None
                self._schedule_save()
                return True
        self._peaks.append(new_entry)
        self._peaks_sorted = None
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

    async def async_remove(self) -> None:
        """Delete this subentry's persisted store from disk. Safe if never written."""
        await self._store.async_remove()
        LOGGER.debug(
            "Subentry %s: removed persisted peaks-history store",
            self._subentry_id,
        )


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
    Persisted Happy Hours window history, keyed by ``start`` (ISO with offset).

    ENGIE republishes a window under both ``today`` and ``tomorrow`` keys, so
    dedup by ``start`` keeps upserts idempotent.
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
        self._windows_sorted: list[dict[str, Any]] | None = None
        self._loaded: bool = False

    async def async_load(self) -> None:
        """Load persisted Happy Hours windows into memory."""
        data = await self._store.async_load()
        if isinstance(data, dict):
            raw = data.get("windows")
            if isinstance(raw, list):
                self._windows = [w for w in raw if _is_valid_happy_hour(w)]
                self._windows_sorted = None
        self._loaded = True
        LOGGER.debug(
            "Subentry %s: loaded %d historical Happy Hours windows from store",
            self._subentry_id,
            len(self._windows),
        )

    @property
    def windows(self) -> list[dict[str, Any]]:
        """Return historical windows sorted by ``start`` ascending."""
        if self._windows_sorted is None:
            self._windows_sorted = sorted(self._windows, key=lambda w: w["start"])
        return self._windows_sorted

    @property
    def loaded(self) -> bool:
        """Return True once ``async_load`` has populated the store from disk."""
        return self._loaded

    def upsert(self, start: str, end: str) -> bool:
        """Insert or update a window keyed by ``start``. Return True on change."""
        new_entry = {"start": start, "end": end}
        for index, existing in enumerate(self._windows):
            if existing.get("start") == start:
                if existing == new_entry:
                    return False
                self._windows[index] = new_entry
                self._windows_sorted = None
                self._schedule_save()
                return True
        self._windows.append(new_entry)
        self._windows_sorted = None
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

    async def async_remove(self) -> None:
        """Delete this subentry's persisted Happy Hours store from disk."""
        await self._store.async_remove()
        LOGGER.debug(
            "Subentry %s: removed persisted Happy Hours store",
            self._subentry_id,
        )


def _is_valid_happy_hour(window: Any) -> bool:
    """Return True if ``window`` has the minimum shape we require."""
    return (
        isinstance(window, dict)
        and isinstance(window.get("start"), str)
        and isinstance(window.get("end"), str)
    )
