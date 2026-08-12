"""Tests for the import_history / clear_import_history service handlers."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
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

from custom_components.engie_be._statistics import (
    STREAM_CONSUMPTION,
    STREAM_CONSUMPTION_COST,
    STREAM_GAS,
    STREAM_GAS_COST,
    STREAM_INJECTION,
    STREAM_INJECTION_COST,
    streams_for_energy_types,
)
from custom_components.engie_be.api import (
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)


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
                unique_id="000000000001",
                data={
                    CONF_BUSINESS_AGREEMENT_NUMBER: "000000000001",
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


async def test_service_raises_when_entry_is_not_loaded(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Targeting a BAN device on an unloaded entry raises service_entry_reloading."""
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))

    ban_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, subentry_id)}
    )
    assert ban_device is not None

    # Unload the entry. This is the state HA passes through mid-reload and
    # the one it rests in after a manual disable. Nothing in the
    # integration ever sets ``runtime_data.client`` to None, so unloading
    # is the real way to reach this guard rather than nulling the client.
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

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


async def test_clear_import_history_dispatches_expected_streams_across_bans(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Each targeted BAN is cleared with exactly the resolved stream set."""
    entry = await _setup_two_ban_entry(hass)
    device_registry = dr.async_get(hass)
    ban_devices = [
        device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
        for subentry_id in entry.subentries
    ]
    assert all(d is not None for d in ban_devices)
    device_ids = [d.id for d in ban_devices]

    with patch(
        "custom_components.engie_be.async_clear_usage_history",
        new=AsyncMock(return_value=None),
    ) as mock_clear:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_IMPORT_HISTORY,
            {
                "device_id": device_ids,
                "energy_type": ["consumption"],
                "include_costs": True,
            },
            blocking=True,
        )

    expected = streams_for_energy_types(["consumption"], include_costs=True)
    assert mock_clear.await_count == 2
    for call_args in mock_clear.await_args_list:
        assert call_args.kwargs["streams"] == expected


async def test_clear_import_history_clears_costs_by_default(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Omitting ``include_costs`` clears cost streams too.

    Clearing used to default to energy-only, which left a cost stream
    alive with no energy stream behind it. That is the one incoherent
    outcome, so the default is on for clearing (and stays off for
    importing, where costs are extra work rather than cleanup).
    """
    entry = await _setup_two_ban_entry(hass)
    device_registry = dr.async_get(hass)
    ban_devices = [
        device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
        for subentry_id in entry.subentries
    ]
    assert all(d is not None for d in ban_devices)
    device_ids = [d.id for d in ban_devices]

    with patch(
        "custom_components.engie_be.async_clear_usage_history",
        new=AsyncMock(return_value=None),
    ) as mock_clear:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_IMPORT_HISTORY,
            {"device_id": device_ids},
            blocking=True,
        )

    expected = frozenset(
        {
            STREAM_CONSUMPTION,
            STREAM_INJECTION,
            STREAM_GAS,
            STREAM_CONSUMPTION_COST,
            STREAM_INJECTION_COST,
            STREAM_GAS_COST,
        }
    )
    assert mock_clear.await_count == 2
    for call_args in mock_clear.await_args_list:
        assert call_args.kwargs["streams"] == expected


async def test_clear_import_history_creates_repairs_issue_on_failure(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A failing clear raises HomeAssistantError and creates a Repairs issue."""
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    device_registry = dr.async_get(hass)
    ban_device = device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert ban_device is not None

    with (
        patch(
            "custom_components.engie_be.async_clear_usage_history",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_IMPORT_HISTORY,
            {"device_id": [ban_device.id]},
            blocking=True,
        )

    assert exc_info.value.translation_key == "clear_history_failed"
    issue_registry = ir.async_get(hass)
    issue = issue_registry.async_get_issue(
        DOMAIN, f"service_clear_failed_{subentry_id}"
    )
    assert issue is not None
    assert issue.translation_key == "service_clear_failed"


async def test_clear_import_history_clears_repairs_issue_on_success(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A prior clear-failure Repairs issue is cleared once the clear succeeds."""
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    device_registry = dr.async_get(hass)
    ban_device = device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert ban_device is not None

    issue_id = f"service_clear_failed_{subentry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="service_clear_failed",
        translation_placeholders={"title": "stale issue"},
    )
    issue_registry = ir.async_get(hass)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    with patch(
        "custom_components.engie_be.async_clear_usage_history",
        new=AsyncMock(return_value=None),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_IMPORT_HISTORY,
            {"device_id": [ban_device.id]},
            blocking=True,
        )

    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_clear_import_history_partial_failure_processes_all_and_raises(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """One failing target does not abort the other, only it gets an issue."""
    entry = await _setup_two_ban_entry(hass)
    subentry_ids = list(entry.subentries)
    device_registry = dr.async_get(hass)
    ban_devices = [
        device_registry.async_get_device(identifiers={(DOMAIN, sid)})
        for sid in subentry_ids
    ]
    assert all(d is not None for d in ban_devices)
    device_ids = [d.id for d in ban_devices]

    call_count = 0

    async def _fake_clear(*_args: object, **_kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")

    with (
        patch(
            "custom_components.engie_be.async_clear_usage_history",
            side_effect=_fake_clear,
        ) as mock_clear,
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_IMPORT_HISTORY,
            {"device_id": device_ids},
            blocking=True,
        )

    # Both targets were attempted despite the first failing.
    assert mock_clear.await_count == 2
    assert "1 of 2" in exc_info.value.translation_placeholders["error"]

    issue_registry = ir.async_get(hass)
    present = [
        sid
        for sid in subentry_ids
        if issue_registry.async_get_issue(DOMAIN, f"service_clear_failed_{sid}")
        is not None
    ]
    assert len(present) == 1


async def test_clear_import_history_cancelled_error_reraised_without_issue(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A CancelledError propagates and does not create a Repairs issue."""
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    device_registry = dr.async_get(hass)
    ban_device = device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert ban_device is not None

    with (
        patch(
            "custom_components.engie_be.async_clear_usage_history",
            side_effect=asyncio.CancelledError,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_IMPORT_HISTORY,
            {"device_id": [ban_device.id]},
            blocking=True,
        )

    issue_registry = ir.async_get(hass)
    assert (
        issue_registry.async_get_issue(DOMAIN, f"service_clear_failed_{subentry_id}")
        is None
    )


async def test_import_history_defaults_to_energy_streams_only(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Omitting ``include_costs`` on import stays energy-only.

    Regression guard for the asymmetry with ``engie_be.clear_import_history``:
    importing is additive and costs double the work against ENGIE's
    API, so opting in stays required there. Do not "fix" this into
    symmetry with clearing.
    """
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

    expected = frozenset({STREAM_CONSUMPTION, STREAM_INJECTION, STREAM_GAS})
    assert mock_import.await_count == 2
    for call_args in mock_import.await_args_list:
        assert call_args.kwargs["streams"] == expected


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
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": device_ids},
            blocking=True,
        )

    assert exc_info.value.translation_key == "import_history_failed"
    assert mock_import.await_count == 2
    # LOGGER.exception must have been called exactly once for the failing BAN.
    assert mock_logger.exception.call_count == 1
    exc_log_call: call = mock_logger.exception.call_args
    assert "unexpected error" in exc_log_call.args[0]


async def test_import_history_continues_when_one_ban_auth_rejected(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """An auth rejection for one BAN logs a warning-and-continue, not an exception."""
    entry = await _setup_two_ban_entry(hass)
    device_registry = dr.async_get(hass)
    subentry_ids = list(entry.subentries)
    ban_devices = [
        device_registry.async_get_device(identifiers={(DOMAIN, sid)})
        for sid in subentry_ids
    ]
    assert all(d is not None for d in ban_devices)
    device_ids = [d.id for d in ban_devices]

    call_count = 0

    async def _fake_import(*_args: object, **_kwargs: object) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise EngieBeApiClientAuthenticationError("expired")
        return 42

    with (
        patch(
            "custom_components.engie_be.async_import_usage_history",
            side_effect=_fake_import,
        ) as mock_import,
        patch("custom_components.engie_be.LOGGER") as mock_logger,
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": device_ids},
            blocking=True,
        )

    assert exc_info.value.translation_key == "import_history_failed"
    assert mock_import.await_count == 2
    assert mock_logger.warning.call_count == 1
    warning_call: call = mock_logger.warning.call_args
    assert "authentication rejected" in warning_call.args[0]
    mock_logger.exception.assert_not_called()


async def test_services_registered_when_entry_fails_to_setup(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Services exist even when no config entry loads successfully.

    Regression guard for the ``action-setup`` quality-scale rule: services
    used to be registered at the end of ``async_setup_entry``, so an entry
    that failed on expired credentials left automations calling
    ``engie_be.import_history`` with a bare "service not found".
    """
    entry = _build_entry(hass)

    with patch(
        "custom_components.engie_be.EngieBeApiClient.async_refresh_token",
        side_effect=EngieBeApiClientAuthenticationError("token expired"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is not ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, SERVICE_IMPORT_HISTORY)
    assert hass.services.has_service(DOMAIN, SERVICE_CLEAR_IMPORT_HISTORY)


async def test_import_history_on_failed_entry_reports_not_loaded(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Calling a service against a never-loaded entry raises, it does not no-op.

    ``runtime_data`` is not a usable liveness signal here: it is assigned
    early in ``async_setup_entry``, before the token refresh that fails,
    and HA only deletes it on a real unload. Gating on it would let the
    import proceed against an unauthenticated client, where the handler
    swallows the auth error as a log warning and the user sees a silent
    success. ``_resolve_targets`` gates on entry state instead.
    """
    entry = _build_entry(hass)
    subentry_id = next(iter(entry.subentries))
    # Platforms never load on a failed setup, so no device is created for
    # us. Register the business-agreement device directly, matching the
    # identifier scheme ``_resolve_targets`` looks for.
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry_id,
        identifiers={(DOMAIN, subentry_id)},
        name="Rue de la Loi 16, 1000 Brussels",
    )

    with patch(
        "custom_components.engie_be.EngieBeApiClient.async_refresh_token",
        side_effect=EngieBeApiClientAuthenticationError("token expired"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is not ConfigEntryState.LOADED
    # The entry kept a live-looking client despite never loading, which is
    # exactly the trap this guard exists for.
    assert getattr(entry.runtime_data, "client", None) is not None

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": [device.id]},
            blocking=True,
        )
    assert err.value.translation_key == "service_entry_reloading"


async def test_import_history_creates_repairs_issue_on_failure(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A failing target raises HomeAssistantError and creates a Repairs issue."""
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    device_registry = dr.async_get(hass)
    ban_device = device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert ban_device is not None

    with (
        patch(
            "custom_components.engie_be.async_import_usage_history",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": [ban_device.id]},
            blocking=True,
        )

    assert exc_info.value.translation_key == "import_history_failed"
    issue_registry = ir.async_get(hass)
    issue = issue_registry.async_get_issue(
        DOMAIN, f"service_import_failed_{subentry_id}"
    )
    assert issue is not None
    assert issue.translation_key == "service_import_failed"


async def test_import_history_validation_error_raises_without_repairs_or_traceback(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    A user-input error propagates clean, with no Repairs card and no traceback.

    The window check (backwards or empty explicit window) raises
    ``ServiceValidationError`` from inside the orchestrator. That is a
    form-level input error, not an import failure, so the handler must
    re-raise it as-is rather than log an exception traceback and create a
    service_import_failed Repairs card.
    """
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    device_registry = dr.async_get(hass)
    ban_device = device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert ban_device is not None

    validation_error = ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="import_window_empty",
        translation_placeholders={"start": "2026-06-10", "end": "2026-06-02"},
    )

    with (
        patch(
            "custom_components.engie_be.async_import_usage_history",
            side_effect=validation_error,
        ),
        patch("custom_components.engie_be.LOGGER") as mock_logger,
        pytest.raises(ServiceValidationError) as exc_info,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": [ban_device.id]},
            blocking=True,
        )

    # The specific validation message survives, not the generic aggregate.
    assert exc_info.value.translation_key == "import_window_empty"
    # No Repairs card for a form-level input error.
    issue_registry = ir.async_get(hass)
    assert (
        issue_registry.async_get_issue(DOMAIN, f"service_import_failed_{subentry_id}")
        is None
    )
    # No ERROR-level traceback logged for a user typo.
    assert mock_logger.exception.call_count == 0


async def test_import_history_clears_repairs_issue_on_success(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A prior Repairs issue is cleared once the target succeeds."""
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    device_registry = dr.async_get(hass)
    ban_device = device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert ban_device is not None

    issue_id = f"service_import_failed_{subentry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="service_import_failed",
        translation_placeholders={"title": "stale issue"},
    )
    issue_registry = ir.async_get(hass)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    with patch(
        "custom_components.engie_be.async_import_usage_history",
        new=AsyncMock(return_value=42),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": [ban_device.id]},
            blocking=True,
        )

    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_import_history_auth_failure_creates_issue(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """An authentication rejection also creates the Repairs issue."""
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    device_registry = dr.async_get(hass)
    ban_device = device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert ban_device is not None

    with (
        patch(
            "custom_components.engie_be.async_import_usage_history",
            side_effect=EngieBeApiClientAuthenticationError("expired"),
        ),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": [ban_device.id]},
            blocking=True,
        )

    issue_registry = ir.async_get(hass)
    issue = issue_registry.async_get_issue(
        DOMAIN, f"service_import_failed_{subentry_id}"
    )
    assert issue is not None


async def test_import_history_partial_failure_processes_all_targets_and_raises(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """One BAN failing does not short-circuit the other, and only it gets an issue."""
    entry = await _setup_two_ban_entry(hass)
    subentry_ids = list(entry.subentries)
    device_registry = dr.async_get(hass)
    ban_devices = [
        device_registry.async_get_device(identifiers={(DOMAIN, sid)})
        for sid in subentry_ids
    ]
    assert all(d is not None for d in ban_devices)
    device_ids = [d.id for d in ban_devices]

    call_count = 0

    async def _fake_import(*_args: object, **_kwargs: object) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        return 42

    with (
        patch(
            "custom_components.engie_be.async_import_usage_history",
            side_effect=_fake_import,
        ) as mock_import,
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": device_ids},
            blocking=True,
        )

    assert mock_import.await_count == 2
    issue_registry = ir.async_get(hass)
    issues = [
        issue_registry.async_get_issue(DOMAIN, f"service_import_failed_{sid}")
        for sid in subentry_ids
    ]
    present = [issue for issue in issues if issue is not None]
    assert len(present) == 1


async def test_import_history_cancelled_error_reraised_without_issue(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A CancelledError propagates as-is, without creating a Repairs issue."""
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    device_registry = dr.async_get(hass)
    ban_device = device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert ban_device is not None

    with (
        patch(
            "custom_components.engie_be.async_import_usage_history",
            side_effect=asyncio.CancelledError(),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": [ban_device.id]},
            blocking=True,
        )

    issue_registry = ir.async_get(hass)
    issue = issue_registry.async_get_issue(
        DOMAIN, f"service_import_failed_{subentry_id}"
    )
    assert issue is None


async def test_import_history_all_targets_fail_with_mixed_exceptions(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Two failing targets of different exception types each get their own issue.

    Regression test for the aggregate path being previously only ever
    exercised with exactly one failing target: target 1 fails with the
    warning-level auth branch, target 2 with the exception-level generic
    branch, and both must independently raise an issue and be reflected
    in the aggregate error.
    """
    entry = await _setup_two_ban_entry(hass)
    subentry_ids = list(entry.subentries)
    device_registry = dr.async_get(hass)
    ban_devices = [
        device_registry.async_get_device(identifiers={(DOMAIN, sid)})
        for sid in subentry_ids
    ]
    assert all(d is not None for d in ban_devices)
    device_ids = [d.id for d in ban_devices]

    call_count = 0

    async def _fake_import(*_args: object, **_kwargs: object) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise EngieBeApiClientAuthenticationError("expired")
        raise RuntimeError("boom")

    with (
        patch(
            "custom_components.engie_be.async_import_usage_history",
            side_effect=_fake_import,
        ),
        patch("custom_components.engie_be.LOGGER") as mock_logger,
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": device_ids},
            blocking=True,
        )

    error_message = exc_info.value.translation_placeholders["error"]
    assert "2 of 2" in error_message
    assert "***0001" in error_message
    assert "***0002" in error_message

    issue_registry = ir.async_get(hass)
    issues = [
        issue_registry.async_get_issue(DOMAIN, f"service_import_failed_{sid}")
        for sid in subentry_ids
    ]
    assert all(issue is not None for issue in issues)

    assert mock_logger.warning.call_count == 1
    assert mock_logger.exception.call_count == 1


async def test_import_history_success_clears_setup_time_issue_too(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    A successful service call also clears a prior setup-time backfill issue.

    ``setup_import_failed_{id}``'s own text tells the user to retry via
    this exact service action - a success here must clear it too, not
    just this call path's own ``service_import_failed_{id}``.
    """
    entry = await _setup_entry(hass)
    subentry_id = next(iter(entry.subentries))
    device_registry = dr.async_get(hass)
    ban_device = device_registry.async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert ban_device is not None

    setup_issue_id = f"setup_import_failed_{subentry_id}"
    service_issue_id = f"service_import_failed_{subentry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        setup_issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="setup_import_failed",
        translation_placeholders={"title": "stale setup-time issue"},
    )
    issue_registry = ir.async_get(hass)
    assert issue_registry.async_get_issue(DOMAIN, setup_issue_id) is not None

    with patch(
        "custom_components.engie_be.async_import_usage_history",
        new=AsyncMock(return_value=42),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_HISTORY,
            {"device_id": [ban_device.id]},
            blocking=True,
        )

    assert issue_registry.async_get_issue(DOMAIN, setup_issue_id) is None
    assert issue_registry.async_get_issue(DOMAIN, service_issue_id) is None
