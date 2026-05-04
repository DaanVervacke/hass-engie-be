"""Config flow for the ENGIE Belgium integration."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigSubentry,
    ConfigSubentryFlow,
    FlowType,
    SubentryFlowContext,
    SubentryFlowResult,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
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
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    LOGGER,
    MAX_UPDATE_INTERVAL_MINUTES,
    MFA_METHOD_EMAIL,
    MFA_METHOD_SMS,
    MIN_UPDATE_INTERVAL_MINUTES,
    SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class EngieBeFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for ENGIE Belgium."""

    VERSION = 3

    def __init__(self) -> None:
        """Initialise the flow handler."""
        super().__init__()
        self._user_input: dict[str, Any] = {}
        self._auth_flow_state: AuthFlowState | None = None
        self._client: EngieBeApiClient | None = None
        self._reauth_mfa_method: str = MFA_METHOD_SMS

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,  # noqa: ARG004
    ) -> EngieBeOptionsFlowHandler:
        """Return the options flow handler."""
        return EngieBeOptionsFlowHandler()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls,
        config_entry: config_entries.ConfigEntry,  # noqa: ARG003
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the supported subentry flow handlers."""
        return {
            SUBENTRY_TYPE_CUSTOMER_ACCOUNT: CustomerAccountSubentryFlowHandler,
        }

    # ------------------------------------------------------------------
    # Step 1: credentials + MFA method
    # ------------------------------------------------------------------

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial credentials step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._user_input = user_input

            try:
                self._client = EngieBeApiClient(
                    session=async_get_clientsession(self.hass),
                    client_id=user_input.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
                )
                self._auth_flow_state = await self._client.async_start_authentication(
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    mfa_method=user_input.get(CONF_MFA_METHOD, MFA_METHOD_SMS),
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
                mfa_method = user_input.get(CONF_MFA_METHOD, MFA_METHOD_SMS)
                if mfa_method == MFA_METHOD_EMAIL:
                    return await self.async_step_mfa_email()
                return await self.async_step_mfa_sms()

        return self.async_show_form(
            step_id="user",
            description_placeholders={
                "user_management_url": "https://www.engie.be/nl/energiedesk/usermanagement/manage-access/",
            },
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
    # Step 2a: SMS MFA code entry
    # ------------------------------------------------------------------

    async def async_step_mfa_sms(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the SMS MFA code entry step."""
        return await self._handle_mfa_step(
            step_id="mfa_sms",
            mfa_method=MFA_METHOD_SMS,
            user_input=user_input,
        )

    # ------------------------------------------------------------------
    # Step 2b: email MFA code entry
    # ------------------------------------------------------------------

    async def async_step_mfa_email(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the email MFA code entry step."""
        return await self._handle_mfa_step(
            step_id="mfa_email",
            mfa_method=MFA_METHOD_EMAIL,
            user_input=user_input,
        )

    # ------------------------------------------------------------------
    # Shared MFA handler
    # ------------------------------------------------------------------

    async def _handle_mfa_step(
        self,
        *,
        step_id: str,
        mfa_method: str,
        user_input: dict[str, Any] | None,
    ) -> config_entries.ConfigFlowResult:
        """Handle MFA code entry for both SMS and email methods."""
        errors: dict[str, str] = {}

        if user_input is not None and self._auth_flow_state is not None:
            try:
                access_token, refresh_token = await self._complete_mfa(
                    mfa_code=user_input["code"],
                    mfa_method=mfa_method,
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
                username = self._user_input[CONF_USERNAME]
                await self.async_set_unique_id(slugify(username))
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"ENGIE Belgium ({username})",
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: self._user_input[CONF_PASSWORD],
                        CONF_CLIENT_ID: self._user_input.get(
                            CONF_CLIENT_ID, DEFAULT_CLIENT_ID
                        ),
                        CONF_ACCESS_TOKEN: access_token,
                        CONF_REFRESH_TOKEN: refresh_token,
                    },
                )

        return self.async_show_form(
            step_id=step_id,
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

    async def _complete_mfa(
        self,
        *,
        mfa_code: str,
        mfa_method: str,
    ) -> tuple[str, str]:
        """
        Submit an MFA code and return the resulting (access, refresh) tokens.

        Shared between the initial-setup flow and the reauth flow. The caller is
        responsible for catching API exceptions and surfacing them as form errors.
        """
        if self._client is None or self._auth_flow_state is None:
            msg = "MFA completion called without an active auth flow"
            raise EngieBeApiClientError(msg)

        try:
            return await self._client.async_complete_authentication(
                flow_state=self._auth_flow_state,
                mfa_code=mfa_code,
                mfa_method=mfa_method,
            )
        finally:
            self._auth_flow_state = None

    # ------------------------------------------------------------------
    # Chain into subentry picker after the parent entry is created
    # ------------------------------------------------------------------

    async def async_on_create_entry(
        self,
        result: config_entries.ConfigFlowResult,
    ) -> config_entries.ConfigFlowResult:
        """
        Chain into the customer-account subentry picker after parent entry is created.

        Returns the same result, populated with ``next_flow`` so the frontend
        immediately opens the subentry picker step.
        """
        entry = result["result"]
        subentry_result = await self.hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_CUSTOMER_ACCOUNT),
            context=SubentryFlowContext(source=SOURCE_USER),
        )
        result["next_flow"] = (
            FlowType.CONFIG_SUBENTRIES_FLOW,
            subentry_result["flow_id"],
        )
        return result

    # ------------------------------------------------------------------
    # Reauth flow
    # ------------------------------------------------------------------

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],  # noqa: ARG002
    ) -> config_entries.ConfigFlowResult:
        """Begin the reauth flow when stored credentials/tokens stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Restart authentication using stored credentials, prompt for MFA method."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            mfa_method = user_input.get(CONF_MFA_METHOD, MFA_METHOD_SMS)
            self._reauth_mfa_method = mfa_method

            try:
                self._client = EngieBeApiClient(
                    session=async_get_clientsession(self.hass),
                    client_id=entry.data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
                )
                self._auth_flow_state = await self._client.async_start_authentication(
                    username=entry.data[CONF_USERNAME],
                    password=entry.data[CONF_PASSWORD],
                    mfa_method=mfa_method,
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
                return await self.async_step_reauth_mfa()

        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"username": entry.data.get(CONF_USERNAME, "")},
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MFA_METHOD,
                        default=self._reauth_mfa_method,
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

    async def async_step_reauth_mfa(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Collect the MFA code during reauth and persist refreshed tokens."""
        errors: dict[str, str] = {}

        if user_input is not None and self._auth_flow_state is not None:
            try:
                access_token, refresh_token = await self._complete_mfa(
                    mfa_code=user_input["code"],
                    mfa_method=self._reauth_mfa_method,
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
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={
                        CONF_ACCESS_TOKEN: access_token,
                        CONF_REFRESH_TOKEN: refresh_token,
                    },
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_mfa",
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


class CustomerAccountSubentryFlowHandler(ConfigSubentryFlow):
    """Handle adding ENGIE customer-account subentries to an existing config entry."""

    def __init__(self) -> None:
        """Initialise the subentry flow handler."""
        super().__init__()
        self._available_accounts: list[dict[str, Any]] = []

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """Show the multi-select picker for customer accounts."""
        entry = self._get_entry()

        relations_or_abort = await self._fetch_relations(entry)
        if isinstance(relations_or_abort, str):
            return self.async_abort(reason=relations_or_abort)

        already_configured = {
            subentry.unique_id
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_CUSTOMER_ACCOUNT
            and subentry.unique_id is not None
        }

        accounts = _extract_accounts(relations_or_abort)
        self._available_accounts = [
            account
            for account in accounts
            if account[CONF_CUSTOMER_NUMBER] not in already_configured
        ]

        if not self._available_accounts:
            return self.async_abort(reason="no_accounts_available")

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_schema(),
            )

        selected = user_input.get(CONF_SELECTED_ACCOUNTS, [])
        if not selected:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_schema(),
                errors={"base": "no_accounts_selected"},
            )

        picked = [
            account
            for account in self._available_accounts
            if account[CONF_CUSTOMER_NUMBER] in selected
        ]

        # Programmatically add every pick after the first as a subentry on
        # the parent entry. The first pick is returned via async_create_entry
        # so the framework persists it via the standard ConfigSubentryFlow
        # finish path.
        for extra in picked[1:]:
            self.hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(extra),
                    subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
                    title=_subentry_title(extra),
                    unique_id=extra[CONF_CUSTOMER_NUMBER],
                ),
            )

        first = picked[0]
        return self.async_create_entry(
            title=_subentry_title(first),
            data=first,
            unique_id=first[CONF_CUSTOMER_NUMBER],
        )

    async def _fetch_relations(
        self,
        entry: config_entries.ConfigEntry,
    ) -> dict[str, Any] | str:
        """
        Fetch customer-account relations using a fresh client.

        Returns the response dict on success, or an abort reason string on
        failure. A fresh client is built from the parent entry's stored tokens
        because this step can run before async_setup_entry has finished.
        """
        client = EngieBeApiClient(
            session=async_get_clientsession(self.hass),
            client_id=entry.data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
            access_token=entry.data.get(CONF_ACCESS_TOKEN),
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        )
        try:
            return await client.async_get_customer_account_relations()
        except EngieBeApiClientAuthenticationError as exception:
            LOGGER.warning(exception)
            return "auth"
        except EngieBeApiClientCommunicationError as exception:
            LOGGER.error(exception)
            return "connection"
        except EngieBeApiClientError as exception:
            LOGGER.exception(exception)
            return "unknown"

    def _build_schema(self) -> vol.Schema:
        """Build the multi-select schema for the available customer accounts."""
        options = [
            selector.SelectOptionDict(
                value=account[CONF_CUSTOMER_NUMBER],
                label=_subentry_title(account),
            )
            for account in self._available_accounts
        ]
        return vol.Schema(
            {
                vol.Required(CONF_SELECTED_ACCOUNTS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    ),
                ),
            },
        )


# ----------------------------------------------------------------------
# Helpers (module level so they can be unit-tested without HA boilerplate)
# ----------------------------------------------------------------------


def _extract_accounts(relations: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Flatten a customer-account-relations response into per-account dicts.

    Each returned dict carries the subset of fields stored in the corresponding
    ConfigSubentry. Inactive business agreements are skipped; if no active
    agreement is present the account is still surfaced so the user can pick it
    (the address fields stay empty).
    """
    accounts: list[dict[str, Any]] = []
    for item in relations.get("items", []):
        customer_account = item.get("customerAccount") or {}
        customer_number = customer_account.get("customerAccountNumber")
        if not customer_number:
            continue

        agreement = _pick_active_agreement(customer_account.get("businessAgreements"))
        address = (agreement or {}).get("consumptionAddress") or {}

        accounts.append(
            {
                CONF_CUSTOMER_NUMBER: customer_number,
                CONF_BUSINESS_AGREEMENT_NUMBER: (agreement or {}).get(
                    "businessAgreementNumber",
                ),
                CONF_PREMISES_NUMBER: address.get("premisesNumber"),
                CONF_ACCOUNT_HOLDER_NAME: customer_account.get("name"),
                CONF_CONSUMPTION_ADDRESS: _format_address(address),
            },
        )
    return accounts


def _pick_active_agreement(
    agreements: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Return the first active business agreement, or the first one available."""
    if not agreements:
        return None
    for agreement in agreements:
        if agreement.get("active"):
            return agreement
    return agreements[0]


def _format_address(address: dict[str, Any]) -> str:
    """Format a consumption address as ``street houseNumber, postalCode city``."""
    if not address:
        return ""
    street = address.get("street") or ""
    house_number = address.get("houseNumber") or ""
    postal_code = address.get("postalCode") or ""
    city = address.get("city") or ""
    line1 = " ".join(part for part in (street, house_number) if part).strip()
    line2 = " ".join(part for part in (postal_code, city) if part).strip()
    return ", ".join(part for part in (line1, line2) if part)


def _subentry_title(account: dict[str, Any]) -> str:
    """
    Build a user-friendly subentry title.

    Falls back from address to account holder name to customer number so the
    title always renders something useful.
    """
    address = account.get(CONF_CONSUMPTION_ADDRESS)
    if address:
        return address
    holder = account.get(CONF_ACCOUNT_HOLDER_NAME)
    if holder:
        return holder
    return account[CONF_CUSTOMER_NUMBER]


class EngieBeOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for ENGIE Belgium."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Manage the integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UPDATE_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_UPDATE_INTERVAL,
                            DEFAULT_UPDATE_INTERVAL_MINUTES,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_UPDATE_INTERVAL_MINUTES,
                            max=MAX_UPDATE_INTERVAL_MINUTES,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="minutes",
                        ),
                    ),
                },
            ),
        )
