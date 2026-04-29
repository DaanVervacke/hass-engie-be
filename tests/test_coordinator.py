"""Tests for the ENGIE Belgium DataUpdateCoordinator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
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

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "prices_sample.json"


def _build_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a MockConfigEntry with credentials and an empty runtime placeholder."""
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
        coordinator=MagicMock(),  # placeholder; coordinator under test is built below
        last_options=dict(entry.options),
    )


async def test_async_update_data_returns_payload_on_success(
    hass: HomeAssistant,
) -> None:
    """A successful API call returns the payload and stamps last_successful_fetch."""
    entry = _build_entry(hass)
    payload = json.loads(_FIXTURE_PATH.read_text())

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=payload)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    result = await coordinator._async_update_data()

    assert result == payload
    assert coordinator.last_successful_fetch is not None
    client.async_get_prices.assert_awaited_once_with("000000000000")


async def test_async_update_data_raises_config_entry_auth_failed_on_auth_error(
    hass: HomeAssistant,
) -> None:
    """Authentication errors must surface as ConfigEntryAuthFailed for reauth."""
    entry = _build_entry(hass)

    client = MagicMock()
    original = EngieBeApiClientAuthenticationError("token rejected")
    client.async_get_prices = AsyncMock(side_effect=original)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)

    with pytest.raises(ConfigEntryAuthFailed) as exc_info:
        await coordinator._async_update_data()

    assert exc_info.value.__cause__ is original
    assert coordinator.last_successful_fetch is None


async def test_async_update_data_raises_update_failed_on_generic_error(
    hass: HomeAssistant,
) -> None:
    """Generic API errors must surface as UpdateFailed for the coordinator."""
    entry = _build_entry(hass)

    client = MagicMock()
    original = EngieBeApiClientError("upstream 500")
    client.async_get_prices = AsyncMock(side_effect=original)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)

    with pytest.raises(UpdateFailed) as exc_info:
        await coordinator._async_update_data()

    assert exc_info.value.__cause__ is original
    assert coordinator.last_successful_fetch is None
