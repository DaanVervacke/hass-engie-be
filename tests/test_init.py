"""Tests for ENGIE Belgium async_setup_entry and the periodic refresh callback."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be import (
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

    assert entry.version == 2
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

    assert entry.version == 2
    assert CONF_UPDATE_INTERVAL not in entry.options


@pytest.mark.skip(
    reason=(
        "v2->v3 migration is implemented in a follow-up commit; until then "
        "a v3 entry is the latest schema and never reaches async_migrate_entry."
    )
)
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
