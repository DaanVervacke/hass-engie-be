"""Config flow for the ENGIE Belgium integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

from .api import (
    AuthFlowState,
    EngieBeApiClient,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientCommunicationError,
    EngieBeApiClientError,
    EngieBeApiClientMfaError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CUSTOMER_NUMBER,
    CONF_MFA_METHOD,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    LOGGER,
    MFA_METHOD_EMAIL,
    MFA_METHOD_SMS,
)


class EngieBeFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for ENGIE Belgium."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow handler."""
        super().__init__()
        self._user_input: dict[str, Any] = {}
        self._auth_flow_state: AuthFlowState | None = None
        self._client: EngieBeApiClient | None = None

    # ------------------------------------------------------------------
    # Step 1: credentials + customer number + MFA method
    # ------------------------------------------------------------------

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial credentials step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Reject email MFA for now
            if user_input.get(CONF_MFA_METHOD) == MFA_METHOD_EMAIL:
                errors["base"] = "mfa_email_not_supported"
            else:
                self._user_input = user_input
                try:
                    self._client = EngieBeApiClient(
                        session=async_get_clientsession(self.hass),
                        client_id=user_input.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
                    )
                    self._auth_flow_state = (
                        await self._client.async_start_authentication(
                            username=user_input[CONF_USERNAME],
                            password=user_input[CONF_PASSWORD],
                        )
                    )
                except EngieBeApiClientAuthenticationError as exception:
                    LOGGER.warning(exception)
                    errors["base"] = "auth"
                except EngieBeApiClientCommunicationError as exception:
                    LOGGER.error(exception)
                    errors["base"] = "connection"
                except EngieBeApiClientError as exception:
                    LOGGER.exception(exception)
                    errors["base"] = "unknown"
                else:
                    return await self.async_step_mfa()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=(user_input or {}).get(CONF_USERNAME, vol.UNDEFINED),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        ),
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD,
                        ),
                    ),
                    vol.Required(
                        CONF_CUSTOMER_NUMBER,
                        default=(user_input or {}).get(
                            CONF_CUSTOMER_NUMBER, vol.UNDEFINED
                        ),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        ),
                    ),
                    vol.Required(
                        CONF_CLIENT_ID,
                        default=(user_input or {}).get(
                            CONF_CLIENT_ID, DEFAULT_CLIENT_ID
                        ),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        ),
                    ),
                    vol.Required(
                        CONF_MFA_METHOD,
                        default=(user_input or {}).get(CONF_MFA_METHOD, MFA_METHOD_SMS),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=MFA_METHOD_SMS,
                                    label="SMS",
                                ),
                                selector.SelectOptionDict(
                                    value=MFA_METHOD_EMAIL,
                                    label="Email",
                                ),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        ),
                    ),
                },
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2: MFA code entry
    # ------------------------------------------------------------------

    async def async_step_mfa(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the MFA code entry step."""
        errors: dict[str, str] = {}

        if user_input is not None and self._auth_flow_state is not None:
            try:
                (
                    access_token,
                    refresh_token,
                ) = await self._client.async_complete_authentication(
                    flow_state=self._auth_flow_state,
                    mfa_code=user_input["code"],
                )
            except EngieBeApiClientMfaError as exception:
                LOGGER.warning(exception)
                errors["base"] = "invalid_mfa_code"
            except EngieBeApiClientAuthenticationError as exception:
                LOGGER.warning(exception)
                errors["base"] = "auth"
            except EngieBeApiClientCommunicationError as exception:
                LOGGER.error(exception)
                errors["base"] = "connection"
            except EngieBeApiClientError as exception:
                LOGGER.exception(exception)
                errors["base"] = "unknown"
            else:
                self._auth_flow_state = None

                await self.async_set_unique_id(slugify(self._user_input[CONF_USERNAME]))
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=self._user_input[CONF_USERNAME],
                    data={
                        CONF_USERNAME: self._user_input[CONF_USERNAME],
                        CONF_PASSWORD: self._user_input[CONF_PASSWORD],
                        CONF_CUSTOMER_NUMBER: self._user_input[CONF_CUSTOMER_NUMBER],
                        CONF_CLIENT_ID: self._user_input.get(
                            CONF_CLIENT_ID, DEFAULT_CLIENT_ID
                        ),
                        CONF_ACCESS_TOKEN: access_token,
                        CONF_REFRESH_TOKEN: refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="mfa",
            data_schema=vol.Schema(
                {
                    vol.Required("code"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        ),
                    ),
                },
            ),
            errors=errors,
        )
