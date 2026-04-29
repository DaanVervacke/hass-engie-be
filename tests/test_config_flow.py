"""Smoke tests for the ENGIE Belgium config flow."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.api import (
    AuthFlowState,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientMfaError,
)
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CUSTOMER_NUMBER,
    CONF_MFA_METHOD,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    MFA_METHOD_SMS,
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


async def test_user_flow_happy_path_sms(hass: HomeAssistant) -> None:
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


async def test_user_flow_invalid_credentials(hass: HomeAssistant) -> None:
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


async def test_user_flow_invalid_mfa_code(hass: HomeAssistant) -> None:
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


async def test_user_flow_duplicate_aborts(hass: HomeAssistant) -> None:
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


@pytest.mark.asyncio
async def test_reauth_flow_updates_tokens(hass: HomeAssistant) -> None:
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
