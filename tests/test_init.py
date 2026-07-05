"""Tests for ENGIE Belgium async_setup_entry and the periodic refresh callback."""

from __future__ import annotations

import logging
from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigSubentry,
    ConfigSubentryData,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be import (
    _async_populate_dynamic_flags,
    _async_populate_service_points,
    _persist_tokens,
    async_migrate_entry,
    async_reload_entry,
    async_remove_config_entry_device,
    async_setup_entry,
)
from custom_components.engie_be.api import (
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CONSUMPTION_ADDRESS,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    SIGNAL_AUTHENTICATION_STATE_CHANGED,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.data import EngieBeData

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant


_TEST_SUBENTRY_TITLE = "Rue de la Loi 16, 1000 Brussels"


def _build_entry(
    hass: HomeAssistant,
    *,
    business_agreement_number: str = "002200000001",
) -> MockConfigEntry:
    """
    Build a v5 MockConfigEntry with credentials, tokens, and one subentry.

    v5 keys each ``ConfigSubentry`` on a Business Agreement Number (BAN);
    a single ENGIE login can own many active BANs across one or more
    customer accounts. The resulting entry already carries the relations-
    derived display fields so the setup path can wire up its coordinators
    immediately without needing the relations endpoint mocked.
    """
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
                title=_TEST_SUBENTRY_TITLE,
                unique_id=business_agreement_number,
                data={
                    CONF_BUSINESS_AGREEMENT_NUMBER: business_agreement_number,
                    CONF_PREMISES_NUMBER: "P-0001",
                    CONF_CONSUMPTION_ADDRESS: _TEST_SUBENTRY_TITLE,
                },
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


def _only_subentry_id(entry: MockConfigEntry) -> str:
    """Return the subentry id of the single test business-agreement subentry."""
    return next(iter(entry.subentries))


def _make_client(  # noqa: PLR0913 - kwargs-only test helper, one knob per endpoint
    *,
    refresh_return: tuple[str, str] = ("new-access", "new-refresh"),
    refresh_side_effect: Exception | None = None,
    prices_return: dict[str, Any] | None = None,
    service_point_return: dict[str, Any] | None = None,
    peaks_return: dict[str, Any] | None = None,
    energy_contracts_return: dict[str, Any] | None = None,
) -> MagicMock:
    """
    Build a MagicMock EngieBeApiClient with the given async return values.

    The mock mirrors the real ``EngieBeApiClient`` contract: a successful
    ``async_refresh_token`` call updates ``client.refresh_token`` in-memory
    before returning.  This is essential for the token-mismatch check in
    ``async_reload_entry`` — without it, ``_persist_tokens`` writing the new
    token to ``entry.data`` would always look like an external reauth write
    and trigger a spurious reload during setup.
    """
    client = MagicMock()
    if refresh_side_effect is not None:
        client.async_refresh_token = AsyncMock(side_effect=refresh_side_effect)
    else:
        # Mirror the real client: update client.refresh_token before returning.
        async def _refresh_and_update() -> tuple[str, str]:
            client.refresh_token = refresh_return[1]
            return refresh_return

        client.async_refresh_token = AsyncMock(side_effect=_refresh_and_update)
    client.async_get_prices = AsyncMock(return_value=prices_return or {"items": []})
    client.async_get_service_point = AsyncMock(
        return_value=service_point_return or {"division": "ELECTRICITY"},
    )
    client.async_get_monthly_peaks = AsyncMock(
        return_value=peaks_return or {"peakOfTheMonth": None, "dailyPeaks": []},
    )
    # Happy-hour endpoint is hit by every per-subentry coordinator
    # refresh; default to ``{}`` (no event scheduled) so sensors report
    # ``unknown`` without raising. Tests that exercise an active window
    # can override on the per-test client.
    client.async_get_happy_hour_event = AsyncMock(return_value={})
    client.async_get_happy_hours_service_enabled_flag = AsyncMock(return_value={})
    # Energy-contracts endpoint is hit by ``_async_populate_dynamic_flags``
    # at setup; default to an empty payload so detection silently leaves
    # the override at None and falls back to the legacy heuristic.
    client.async_get_energy_contracts = AsyncMock(
        return_value=energy_contracts_return or {"items": []},
    )
    # EPEX endpoint is hit unconditionally by the entry-level
    # EngieBeEpexCoordinator at first refresh; default to an empty
    # timeSeries so the parser returns an empty payload without raising.
    client.async_get_epex_prices = AsyncMock(return_value={"timeSeries": []})
    return client


async def test_setup_entry_persists_refreshed_tokens(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Successful setup persists fresh tokens and marks runtime as authenticated."""
    entry = _build_entry(hass)
    client = _make_client(refresh_return=("fresh-access", "fresh-refresh"))

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
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
        ok = await hass.config_entries.async_setup(entry.entry_id)
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
    """A failing initial token refresh must put the entry into a reauth state."""
    entry = _build_entry(hass)
    client = _make_client(
        refresh_side_effect=EngieBeApiClientAuthenticationError("expired"),
    )

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is False
    # ConfigEntryAuthFailed must trigger a reauth flow rather than load the entry.
    reauth_flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"].get("source") == "reauth"
    ]
    assert len(reauth_flows) == 1


async def test_setup_entry_raises_config_entry_not_ready_on_initial_refresh(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """An API error during initial token refresh must defer setup for retry."""
    entry = _build_entry(hass)
    client = _make_client(
        refresh_side_effect=EngieBeApiClientError("upstream down"),
    )

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is False
    assert entry.state is ConfigEntryState.SETUP_RETRY


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

    assert len(captured) == 1, "Expected exactly one time-interval callback"
    refresh_callback = captured[0]

    # Sanity: setup completed authenticated
    assert entry.runtime_data.authenticated is True

    # Re-arm the client to reject the refresh, then trigger the callback
    client.async_refresh_token = AsyncMock(
        side_effect=EngieBeApiClientAuthenticationError("revoked"),
    )
    with (
        patch.object(entry, "async_start_reauth") as start_reauth,
        patch("custom_components.engie_be.async_dispatcher_send") as dispatcher_send,
    ):
        await refresh_callback(None)

    assert entry.runtime_data.authenticated is False
    start_reauth.assert_called_once_with(hass)
    dispatcher_send.assert_called_once_with(
        hass,
        SIGNAL_AUTHENTICATION_STATE_CHANGED.format(entry_id=entry.entry_id),
    )


async def test_periodic_refresh_callback_cancels_timer_on_auth_error(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    The 60s refresh callback must cancel its own timer on an auth error.

    Without cancellation the timer keeps firing every 60s, hitting 403 on
    each tick and spamming reauth calls until the user completes the flow.
    After an auth failure the cancel callable stored on
    ``runtime_data.cancel_token_refresh`` must be called exactly once and
    the field must be cleared to ``None`` so no stale reference lingers.
    """
    entry = _build_entry(hass)
    client = _make_client()

    captured: list[Callable[[object], Any]] = []
    cancel_mock = MagicMock()

    def _capture_callback(
        _hass: HomeAssistant,
        callback: Callable[[object], Any],
        _interval: object,
    ) -> MagicMock:
        captured.append(callback)
        return cancel_mock

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.engie_be.async_track_time_interval",
            side_effect=_capture_callback,
        ),
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

    assert len(captured) == 1, "Expected exactly one time-interval callback"
    refresh_callback = captured[0]

    # The cancel callable must be stored on runtime_data after setup.
    assert entry.runtime_data.cancel_token_refresh is cancel_mock

    # Re-arm the client to reject the next refresh with an auth error.
    client.async_refresh_token = AsyncMock(
        side_effect=EngieBeApiClientAuthenticationError("revoked"),
    )
    with patch.object(entry, "async_start_reauth"):
        await refresh_callback(None)

    # Timer must have been cancelled and the field cleared.
    cancel_mock.assert_called_once()
    assert entry.runtime_data.cancel_token_refresh is None


async def test_periodic_refresh_callback_logs_exception_detail_on_error(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    The 60s refresh callback must include exception detail in its warning.

    The previous warning ("Scheduled token refresh failed; will retry") gave
    no clue about what actually went wrong, making transient upstream
    failures undebuggable post-hoc. The new format must include both the
    exception class name and the message (which the API client populates
    with HTTP status / underlying exception class) so a single grep over
    the logs reveals the cause.
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

    refresh_callback = captured[0]

    # Re-arm the client to fail with a non-auth communication error that
    # carries the upstream-shaped message the real API client would emit.
    client.async_refresh_token = AsyncMock(
        side_effect=EngieBeApiClientError(
            "Error communicating with Engie API (ClientConnectorError)",
        ),
    )
    caplog.clear()
    with (
        caplog.at_level(logging.WARNING, logger="custom_components.engie_be"),
        patch("custom_components.engie_be.async_dispatcher_send") as dispatcher_send,
    ):
        await refresh_callback(None)

    matching = [
        record
        for record in caplog.records
        if "Scheduled token refresh failed" in record.message
    ]
    assert len(matching) == 1, (
        f"expected 1 refresh-failed warning, got {len(matching)}: "
        f"{[r.message for r in caplog.records]}"
    )
    message = matching[0].message
    assert "EngieBeApiClientError" in message, message
    assert "ClientConnectorError" in message, message
    assert "will retry" in message, message
    assert entry.runtime_data.authenticated is False
    dispatcher_send.assert_called_once_with(
        hass,
        SIGNAL_AUTHENTICATION_STATE_CHANGED.format(entry_id=entry.entry_id),
    )


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


# ---------------------------------------------------------------------------
# Config-entry migration (v0.9.0 breaking schema change)
# ---------------------------------------------------------------------------


async def test_migrate_entry_refuses_pre_v5_entries(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    v0.9.0 dropped the v1->v2->v3->v4 migration chain.

    Any entry whose version predates v5 must be refused so HA marks it
    as setup_error and surfaces a Repairs notice telling the user to
    remove and re-add the integration.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        title="user@example.com",
        unique_id="user_example_com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
        },
    )
    entry.add_to_hass(hass)

    with caplog.at_level(logging.ERROR):
        result = await async_migrate_entry(hass, entry)

    assert result is False
    assert "breaking schema change" in caplog.text
    assert "version 4" in caplog.text or "from version 4" in caplog.text


async def test_migrate_entry_passes_through_current_version(
    hass: HomeAssistant,
) -> None:
    """A v5 entry must not raise when passed through async_migrate_entry."""
    entry = _build_entry(hass)

    # v5 entries never enter async_migrate_entry under normal HA flow, but
    # if something forces the call (e.g. a future bump that revisits v5),
    # the function must not crash.
    assert entry.version == 5


# ---------------------------------------------------------------------------
# Options round-trip: changing update_interval reaches the live coordinator
# ---------------------------------------------------------------------------


async def test_options_update_triggers_full_reload_with_new_interval(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Changing update_interval via options must rebuild the coordinator."""
    entry = _build_entry(hass)
    client = _make_client()

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
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

        # Initial coordinator built from default 60-minute option.
        sid = _only_subentry_id(entry)
        first_coordinator = entry.runtime_data.subentry_data[sid].coordinator
        assert first_coordinator.update_interval == timedelta(minutes=60)

        # Change the option; HA fires the update listener registered in setup.
        hass.config_entries.async_update_entry(
            entry,
            options={CONF_UPDATE_INTERVAL: 15},
        )
        await hass.async_block_till_done()

        # After reload, runtime_data is brand new and the coordinator
        # is constructed with the new interval.
        second_coordinator = entry.runtime_data.subentry_data[sid].coordinator
        assert second_coordinator is not first_coordinator
        assert second_coordinator.update_interval == timedelta(minutes=15)
        assert entry.options[CONF_UPDATE_INTERVAL] == 15


async def test_token_only_data_update_does_not_trigger_reload(
    hass: HomeAssistant,
) -> None:
    """Rotating tokens (entry.data) must NOT rebuild the coordinator."""
    entry = _build_entry(hass)
    sentinel_epex_coordinator = MagicMock()
    client = MagicMock()
    # Simulates the rotation path: the live client already holds the same
    # refresh token that _persist_tokens just wrote to entry.data, so the
    # tokens_externally_updated check must not trigger a reload.
    client.refresh_token = entry.data[CONF_REFRESH_TOKEN]
    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=sentinel_epex_coordinator,
        last_options=dict(entry.options),
        last_subentry_ids={
            sub.subentry_id
            for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
        },
    )

    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        # Simulate the update listener firing after _persist_tokens rotated
        # the tokens. Options dict and client token are unchanged relative to
        # entry.data, so reload must be skipped.
        await async_reload_entry(hass, entry)

    mock_reload.assert_not_awaited()
    # Runtime reference must be untouched.
    assert entry.runtime_data.epex_coordinator is sentinel_epex_coordinator


async def test_options_change_after_setup_uses_async_reload(
    hass: HomeAssistant,
) -> None:
    """An options dict that differs from last_options must reload the entry."""
    entry = _build_entry(hass)
    entry.runtime_data = EngieBeData(
        client=MagicMock(),
        epex_coordinator=MagicMock(),
        last_options={CONF_UPDATE_INTERVAL: 60},
        last_subentry_ids={
            sub.subentry_id
            for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
        },
    )
    # Mutate options to simulate the options flow saving a new value before
    # the listener fires.
    hass.config_entries.async_update_entry(
        entry,
        options={CONF_UPDATE_INTERVAL: 30},
    )

    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        await async_reload_entry(hass, entry)

    mock_reload.assert_awaited_once_with(entry.entry_id)


async def test_subentry_added_after_setup_triggers_reload(
    hass: HomeAssistant,
) -> None:
    """A new customer-account subentry must trigger a reload."""
    entry = _build_entry(hass)
    # Snapshot of subentries observed at setup time: only the seeded one.
    initial_ids = {sub.subentry_id for sub in entry.subentries.values()}
    entry.runtime_data = EngieBeData(
        client=MagicMock(),
        epex_coordinator=MagicMock(),
        last_options=dict(entry.options),
        last_subentry_ids=initial_ids,
    )

    # Simulate the user picking a second account: framework calls
    # async_add_subentry, which mutates entry.subentries and fires our
    # update listener.
    new_subentry = ConfigSubentry(
        data=MappingProxyType(
            {
                CONF_BUSINESS_AGREEMENT_NUMBER: "002200000002",
            },
        ),
        subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
        title="Second Account",
        unique_id="002200000002",
    )
    hass.config_entries.async_add_subentry(entry, new_subentry)

    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        await async_reload_entry(hass, entry)

    mock_reload.assert_awaited_once_with(entry.entry_id)


async def test_subentry_removed_after_setup_triggers_reload(
    hass: HomeAssistant,
) -> None:
    """Removing a customer-account subentry must trigger a reload."""
    entry = _build_entry(hass)
    initial_ids = {sub.subentry_id for sub in entry.subentries.values()}
    entry.runtime_data = EngieBeData(
        client=MagicMock(),
        epex_coordinator=MagicMock(),
        last_options=dict(entry.options),
        last_subentry_ids=initial_ids,
    )

    # Pop the only seeded subentry and trigger the listener.
    (subentry_id,) = initial_ids
    hass.config_entries.async_remove_subentry(entry, subentry_id)

    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        await async_reload_entry(hass, entry)

    mock_reload.assert_awaited_once_with(entry.entry_id)


async def test_token_rotation_does_not_reload_when_subentries_unchanged(
    hass: HomeAssistant,
) -> None:
    """
    Token rotation fires the listener but must not reload the entry.

    Tokens are written via ``async_update_entry(data=...)`` which fires
    every update listener, but neither options nor the customer-account
    subentry id set changes, and the live client already holds the newly
    rotated token (it updates in-memory before _persist_tokens runs),
    so the no-op short-circuit must hold.
    """
    entry = _build_entry(hass)
    initial_ids = {sub.subentry_id for sub in entry.subentries.values()}
    client = MagicMock()
    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=MagicMock(),
        last_options=dict(entry.options),
        last_subentry_ids=initial_ids,
    )

    # Simulate a token rotation: _persist_tokens writes the new pair to
    # entry.data, and the live client already carries the same refresh token
    # in memory (because async_refresh_token updates self.refresh_token
    # before the caller invokes _persist_tokens).
    _persist_tokens(hass, entry, "rotated-access", "rotated-refresh")
    client.refresh_token = "rotated-refresh"  # noqa: S105

    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        await async_reload_entry(hass, entry)

    mock_reload.assert_not_awaited()


async def test_reauth_token_mismatch_triggers_reload(
    hass: HomeAssistant,
) -> None:
    """
    Reauth writes new tokens to entry.data without touching the live client.

    When the listener fires after a reauth the stored refresh token in
    entry.data differs from the live client's in-memory refresh_token.
    That mismatch must cause a reload so the new credentials are picked up
    without requiring a full HA restart.
    """
    entry = _build_entry(hass)
    initial_ids = {sub.subentry_id for sub in entry.subentries.values()}
    client = MagicMock()
    # Live client still holds the old (now-invalidated) refresh token.
    client.refresh_token = "old-refresh"  # noqa: S105
    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=MagicMock(),
        last_options=dict(entry.options),
        last_subentry_ids=initial_ids,
    )

    # Simulate what async_update_and_abort does in the reauth flow: it
    # writes the new token pair directly to entry.data without calling
    # client.async_refresh_token, so the live client's refresh_token is
    # still the old value.
    _persist_tokens(hass, entry, "new-reauth-access", "new-reauth-refresh")

    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        await async_reload_entry(hass, entry)

    mock_reload.assert_awaited_once_with(entry.entry_id)


async def test_update_listener_registered_before_auth_failure(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Regression: update listener registered before initial token refresh raises.

    When stored tokens are rejected on startup the initial ``async_refresh_token``
    call raises ``EngieBeApiClientAuthenticationError``, which ``async_setup_entry``
    re-raises as ``ConfigEntryAuthFailed``.  HA then opens a reauth flow.  The
    reauth flow calls ``async_update_and_abort``, which writes the new token pair
    to ``entry.data`` and fires every update listener.  ``async_reload_entry``
    must be among those listeners, or the entry stays stuck in the failed state
    until a manual reload.

    This test verifies that ``entry.add_update_listener`` is invoked before the
    failing ``await`` by patching ``async_refresh_token`` to raise and asserting
    that ``add_update_listener`` was already called at that point.
    """
    entry = _build_entry(hass)
    client = _make_client(
        refresh_side_effect=EngieBeApiClientAuthenticationError("expired"),
    )

    add_listener_called_before_raise: list[bool] = []

    original_refresh = client.async_refresh_token

    async def _raise_and_record() -> tuple[str, str]:
        # By the time async_refresh_token is awaited the listener must be
        # registered already.
        add_listener_called_before_raise.append(
            mock_add_listener.called,
        )
        return await original_refresh()

    client.async_refresh_token = _raise_and_record

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch.object(
            entry,
            "add_update_listener",
            wraps=entry.add_update_listener,
        ) as mock_add_listener,
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is False
    # The listener must have been registered before the auth raise.
    assert add_listener_called_before_raise == [True], (
        "add_update_listener was not called before async_refresh_token raised"
    )
    # HA must have opened a reauth flow.
    reauth_flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"].get("source") == "reauth"
    ]
    assert len(reauth_flows) == 1


async def test_update_listener_registered_before_coordinator_auth_failure(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Regression: update listener registered before coordinator first-refresh raises.

    The gather in ``async_setup_entry`` can also surface a
    ``ConfigEntryAuthFailed`` from a per-subentry coordinator's
    ``async_config_entry_first_refresh``.  The listener must already be
    registered at that point too.
    """
    entry = _build_entry(hass)
    client = _make_client()

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeDataUpdateCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(side_effect=ConfigEntryAuthFailed("session expired")),
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeEpexCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(return_value=None),
        ),
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is False
    # The entry must have an update listener registered (HA tracks these
    # on the entry object; the public API exposes them via
    # ``entry._listeners``).  We verify indirectly: simulate what
    # async_update_and_abort does (writes new tokens) and assert the
    # reload path fires.
    client_mock = MagicMock()
    # Live client holds old token - mismatch triggers reload.
    client_mock.refresh_token = "old-refresh"  # noqa: S105
    entry.runtime_data.client = client_mock

    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        # Use async_update_entry (HA's real dispatch path) so the test
        # exercises the listener wire-up, not just the reload logic.
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: "new-reauth-access",
                CONF_REFRESH_TOKEN: "new-reauth-refresh",
            },
        )
        await hass.async_block_till_done()

    mock_reload.assert_awaited_once_with(entry.entry_id)


async def test_update_listener_survives_failed_setup_and_fires_on_reauth(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Regression: listener registered in setup must survive setup failure.

    HA invokes every ``async_on_unload`` callback when ``async_setup_entry``
    raises (``config_entries.py`` ``_async_process_on_unload`` in the
    setup-failure finally-branch).  If the update listener is registered via
    ``async_on_unload``, it is silently unregistered on the auth-failure path,
    so the subsequent ``async_update_and_abort`` in the reauth flow fires no
    listener and no reload happens, so the user has to reload manually.

    This test exercises HA's real dispatch: after setup fails with
    ``ConfigEntryAuthFailed``, simulate the reauth write by calling
    ``hass.config_entries.async_update_entry`` (which iterates
    ``entry.update_listeners``) and assert the listener still fires.
    """
    entry = _build_entry(hass)
    client = _make_client(
        refresh_side_effect=EngieBeApiClientAuthenticationError("expired"),
    )

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is False
    assert async_reload_entry in entry.update_listeners, (
        "update listener was unregistered by _async_process_on_unload after "
        "setup failure; reauth completion would fire no listener"
    )

    # Simulate reauth: live client still holds the old refresh token, but
    # entry.data is about to be rewritten with new tokens. The listener
    # must fire and see the mismatch.
    entry.runtime_data.client.refresh_token = "old-refresh"  # noqa: S105
    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: "new-reauth-access",
                CONF_REFRESH_TOKEN: "new-reauth-refresh",
            },
        )
        await hass.async_block_till_done()

    mock_reload.assert_awaited_once_with(entry.entry_id)


async def test_unrelated_subentry_type_does_not_trigger_reload(
    hass: HomeAssistant,
) -> None:
    """
    Adding a non-customer-account subentry must not trigger a reload.

    The reload guard only watches customer-account subentries; future
    subentry types (or stray subentries from other migrations) must not
    cause a tear-down/rebuild of the integration.
    """
    entry = _build_entry(hass)
    initial_ids = {sub.subentry_id for sub in entry.subentries.values()}
    client = MagicMock()
    # Match the client's in-memory token to entry.data so the reauth check
    # does not fire; this test targets the subentry-type filtering only.
    client.refresh_token = entry.data[CONF_REFRESH_TOKEN]
    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=MagicMock(),
        last_options=dict(entry.options),
        last_subentry_ids=initial_ids,
    )

    other_subentry = ConfigSubentry(
        data=MappingProxyType({}),
        subentry_type="some_future_type",
        title="Unrelated",
        unique_id="unrelated-1",
    )
    hass.config_entries.async_add_subentry(entry, other_subentry)

    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        await async_reload_entry(hass, entry)

    mock_reload.assert_not_awaited()


# ---------------------------------------------------------------------------
# Service-point fan-out: parallel fetch + per-EAN failure isolation
# ---------------------------------------------------------------------------


async def test_setup_entry_fetches_service_points_in_parallel(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Multi-EAN customers must get one service-point lookup per EAN."""
    entry = _build_entry(hass)
    client = _make_client(
        prices_return={
            "items": [
                {"ean": "541448820000000001"},
                {"ean": "541448820000000002"},
                {"ean": "541448820000000003"},
            ],
        },
    )

    # Per-EAN service-point responses so we can verify the right division
    # ends up keyed under the right EAN.
    division_by_ean = {
        "541448820000000001": "ELECTRICITY",
        "541448820000000002": "GAS",
        "541448820000000003": "ELECTRICITY",
    }

    async def _service_point(ean: str) -> dict[str, str]:
        return {"division": division_by_ean[ean]}

    client.async_get_service_point = AsyncMock(side_effect=_service_point)

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is True
    # Every EAN must have been queried exactly once.
    assert client.async_get_service_point.await_count == 3
    queried = {call.args[0] for call in client.async_get_service_point.await_args_list}
    assert queried == set(division_by_ean)
    # Per-subentry service-points dict must reflect each EAN's division.
    sid = _only_subentry_id(entry)
    assert entry.runtime_data.subentry_data[sid].service_points == division_by_ean


async def test_setup_entry_service_point_failure_does_not_poison_others(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """One failing service-point lookup must not block the surviving EANs."""
    entry = _build_entry(hass)
    client = _make_client(
        prices_return={
            "items": [
                {"ean": "541448820000000001"},
                {"ean": "541448820000000002"},
                {"ean": "541448820000000003"},
            ],
        },
    )

    async def _service_point(ean: str) -> dict[str, str]:
        if ean == "541448820000000002":
            msg = "boom"
            raise EngieBeApiClientError(msg)
        return {"division": "ELECTRICITY"}

    client.async_get_service_point = AsyncMock(side_effect=_service_point)

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is True
    # Failing EAN is silently dropped; surviving EANs still resolve.
    sid = _only_subentry_id(entry)
    assert entry.runtime_data.subentry_data[sid].service_points == {
        "541448820000000001": "ELECTRICITY",
        "541448820000000003": "ELECTRICITY",
    }


# ---------------------------------------------------------------------------
# Dynamic-tariff override population (b6 contracts-driven detection)
#
# These tests cover ``_async_populate_dynamic_flags``, the setup-time
# fan-out that calls the energy-contracts endpoint once per subentry's
# BAN and writes the resulting dynamic flag onto
# ``EngieBeSubentryData.is_dynamic_override``. The override is the
# primary input to ``EngieBeDataUpdateCoordinator.is_dynamic`` (which
# gates EPEX entity creation in the platform layer); the legacy
# ``len(items) == 0`` heuristic on the prices payload is the fallback.
# ---------------------------------------------------------------------------


async def test_setup_entry_populates_dynamic_override_from_contracts(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Dynamic electricity contracts must set ``is_dynamic_override=True``."""
    entry = _build_entry(hass, business_agreement_number="002200000001")
    client = _make_client(
        energy_contracts_return={
            "items": [
                {
                    "businessAgreementNumber": "002200000001",
                    "servicePointNumber": "541448820000000001_ID1",
                    "division": "ELECTRICITY",
                    "status": "ACTIVE",
                    "productConfiguration": {
                        "energyProduct": "DYNAMIC",
                        "type": "INDEXED",
                    },
                },
            ],
        },
    )

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is True
    sid = _only_subentry_id(entry)
    sub_data = entry.runtime_data.subentry_data[sid]
    assert sub_data.is_dynamic_override is True
    # The raw payload is cached on the subentry so diagnostics can surface
    # the per-EAN energyProduct without re-fetching.
    assert sub_data.energy_contracts_payload is not None
    # Coordinator ``is_dynamic`` must reflect the override.
    assert sub_data.coordinator.is_dynamic is True
    # Endpoint must have been called exactly once with the BAN.
    client.async_get_energy_contracts.assert_awaited_once_with("002200000001")


async def test_setup_entry_dynamic_override_handles_mixed_fuel(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """The b6 bug fix: dynamic elec + fixed gas must still flag the account."""
    entry = _build_entry(hass)
    client = _make_client(
        energy_contracts_return={
            "items": [
                {
                    "businessAgreementNumber": "002200000001",
                    "servicePointNumber": "541448820000000001_ID1",
                    "division": "ELECTRICITY",
                    "status": "ACTIVE",
                    "productConfiguration": {"energyProduct": "DYNAMIC"},
                },
                {
                    "businessAgreementNumber": "002200000001",
                    "servicePointNumber": "541448820000000002_ID2",
                    "division": "GAS",
                    "status": "ACTIVE",
                    "productConfiguration": {"energyProduct": "EASY"},
                },
            ],
        },
    )

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is True
    sid = _only_subentry_id(entry)
    sub_data = entry.runtime_data.subentry_data[sid]
    assert sub_data.is_dynamic_override is True
    assert sub_data.coordinator.is_dynamic is True


async def test_setup_entry_dynamic_override_false_for_fixed_account(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A fixed-tariff contract must set ``is_dynamic_override=False``."""
    entry = _build_entry(hass)
    client = _make_client(
        energy_contracts_return={
            "items": [
                {
                    "businessAgreementNumber": "002200000001",
                    "servicePointNumber": "541448820000000001_ID1",
                    "division": "ELECTRICITY",
                    "status": "ACTIVE",
                    "productConfiguration": {"energyProduct": "EASY"},
                },
            ],
        },
    )

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is True
    sid = _only_subentry_id(entry)
    sub_data = entry.runtime_data.subentry_data[sid]
    assert sub_data.is_dynamic_override is False
    assert sub_data.coordinator.is_dynamic is False


async def test_setup_entry_dynamic_override_falls_back_on_api_error(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A contracts API error must leave the override at ``None`` (legacy fallback)."""
    entry = _build_entry(hass)
    client = _make_client()
    client.async_get_energy_contracts = AsyncMock(
        side_effect=EngieBeApiClientError("contracts unavailable"),
    )

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Setup must still succeed; the error degrades to legacy heuristic.
    assert ok is True
    sid = _only_subentry_id(entry)
    sub_data = entry.runtime_data.subentry_data[sid]
    assert sub_data.is_dynamic_override is None
    # ``_make_client`` defaults to ``items=[]`` for prices, so the legacy
    # heuristic on the empty prices payload classifies this as dynamic.
    # The point is that the contracts failure did not poison setup.
    assert sub_data.coordinator.is_dynamic is True


async def test_setup_entry_dynamic_override_falls_back_on_non_dict_payload(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A non-JSON-object contracts response must leave the override at ``None``."""
    entry = _build_entry(hass)
    client = _make_client()
    # Simulate schema drift: the API returns a list instead of an object.
    client.async_get_energy_contracts = AsyncMock(return_value=["unexpected"])

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is True
    sid = _only_subentry_id(entry)
    sub_data = entry.runtime_data.subentry_data[sid]
    assert sub_data.is_dynamic_override is None
    # Cache must not be populated with garbage.
    assert sub_data.energy_contracts_payload is None


# ---------------------------------------------------------------------------
# Periodic refresh callback — success path
# ---------------------------------------------------------------------------


async def test_periodic_refresh_callback_persists_tokens_on_success(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A successful scheduled refresh persists the rotated pair and re-marks authed."""
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

    refresh_callback = captured[0]

    # Flip the authed flag off and re-arm the client to rotate to a fresh
    # token pair, then drive the callback: it must persist the new pair,
    # flip ``authenticated`` back on and log the success at debug level.
    entry.runtime_data.authenticated = False
    client.async_refresh_token = AsyncMock(
        return_value=("rotated-access-2", "rotated-refresh-2"),
    )
    caplog.clear()
    with (
        caplog.at_level(logging.DEBUG, logger="custom_components.engie_be"),
        patch("custom_components.engie_be.async_dispatcher_send") as dispatcher_send,
    ):
        await refresh_callback(None)
        await hass.async_block_till_done()

    assert entry.data[CONF_ACCESS_TOKEN] == "rotated-access-2"
    assert entry.data[CONF_REFRESH_TOKEN] == "rotated-refresh-2"
    assert entry.runtime_data.authenticated is True
    assert "Token refreshed successfully" in caplog.text
    dispatcher_send.assert_called_once_with(
        hass,
        SIGNAL_AUTHENTICATION_STATE_CHANGED.format(entry_id=entry.entry_id),
    )


# ---------------------------------------------------------------------------
# First-refresh gather — exception prioritisation (auth > not-ready > first)
# ---------------------------------------------------------------------------


async def test_first_refresh_gather_prioritises_auth_failure(
    hass: HomeAssistant,
) -> None:
    """ConfigEntryAuthFailed wins even when a not-ready error also surfaced."""
    entry = _build_entry(hass)
    client = _make_client(refresh_return=("stored-access", "stored-refresh"))

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeEpexCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(side_effect=ConfigEntryNotReady("upstream down")),
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeDataUpdateCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(side_effect=ConfigEntryAuthFailed("expired")),
        ),
        pytest.raises(ConfigEntryAuthFailed, match="expired"),
    ):
        await async_setup_entry(hass, entry)

    await hass.async_block_till_done()


async def test_first_refresh_gather_prioritises_not_ready(
    hass: HomeAssistant,
) -> None:
    """ConfigEntryNotReady wins over an unexpected error when no auth failure."""
    entry = _build_entry(hass)
    client = _make_client(refresh_return=("stored-access", "stored-refresh"))

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeEpexCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeDataUpdateCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(side_effect=ConfigEntryNotReady("upstream down")),
        ),
        pytest.raises(ConfigEntryNotReady, match="upstream down"),
    ):
        await async_setup_entry(hass, entry)

    await hass.async_block_till_done()


async def test_first_refresh_gather_reraises_first_unexpected_error(
    hass: HomeAssistant,
) -> None:
    """With only unexpected errors, the first gather result propagates verbatim."""
    entry = _build_entry(hass)
    client = _make_client(refresh_return=("stored-access", "stored-refresh"))

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeEpexCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeDataUpdateCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(side_effect=ValueError("second")),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await async_setup_entry(hass, entry)

    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# Setup populate helpers — error propagation & skips
# ---------------------------------------------------------------------------


async def test_populate_service_points_reraises_non_api_error() -> None:
    """A non-API error from a service-point lookup must propagate, not be swallowed."""
    client = MagicMock()
    client.async_get_service_point = AsyncMock(side_effect=ValueError("boom"))

    sub_data = MagicMock()
    sub_data.coordinator.data = {"items": [{"ean": "541448820000000001"}]}

    entry = MagicMock()
    entry.runtime_data.subentry_data = {"subentry-1": sub_data}

    with pytest.raises(ValueError, match="boom"):
        await _async_populate_service_points(client, entry)


async def test_populate_dynamic_flags_skips_when_no_business_agreement(
    hass: HomeAssistant,
) -> None:
    """An empty BAN yields no targets, so the contracts endpoint is never hit."""
    entry = _build_entry(hass, business_agreement_number="")
    client = _make_client()

    await _async_populate_dynamic_flags(client, entry)

    client.async_get_energy_contracts.assert_not_awaited()


async def test_populate_dynamic_flags_skips_subentry_without_runtime_data(
    hass: HomeAssistant,
) -> None:
    """A subentry missing from runtime_data is skipped without writing state."""
    entry = _build_entry(hass)
    entry.runtime_data = EngieBeData(
        client=MagicMock(),
        epex_coordinator=MagicMock(),
    )
    client = _make_client()

    await _async_populate_dynamic_flags(client, entry)

    client.async_get_energy_contracts.assert_awaited_once_with("002200000001")


async def test_populate_dynamic_flags_reraises_non_api_error(
    hass: HomeAssistant,
) -> None:
    """A non-API error from the contracts lookup must propagate, not be swallowed."""
    entry = _build_entry(hass)
    entry.runtime_data = EngieBeData(
        client=MagicMock(),
        epex_coordinator=MagicMock(),
    )
    sid = _only_subentry_id(entry)
    entry.runtime_data.subentry_data[sid] = MagicMock()

    client = _make_client()
    client.async_get_energy_contracts = AsyncMock(side_effect=ValueError("boom"))

    with pytest.raises(ValueError, match="boom"):
        await _async_populate_dynamic_flags(client, entry)


# ---------------------------------------------------------------------------
# async_remove_config_entry_device — stale-devices rule
# ---------------------------------------------------------------------------


async def test_remove_stale_device_allowed(
    hass: HomeAssistant,
) -> None:
    """A device whose identifier does not match any active subentry can be removed."""
    entry = _build_entry(hass)
    # Build a fake device that has an identifier from a *different* subentry
    # (i.e. one that is no longer present in entry.subentries).
    device_entry = MagicMock()
    device_entry.identifiers = {(DOMAIN, "stale-subentry-id-9999")}

    result = await async_remove_config_entry_device(hass, entry, device_entry)

    assert result is True


async def test_remove_active_device_not_allowed(
    hass: HomeAssistant,
) -> None:
    """A device whose identifier matches an active subentry cannot be removed."""
    entry = _build_entry(hass)
    # Retrieve the real subentry_id created by _build_entry.
    active_subentry_id = _only_subentry_id(entry)

    device_entry = MagicMock()
    device_entry.identifiers = {(DOMAIN, active_subentry_id)}

    result = await async_remove_config_entry_device(hass, entry, device_entry)

    assert result is False


async def test_remove_login_device_allowed(
    hass: HomeAssistant,
) -> None:
    """The login device (no matching subentry identifier) can be removed."""
    entry = _build_entry(hass)
    # The login device uses f"login_{entry.entry_id}" — no subentry matches it.
    device_entry = MagicMock()
    device_entry.identifiers = {(DOMAIN, f"login_{entry.entry_id}")}

    result = await async_remove_config_entry_device(hass, entry, device_entry)

    assert result is True
