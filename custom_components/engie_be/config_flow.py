"""Config flow for the ENGIE Belgium integration."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

from ._relations import (
    extract_accounts,
    flatten_customer_account,
    iter_account_identifiers,
    subentry_title,
)
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
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CLIENT_ID,
    CONF_CUSTOMER_NUMBER,
    CONF_MFA_METHOD,
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
        # Set after successful MFA on the initial-setup flow; consumed by
        # ``async_step_select_accounts``.
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._available_accounts: list[dict[str, Any]] = []

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

                # Stash the freshly-issued tokens for the picker step,
                # which uses them to fetch customer-account relations
                # before any ConfigEntry is persisted. Finishing happens
                # in async_step_select_accounts so we can pass the chosen
                # subentries to async_create_entry in a single call.
                self._access_token = access_token
                self._refresh_token = refresh_token
                return await self.async_step_select_accounts()

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
    # Customer-account picker (chained from MFA success on initial setup)
    # ------------------------------------------------------------------

    async def async_step_select_accounts(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Show the customer-account multi-select after MFA succeeds.

        Runs as the final step of the initial-setup flow. Fetches the
        customer-account-relations payload using the just-issued tokens
        and presents a multi-select. On submit, creates the parent
        ``ConfigEntry`` together with one ``ConfigSubentry`` per chosen
        account in a single ``async_create_entry`` call (the only
        supported way to create entry + subentries atomically; HA's
        ``next_flow`` mechanism does not accept subentry flows).

        On the first call the picker is rendered. On submit the chosen
        identifiers are translated into ``ConfigSubentryData`` records
        and the parent entry is created.
        """
        username = self._user_input[CONF_USERNAME]
        errors: dict[str, str] = {}

        if not self._available_accounts:
            relations_or_error = await self._async_fetch_initial_relations()
            if isinstance(relations_or_error, str):
                # Fetching the relations payload at this point is best-effort:
                # we have valid tokens but the WAF or the API may still trip.
                # Fall back to creating the entry without any subentries; the
                # user can add accounts via the entry's "+ Add" picker later.
                LOGGER.warning(
                    "Skipping initial subentry picker: %s", relations_or_error
                )
                return self._async_finish_initial_setup(subentries=())
            self._available_accounts = extract_accounts(relations_or_error)

        if not self._available_accounts:
            # Account on this login has zero customer accounts attached;
            # finish without subentries so the user gets a usable entry.
            return self._async_finish_initial_setup(subentries=())

        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_ACCOUNTS, [])
            if not selected:
                errors["base"] = "no_accounts_selected"
            else:
                picked = [
                    account
                    for account in self._available_accounts
                    if account[CONF_CUSTOMER_NUMBER] in selected
                ]
                subentries = tuple(
                    ConfigSubentryData(
                        data=account,
                        subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
                        title=subentry_title(account),
                        unique_id=account[CONF_CUSTOMER_NUMBER],
                    )
                    for account in picked
                )
                return self._async_finish_initial_setup(subentries=subentries)

        return self.async_show_form(
            step_id="select_accounts",
            data_schema=self._async_build_picker_schema(),
            errors=errors,
            description_placeholders={"username": username},
        )

    @callback
    def _async_finish_initial_setup(
        self,
        *,
        subentries: tuple[ConfigSubentryData, ...],
    ) -> config_entries.ConfigFlowResult:
        """Create the parent config entry plus any chosen subentries."""
        username = self._user_input[CONF_USERNAME]
        return self.async_create_entry(
            title=f"ENGIE Belgium ({username})",
            data={
                CONF_USERNAME: username,
                CONF_PASSWORD: self._user_input[CONF_PASSWORD],
                CONF_CLIENT_ID: self._user_input.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
                CONF_ACCESS_TOKEN: self._access_token,
                CONF_REFRESH_TOKEN: self._refresh_token,
            },
            subentries=subentries,
        )

    async def _async_fetch_initial_relations(self) -> dict[str, Any] | str:
        """Fetch customer-account relations using the just-issued tokens."""
        client = EngieBeApiClient(
            session=async_get_clientsession(self.hass),
            client_id=self._user_input.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
            access_token=self._access_token,
            refresh_token=self._refresh_token,
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

    @callback
    def _async_build_picker_schema(self) -> vol.Schema:
        """Build the multi-select schema for the available customer accounts."""
        options = [
            selector.SelectOptionDict(
                value=account[CONF_CUSTOMER_NUMBER],
                label=subentry_title(account),
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

        already_configured = _collect_configured_identifiers(entry)
        self._available_accounts = _candidates_excluding_configured(
            relations_or_abort,
            already_configured,
        )

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


def _subentry_title(account: dict[str, Any]) -> str:
    """Build a user-friendly subentry title (delegates to shared helper)."""
    return subentry_title(account)


def _collect_configured_identifiers(
    entry: config_entries.ConfigEntry,
) -> set[str]:
    """
    Collect every identifier already claimed by an existing subentry.

    For each customer-account subentry on ``entry`` this gathers the
    subentry's ``unique_id`` plus its stored ``customer_number`` and
    ``business_agreement_number`` data fields. The returned set is the
    union used by the picker to decide whether a candidate from the
    relations payload is already configured.
    """
    configured: set[str] = set()
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_CUSTOMER_ACCOUNT:
            continue
        if subentry.unique_id:
            configured.add(subentry.unique_id)
        data = subentry.data or {}
        stored_can = data.get(CONF_CUSTOMER_NUMBER)
        stored_ban = data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if stored_can:
            configured.add(stored_can)
        if stored_ban:
            configured.add(stored_ban)
    return configured


def _candidates_excluding_configured(
    relations: dict[str, Any],
    already_configured: set[str],
) -> list[dict[str, Any]]:
    """
    Walk the raw relations payload and return picker-ready candidates.

    A candidate is included only when none of its identifiers (CAN +
    every BAN, including inactive ones) intersects ``already_configured``.
    Walking the raw payload -- rather than the flat dicts produced by
    ``extract_accounts`` -- is what lets the picker dedupe against
    legacy subentries whose stored identifier is a now-inactive BAN.
    """
    candidates: list[dict[str, Any]] = []
    for item in relations.get("items", []):
        customer_account = item.get("customerAccount") or {}
        if not customer_account.get("customerAccountNumber"):
            continue
        candidate_ids = set(iter_account_identifiers(customer_account))
        if candidate_ids & already_configured:
            continue
        candidates.append(flatten_customer_account(customer_account))
    return candidates


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
