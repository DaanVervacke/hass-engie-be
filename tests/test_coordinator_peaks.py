"""Tests for capacity-tariff peaks handling in the coordinator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

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
    """A successful peaks fetch is merged into coordinator data under ``peaks``."""
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(return_value=peaks)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    result = await coordinator._async_update_data()

    assert result["peaks"] == peaks
    assert "items" in result
    client.async_get_monthly_peaks.assert_awaited_once()
    args = client.async_get_monthly_peaks.await_args.args
    assert args[0] == "000000000000"
    # year, month must be ints reflecting current local time
    assert isinstance(args[1], int)
    assert 1 <= args[2] <= 12


async def test_update_keeps_last_known_peaks_on_peaks_failure(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If peaks fail mid-cycle, the previous peaks payload is retained."""
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    previous_peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(
        side_effect=EngieBeApiClientError("upstream 503"),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    # Seed previous coordinator data so the fallback has something to keep.
    coordinator.data = {"items": [], "peaks": previous_peaks}

    result = await coordinator._async_update_data()

    assert result["peaks"] == previous_peaks
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
