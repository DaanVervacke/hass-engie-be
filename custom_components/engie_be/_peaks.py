"""
Shared helpers for capacity-tariff (captar) peak data.

These helpers unwrap the ``peaks`` wrapper that the coordinator stores under
``coordinator.data["peaks"]``. They are imported by both the sensor and
calendar platforms to keep payload-shape knowledge in a single place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator


def peaks_payload(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """
    Return the inner peaks dict from coordinator data, or ``None``.

    The coordinator wraps the API response as
    ``{"data", "year", "month", "is_fallback"}``. This helper unwraps it.
    """
    if not isinstance(coordinator.data, dict):
        return None
    wrapper = coordinator.data.get("peaks")
    if not isinstance(wrapper, dict):
        return None
    payload = wrapper.get("data")
    return payload if isinstance(payload, dict) else None


def peaks_meta(
    coordinator: EngieBeDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """Return ``{year, month, is_fallback}`` for the active peaks payload."""
    if not isinstance(coordinator.data, dict):
        return None
    wrapper = coordinator.data.get("peaks")
    if not isinstance(wrapper, dict):
        return None
    year = wrapper.get("year")
    month = wrapper.get("month")
    if not isinstance(year, int) or not isinstance(month, int):
        return None
    return {
        "year": year,
        "month": month,
        "is_fallback": bool(wrapper.get("is_fallback", False)),
    }
