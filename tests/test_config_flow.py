"""Smoke tests for the ENGIE Belgium config flow (v5 ConfigSubentries)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigSubentryData,
    SubentryFlowContext,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be import async_reload_entry
from custom_components.engie_be.api import (
    AuthFlowState,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientCommunicationError,
    EngieBeApiClientError,
    EngieBeApiClientMfaError,
)
from custom_components.engie_be.config_flow import (
    EngieBeFlowHandler,
    _collect_configured_identifiers,
)
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CLIENT_ID,
    CONF_CONSUMPTION_ADDRESS,
    CONF_MFA_METHOD,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    CONF_SELECTED_ACCOUNTS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    MAX_UPDATE_INTERVAL_MINUTES,
    MFA_METHOD_EMAIL,
    MFA_METHOD_SMS,
    MIN_UPDATE_INTERVAL_MINUTES,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.data import EngieBeData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------

_USER_INPUT = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "hunter2",
    CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
    CONF_MFA_METHOD: MFA_METHOD_SMS,
}

_TOKENS = ("new-access-token", "new-refresh-token")
_EXPECTED_TITLE = f"ENGIE Belgium ({_USER_INPUT[CONF_USERNAME]})"


def _fake_flow_state() -> AuthFlowState:
    """Return a placeholder AuthFlowState for mocking."""
    return AuthFlowState(
        session=None,  # type: ignore[arg-type]
        authorize_state="state",
        login_state="login",
        mfa_challenge_state="mfa",
        code_verifier="verifier",
    )


def _load_relations_fixture() -> dict[str, Any]:
    """Load the shared two-account relations fixture."""
    path = Path(__file__).parent / "fixtures" / "customer_account_relations_sample.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _empty_relations() -> dict[str, Any]:
    """Return a relations payload with no accounts (picker aborts cleanly)."""
    return {"items": []}


def _build_parent_entry(
    hass: HomeAssistant,
    *,
    subentries: tuple[ConfigSubentryData, ...] = (),
) -> MockConfigEntry:
    """Build a v5 parent config entry, optionally with pre-existing subentries."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user_example_com",
        data={
            CONF_USERNAME: _USER_INPUT[CONF_USERNAME],
            CONF_PASSWORD: _USER_INPUT[CONF_PASSWORD],
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "old-access",
            CONF_REFRESH_TOKEN: "old-refresh",
        },
        version=5,
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# Initial setup flow: parent entry creation (chains into subentry picker)
# ---------------------------------------------------------------------------


async def test_user_flow_happy_path_sms(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A successful SMS-based setup creates a parent entry without customer number."""
    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(return_value=_TOKENS),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
            AsyncMock(return_value=_empty_relations()),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "mfa_sms"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    # With an empty relations payload the picker step short-circuits and
    # the parent entry is created with zero subentries; the user can add
    # accounts later via the entry's "+ Add" picker.
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == _EXPECTED_TITLE
    assert result["data"][CONF_ACCESS_TOKEN] == _TOKENS[0]
    assert result["data"][CONF_REFRESH_TOKEN] == _TOKENS[1]
    # v5: BAN identifiers live on subentries, not on the parent entry data.
    assert CONF_BUSINESS_AGREEMENT_NUMBER not in result["data"]
    assert result["result"].subentries == {}


async def test_user_flow_happy_path_with_account_picker(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """MFA success surfaces the picker; chosen accounts become subentries."""
    relations = _load_relations_fixture()

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(return_value=_TOKENS),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
            AsyncMock(return_value=relations),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "select_accounts"

        # v4: picker offers BANs, one per active business agreement. The
        # first item's first agreement is the canonical "single household"
        # of the test fixture, so picking only its BAN must produce
        # exactly one subentry keyed by that BAN.
        first_ban = relations["items"][0]["customerAccount"]["businessAgreements"][0][
            "businessAgreementNumber"
        ]
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_accounts": [first_ban]},
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == _EXPECTED_TITLE
    assert result["data"][CONF_ACCESS_TOKEN] == _TOKENS[0]
    assert CONF_BUSINESS_AGREEMENT_NUMBER not in result["data"]

    entry = result["result"]
    assert len(entry.subentries) == 1
    only_subentry = next(iter(entry.subentries.values()))
    assert only_subentry.unique_id == first_ban
    # v5: each subentry is keyed by the BAN (canonical identifier for every
    # downstream endpoint).
    assert only_subentry.data[CONF_BUSINESS_AGREEMENT_NUMBER] == first_ban


async def test_user_flow_select_accounts_requires_selection(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Submitting the picker with no selection re-shows the form with an error."""
    relations = _load_relations_fixture()

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(return_value=_TOKENS),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
            AsyncMock(return_value=relations),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )
        assert result["step_id"] == "select_accounts"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"selected_accounts": []}
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "select_accounts"
    assert result["errors"] == {"base": "no_accounts_selected"}


async def test_user_flow_happy_path_email(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A successful email-based setup creates a parent entry with tokens."""
    user_input = {**_USER_INPUT, CONF_MFA_METHOD: MFA_METHOD_EMAIL}

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(return_value=_TOKENS),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
            AsyncMock(return_value=_empty_relations()),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "mfa_email"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "654321"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ACCESS_TOKEN] == _TOKENS[0]
    assert result["data"][CONF_REFRESH_TOKEN] == _TOKENS[1]


async def test_user_flow_invalid_credentials(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Bad credentials surface as a form-level auth error."""
    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
        AsyncMock(side_effect=EngieBeApiClientAuthenticationError("bad creds")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "auth"}


async def test_user_flow_invalid_mfa_code(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A bad MFA code surfaces as invalid_mfa_code and stays on the MFA step."""
    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(side_effect=EngieBeApiClientMfaError("bad code")),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "000000"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "mfa_sms"
    assert result["errors"] == {"base": "invalid_mfa_code"}


async def test_user_flow_duplicate_aborts(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Configuring the same username twice aborts at the user step before MFA."""
    _build_parent_entry(hass)

    # The abort must happen before any ENGIE auth traffic is initiated.
    # Mock async_start_authentication so the test fails loudly if reached.
    start_auth = AsyncMock(
        side_effect=AssertionError(
            "async_start_authentication must not be called for a duplicate login",
        ),
    )
    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
        start_auth,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    start_auth.assert_not_called()


async def test_user_flow_connection_error_recovers(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A transient connection error surfaces, then the user can retry and succeed."""
    start_auth = AsyncMock(
        side_effect=[
            EngieBeApiClientCommunicationError("network down"),
            _fake_flow_state(),
        ]
    )
    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            start_auth,
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(return_value=_TOKENS),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
            AsyncMock(return_value=_empty_relations()),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "connection"}

        # Retry: same input, this time the client succeeds.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "mfa_sms"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert start_auth.await_count == 2


async def test_user_flow_unknown_error_recovers(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A generic API error surfaces as 'unknown' and is recoverable on retry."""
    start_auth = AsyncMock(
        side_effect=[
            EngieBeApiClientError("boom"),
            _fake_flow_state(),
        ]
    )
    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            start_auth,
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(return_value=_TOKENS),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
            AsyncMock(return_value=_empty_relations()),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        assert result["errors"] == {"base": "unknown"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        assert result["step_id"] == "mfa_sms"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY


# ---------------------------------------------------------------------------
# MFA step: non-MFA error recovery (auth / connection / unknown)
# ---------------------------------------------------------------------------


async def test_mfa_step_auth_error_recovers(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    A post-MFA auth failure maps to post_mfa_auth_failed, not auth.

    The user already proved their password and verification code in
    earlier steps; surfacing ``auth`` ("Invalid username or password.")
    here would be misleading. The dedicated key explains the issue
    happened after MFA acceptance.
    """
    complete = AsyncMock(side_effect=[EngieBeApiClientAuthenticationError("expired")])
    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            complete,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "mfa_sms"
    assert result["errors"] == {"base": "post_mfa_auth_failed"}


async def test_user_step_credential_error_keeps_auth_key(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    The user step (pre-MFA) must still surface 'auth' for bad creds.

    Regression guard for the post_mfa_auth_failed split: only the
    post-MFA branches were rerouted. A failed
    ``async_start_authentication`` call genuinely means the username or
    password was wrong, so the original ``auth`` key (and its "Invalid
    username or password." message) must remain in place.
    """
    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
        AsyncMock(side_effect=EngieBeApiClientAuthenticationError("bad creds")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "auth"}


async def test_mfa_step_connection_error_recovers(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A connection failure during MFA exchange surfaces as 'connection'."""
    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(side_effect=EngieBeApiClientCommunicationError("network down")),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["step_id"] == "mfa_sms"
    assert result["errors"] == {"base": "connection"}


async def test_mfa_step_unknown_error_recovers(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A generic API error during MFA exchange surfaces as 'unknown'."""
    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(side_effect=EngieBeApiClientError("boom")),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["step_id"] == "mfa_sms"
    assert result["errors"] == {"base": "unknown"}


# ---------------------------------------------------------------------------
# Customer-account subentry picker
# ---------------------------------------------------------------------------


async def _init_subentry_flow(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> dict[str, Any]:
    """Start the customer-account subentry flow against the given parent entry."""
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BUSINESS_AGREEMENT),
        context=SubentryFlowContext(source=SOURCE_USER),
    )


async def test_subentry_picker_creates_first_and_appends_extras(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Picking multiple accounts: first becomes CREATE_ENTRY, others auto-added."""
    entry = _build_parent_entry(hass)
    relations = _load_relations_fixture()

    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
        AsyncMock(return_value=relations),
    ):
        result = await _init_subentry_flow(hass, entry)
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            # v4 picker offers BANs, not CANs. The shared fixture has one
            # active BAN per customer account, so the BANs map 1:1 to the
            # legacy CANs the v3 picker used to expose.
            {CONF_SELECTED_ACCOUNTS: ["002200000001", "002200000002"]},
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    # Two subentries on the parent entry: first via async_create_entry,
    # second auto-added by the picker via async_add_subentry.
    assert len(entry.subentries) == 2
    business_agreements = {sub.unique_id for sub in entry.subentries.values()}
    assert business_agreements == {"002200000001", "002200000002"}


async def test_subentry_picker_multi_pick_collapses_to_single_reload(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Multi-pick add must trigger exactly one config-entry reload.

    The picker writes each selected business agreement with a separate
    ``async_add_subentry`` call (plus the framework finish-path add for the
    first pick), each of which schedules the integration's update listener.
    The ``pending_subentry_target`` gate must suppress the intermediate
    reloads so the listener reloads once, when the full selected set lands.
    """
    entry = _build_parent_entry(hass)
    entry.runtime_data = EngieBeData(
        client=AsyncMock(),
        epex_coordinator=AsyncMock(),
        last_options=dict(entry.options),
        last_subentry_ids=set(),
    )
    # Register directly on update_listeners (not via async_on_unload) so it
    # survives any simulated reload, matching the real async_setup_entry.
    if async_reload_entry not in entry.update_listeners:
        entry.add_update_listener(async_reload_entry)
    relations = _load_relations_fixture()

    reload_calls: list[str] = []

    async def _fake_reload(entry_id: str) -> None:
        reload_calls.append(entry_id)
        # Mirror a real reload: a fresh runtime_data whose snapshot equals
        # the current subentry-id set (and no pending target).
        entry.runtime_data = EngieBeData(
            client=AsyncMock(),
            epex_coordinator=AsyncMock(),
            last_options=dict(entry.options),
            last_subentry_ids={
                sub.subentry_id
                for sub in entry.subentries.values()
                if sub.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
            },
        )

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
            AsyncMock(return_value=relations),
        ),
        patch.object(
            hass.config_entries,
            "async_reload",
            side_effect=_fake_reload,
        ),
    ):
        result = await _init_subentry_flow(hass, entry)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_SELECTED_ACCOUNTS: ["002200000001", "002200000002"]},
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert len(entry.subentries) == 2
    # Exactly one reload despite two separate subentry writes.
    assert reload_calls == [entry.entry_id]
    # The gate is cleared after the reload fires.
    assert entry.runtime_data.pending_subentry_target is None


async def test_subentry_picker_single_pick_reloads_once(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A single-pick add still triggers exactly one reload through the gate."""
    entry = _build_parent_entry(hass)
    entry.runtime_data = EngieBeData(
        client=AsyncMock(),
        epex_coordinator=AsyncMock(),
        last_options=dict(entry.options),
        last_subentry_ids=set(),
    )
    if async_reload_entry not in entry.update_listeners:
        entry.add_update_listener(async_reload_entry)
    relations = _load_relations_fixture()

    reload_calls: list[str] = []

    async def _fake_reload(entry_id: str) -> None:
        reload_calls.append(entry_id)

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
            AsyncMock(return_value=relations),
        ),
        patch.object(
            hass.config_entries,
            "async_reload",
            side_effect=_fake_reload,
        ),
    ):
        result = await _init_subentry_flow(hass, entry)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_SELECTED_ACCOUNTS: ["002200000001"]},
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert len(entry.subentries) == 1
    assert reload_calls == [entry.entry_id]
    assert entry.runtime_data.pending_subentry_target is None


async def test_subentry_picker_skips_already_configured(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Accounts already attached as subentries are filtered out of the picker."""
    # v5 subentry: business_agreement_number is the canonical BAN identifier.
    existing = ConfigSubentryData(
        data={
            CONF_BUSINESS_AGREEMENT_NUMBER: "002200000001",
            CONF_PREMISES_NUMBER: "5100000001",
            CONF_ACCOUNT_HOLDER_NAME: "Test Customer One",
            CONF_CONSUMPTION_ADDRESS: "TESTSTRAAT 1, 1000 BRUSSELS",
        },
        subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
        title="TESTSTRAAT 1, 1000 BRUSSELS",
        unique_id="002200000001",
    )
    entry = _build_parent_entry(hass, subentries=(existing,))
    relations = _load_relations_fixture()

    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
        AsyncMock(return_value=relations),
    ):
        result = await _init_subentry_flow(hass, entry)
        assert result["type"] is data_entry_flow.FlowResultType.FORM

        # Only the second BAN should be selectable now.
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_SELECTED_ACCOUNTS: ["002200000002"]},
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert len(entry.subentries) == 2


async def test_subentry_picker_aborts_when_all_configured(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """When every relation account is already a subentry, the picker aborts."""
    # v5 subentries: keyed by business_agreement_number (BAN).
    existing = (
        ConfigSubentryData(
            data={
                CONF_BUSINESS_AGREEMENT_NUMBER: "002200000001",
            },
            subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
            title="One",
            unique_id="002200000001",
        ),
        ConfigSubentryData(
            data={
                CONF_BUSINESS_AGREEMENT_NUMBER: "002200000002",
            },
            subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
            title="Two",
            unique_id="002200000002",
        ),
    )
    entry = _build_parent_entry(hass, subentries=existing)
    relations = _load_relations_fixture()

    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
        AsyncMock(return_value=relations),
    ):
        result = await _init_subentry_flow(hass, entry)

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_accounts_available"


async def test_subentry_picker_aborts_when_relations_empty(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """An empty relations payload aborts with no_accounts_available."""
    entry = _build_parent_entry(hass)

    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
        AsyncMock(return_value=_empty_relations()),
    ):
        result = await _init_subentry_flow(hass, entry)

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_accounts_available"


async def test_subentry_picker_no_selection_reshows_form(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Submitting an empty selection re-renders the form with an error."""
    entry = _build_parent_entry(hass)
    relations = _load_relations_fixture()

    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
        AsyncMock(return_value=relations),
    ):
        result = await _init_subentry_flow(hass, entry)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_SELECTED_ACCOUNTS: []},
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_accounts_selected"}


@pytest.mark.parametrize(
    ("exception", "reason"),
    [
        (EngieBeApiClientAuthenticationError("expired"), "auth"),
        (EngieBeApiClientCommunicationError("offline"), "connection"),
        (EngieBeApiClientError("boom"), "unknown"),
    ],
)
async def test_subentry_picker_relations_error_aborts(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
    exception: Exception,
    reason: str,
) -> None:
    """Each relations-fetch error maps to a distinct abort reason."""
    entry = _build_parent_entry(hass)

    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
        AsyncMock(side_effect=exception),
    ):
        result = await _init_subentry_flow(hass, entry)

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == reason


# ---------------------------------------------------------------------------
# Reauth flow
# ---------------------------------------------------------------------------


async def test_reauth_flow_updates_tokens(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Reauth re-uses stored creds, prompts for MFA, and updates tokens in place."""
    entry = _build_parent_entry(hass)

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(return_value=_TOKENS),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MFA_METHOD: MFA_METHOD_SMS}
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reauth_mfa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_ACCESS_TOKEN] == _TOKENS[0]
    assert entry.data[CONF_REFRESH_TOKEN] == _TOKENS[1]
    # Stored credentials must remain untouched
    assert entry.data[CONF_USERNAME] == _USER_INPUT[CONF_USERNAME]
    assert entry.data[CONF_PASSWORD] == _USER_INPUT[CONF_PASSWORD]
    # v5: parent entry never carries a BAN; reauth must not add one.
    assert CONF_BUSINESS_AGREEMENT_NUMBER not in entry.data


async def test_reauth_flow_email_updates_tokens(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Reauth via email follows the same shape as SMS and updates tokens."""
    entry = _build_parent_entry(hass)

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(return_value=_TOKENS),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MFA_METHOD: MFA_METHOD_EMAIL}
        )
        assert result["step_id"] == "reauth_mfa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "654321"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_ACCESS_TOKEN] == _TOKENS[0]


async def test_reauth_confirm_auth_error_recovers(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Reauth confirm step: auth error surfaces, retry succeeds."""
    entry = _build_parent_entry(hass)
    start_auth = AsyncMock(
        side_effect=[
            EngieBeApiClientAuthenticationError("creds rotated"),
            _fake_flow_state(),
        ]
    )

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            start_auth,
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(return_value=_TOKENS),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MFA_METHOD: MFA_METHOD_SMS}
        )
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "auth"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MFA_METHOD: MFA_METHOD_SMS}
        )
        assert result["step_id"] == "reauth_mfa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_reauth_confirm_connection_error(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Reauth confirm step: connection error surfaces as 'connection'."""
    entry = _build_parent_entry(hass)

    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
        AsyncMock(side_effect=EngieBeApiClientCommunicationError("offline")),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MFA_METHOD: MFA_METHOD_SMS}
        )

    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "connection"}


async def test_reauth_confirm_unknown_error(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Reauth confirm step: generic API error surfaces as 'unknown'."""
    entry = _build_parent_entry(hass)

    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
        AsyncMock(side_effect=EngieBeApiClientError("boom")),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MFA_METHOD: MFA_METHOD_SMS}
        )

    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "unknown"}


async def test_reauth_mfa_invalid_code_recovers(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Reauth MFA step: invalid code surfaces as 'invalid_mfa_code'."""
    entry = _build_parent_entry(hass)

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(side_effect=EngieBeApiClientMfaError("nope")),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MFA_METHOD: MFA_METHOD_SMS}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "000000"}
        )

    assert result["step_id"] == "reauth_mfa"
    assert result["errors"] == {"base": "invalid_mfa_code"}


async def test_reauth_mfa_auth_error(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Reauth MFA step: post-MFA auth error maps to 'post_mfa_auth_failed'.

    Same reasoning as ``test_mfa_step_auth_error_recovers``: the user
    already passed credentials and MFA before this branch can fire.
    """
    entry = _build_parent_entry(hass)

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(side_effect=EngieBeApiClientAuthenticationError("expired")),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MFA_METHOD: MFA_METHOD_SMS}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["step_id"] == "reauth_mfa"
    assert result["errors"] == {"base": "post_mfa_auth_failed"}


async def test_reauth_mfa_connection_error(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Reauth MFA step: connection error surfaces as 'connection'."""
    entry = _build_parent_entry(hass)

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(side_effect=EngieBeApiClientCommunicationError("offline")),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MFA_METHOD: MFA_METHOD_SMS}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["step_id"] == "reauth_mfa"
    assert result["errors"] == {"base": "connection"}


async def test_reauth_mfa_unknown_error(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Reauth MFA step: generic API error surfaces as 'unknown'."""
    entry = _build_parent_entry(hass)

    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(side_effect=EngieBeApiClientError("boom")),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MFA_METHOD: MFA_METHOD_SMS}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["step_id"] == "reauth_mfa"
    assert result["errors"] == {"base": "unknown"}


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


async def test_options_flow_updates_interval(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """The options flow stores a new update interval on the entry."""
    entry = _build_parent_entry(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_UPDATE_INTERVAL: 60})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: 120}
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_UPDATE_INTERVAL] == 120


async def test_options_flow_rejects_out_of_range(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """The options-flow schema enforces the configured min/max bounds."""
    entry = _build_parent_entry(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_UPDATE_INTERVAL: 60})

    result = await hass.config_entries.options.async_init(entry.entry_id)

    too_low = MIN_UPDATE_INTERVAL_MINUTES - 1
    too_high = MAX_UPDATE_INTERVAL_MINUTES + 1

    for bad in (too_low, too_high):
        with pytest.raises(vol.Invalid):
            await hass.config_entries.options.async_configure(
                result["flow_id"], {CONF_UPDATE_INTERVAL: bad}
            )


# ---------------------------------------------------------------------------
# Defensive branches: post-MFA relations fetch failures and internal guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exception",
    [
        EngieBeApiClientAuthenticationError("auth"),
        EngieBeApiClientCommunicationError("connection"),
        EngieBeApiClientError("unknown"),
    ],
)
async def test_user_flow_relations_fetch_error_creates_entry_without_subentries(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
    exception: EngieBeApiClientError,
) -> None:
    """
    A failed post-MFA relations fetch falls back to a subentry-less entry.

    Exercises ``_async_fetch_initial_relations`` error handling (each of the
    auth / communication / generic branches) plus the ``select_accounts``
    fallback that creates the parent entry with no subentries.
    """
    with (
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_start_authentication",
            AsyncMock(return_value=_fake_flow_state()),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_complete_authentication",
            AsyncMock(return_value=_TOKENS),
        ),
        patch(
            "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
            AsyncMock(side_effect=exception),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == _EXPECTED_TITLE
    assert result["data"][CONF_ACCESS_TOKEN] == _TOKENS[0]
    assert result["result"].subentries == {}


async def test_complete_mfa_without_active_flow_raises() -> None:
    """``_complete_mfa`` rejects completion when no auth flow is active."""
    handler = EngieBeFlowHandler()
    # ``_client`` and ``_auth_flow_state`` default to None on a fresh handler.
    with pytest.raises(EngieBeApiClientError):
        await handler._complete_mfa(
            mfa_code="123456",
            mfa_method=MFA_METHOD_SMS,
        )


def test_collect_configured_identifiers_skips_non_business_agreement() -> None:
    """Only business-agreement subentries contribute configured identifiers."""
    business = SimpleNamespace(
        subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
        unique_id="BAN-1",
        data={CONF_BUSINESS_AGREEMENT_NUMBER: "BAN-1"},
    )
    other = SimpleNamespace(
        subentry_type="some_other_type",
        unique_id="OTHER",
        data={CONF_BUSINESS_AGREEMENT_NUMBER: "OTHER"},
    )
    entry = SimpleNamespace(subentries={"a": business, "b": other})

    assert _collect_configured_identifiers(entry) == {"BAN-1"}
