"""Smoke tests for the ENGIE Belgium config flow."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
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
    CONF_CLIENT_ID,
    CONF_CUSTOMER_NUMBER,
    CONF_MFA_METHOD,
    CONF_REFRESH_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    MAX_UPDATE_INTERVAL_MINUTES,
    MFA_METHOD_EMAIL,
    MFA_METHOD_SMS,
    MIN_UPDATE_INTERVAL_MINUTES,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_INPUT = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "hunter2",
    CONF_CUSTOMER_NUMBER: "123456789",
    CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
    CONF_MFA_METHOD: MFA_METHOD_SMS,
}

_TOKENS = ("new-access-token", "new-refresh-token")


def _fake_flow_state() -> AuthFlowState:
    """Return a placeholder AuthFlowState for mocking."""
    return AuthFlowState(
        session=None,  # type: ignore[arg-type]
        authorize_state="state",
        login_state="login",
        mfa_challenge_state="mfa",
        code_verifier="verifier",
    )


# ---------------------------------------------------------------------------
# Initial setup flow
# ---------------------------------------------------------------------------


async def test_user_flow_happy_path_sms(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A successful SMS-based setup creates a config entry with tokens."""
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

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == _USER_INPUT[CONF_USERNAME]
    assert result["data"][CONF_ACCESS_TOKEN] == _TOKENS[0]
    assert result["data"][CONF_REFRESH_TOKEN] == _TOKENS[1]
    assert result["data"][CONF_CUSTOMER_NUMBER] == "00123456789"


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
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="user_example_com",
        data={CONF_USERNAME: _USER_INPUT[CONF_USERNAME]},
        version=2,
    ).add_to_hass(hass)

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


# ---------------------------------------------------------------------------
# Reauth flow
# ---------------------------------------------------------------------------


async def test_reauth_flow_updates_tokens(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Reauth re-uses stored creds, prompts for MFA, and updates tokens in place."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user_example_com",
        data={
            CONF_USERNAME: _USER_INPUT[CONF_USERNAME],
            CONF_PASSWORD: _USER_INPUT[CONF_PASSWORD],
            CONF_CUSTOMER_NUMBER: "00123456789",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "old-access",
            CONF_REFRESH_TOKEN: "old-refresh",
        },
        version=2,
    )
    entry.add_to_hass(hass)

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


# ---------------------------------------------------------------------------
# Initial setup flow: email MFA + non-auth error recovery
# ---------------------------------------------------------------------------


async def test_user_flow_happy_path_email(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A successful email-based setup creates a config entry with tokens."""
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
    complete = AsyncMock(
        side_effect=[
            EngieBeApiClientAuthenticationError("expired"),
        ]
    )
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
# Reauth flow: email + every error branch (start_authentication and MFA)
# ---------------------------------------------------------------------------


def _build_reauth_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a v2 entry suitable for reauth flow tests."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user_example_com",
        data={
            CONF_USERNAME: _USER_INPUT[CONF_USERNAME],
            CONF_PASSWORD: _USER_INPUT[CONF_PASSWORD],
            CONF_CUSTOMER_NUMBER: "00123456789",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "old-access",
            CONF_REFRESH_TOKEN: "old-refresh",
        },
        version=2,
    )
    entry.add_to_hass(hass)
    return entry


async def test_reauth_flow_email_updates_tokens(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Reauth via email follows the same shape as SMS and updates tokens."""
    entry = _build_reauth_entry(hass)

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
    entry = _build_reauth_entry(hass)
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
    entry = _build_reauth_entry(hass)

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
    entry = _build_reauth_entry(hass)

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
    entry = _build_reauth_entry(hass)

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
    entry = _build_reauth_entry(hass)

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
    entry = _build_reauth_entry(hass)

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
    entry = _build_reauth_entry(hass)

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
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user_example_com",
        data={
            CONF_USERNAME: _USER_INPUT[CONF_USERNAME],
            CONF_PASSWORD: _USER_INPUT[CONF_PASSWORD],
            CONF_CUSTOMER_NUMBER: "00123456789",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "stored-access",
            CONF_REFRESH_TOKEN: "stored-refresh",
        },
        options={CONF_UPDATE_INTERVAL: 60},
        version=2,
    )
    entry.add_to_hass(hass)

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
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user_example_com",
        data={
            CONF_USERNAME: _USER_INPUT[CONF_USERNAME],
            CONF_PASSWORD: _USER_INPUT[CONF_PASSWORD],
            CONF_CUSTOMER_NUMBER: "00123456789",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "stored-access",
            CONF_REFRESH_TOKEN: "stored-refresh",
        },
        options={CONF_UPDATE_INTERVAL: 60},
        version=2,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    too_low = MIN_UPDATE_INTERVAL_MINUTES - 1
    too_high = MAX_UPDATE_INTERVAL_MINUTES + 1

    for bad in (too_low, too_high):
        with pytest.raises(vol.Invalid):
            await hass.config_entries.options.async_configure(
                result["flow_id"], {CONF_UPDATE_INTERVAL: bad}
            )
