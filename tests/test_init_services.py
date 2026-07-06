"""Tests for the import_history / clear_import_history service handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CONSUMPTION_ADDRESS,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SERVICE_CLEAR_IMPORT_HISTORY,
    SERVICE_IMPORT_HISTORY,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _build_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        title="user@example.com",
        unique_id="user_example_com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_ACCESS_TOKEN: "stored-access",
            CONF_REFRESH_TOKEN: "stored-refresh",
        },
        options={"update_interval": 60},
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                title="Rue de la Loi 16, 1000 Brussels",
                unique_id="002200000001",
                data={
                    CONF_BUSINESS_AGREEMENT_NUMBER: "002200000001",
                    CONF_PREMISES_NUMBER: "P-0001",
                    CONF_CONSUMPTION_ADDRESS: "Rue de la Loi 16, 1000 Brussels",
                },
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


def _make_client() -> MagicMock:
    client = MagicMock()

    async def _refresh_and_update() -> tuple[str, str]:
        client.refresh_token = "new-refresh"  # noqa: S105
        return ("new-access", "new-refresh")

    client.async_refresh_token = AsyncMock(side_effect=_refresh_and_update)
    client.async_get_prices = AsyncMock(return_value={"items": []})
    client.async_get_service_point = AsyncMock(return_value={"division": "ELECTRICITY"})
    client.async_get_monthly_peaks = AsyncMock(
        return_value={"peakOfTheMonth": None, "dailyPeaks": []}
    )
    client.async_get_happy_hour_event = AsyncMock(return_value={})
    client.async_get_happy_hours_service_enabled_flag = AsyncMock(return_value={})
    client.async_get_energy_contracts = AsyncMock(return_value={"items": []})
    client.async_get_epex_prices = AsyncMock(return_value={"timeSeries": []})
    return client


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build, add, and fully set up one config entry; return the entry."""
    entry = _build_entry(hass)
    client = _make_client()
    with (
        patch("custom_components.engie_be.EngieBeApiClient", return_value=client),
        patch(
            "custom_components.engie_be.coordinator.EngieBeDataUpdateCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeEpexCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(return_value=None),
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_import_history_service_raises_when_no_device_targeted(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """import_history with no device_id raises service_no_target_device."""
    await _setup_entry(hass)

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN, SERVICE_IMPORT_HISTORY, {}, blocking=True
        )

    assert exc_info.value.translation_key == "service_no_target_device"


async def test_clear_import_history_service_raises_when_no_device_targeted(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """clear_import_history with no device_id raises service_no_target_device."""
    await _setup_entry(hass)

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN, SERVICE_CLEAR_IMPORT_HISTORY, {}, blocking=True
        )

    assert exc_info.value.translation_key == "service_no_target_device"


async def test_service_raises_when_all_targets_are_non_ban_devices(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Targeting only the login device raises service_no_valid_target."""
    entry = await _setup_entry(hass)

    login_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"login_{entry.entry_id}")}
    )
    assert login_device is not None

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": [login_device.id]},
            blocking=True,
        )

    assert exc_info.value.translation_key == "service_no_valid_target"


async def test_service_raises_when_entry_is_reloading(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Targeting a BAN device while runtime_data.client is None raises service_entry_reloading."""  # noqa: E501
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))

    ban_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, subentry_id)}
    )
    assert ban_device is not None

    # Simulate the reload race: client is transiently None.
    entry.runtime_data.client = None

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": [ban_device.id]},
            blocking=True,
        )

    assert exc_info.value.translation_key == "service_entry_reloading"
