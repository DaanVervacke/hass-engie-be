"""
Persistent storage of historical captar peak windows.

ENGIE's peaks endpoint only returns the *current* month's
``peakOfTheMonth``; once a new month rolls over, the previous month's
peak is no longer available from the API. This store persists every
non-fallback peak we observe so the calendar entity can keep showing
historical events across restarts.
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

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialise the store for one config entry."""
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORE_VERSION,
            f"{DOMAIN}.peaks_history.{entry_id}",
        )
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
            "Loaded %d historical peaks from store",
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

    async def async_save_now(self) -> None:
        """
        Flush pending peaks to disk immediately, bypassing the save delay.

        Used by the v2 to v3 migration so that, if HA crashes within the
        save-delay window after migration, the carried-over peaks are not
        lost.
        """
        await self._store.async_save(self._data_to_save())

    def _data_to_save(self) -> dict[str, Any]:
        """Return the payload persisted by ``Store``."""
        return {"peaks": self.peaks}

    def summary(self) -> dict[str, Any]:
        """Return a small summary suitable for diagnostics."""
        if not self._peaks:
            return {"count": 0, "oldest": None, "newest": None, "latest_peakKW": None}
        sorted_peaks = self.peaks
        oldest = sorted_peaks[0]
        newest = sorted_peaks[-1]
        return {
            "count": len(sorted_peaks),
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
