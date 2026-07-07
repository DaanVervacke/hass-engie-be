"""Tests for the import_history / clear_import_history service handlers."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, call, patch

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

from custom_components.engie_be.api import EngieBeApiClientError


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


async def test_service_raises_when_energy_type_is_explicitly_empty(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    energy_type: [] (user unchecked all) raises service_no_energy_type_selected.

    Distinct from omitting the field, which falls back to all streams
    as a safety net for programmatic callers.
    """
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    ban_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, subentry_id)}
    )
    assert ban_device is not None

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": [ban_device.id], "energy_type": []},
            blocking=True,
        )

    assert exc_info.value.translation_key == "service_no_energy_type_selected"


async def test_clear_service_raises_when_energy_type_is_explicitly_empty(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """clear_import_history with energy_type: [] raises the same validation error."""
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    ban_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, subentry_id)}
    )
    assert ban_device is not None

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_IMPORT_HISTORY,
            {"device_id": [ban_device.id], "energy_type": []},
            blocking=True,
        )

    assert exc_info.value.translation_key == "service_no_energy_type_selected"


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


async def test_import_history_service_bumps_end_date_by_one_day(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """end_date is inclusive at the service boundary; orchestrator gets +1 day."""
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    ban_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, subentry_id)}
    )
    assert ban_device is not None

    with patch(
        "custom_components.engie_be.async_import_usage_history",
        new=AsyncMock(return_value=0),
    ) as mocked:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {
                "device_id": [ban_device.id],
                "start_date": "2026-04-01",
                "end_date": "2026-04-15",
            },
            blocking=True,
        )

    assert mocked.await_count == 1
    kwargs = mocked.await_args.kwargs
    assert kwargs["start_date"] == date(2026, 4, 1)
    assert kwargs["end_date"] == date(2026, 4, 16)


async def test_import_history_service_end_date_none_stays_none(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Omitting end_date leaves the orchestrator to auto-select (None passthrough)."""
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    ban_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, subentry_id)}
    )
    assert ban_device is not None

    with patch(
        "custom_components.engie_be.async_import_usage_history",
        new=AsyncMock(return_value=0),
    ) as mocked:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": [ban_device.id]},
            blocking=True,
        )

    assert mocked.await_count == 1
    assert mocked.await_args.kwargs["end_date"] is None


def _build_two_ban_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Config entry with two business-agreement subentries for gather tests."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        title="user@example.com",
        unique_id="user_example_com_two",
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
                unique_id="000000000001",
                data={
                    CONF_BUSINESS_AGREEMENT_NUMBER: "000000000001",
                    CONF_PREMISES_NUMBER: "P-0001",
                    CONF_CONSUMPTION_ADDRESS: "Rue de la Loi 16, 1000 Brussels",
                },
            ),
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                title="Wetstraat 16, 1000 Brussels",
                unique_id="000000000002",
                data={
                    CONF_BUSINESS_AGREEMENT_NUMBER: "000000000002",
                    CONF_PREMISES_NUMBER: "P-0002",
                    CONF_CONSUMPTION_ADDRESS: "Wetstraat 16, 1000 Brussels",
                },
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def _setup_two_ban_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build and fully set up a two-BAN config entry."""
    entry = _build_two_ban_entry(hass)
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


async def test_import_history_dispatches_in_parallel_across_bans(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Both BANs are dispatched via asyncio.gather; both mock calls happen."""
    entry = await _setup_two_ban_entry(hass)
    device_registry = dr.async_get(hass)
    ban_devices = [
        device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
        for subentry_id in entry.subentries
    ]
    assert all(d is not None for d in ban_devices)
    device_ids = [d.id for d in ban_devices]

    with patch(
        "custom_components.engie_be.async_import_usage_history",
        new=AsyncMock(return_value=42),
    ) as mock_import:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": device_ids},
            blocking=True,
        )

    assert mock_import.await_count == 2
    # Both subentries must appear in the call args (3rd positional arg);
    # order is not guaranteed because gather schedules concurrently.
    called_bans = sorted(
        c.args[2].data[CONF_BUSINESS_AGREEMENT_NUMBER]
        for c in mock_import.await_args_list
    )
    assert called_bans == ["000000000001", "000000000002"]


async def test_import_history_continues_when_one_ban_fails(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """If one BAN raises, the other still runs and no exception escapes."""
    entry = await _setup_two_ban_entry(hass)
    device_registry = dr.async_get(hass)
    subentry_ids = list(entry.subentries)
    ban_devices = [
        device_registry.async_get_device(identifiers={(DOMAIN, sid)})
        for sid in subentry_ids
    ]
    assert all(d is not None for d in ban_devices)
    device_ids = [d.id for d in ban_devices]

    # First call raises; second succeeds. Accept positional + keyword args to
    # match the real signature: async_import_usage_history(hass, client, subentry, ...).
    call_count = 0

    async def _fake_import(*_args: object, **_kwargs: object) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise EngieBeApiClientError("boom")
        return 42

    with (
        patch(
            "custom_components.engie_be.async_import_usage_history",
            side_effect=_fake_import,
        ) as mock_import,
        patch("custom_components.engie_be.LOGGER") as mock_logger,
    ):
        # Must not raise.
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": device_ids},
            blocking=True,
        )

    assert mock_import.await_count == 2
    # LOGGER.exception must have been called exactly once for the failing BAN.
    assert mock_logger.exception.call_count == 1
    exc_log_call: call = mock_logger.exception.call_args
    assert "unexpected error" in exc_log_call.args[0]
