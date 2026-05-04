"""Smoke tests for the ENGIE Belgium config flow (v3 ConfigSubentries)."""

from __future__ import annotations

import json
from pathlib import Path
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

from custom_components.engie_be.api import (
    AuthFlowState,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientCommunicationError,
    EngieBeApiClientError,
    EngieBeApiClientMfaError,
)
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CLIENT_ID,
    CONF_CONSUMPTION_ADDRESS,
    CONF_CUSTOMER_NUMBER,
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
    SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
)

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
    """Build a v3 parent config entry, optionally with pre-existing subentries."""
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
        version=3,
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
    # v3: customer number lives on subentries, not on the parent entry data.
    assert CONF_CUSTOMER_NUMBER not in result["data"]
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

        # Pick only the first account; the second must NOT become a subentry.
        first_can = relations["items"][0]["customerAccount"]["customerAccountNumber"]
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_accounts": [first_can]},
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == _EXPECTED_TITLE
    assert result["data"][CONF_ACCESS_TOKEN] == _TOKENS[0]
    assert CONF_CUSTOMER_NUMBER not in result["data"]

    entry = result["result"]
    assert len(entry.subentries) == 1
    only_subentry = next(iter(entry.subentries.values()))
    assert only_subentry.unique_id == first_can
    assert only_subentry.data[CONF_CUSTOMER_NUMBER] == first_can


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
    """Configuring the same username twice aborts with already_configured."""
    _build_parent_entry(hass)

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
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


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
    """An auth failure during MFA exchange surfaces and is recoverable."""
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
        (entry.entry_id, SUBENTRY_TYPE_CUSTOMER_ACCOUNT),
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
            {CONF_SELECTED_ACCOUNTS: ["1500000001", "1500000002"]},
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    # Two subentries on the parent entry: first via async_create_entry,
    # second auto-added by the picker via async_add_subentry.
    assert len(entry.subentries) == 2
    customer_numbers = {sub.unique_id for sub in entry.subentries.values()}
    assert customer_numbers == {"1500000001", "1500000002"}


async def test_subentry_picker_skips_already_configured(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Accounts already attached as subentries are filtered out of the picker."""
    existing = ConfigSubentryData(
        data={
            CONF_CUSTOMER_NUMBER: "1500000001",
            CONF_BUSINESS_AGREEMENT_NUMBER: "002200000001",
            CONF_PREMISES_NUMBER: "5100000001",
            CONF_ACCOUNT_HOLDER_NAME: "Test Customer One",
            CONF_CONSUMPTION_ADDRESS: "TESTSTRAAT 1, 1000 BRUSSELS",
        },
        subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
        title="TESTSTRAAT 1, 1000 BRUSSELS",
        unique_id="1500000001",
    )
    entry = _build_parent_entry(hass, subentries=(existing,))
    relations = _load_relations_fixture()

    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
        AsyncMock(return_value=relations),
    ):
        result = await _init_subentry_flow(hass, entry)
        assert result["type"] is data_entry_flow.FlowResultType.FORM

        # Only the second account should be selectable now.
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_SELECTED_ACCOUNTS: ["1500000002"]},
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert len(entry.subentries) == 2


async def test_subentry_picker_aborts_when_all_configured(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """When every relation account is already a subentry, the picker aborts."""
    existing = (
        ConfigSubentryData(
            data={CONF_CUSTOMER_NUMBER: "1500000001"},
            subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
            title="One",
            unique_id="1500000001",
        ),
        ConfigSubentryData(
            data={CONF_CUSTOMER_NUMBER: "1500000002"},
            subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
            title="Two",
            unique_id="1500000002",
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


async def test_subentry_picker_dedupes_by_business_agreement_number(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Legacy BAN-shaped unique_ids must dedupe candidates whose CAN differs.

    Reproduces the duplicate-device bug: a v2-migrated subentry stores
    its identifier as a businessAgreementNumber, but the picker
    candidate carries the canonical customerAccountNumber. Without the
    BAN-aware dedupe the same physical premises gets added a second
    time.
    """
    legacy_subentry = ConfigSubentryData(
        data={
            # Legacy v2-migrated shape: customer_number holds the BAN
            # because that is what the legacy v2 endpoints accepted.
            CONF_CUSTOMER_NUMBER: "002200000001",
            CONF_BUSINESS_AGREEMENT_NUMBER: "002200000001",
        },
        subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
        title="Legacy",
        unique_id="002200000001",
    )
    entry = _build_parent_entry(hass, subentries=(legacy_subentry,))
    relations = _load_relations_fixture()

    with patch(
        "custom_components.engie_be.config_flow.EngieBeApiClient.async_get_customer_account_relations",
        AsyncMock(return_value=relations),
    ):
        result = await _init_subentry_flow(hass, entry)
        assert result["type"] is data_entry_flow.FlowResultType.FORM

        # Only CAN 1500000002 should remain selectable; CAN 1500000001
        # is the canonical match for the legacy BAN-keyed subentry and
        # must be filtered out.
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_SELECTED_ACCOUNTS: ["1500000002"]},
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert len(entry.subentries) == 2
    stored_ids = {s.unique_id for s in entry.subentries.values()}
    assert stored_ids == {"002200000001", "1500000002"}


async def test_subentry_picker_aborts_when_only_legacy_ban_match_available(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """All candidates matched by BAN must abort, not silently fall through."""
    legacy_subentries = (
        ConfigSubentryData(
            data={
                CONF_CUSTOMER_NUMBER: "002200000001",
                CONF_BUSINESS_AGREEMENT_NUMBER: "002200000001",
            },
            subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
            title="Legacy 1",
            unique_id="002200000001",
        ),
        ConfigSubentryData(
            data={
                CONF_CUSTOMER_NUMBER: "002200000002",
                CONF_BUSINESS_AGREEMENT_NUMBER: "002200000002",
            },
            subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
            title="Legacy 2",
            unique_id="002200000002",
        ),
    )
    entry = _build_parent_entry(hass, subentries=legacy_subentries)
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
    # v3: parent entry never carried customer_number; reauth must not add it.
    assert CONF_CUSTOMER_NUMBER not in entry.data


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
    """Reauth MFA step: auth error surfaces as 'auth'."""
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
    assert result["errors"] == {"base": "auth"}


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
