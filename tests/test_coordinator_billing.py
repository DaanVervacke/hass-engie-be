"""Tests for coordinator-driven billing (account-balance) fetching."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.engie_be.api import (
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

_FIXTURES = Path(__file__).parent / "fixtures"
_PRICES = _FIXTURES / "prices_sample.json"
_PEAKS = _FIXTURES / "peaks_2026_04.json"
_FLAGS_NOT_ENROLLED = _FIXTURES / "feature_flags_not_enrolled.json"
_BILLING_OPEN = _FIXTURES / "billing_open_debit.json"
_BILLING_CLEAR = _FIXTURES / "billing_cleared.json"

pytestmark = pytest.mark.billing


def _load(path: Path) -> dict:
    """Return a fresh copy of a JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def _make_client(
    *,
    billing_payload: dict | Exception,
) -> MagicMock:
    """Build a client mock primed for a full coordinator refresh."""
    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=_load(_PRICES))
    client.async_get_monthly_peaks = AsyncMock(return_value=_load(_PEAKS))
    client.async_get_happy_hours_service_enabled_flag = AsyncMock(
        return_value=_load(_FLAGS_NOT_ENROLLED),
    )
    client.async_get_happy_hour_event = AsyncMock(return_value={})
    client.async_get_month_report = AsyncMock(return_value={})
    client.async_get_solar_surplus_shown_dashboard_flag = AsyncMock(
        return_value={"value": False},
    )
    client.async_get_solar_surplus_forecasts = AsyncMock(return_value={"forecasts": []})
    client.async_get_dgo_tou_is_active_flag = AsyncMock(return_value={"value": False})
    client.async_get_tou_schedules = AsyncMock(return_value={"items": []})

    if isinstance(billing_payload, Exception):
        client.async_get_account_balance = AsyncMock(side_effect=billing_payload)
    else:
        client.async_get_account_balance = AsyncMock(return_value=billing_payload)

    return client


async def test_billing_open_debit_stored_in_wrapper(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """A successful billing fetch stores a wrapper in coordinator.data['billing']."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(billing_payload=_load(_BILLING_OPEN))
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    result = await coord._async_update_data()

    assert "billing" in result
    wrapper = result["billing"]
    assert isinstance(wrapper, dict)
    assert "data" in wrapper
    assert "fetched_at" in wrapper
    assert wrapper["data"]["status"] == "OPEN_DEBIT"
    assert wrapper["data"]["overview"]["openAmount"] == pytest.approx(80.6)


async def test_billing_cleared_stored_in_wrapper(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """A CLEAR billing response stores a wrapper with zero amounts."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(billing_payload=_load(_BILLING_CLEAR))
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    result = await coord._async_update_data()

    assert "billing" in result
    assert result["billing"]["data"]["status"] == "CLEAR"
    assert result["billing"]["data"]["overview"]["openAmount"] == pytest.approx(0.0)


async def test_transient_error_preserves_previous_wrapper(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """A transient API error keeps the last-known billing wrapper unchanged."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(billing_payload=EngieBeApiClientError("connection reset"))
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    previous_wrapper = {
        "data": _load(_BILLING_OPEN),
        "fetched_at": "2026-07-07T00:00:00+00:00",
    }
    coord.data = {"billing": previous_wrapper}

    result = await coord._async_update_data()

    assert result["billing"] is previous_wrapper


async def test_auth_error_escalates_to_reauth(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """Auth failures on the billing endpoint escalate to ConfigEntryAuthFailed."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        billing_payload=EngieBeApiClientAuthenticationError("401"),
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()
