"""Shared helpers for solar-surplus coordinator payloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .data import unwrap_dict_payload

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator


def solar_surplus_payload(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """
    Return the inner solar-surplus dict from coordinator data, or ``None``.

    The coordinator wraps the API response as
    ``{"data": {ean: forecasts_list}, "fetched_at": ISO}``. This helper
    unwraps it. The returned dict is keyed by EAN with per-EAN forecast
    lists as values. Callers do their own per-EAN lookup.
    """
    return unwrap_dict_payload(coordinator, "solar_surplus")
