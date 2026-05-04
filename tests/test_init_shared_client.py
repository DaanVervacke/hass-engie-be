"""
Tests for the shared ENGIE API client registry in ``__init__.py``.

The registry lives at ``hass.data[DOMAIN]["clients"][login_key]`` and
exists so multiple config entries that share the same ENGIE login (one
per customer-account-number) can also share one ``EngieBeApiClient``
and one 60s token-refresh task. These tests verify:

- A single client is created the first time a login appears, and the
  refresh task is armed only after the initial token refresh succeeds.
- A second sibling entry with the same login reuses the existing
  client (no second instantiation, no second refresh task).
- Token rotation fans out to *every* sibling's ``entry.data`` so a
  later reload of any sibling reads the rotated tokens, not the
  revoked ones.
- A scheduled refresh that ENGIE rejects starts reauth on every
  sibling (not just the entry whose timer happened to fire).
- When fresh tokens land on a sibling, any in-progress reauth flow
  for that sibling is auto-dismissed: the user provides credentials
  once and every account recovers.
- Releasing one of two siblings keeps the refresh task running for
  the other; releasing the last sibling cancels it and clears the
  registry slot.
- A setup that fails the initial token refresh rolls the registry
  slot back so it doesn't leak a half-armed client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be import (
    _dismiss_reauth_flows,
    _get_clients_registry,
    _persist_tokens_for_login,
    _shared_client_key,
    _start_reauth_for_login,
)
from custom_components.engie_be.api import EngieBeApiClientAuthenticationError
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Shared across every sibling entry built in this module so they all
# slugify to the same shared-client key.
_USERNAME = "user@example.com"
_PASSWORD = "hunter2"  # noqa: S105 - test fixture, not a real credential
_STORED_ACCESS = "stored-access"
_STORED_REFRESH = "stored-refresh"


def _build_sibling_entry(
    hass: HomeAssistant,
    customer_number: str,
) -> MockConfigEntry:
    """
    Build one MockConfigEntry for a given customer account.

    Sibling entries share ``_USERNAME`` (so they hash to the same
    shared-client key) but differ in ``customer_number``, mirroring
    the post-v3 schema where ``unique_id = slugify(username) +
    "_" + customer_number``.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title=f"{_USERNAME} ({customer_number})",
        unique_id=f"user_example_com_{customer_number}",
        data={
            CONF_USERNAME: _USERNAME,
            CONF_PASSWORD: _PASSWORD,
            CONF_CUSTOMER_NUMBER: customer_number,
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: _STORED_ACCESS,
            CONF_REFRESH_TOKEN: _STORED_REFRESH,
        },
        options={"update_interval": 60},
    )
    entry.add_to_hass(hass)
    return entry


def _make_client(
    *,
    refresh_return: tuple[str, str] = ("new-access", "new-refresh"),
    refresh_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock EngieBeApiClient with sensible defaults."""
    client = MagicMock()
    if refresh_side_effect is not None:
        client.async_refresh_token = AsyncMock(side_effect=refresh_side_effect)
    else:
        client.async_refresh_token = AsyncMock(return_value=refresh_return)
    client.async_get_prices = AsyncMock(return_value={"items": []})
    client.async_get_service_point = AsyncMock(
        return_value={"division": "ELECTRICITY"},
    )
    client.async_get_monthly_peaks = AsyncMock(
        return_value={"peakOfTheMonth": None, "dailyPeaks": []},
    )
    return client


def _patch_first_refresh() -> Any:
    """Patch DataUpdateCoordinator.async_config_entry_first_refresh to a no-op."""
    return patch(
        "custom_components.engie_be.coordinator.EngieBeDataUpdateCoordinator"
        ".async_config_entry_first_refresh",
        new=AsyncMock(return_value=None),
    )


# ---------------------------------------------------------------------------
# Acquire / release lifecycle
# ---------------------------------------------------------------------------


async def test_first_entry_creates_shared_client_and_arms_refresh_task(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """First entry to set up creates the registry slot and arms the timer."""
    entry = _build_sibling_entry(hass, customer_number="1500000001")
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
        _patch_first_refresh(),
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is True
    registry = _get_clients_registry(hass)
    key = _shared_client_key(entry)
    assert key in registry
    shared = registry[key]
    assert shared.client is client
    assert shared.entry_ids == {entry.entry_id}
    # Exactly one refresh task armed for this login.
    assert len(captured) == 1
    assert shared.cancel_refresh is not None


async def test_second_sibling_reuses_client_and_does_not_arm_second_task(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A sibling for the same login attaches to the existing shared client."""
    first = _build_sibling_entry(hass, customer_number="1500000001")
    second = _build_sibling_entry(hass, customer_number="1500000002")
    client = _make_client()

    instantiations = 0

    def _factory(*_args: object, **_kwargs: object) -> MagicMock:
        nonlocal instantiations
        instantiations += 1
        return client

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
            side_effect=_factory,
        ),
        patch(
            "custom_components.engie_be.async_track_time_interval",
            side_effect=_capture_callback,
        ),
        _patch_first_refresh(),
    ):
        assert await hass.config_entries.async_setup(first.entry_id) is True
        assert await hass.config_entries.async_setup(second.entry_id) is True
        await hass.async_block_till_done()

    # Client instantiated exactly once across both setups.
    assert instantiations == 1
    # Refresh task armed exactly once.
    assert len(captured) == 1

    registry = _get_clients_registry(hass)
    key = _shared_client_key(first)
    assert key == _shared_client_key(second)
    shared = registry[key]
    assert shared.client is client
    assert shared.entry_ids == {first.entry_id, second.entry_id}
    # Both runtime_data instances point at the same shared client.
    assert first.runtime_data.client is second.runtime_data.client is client


async def test_releasing_one_sibling_keeps_task_running_for_the_other(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Refresh task survives a sibling unload as long as another sibling is up.

    The cancel handle returned by ``async_track_time_interval`` is only
    invoked when the *last* entry for a login is released.
    """
    first = _build_sibling_entry(hass, customer_number="1500000001")
    second = _build_sibling_entry(hass, customer_number="1500000002")
    client = _make_client()
    cancel_handle = MagicMock()

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.engie_be.async_track_time_interval",
            return_value=cancel_handle,
        ),
        _patch_first_refresh(),
    ):
        await hass.config_entries.async_setup(first.entry_id)
        await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

        # Unload only the first sibling.
        assert await hass.config_entries.async_unload(first.entry_id) is True
        await hass.async_block_till_done()

    cancel_handle.assert_not_called()
    registry = _get_clients_registry(hass)
    key = _shared_client_key(second)
    assert key in registry
    assert registry[key].entry_ids == {second.entry_id}


async def test_releasing_last_sibling_cancels_task_and_clears_slot(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """The last entry for a login tears the refresh task down on unload."""
    entry = _build_sibling_entry(hass, customer_number="1500000001")
    client = _make_client()
    cancel_handle = MagicMock()

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.engie_be.async_track_time_interval",
            return_value=cancel_handle,
        ),
        _patch_first_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id) is True
        await hass.async_block_till_done()

    cancel_handle.assert_called_once_with()
    registry = _get_clients_registry(hass)
    assert _shared_client_key(entry) not in registry


async def test_failed_initial_refresh_rolls_back_registry_slot(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    A setup that fails the initial token refresh must not leak a slot.

    Vector: ENGIE rejects the stored refresh_token at setup time.
    HA raises ``ConfigEntryAuthFailed`` and does *not* call
    ``async_unload_entry``, so the rollback has to happen inline.
    Otherwise the registry would pin a stale client + arm a 60s
    callback against credentials that just got rejected.
    """
    entry = _build_sibling_entry(hass, customer_number="1500000001")
    client = _make_client(
        refresh_side_effect=EngieBeApiClientAuthenticationError("expired"),
    )
    cancel_handle = MagicMock()

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.engie_be.async_track_time_interval",
            return_value=cancel_handle,
        ),
    ):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is False
    # Registry must be empty: no leak of a half-armed client.
    registry = _get_clients_registry(hass)
    assert _shared_client_key(entry) not in registry
    # Refresh task must never have been armed in the first place
    # (we defer arming until after the first successful refresh).
    cancel_handle.assert_not_called()


# ---------------------------------------------------------------------------
# Token write-back fan-out
# ---------------------------------------------------------------------------


async def test_token_writeback_fans_out_to_all_siblings(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Rotated tokens land on every config entry that shares the login.

    A rotation that updated only one entry would leave a sibling
    holding a now-revoked refresh_token; on next reload that sibling
    would unilaterally start a reauth loop using stale credentials.
    """
    first = _build_sibling_entry(hass, customer_number="1500000001")
    second = _build_sibling_entry(hass, customer_number="1500000002")
    client = _make_client(refresh_return=("rot-access", "rot-refresh"))

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        _patch_first_refresh(),
    ):
        await hass.config_entries.async_setup(first.entry_id)
        await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

    # Both setup paths run an initial refresh (each serialised under
    # the shared lock); the final stored value must be the rotated one
    # on both entries.
    assert first.data[CONF_ACCESS_TOKEN] == "rot-access"
    assert first.data[CONF_REFRESH_TOKEN] == "rot-refresh"
    assert second.data[CONF_ACCESS_TOKEN] == "rot-access"
    assert second.data[CONF_REFRESH_TOKEN] == "rot-refresh"
    # Credentials and customer numbers must survive the rotation.
    assert first.data[CONF_USERNAME] == second.data[CONF_USERNAME] == "user@example.com"
    assert first.data[CONF_CUSTOMER_NUMBER] == "1500000001"
    assert second.data[CONF_CUSTOMER_NUMBER] == "1500000002"


# ---------------------------------------------------------------------------
# Reauth fan-out and auto-dismissal
# ---------------------------------------------------------------------------


async def test_reauth_fans_out_to_every_sibling(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    A scheduled-refresh auth failure starts reauth on every sibling entry.

    If reauth only fired on the entry whose timer happened to win the
    race, the other siblings would silently stop polling and the user
    would never be told they need to re-enter credentials.
    """
    first = _build_sibling_entry(hass, customer_number="1500000001")
    second = _build_sibling_entry(hass, customer_number="1500000002")
    client = _make_client()

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        _patch_first_refresh(),
    ):
        await hass.config_entries.async_setup(first.entry_id)
        await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

    registry = _get_clients_registry(hass)
    shared = registry[_shared_client_key(first)]

    with (
        patch.object(first, "async_start_reauth") as first_reauth,
        patch.object(second, "async_start_reauth") as second_reauth,
    ):
        _start_reauth_for_login(hass, shared)

    first_reauth.assert_called_once_with(hass)
    second_reauth.assert_called_once_with(hass)


async def test_token_writeback_dismisses_inflight_reauth_flows(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Fresh tokens auto-cancel reauth dialogs sitting on sibling entries.

    User-visible behaviour we are guarding: a multi-account user
    completes reauth on one entry; the rotated credentials propagate
    to siblings via ``_persist_tokens_for_login`` and the leftover
    reauth dialogs disappear instead of asking for the same login
    a second and third time.
    """
    first = _build_sibling_entry(hass, customer_number="1500000001")
    second = _build_sibling_entry(hass, customer_number="1500000002")
    client = _make_client()

    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        _patch_first_refresh(),
    ):
        await hass.config_entries.async_setup(first.entry_id)
        await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

    # Drive both siblings into a reauth-pending state.
    first.async_start_reauth(hass)
    second.async_start_reauth(hass)
    await hass.async_block_till_done()

    pending_before = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN,
        match_context={"source": SOURCE_REAUTH},
    )
    assert {flow["context"]["entry_id"] for flow in pending_before} == {
        first.entry_id,
        second.entry_id,
    }

    # Simulate a successful refresh on the shared client: tokens fan
    # out, dismiss helpers fire for both siblings.
    registry = _get_clients_registry(hass)
    shared = registry[_shared_client_key(first)]
    _persist_tokens_for_login(hass, shared, "fresh-access", "fresh-refresh")
    await hass.async_block_till_done()

    pending_after = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN,
        match_context={"source": SOURCE_REAUTH},
    )
    assert pending_after == []
    # Tokens reached both entries.
    assert first.data[CONF_ACCESS_TOKEN] == "fresh-access"
    assert second.data[CONF_ACCESS_TOKEN] == "fresh-access"


async def test_dismiss_reauth_flows_is_safe_when_no_flow_pending(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """The dismiss helper must be a no-op when no reauth flow exists."""
    entry = _build_sibling_entry(hass, customer_number="1500000001")
    # Should simply return without raising.
    _dismiss_reauth_flows(hass, entry.entry_id)


# ---------------------------------------------------------------------------
# Shared-client key derivation
# ---------------------------------------------------------------------------


def test_shared_client_key_is_derived_from_username_not_unique_id(
    hass: HomeAssistant,
) -> None:
    """
    Key uses ``slugify(username)``, independent of the unique_id format.

    Post-v2->v3 migration the unique_id gains a ``_<customer_number>``
    suffix, but the login (and therefore the shared Auth0 token) does
    not change. Deriving the key from the username keeps the registry
    stable across schema versions.
    """
    v2_entry = _build_sibling_entry(hass, customer_number="1500000001")
    # Simulate a post-v3 unique_id while leaving the username alone.
    v2_entry.unique_id = "user_example_com_1500000001"
    assert _shared_client_key(v2_entry) == "user_example_com"
