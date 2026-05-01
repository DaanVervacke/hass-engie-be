"""Tests for capacity-tariff peaks handling in the coordinator."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.api import (
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CUSTOMER_NUMBER,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    DOMAIN,
)
from custom_components.engie_be.coordinator import EngieBeDataUpdateCoordinator
from custom_components.engie_be.data import EngieBeData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_PRICES_FIXTURE = Path(__file__).parent / "fixtures" / "prices_sample.json"
_PEAKS_FIXTURE = Path(__file__).parent / "fixtures" / "peaks_2026_04.json"

_BRUSSELS = ZoneInfo("Europe/Brussels")


def _build_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a MockConfigEntry with default credentials and options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="user@example.com",
        unique_id="user_example_com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_CUSTOMER_NUMBER: "000000000000",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "stored-access",
            CONF_REFRESH_TOKEN: "stored-refresh",
        },
        options={"update_interval": 60},
    )
    entry.add_to_hass(hass)
    return entry


def _attach_runtime(entry: MockConfigEntry, client: MagicMock) -> None:
    """Attach an EngieBeData runtime stub with the given mocked client."""
    entry.runtime_data = EngieBeData(
        client=client,
        coordinator=MagicMock(),
        last_options=dict(entry.options),
    )


async def test_update_merges_peaks_into_payload(hass: HomeAssistant) -> None:
    """A successful peaks fetch is merged as a wrapper under ``peaks``."""
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(return_value=peaks)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    result = await coordinator._async_update_data()

    wrapper = result["peaks"]
    assert wrapper["data"] == peaks
    assert wrapper["is_fallback"] is False
    assert isinstance(wrapper["year"], int)
    assert 1 <= wrapper["month"] <= 12
    assert "items" in result
    client.async_get_monthly_peaks.assert_awaited_once()
    args = client.async_get_monthly_peaks.await_args.args
    assert args[0] == "000000000000"


async def test_update_keeps_last_known_peaks_on_peaks_failure(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If peaks fail mid-cycle, the previous peaks wrapper is retained."""
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    previous_peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))
    previous_wrapper = {
        "data": previous_peaks,
        "year": 2026,
        "month": 4,
        "is_fallback": False,
    }

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(
        side_effect=EngieBeApiClientError("upstream 503"),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    # Seed previous coordinator data so the fallback has something to keep.
    coordinator.data = {"items": [], "peaks": previous_wrapper}

    result = await coordinator._async_update_data()

    assert result["peaks"] == previous_wrapper
    assert any(
        "Failed to fetch monthly peaks" in record.message for record in caplog.records
    )


async def test_update_omits_peaks_key_when_no_previous_and_fetch_fails(
    hass: HomeAssistant,
) -> None:
    """First-ever poll with a failing peaks endpoint must not crash."""
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(
        side_effect=EngieBeApiClientError("boom"),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    result = await coordinator._async_update_data()

    assert "peaks" not in result
    assert "items" in result


async def test_peaks_auth_error_triggers_reauth(hass: HomeAssistant) -> None:
    """An auth failure on the peaks call still surfaces as ConfigEntryAuthFailed."""
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(
        side_effect=EngieBeApiClientAuthenticationError("token rejected"),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_falls_back_to_previous_month_when_current_has_no_peak(
    hass: HomeAssistant,
) -> None:
    """When current month lacks peakOfTheMonth, fall back to previous month."""
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    previous_peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))
    empty_current = {"year": 2026, "month": 5, "dailyPeaks": []}

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(
        side_effect=[empty_current, previous_peaks],
    )
    _attach_runtime(entry, client)

    fixed_now = datetime(2026, 5, 1, 9, 0, tzinfo=_BRUSSELS)
    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=fixed_now,
    ):
        coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
        result = await coordinator._async_update_data()

    wrapper = result["peaks"]
    assert wrapper["data"] == previous_peaks
    assert wrapper["year"] == 2026
    assert wrapper["month"] == 4
    assert wrapper["is_fallback"] is True
    # Two API calls: current then previous
    assert client.async_get_monthly_peaks.await_count == 2
    first_call = client.async_get_monthly_peaks.await_args_list[0].args
    second_call = client.async_get_monthly_peaks.await_args_list[1].args
    assert first_call == ("000000000000", 2026, 5)
    assert second_call == ("000000000000", 2026, 4)


async def test_january_falls_back_to_previous_december(
    hass: HomeAssistant,
) -> None:
    """January with no peak yet must fall back to December of the prior year."""
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    previous_peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))
    empty_current = {"year": 2026, "month": 1, "dailyPeaks": []}

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(
        side_effect=[empty_current, previous_peaks],
    )
    _attach_runtime(entry, client)

    fixed_now = datetime(2026, 1, 2, 9, 0, tzinfo=_BRUSSELS)
    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=fixed_now,
    ):
        coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
        result = await coordinator._async_update_data()

    wrapper = result["peaks"]
    assert wrapper["year"] == 2025
    assert wrapper["month"] == 12
    assert wrapper["is_fallback"] is True
    second_call = client.async_get_monthly_peaks.await_args_list[1].args
    assert second_call == ("000000000000", 2025, 12)


async def test_fallback_failure_keeps_empty_current_wrapper(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the fallback call also fails, return an is_fallback=False wrapper."""
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    empty_current = {"year": 2026, "month": 5, "dailyPeaks": []}

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(
        side_effect=[empty_current, EngieBeApiClientError("upstream 500")],
    )
    _attach_runtime(entry, client)

    fixed_now = datetime(2026, 5, 1, 9, 0, tzinfo=_BRUSSELS)
    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=fixed_now,
    ):
        coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
        result = await coordinator._async_update_data()

    wrapper = result["peaks"]
    assert wrapper["data"] == empty_current
    assert wrapper["year"] == 2026
    assert wrapper["month"] == 5
    assert wrapper["is_fallback"] is False
    assert any("fallback" in record.message.lower() for record in caplog.records)
