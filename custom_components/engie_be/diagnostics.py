"""Diagnostics support for the ENGIE Belgium integration."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CUSTOMER_NUMBER,
    CONF_REFRESH_TOKEN,
    KEY_EPEX,
    KEY_IS_DYNAMIC,
)
from .data import EpexPayload

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import EngieBeConfigEntry

TO_REDACT: set[str] = {
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_CUSTOMER_NUMBER,
    CONF_CLIENT_ID,
}

EAN_HASH_LENGTH = 8


def _hash_ean(ean: str) -> str:
    """Hash an EAN to a short fingerprint for support correlation."""
    return hashlib.sha256(ean.encode("utf-8")).hexdigest()[:EAN_HASH_LENGTH]


def _summarise_coordinator_data(data: Any) -> dict[str, Any]:
    """Return a privacy-preserving summary of coordinator data."""
    if not isinstance(data, dict):
        return {"raw_type": type(data).__name__}

    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    ean_hashes = [
        _hash_ean(item["ean"])
        for item in items
        if isinstance(item, dict) and isinstance(item.get("ean"), str) and item["ean"]
    ]
    peaks_wrapper = data.get("peaks") if isinstance(data.get("peaks"), dict) else None
    peaks_inner = (
        peaks_wrapper.get("data")
        if isinstance(peaks_wrapper, dict)
        and isinstance(peaks_wrapper.get("data"), dict)
        else None
    )
    if peaks_wrapper is not None:
        year = peaks_wrapper.get("year")
        month = peaks_wrapper.get("month")
        peaks_month = (
            f"{year:04d}-{month:02d}"
            if isinstance(year, int) and isinstance(month, int)
            else None
        )
        peaks_is_fallback = bool(peaks_wrapper.get("is_fallback", False))
    else:
        peaks_month = None
        peaks_is_fallback = None
    return {
        "item_count": len(items),
        "ean_hashes": ean_hashes,
        "top_level_keys": sorted(data.keys()),
        "peaks_present": peaks_inner is not None,
        "peaks_month": peaks_month,
        "peaks_is_fallback": peaks_is_fallback,
        "is_dynamic": bool(data.get(KEY_IS_DYNAMIC, False)),
        "epex": _summarise_epex(data.get(KEY_EPEX)),
    }


def _summarise_epex(payload: Any) -> dict[str, Any] | None:
    """Return a privacy-safe summary of the cached EPEX payload."""
    if not isinstance(payload, EpexPayload):
        return None
    slots = payload.slots
    return {
        "slot_count": len(slots),
        "slot_duration_minutes": slots[0].duration_minutes if slots else None,
        "first_slot_start": slots[0].start.isoformat() if slots else None,
        "last_slot_end": slots[-1].end.isoformat() if slots else None,
        "publication_time": (
            payload.publication_time.isoformat()
            if payload.publication_time is not None
            else None
        ),
        "market_date": payload.market_date,
    }


def _summarise_service_points(service_points: dict[str, str]) -> dict[str, str]:
    """Return service points with EANs replaced by short hashes."""
    return {_hash_ean(ean): division for ean, division in service_points.items()}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator if runtime is not None else None
    peaks_store = getattr(runtime, "peaks_store", None) if runtime is not None else None

    return {
        "entry": {
            "version": entry.version,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "runtime": {
            "authenticated": getattr(runtime, "authenticated", None),
            "service_points": _summarise_service_points(
                getattr(runtime, "service_points", {}) or {},
            ),
            "peaks_history": (
                peaks_store.summary() if peaks_store is not None else None
            ),
        },
        "coordinator": {
            "last_update_success": (
                coordinator.last_update_success if coordinator is not None else None
            ),
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator is not None and coordinator.update_interval is not None
                else None
            ),
            "data_summary": _summarise_coordinator_data(
                coordinator.data if coordinator is not None else None,
            ),
        },
    }
