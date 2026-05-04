"""Tests for the ENGIE Belgium DataUpdateCoordinator."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigSubentryData
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
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CLIENT_ID,
    CONF_CONSUMPTION_ADDRESS,
    CONF_CUSTOMER_NUMBER,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_CLIENT_ID,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
)
from custom_components.engie_be.coordinator import EngieBeDataUpdateCoordinator
from custom_components.engie_be.data import EngieBeData

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "prices_sample.json"
_PEAKS_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "peaks_2026_04.json"


def _build_entry(
    hass: HomeAssistant,
    *,
    options: dict[str, object] | None = None,
    customer_number: str = "000000000000",
) -> MockConfigEntry:
    """Build a v3 MockConfigEntry with one customer-account subentry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        title="user@example.com",
        unique_id="user_example_com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "stored-access",
            CONF_REFRESH_TOKEN: "stored-refresh",
        },
        options=options if options is not None else {"update_interval": 60},
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
                title="placeholder",
                unique_id=customer_number,
                data={
                    CONF_CUSTOMER_NUMBER: customer_number,
                    # Provide all relations-backfillable keys so the
                    # coordinator's one-shot backfill stays disabled and
                    # we don't need to mock the relations endpoint.
                    CONF_BUSINESS_AGREEMENT_NUMBER: "B-0001",
                    CONF_PREMISES_NUMBER: "P-0001",
                    CONF_CONSUMPTION_ADDRESS: "Test 1, 1000 Brussels",
                },
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


def _only_subentry(entry: MockConfigEntry) -> ConfigSubentry:
    """Return the single customer-account subentry on the test entry."""
    return next(iter(entry.subentries.values()))


def _attach_runtime(entry: MockConfigEntry, client: MagicMock) -> None:
    """Attach an EngieBeData runtime stub with the given mocked client."""
    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=MagicMock(),
        subentry_data={},
        authenticated=True,
        last_options=dict(entry.options),
    )


async def test_async_update_data_returns_payload_on_success(
    hass: HomeAssistant,
) -> None:
    """A successful API call returns the payload and stamps last_successful_fetch."""
    entry = _build_entry(hass)
    payload = json.loads(_FIXTURE_PATH.read_text())
    peaks_payload = json.loads(_PEAKS_FIXTURE_PATH.read_text())

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=payload)
    client.async_get_monthly_peaks = AsyncMock(return_value=peaks_payload)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=_only_subentry(entry),
    )
    result = await coordinator._async_update_data()

    # The prices payload is returned with the peaks wrapper merged under "peaks".
    assert result["items"] == payload["items"]
    assert result["peaks"]["data"] == peaks_payload
    assert result["peaks"]["is_fallback"] is False
    assert coordinator.last_successful_fetch is not None
    client.async_get_prices.assert_awaited_once_with("000000000000")
    client.async_get_monthly_peaks.assert_awaited_once()


async def test_async_update_data_raises_config_entry_auth_failed_on_auth_error(
    hass: HomeAssistant,
) -> None:
    """Authentication errors must surface as ConfigEntryAuthFailed for reauth."""
    entry = _build_entry(hass)

    client = MagicMock()
    original = EngieBeApiClientAuthenticationError("token rejected")
    client.async_get_prices = AsyncMock(side_effect=original)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=_only_subentry(entry),
    )

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

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=_only_subentry(entry),
    )

    with pytest.raises(UpdateFailed) as exc_info:
        await coordinator._async_update_data()

    assert exc_info.value.__cause__ is original
    assert coordinator.last_successful_fetch is None


# ---------------------------------------------------------------------------
# update_interval honoring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "expected_minutes"),
    [
        ({"update_interval": 15}, 15),
        ({"update_interval": 240}, 240),
        ({"update_interval": 60}, 60),
        ({}, DEFAULT_UPDATE_INTERVAL_MINUTES),
    ],
)
def test_coordinator_uses_options_update_interval(
    hass: HomeAssistant,
    options: dict[str, int],
    expected_minutes: int,
) -> None:
    """Coordinator's update_interval must reflect the options (or default if absent)."""
    entry = _build_entry(hass, options=options)
    _attach_runtime(entry, MagicMock())

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=_only_subentry(entry),
    )

    assert coordinator.update_interval == timedelta(minutes=expected_minutes)


def test_coordinator_uses_default_when_unrelated_option_set(
    hass: HomeAssistant,
) -> None:
    """If only unrelated options exist, the default interval applies."""
    entry = _build_entry(hass, options={"some_other_option": "value"})
    _attach_runtime(entry, MagicMock())

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=_only_subentry(entry),
    )

    assert coordinator.update_interval == timedelta(
        minutes=DEFAULT_UPDATE_INTERVAL_MINUTES,
    )


def test_coordinator_uses_constant_for_default(hass: HomeAssistant) -> None:
    """Sanity: the documented DEFAULT_UPDATE_INTERVAL_MINUTES is what the code uses."""
    entry = _build_entry(hass, options={})
    _attach_runtime(entry, MagicMock())

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=_only_subentry(entry),
    )

    # If someone bumps the constant, the coordinator must follow.
    assert (
        coordinator.update_interval.total_seconds()
        == DEFAULT_UPDATE_INTERVAL_MINUTES * 60
    )
    # Confirm the option key referenced by the coordinator matches the constant.
    assert CONF_UPDATE_INTERVAL == "update_interval"
