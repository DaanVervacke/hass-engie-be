"""Diagnostics support for the ENGIE Belgium integration."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CLIENT_ID,
    CONF_CONSUMPTION_ADDRESS,
    CONF_CUSTOMER_NUMBER,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    KEY_IS_DYNAMIC,
    SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
)
from .data import EpexPayload

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import EngieBeEpexCoordinator
    from .data import EngieBeConfigEntry, EngieBeSubentryData

TO_REDACT: set[str] = {
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_CUSTOMER_NUMBER,
    CONF_CLIENT_ID,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_PREMISES_NUMBER,
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_CONSUMPTION_ADDRESS,
}

EAN_HASH_LENGTH = 8
TITLE_HASH_LENGTH = 8


def _hash_ean(ean: str) -> str:
    """Hash an EAN to a short fingerprint for support correlation."""
    return hashlib.sha256(ean.encode("utf-8")).hexdigest()[:EAN_HASH_LENGTH]


def _redacted_title(title: str | None) -> str:
    """
    Return a stable, non-identifying fingerprint for a subentry title.

    Subentry titles are user-facing addresses or customer-account holder
    names. Replacing them with a short content-hash keeps support bundles
    privacy-safe while still letting the user correlate two subentries
    in the same diagnostic dump.
    """
    if not title:
        return "**REDACTED**"
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:TITLE_HASH_LENGTH]
    return f"**REDACTED:{digest}**"


def _summarise_coordinator_data(data: Any) -> dict[str, Any]:
    """Return a privacy-preserving summary of per-subentry coordinator data."""
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


def _summarise_epex_coordinator(
    coordinator: EngieBeEpexCoordinator | None,
) -> dict[str, Any]:
    """Return a privacy-safe summary of the entry-level EPEX coordinator."""
    if coordinator is None:
        return {"present": False}
    return {
        "present": True,
        "last_update_success": coordinator.last_update_success,
        "update_interval_seconds": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval is not None
            else None
        ),
        "payload": _summarise_epex(coordinator.data),
    }


def _summarise_service_points(service_points: dict[str, str]) -> dict[str, str]:
    """Return service points with EANs replaced by short hashes."""
    return {_hash_ean(ean): division for ean, division in service_points.items()}


def _summarise_subentry(
    sub_data: EngieBeSubentryData | None,
) -> dict[str, Any]:
    """Return a privacy-safe summary of one subentry's runtime state."""
    if sub_data is None:
        return {"present": False}
    coordinator = sub_data.coordinator
    return {
        "present": True,
        "service_points": _summarise_service_points(sub_data.service_points or {}),
        "peaks_history": (
            sub_data.peaks_store.summary() if sub_data.peaks_store is not None else None
        ),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval is not None
                else None
            ),
            "data_summary": _summarise_coordinator_data(coordinator.data),
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry, including all subentries."""
    runtime = entry.runtime_data

    subentries_summary: dict[str, dict[str, Any]] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_CUSTOMER_ACCOUNT:
            continue
        sub_data = (
            runtime.subentry_data.get(subentry.subentry_id)
            if runtime is not None
            else None
        )
        subentries_summary[subentry.subentry_id] = {
            "title": _redacted_title(subentry.title),
            "data": async_redact_data(dict(subentry.data), TO_REDACT),
            **_summarise_subentry(sub_data),
        }

    return {
        "entry": {
            "version": entry.version,
            "title": _redacted_title(entry.title),
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "runtime": {
            "authenticated": getattr(runtime, "authenticated", None),
        },
        "epex_coordinator": _summarise_epex_coordinator(
            runtime.epex_coordinator if runtime is not None else None,
        ),
        "subentries": subentries_summary,
    }
