"""Tests for ENGIE Belgium async_setup_entry and the periodic refresh callback."""

from __future__ import annotations

import logging
from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigSubentry, ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be import (
    _async_migrate_entity_id_slugs,
    _async_migrate_legacy_subentry_unique_ids,
    _persist_tokens,
    async_migrate_entry,
    async_reload_entry,
)
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
    DOMAIN,
    SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
)
from custom_components.engie_be.data import EngieBeData

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest
    from homeassistant.core import HomeAssistant


_TEST_SUBENTRY_TITLE = "Rue de la Loi 16, 1000 Brussels"


def _build_entry(
    hass: HomeAssistant,
    *,
    customer_number: str = "000000000000",
) -> MockConfigEntry:
    """
    Build a v3 MockConfigEntry with credentials, tokens, and one subentry.

    The customer-account ``ConfigSubentry`` mirrors the shape produced by
    the v3 config flow so the integration's setup path can find at least
    one account to wire up coordinators for.
    """
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
        options={"update_interval": 60},
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
                title=_TEST_SUBENTRY_TITLE,
                unique_id=customer_number,
                data={
                    CONF_CUSTOMER_NUMBER: customer_number,
                    CONF_BUSINESS_AGREEMENT_NUMBER: "B-0001",
                    CONF_PREMISES_NUMBER: "P-0001",
                    CONF_CONSUMPTION_ADDRESS: _TEST_SUBENTRY_TITLE,
                },
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


def _only_subentry_id(entry: MockConfigEntry) -> str:
    """Return the subentry id of the single test customer-account subentry."""
    return next(iter(entry.subentries))


def _make_client(
    *,
    refresh_return: tuple[str, str] = ("new-access", "new-refresh"),
    refresh_side_effect: Exception | None = None,
    prices_return: dict[str, Any] | None = None,
    service_point_return: dict[str, Any] | None = None,
    peaks_return: dict[str, Any] | None = None,
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
    client.async_get_monthly_peaks = AsyncMock(
        return_value=peaks_return or {"peakOfTheMonth": None, "dailyPeaks": []},
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
    with patch.object(entry, "async_start_reauth") as start_reauth:
        await refresh_callback(None)

    assert entry.runtime_data.authenticated is False
    start_reauth.assert_called_once_with(hass)


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
    with caplog.at_level(logging.WARNING, logger="custom_components.engie_be"):
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
    # v3 lifted CONF_CUSTOMER_NUMBER out of entry.data into the subentry.
    assert CONF_CUSTOMER_NUMBER not in entry.data


# ---------------------------------------------------------------------------
# Config-entry migration
# ---------------------------------------------------------------------------


async def test_migrate_v1_converts_hours_to_minutes(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """v1 stored update_interval in hours; migration must rewrite it to minutes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
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
        options={CONF_UPDATE_INTERVAL: 2},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 3
    assert entry.options[CONF_UPDATE_INTERVAL] == 120  # 2h -> 120min


async def test_migrate_v1_without_interval_just_bumps_version(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A v1 entry with no stored update_interval is migrated by version bump only."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
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
        options={},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 3
    assert CONF_UPDATE_INTERVAL not in entry.options


async def test_migrate_v3_is_noop(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A v3 entry passes straight through the migration unchanged."""
    entry = _build_entry(hass)

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 3
    assert entry.options[CONF_UPDATE_INTERVAL] == 60


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
    entry.runtime_data = EngieBeData(
        client=MagicMock(),
        epex_coordinator=sentinel_epex_coordinator,
        last_options=dict(entry.options),
        last_subentry_ids={
            sub.subentry_id
            for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_CUSTOMER_ACCOUNT
        },
    )

    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        # Simulate the update listener firing after _persist_tokens rotated
        # the tokens. Options dict is unchanged, so reload must be skipped.
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
            if sub.subentry_type == SUBENTRY_TYPE_CUSTOMER_ACCOUNT
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
                CONF_CUSTOMER_NUMBER: "111111111111",
                CONF_BUSINESS_AGREEMENT_NUMBER: "B-0002",
            },
        ),
        subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
        title="Second Account",
        unique_id="111111111111",
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
    subentry id set changes, so the no-op short-circuit must hold.
    """
    entry = _build_entry(hass)
    initial_ids = {sub.subentry_id for sub in entry.subentries.values()}
    entry.runtime_data = EngieBeData(
        client=MagicMock(),
        epex_coordinator=MagicMock(),
        last_options=dict(entry.options),
        last_subentry_ids=initial_ids,
    )

    # Simulate a token rotation: only entry.data changes.
    _persist_tokens(hass, entry, "rotated-access", "rotated-refresh")

    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        await async_reload_entry(hass, entry)

    mock_reload.assert_not_awaited()


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
    entry.runtime_data = EngieBeData(
        client=MagicMock(),
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
# Legacy BAN-shaped subentry unique_id migration
# ---------------------------------------------------------------------------


def _relations_payload_for(can: str, ban: str) -> dict[str, Any]:
    """Build a minimal customer-account-relations payload for one account."""
    return {
        "items": [
            {
                "customerAccount": {
                    "customerAccountNumber": can,
                    "businessAgreements": [
                        {"businessAgreementNumber": ban},
                    ],
                },
            },
        ],
    }


async def test_legacy_unique_id_migration_rewrites_ban_to_can(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A BAN-shaped subentry unique_id is rewritten to its canonical CAN."""
    legacy_ban = "002200000099"
    canonical_can = "1500000099"
    entry = _build_entry(hass, customer_number=legacy_ban)
    # Mirror the legacy v2-migrated shape: the BAN sits in both the
    # unique_id (via _build_entry) and in the data block.
    only_subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(
        entry,
        only_subentry,
        data={
            **only_subentry.data,
            CONF_BUSINESS_AGREEMENT_NUMBER: legacy_ban,
        },
    )

    with patch(
        "custom_components.engie_be._async_fetch_relations_for_setup",
        AsyncMock(return_value=_relations_payload_for(canonical_can, legacy_ban)),
    ):
        await _async_migrate_legacy_subentry_unique_ids(hass, entry)

    rewritten = next(iter(entry.subentries.values()))
    assert rewritten.unique_id == canonical_can
    assert rewritten.data[CONF_CUSTOMER_NUMBER] == canonical_can
    # The BAN field is preserved so the picker dedupes correctly even
    # before subsequent setups re-run this migration.
    assert rewritten.data[CONF_BUSINESS_AGREEMENT_NUMBER] == legacy_ban


async def test_legacy_unique_id_migration_is_noop_when_already_canonical(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A subentry that already carries its CAN as unique_id triggers no fetch."""
    canonical_can = "1500000050"
    entry = _build_entry(hass, customer_number=canonical_can)
    # Make sure the BAN field does not look like the unique_id, so the
    # ``needs_rewrite`` filter sees nothing to do.
    only_subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(
        entry,
        only_subentry,
        data={
            **only_subentry.data,
            CONF_CUSTOMER_NUMBER: canonical_can,
            CONF_BUSINESS_AGREEMENT_NUMBER: "002200000050",
        },
    )

    fetch_mock = AsyncMock()
    with patch(
        "custom_components.engie_be._async_fetch_relations_for_setup",
        fetch_mock,
    ):
        await _async_migrate_legacy_subentry_unique_ids(hass, entry)

    assert fetch_mock.await_count == 0
    untouched = next(iter(entry.subentries.values()))
    assert untouched.unique_id == canonical_can


async def test_legacy_unique_id_migration_skips_unknown_account(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A subentry whose stored id no longer maps to any account is left alone."""
    legacy_ban = "002200000077"
    entry = _build_entry(hass, customer_number=legacy_ban)
    only_subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(
        entry,
        only_subentry,
        data={
            **only_subentry.data,
            CONF_BUSINESS_AGREEMENT_NUMBER: legacy_ban,
        },
    )

    # Relations payload contains a totally different account.
    with patch(
        "custom_components.engie_be._async_fetch_relations_for_setup",
        AsyncMock(
            return_value=_relations_payload_for("9999999999", "999999999999"),
        ),
    ):
        await _async_migrate_legacy_subentry_unique_ids(hass, entry)

    untouched = next(iter(entry.subentries.values()))
    assert untouched.unique_id == legacy_ban


async def test_legacy_unique_id_migration_tolerates_relations_failure(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A relations fetch returning None defers the rewrite without raising."""
    legacy_ban = "002200000088"
    entry = _build_entry(hass, customer_number=legacy_ban)
    only_subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(
        entry,
        only_subentry,
        data={
            **only_subentry.data,
            CONF_BUSINESS_AGREEMENT_NUMBER: legacy_ban,
        },
    )

    with patch(
        "custom_components.engie_be._async_fetch_relations_for_setup",
        AsyncMock(return_value=None),
    ):
        await _async_migrate_legacy_subentry_unique_ids(hass, entry)

    untouched = next(iter(entry.subentries.values()))
    assert untouched.unique_id == legacy_ban


# ---------------------------------------------------------------------------
# Entity-id slug migration (one-shot CAN-prefix rewrite of legacy slugs).
# ---------------------------------------------------------------------------


def _seed_registry_entity(  # noqa: PLR0913 - test helper mirrors registry signature
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    domain: str,
    suggested_object_id: str,
    unique_id: str,
    subentry_id: str | None,
) -> str:
    """Register an entity in the registry under ``entry`` and return its id."""
    registry = er.async_get(hass)
    reg = registry.async_get_or_create(
        domain=domain,
        platform=DOMAIN,
        unique_id=unique_id,
        suggested_object_id=suggested_object_id,
        config_entry=entry,
        config_subentry_id=subentry_id,
    )
    return reg.entity_id


async def test_slug_migration_renames_calendar_to_can_prefixed(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A BAN-prefixed calendar entity_id is rewritten to engie_belgium_{CAN}."""
    can = "1500000123"
    entry = _build_entry(hass, customer_number=can)
    subentry_id = _only_subentry_id(entry)
    legacy = _seed_registry_entity(
        hass,
        entry,
        domain="calendar",
        suggested_object_id="002200000123",
        unique_id=f"{entry.entry_id}_{subentry_id}_calendar",
        subentry_id=subentry_id,
    )
    assert legacy == "calendar.002200000123"

    await _async_migrate_entity_id_slugs(hass, entry)

    registry = er.async_get(hass)
    assert registry.async_get(legacy) is None
    renamed = registry.async_get(f"calendar.engie_belgium_{can}")
    assert renamed is not None
    assert renamed.unique_id == f"{entry.entry_id}_{subentry_id}_calendar"


async def test_slug_migration_renames_sensor_with_key_suffix(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A subentry-scoped sensor gets ``_{key}`` appended after the CAN."""
    can = "1500000456"
    entry = _build_entry(hass, customer_number=can)
    subentry_id = _only_subentry_id(entry)
    legacy = _seed_registry_entity(
        hass,
        entry,
        domain="sensor",
        suggested_object_id="002200000456_captar_monthly_peak_power",
        unique_id=f"{entry.entry_id}_{subentry_id}_captar_monthly_peak_power",
        subentry_id=subentry_id,
    )

    await _async_migrate_entity_id_slugs(hass, entry)

    registry = er.async_get(hass)
    assert registry.async_get(legacy) is None
    target = f"sensor.engie_belgium_{can}_captar_monthly_peak_power"
    assert registry.async_get(target) is not None


async def test_slug_migration_renames_price_sensor_without_subentry_prefix(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Price sensors whose unique_id has no subentry prefix still get rewritten."""
    can = "1500000789"
    entry = _build_entry(hass, customer_number=can)
    subentry_id = _only_subentry_id(entry)
    key = "electricity_offtake_price_eur_per_kwh"
    legacy = _seed_registry_entity(
        hass,
        entry,
        domain="sensor",
        suggested_object_id="engie_belgium_electricity_offtake_price",
        unique_id=f"{entry.entry_id}_{key}",
        subentry_id=subentry_id,
    )

    await _async_migrate_entity_id_slugs(hass, entry)

    registry = er.async_get(hass)
    assert registry.async_get(legacy) is None
    assert registry.async_get(f"sensor.engie_belgium_{can}_{key}") is not None


async def test_slug_migration_is_idempotent(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A second pass over an already-migrated entity is a no-op."""
    can = "1500000321"
    entry = _build_entry(hass, customer_number=can)
    subentry_id = _only_subentry_id(entry)
    target = f"sensor.engie_belgium_{can}_captar_monthly_peak_energy"
    _seed_registry_entity(
        hass,
        entry,
        domain="sensor",
        suggested_object_id=f"engie_belgium_{can}_captar_monthly_peak_energy",
        unique_id=f"{entry.entry_id}_{subentry_id}_captar_monthly_peak_energy",
        subentry_id=subentry_id,
    )

    await _async_migrate_entity_id_slugs(hass, entry)
    await _async_migrate_entity_id_slugs(hass, entry)

    registry = er.async_get(hass)
    entries = list(er.async_entries_for_config_entry(registry, entry.entry_id))
    assert [e.entity_id for e in entries] == [target]


async def test_slug_migration_collision_falls_back_to_numeric_suffix(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A pre-existing slug at the target name forces a ``_2`` fallback."""
    can = "1500000654"
    entry = _build_entry(hass, customer_number=can)
    subentry_id = _only_subentry_id(entry)
    target = f"sensor.engie_belgium_{can}_captar_monthly_peak_start"

    # Pre-occupy the target slug with an unrelated registry entry.
    registry = er.async_get(hass)
    registry.async_get_or_create(
        domain="sensor",
        platform="other",
        unique_id="someone-else",
        suggested_object_id=target.removeprefix("sensor."),
    )
    assert registry.async_get(target) is not None

    legacy = _seed_registry_entity(
        hass,
        entry,
        domain="sensor",
        suggested_object_id="002200000654_captar_monthly_peak_start",
        unique_id=f"{entry.entry_id}_{subentry_id}_captar_monthly_peak_start",
        subentry_id=subentry_id,
    )

    await _async_migrate_entity_id_slugs(hass, entry)

    assert registry.async_get(legacy) is None
    assert registry.async_get(target) is not None  # the squatter is untouched
    assert registry.async_get(f"{target}_2") is not None


async def test_slug_migration_leaves_login_scoped_entities_alone(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """The auth binary sensor (no subentry) is never touched by the rewrite."""
    can = "1500000999"
    entry = _build_entry(hass, customer_number=can)
    auth = _seed_registry_entity(
        hass,
        entry,
        domain="binary_sensor",
        suggested_object_id="engie_belgium_authentication",
        unique_id=f"{entry.entry_id}_authentication",
        subentry_id=None,
    )

    await _async_migrate_entity_id_slugs(hass, entry)

    registry = er.async_get(hass)
    assert registry.async_get(auth) is not None
