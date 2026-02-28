"""Custom types for the Engie Belgium integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .api import EngieBeApiClient
    from .coordinator import EngieBeDataUpdateCoordinator


type EngieBeConfigEntry = ConfigEntry[EngieBeData]


@dataclass
class EngieBeData:
    """Runtime data for the Engie Belgium integration."""

    client: EngieBeApiClient
    coordinator: EngieBeDataUpdateCoordinator
    authenticated: bool = field(default=False)
