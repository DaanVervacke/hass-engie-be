"""Tests for ENGIE Belgium async_setup_entry and the periodic refresh callback."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be import (
    _persist_tokens,
    async_setup_entry,
)
from custom_components.engie_be.api import (
    EngieBeApiClientAuthenticationError,
)
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CUSTOMER_NUMBER,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    DOMAIN,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant


def _build_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a MockConfigEntry with stored credentials and tokens."""
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


def _make_client(
    *,
    refresh_return: tuple[str, str] = ("new-access", "new-refresh"),
    refresh_side_effect: Exception | None = None,
    prices_return: dict[str, Any] | None = None,
    service_point_return: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a MagicMock EngieBeApiClient with the given async return values."""
    client = MagicMock()
    if refresh_side_effect is not None:
        client.async_refresh_token = AsyncMock(side_effect=refresh_side_effect)
    else:
        client.async_refresh_token = AsyncMock(return_value=refresh_return)
    client.async_get_prices = AsyncMock(return_value=prices_return or {"items": []})
    client.async_get_service_point = AsyncMock(
        return_value=service_point_return or {"division": "ELECTRICITY"},
    )
    return client


async def test_setup_entry_persists_refreshed_tokens(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Successful setup persists fresh tokens and marks runtime as authenticated."""
    entry = _build_entry(hass)
    client = _make_client(refresh_return=("fresh-access", "fresh-refresh"))

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await async_setup_entry(hass, entry)
        await hass.async_block_till_done()

    assert ok is True
    assert entry.data[CONF_ACCESS_TOKEN] == "fresh-access"
    assert entry.data[CONF_REFRESH_TOKEN] == "fresh-refresh"
    assert entry.runtime_data.authenticated is True
    # Old credentials must be untouched
    assert entry.data[CONF_USERNAME] == "user@example.com"
    assert entry.data[CONF_PASSWORD] == "hunter2"


async def test_setup_entry_raises_config_entry_auth_failed_on_initial_refresh(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A failing initial token refresh must raise ConfigEntryAuthFailed."""
    entry = _build_entry(hass)
    client = _make_client(
        refresh_side_effect=EngieBeApiClientAuthenticationError("expired"),
    )

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, entry)


async def test_periodic_refresh_callback_starts_reauth_on_auth_error(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    The 60s refresh callback must trigger reauth when the API rejects the token.

    Vector B in the reauth design: the path that fires when a long-running
    integration sees its refresh token invalidated mid-session. We capture the
    callback registered via async_track_time_interval, then invoke it directly
    after re-arming the mocked client to raise an auth error.
    """
    entry = _build_entry(hass)
    client = _make_client()

    captured: list[Callable[[object], Any]] = []

    def _capture_callback(
        _hass: HomeAssistant,
        callback: Callable[[object], Any],
        _interval: object,
    ) -> Callable[[], None]:
        captured.append(callback)
        return MagicMock()

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.engie_be.async_track_time_interval",
            side_effect=_capture_callback,
        ),
    ):
        await async_setup_entry(hass, entry)
        await hass.async_block_till_done()

    assert len(captured) == 1, "Expected exactly one time-interval callback"
    refresh_callback = captured[0]

    # Sanity: setup completed authenticated
    assert entry.runtime_data.authenticated is True

    # Re-arm the client to reject the refresh, then trigger the callback
    client.async_refresh_token = AsyncMock(
        side_effect=EngieBeApiClientAuthenticationError("revoked"),
    )
    with patch.object(entry, "async_start_reauth") as start_reauth:
        await refresh_callback(None)

    assert entry.runtime_data.authenticated is False
    start_reauth.assert_called_once_with(hass)


def test_persist_tokens_writes_only_token_fields(
    hass: HomeAssistant,
) -> None:
    """_persist_tokens updates only the token fields, leaving credentials intact."""
    entry = _build_entry(hass)

    _persist_tokens(hass, entry, "rotated-access", "rotated-refresh")

    assert entry.data[CONF_ACCESS_TOKEN] == "rotated-access"
    assert entry.data[CONF_REFRESH_TOKEN] == "rotated-refresh"
    assert entry.data[CONF_USERNAME] == "user@example.com"
    assert entry.data[CONF_PASSWORD] == "hunter2"
    assert entry.data[CONF_CUSTOMER_NUMBER] == "000000000000"
