"""Custom types for the ENGIE Belgium integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry

    from .api import EngieBeApiClient
    from .coordinator import EngieBeDataUpdateCoordinator
    from .store import EngieBePeaksStore


type EngieBeConfigEntry = ConfigEntry[EngieBeData]


@dataclass(slots=True, frozen=True)
class EpexSlot:
    """
    Single EPEX day-ahead market price slot.

    ``start``/``end`` are timezone-aware datetimes (Europe/Brussels).
    ``value_eur_per_kwh`` is the wholesale market price in EUR/kWh
    (raw API value is EUR/MWh and divided by 1000 on ingest).
    ``duration_minutes`` is carried explicitly so a future move from
    hourly to 15-minute publication does not require touching the
    payload shape.
    """

    start: datetime
    end: datetime
    value_eur_per_kwh: float
    duration_minutes: int


@dataclass(slots=True, frozen=True)
class EpexPayload:
    """Latest known EPEX day-ahead slate plus its publication metadata."""

    slots: tuple[EpexSlot, ...]
    publication_time: datetime | None
    market_date: str | None


@dataclass
class EngieBeData:
    """Runtime data for the ENGIE Belgium integration."""

    client: EngieBeApiClient
    coordinator: EngieBeDataUpdateCoordinator
    authenticated: bool = field(default=False)
    last_options: dict[str, Any] = field(default_factory=dict)
    service_points: dict[str, str] = field(default_factory=dict)
    peaks_store: EngieBePeaksStore | None = field(default=None)
