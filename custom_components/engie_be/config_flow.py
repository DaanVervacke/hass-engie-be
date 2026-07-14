"""Config flow for the ENGIE Belgium integration."""

from __future__ import annotations

import asyncio
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import SectionConfig, section
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

from ._relations import (
    extract_business_agreements,
    subentry_title,
)
from .api import (
    AuthFlowState,
    EngieBeApiClient,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientCommunicationError,
    EngieBeApiClientError,
    EngieBeApiClientMfaError,
    mask_identifier,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_EXPOSE_ALL_ENTITIES,
    CONF_IMPORT_END_DATE,
    CONF_IMPORT_ENERGY_TYPES,
    CONF_IMPORT_HISTORY,
    CONF_IMPORT_INCLUDE_COSTS,
    CONF_IMPORT_START_DATE,
    CONF_MFA_METHOD,
    CONF_REFRESH_TOKEN,
    CONF_SELECTED_ACCOUNTS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_CLIENT_ID,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    ENERGY_TYPE_CONSUMPTION,
    ENERGY_TYPE_GAS,
    ENERGY_TYPE_INJECTION,
    ENERGY_TYPE_OPTIONS,
    LOGGER,
    MAX_UPDATE_INTERVAL_MINUTES,
    MFA_METHOD_EMAIL,
    MFA_METHOD_SMS,
    MIN_UPDATE_INTERVAL_MINUTES,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class EngieBeFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for ENGIE Belgium."""

    VERSION = 5

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
        # Accounts picked in select_accounts; carried into import_history_choice
        # and then import_options.
        self._picked_accounts: list[dict[str, Any]] = []
        # Per-BAN import toggle from import_history_choice; maps BAN -> bool.
        self._import_choice: dict[str, bool] = {}

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
            SUBENTRY_TYPE_BUSINESS_AGREEMENT: CustomerAccountSubentryFlowHandler,
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

            # Abort BEFORE the MFA round-trip if this login is already
            # configured. The MFA-time check in `_handle_mfa_step` stays as
            # defense-in-depth but should never be reached for duplicates now.
            await self.async_set_unique_id(slugify(user_input[CONF_USERNAME]))
            self._abort_if_unique_id_configured()

            try:
                self._client = EngieBeApiClient(
                    session=async_get_clientsession(self.hass),
                    client_id=DEFAULT_CLIENT_ID,
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
                # Reached here only AFTER the MFA code was accepted (steps
                # 9 to 13). The credentials and the code are both valid;
                # the Auth0 session itself failed to complete. Telling
                # the user their password is wrong would be misleading.
                LOGGER.warning(exception)
                errors["base"] = "post_mfa_auth_failed"
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
            self._available_accounts = extract_business_agreements(relations_or_error)

        if not self._available_accounts:
            # Account on this login has zero customer accounts attached;
            # finish without subentries so the user gets a usable entry.
            return self._async_finish_initial_setup(subentries=())

        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_ACCOUNTS, [])
            if not selected:
                errors["base"] = "no_accounts_selected"
            else:
                self._picked_accounts = [
                    account
                    for account in self._available_accounts
                    if account[CONF_BUSINESS_AGREEMENT_NUMBER] in selected
                ]
                return await self.async_step_import_history_choice()

        return self.async_show_form(
            step_id="select_accounts",
            data_schema=self._async_build_picker_schema(),
            errors=errors,
            description_placeholders={"username": username},
        )

    # ------------------------------------------------------------------
    # Import history choice step (chained from select_accounts)
    # ------------------------------------------------------------------

    async def async_step_import_history_choice(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Show one toggle per picked BAN to ask whether to backfill history.

        Each BAN gets a collapsed section (``ban_0``, ``ban_1``, ...) with a
        single ``import_history`` boolean. On submit:
        - If all toggles are off, finish the flow immediately with every picked
          account stored using the import-history-off defaults.
        - If any toggle is on, store the choices in ``_import_choice`` and
          advance to ``async_step_import_options`` showing only the toggled-on
          BANs so the user can fill in the detail fields.
        """
        if user_input is not None:
            self._import_choice = {
                account[CONF_BUSINESS_AGREEMENT_NUMBER]: user_input.get(
                    f"ban_{i}", {}
                ).get(CONF_IMPORT_HISTORY, False)
                for i, account in enumerate(self._picked_accounts)
            }
            if any(self._import_choice.values()):
                return await self.async_step_import_options()

            # All off: build subentries with defaults and finish.
            enriched = _apply_import_defaults(self._picked_accounts)
            subentries = tuple(
                ConfigSubentryData(
                    data=account,
                    subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                    title=subentry_title(account),
                    unique_id=account[CONF_BUSINESS_AGREEMENT_NUMBER],
                )
                for account in enriched
            )
            return self._async_finish_initial_setup(subentries=subentries)

        schema, placeholders = _build_import_history_choice_schema(
            self._picked_accounts
        )
        return self.async_show_form(
            step_id="import_history_choice",
            data_schema=schema,
            description_placeholders=placeholders,
        )

    # ------------------------------------------------------------------
    # Import options step (chained from import_history_choice)
    # ------------------------------------------------------------------

    async def async_step_import_options(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Present per-BAN detail sections for BANs with import_history toggled on.

        Only the BANs whose ``import_history`` toggle was on in the preceding
        ``import_history_choice`` step appear here. Each section is keyed by a
        positional identifier (``ban_0``, ``ban_1``, ...) mapped to the
        filtered list. BANs whose toggle was off are merged back in with
        import-history-off defaults on submit.

        Contains four fields: ``import_energy_types`` (multi-select),
        ``import_include_costs`` (bool), ``import_start_date`` (optional date),
        and ``import_end_date`` (optional date).

        Before rendering, the contracts endpoint is queried for each opted-in
        BAN (with ``include_inactive=True``) so the energy-type selector can be
        filtered to only the divisions that BAN actually carries. A failed fetch
        for any BAN falls back to showing all three options for that BAN.
        """
        opted_in = [
            account
            for account in self._picked_accounts
            if self._import_choice.get(account[CONF_BUSINESS_AGREEMENT_NUMBER], False)
        ]
        opted_out = [
            account
            for account in self._picked_accounts
            if not self._import_choice.get(
                account[CONF_BUSINESS_AGREEMENT_NUMBER], False
            )
        ]

        if user_input is not None:
            enriched_in = _apply_import_options(opted_in, user_input)
            enriched_out = _apply_import_defaults(opted_out)
            # Preserve original pick order: rebuild in _picked_accounts sequence.
            ban_to_data: dict[str, dict[str, Any]] = {
                account[CONF_BUSINESS_AGREEMENT_NUMBER]: account
                for account in enriched_in + enriched_out
            }
            enriched = [
                ban_to_data[acct[CONF_BUSINESS_AGREEMENT_NUMBER]]
                for acct in self._picked_accounts
            ]
            subentries = tuple(
                ConfigSubentryData(
                    data=account,
                    subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                    title=subentry_title(account),
                    unique_id=account[CONF_BUSINESS_AGREEMENT_NUMBER],
                )
                for account in enriched
            )
            return self._async_finish_initial_setup(subentries=subentries)

        divisions_by_ban = await _fetch_divisions_for_opted_in(
            self.hass,
            self._access_token,
            self._refresh_token,
            opted_in,
        )
        schema, placeholders = _build_import_options_schema(
            opted_in, divisions_by_ban=divisions_by_ban
        )
        return self.async_show_form(
            step_id="import_options",
            data_schema=schema,
            description_placeholders=placeholders,
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
                CONF_ACCESS_TOKEN: self._access_token,
                CONF_REFRESH_TOKEN: self._refresh_token,
            },
            subentries=subentries,
        )

    async def _async_fetch_initial_relations(self) -> dict[str, Any] | str:
        """Fetch customer-account relations using the just-issued tokens."""
        client = EngieBeApiClient(
            session=async_get_clientsession(self.hass),
            client_id=DEFAULT_CLIENT_ID,
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
                value=account[CONF_BUSINESS_AGREEMENT_NUMBER],
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
    # Reconfigure flow
    # ------------------------------------------------------------------

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Allow the user to change the preferred MFA method.

        This flow does not re-validate credentials against the ENGIE API;
        it simply updates the stored ``mfa_method`` so the reauth flow
        pre-populates the method selector with the user's last choice
        instead of always defaulting to SMS.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_update_and_abort(
                entry,
                data_updates={
                    CONF_MFA_METHOD: user_input.get(CONF_MFA_METHOD, MFA_METHOD_SMS),
                },
                reason="reconfigure_successful",
            )

        return self.async_show_form(
            step_id="reconfigure",
            description_placeholders={
                "username": entry.data.get(CONF_USERNAME, ""),
            },
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MFA_METHOD,
                        default=entry.data.get(CONF_MFA_METHOD, MFA_METHOD_SMS),
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
                    client_id=DEFAULT_CLIENT_ID,
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
                # Same reasoning as ``async_step_mfa``: reaching this
                # branch means MFA was accepted but the post-MFA Auth0
                # sequence failed. Don't blame the user's password.
                LOGGER.warning(exception)
                errors["base"] = "post_mfa_auth_failed"
            except EngieBeApiClientCommunicationError as exception:
                LOGGER.error(exception)
                errors["base"] = "connection"
            except EngieBeApiClientError as exception:
                LOGGER.exception(exception)
                errors["base"] = "unknown"
            else:
                # Use ``async_update_and_abort`` (not
                # ``async_update_reload_and_abort``) because the integration
                # also registers ``add_update_listener(async_reload_entry)``
                # in ``__init__.py`` to drive options-change and subentry
                # add/remove reloads. Combining a config-entry listener
                # with a reloading method in a config flow is deprecated
                # in HA 2026.6 and errors in 2026.12. The listener still
                # fires for any ``async_update_entry`` write (including
                # this one) and short-circuits to no-op when options /
                # subentry-id set are unchanged, so reauth completion
                # still triggers exactly one reload via the listener.
                # See ``.opencode/audit-v0.10.0b1-prerelease.md`` Blocker
                # B1b and HA dev blog 2026-05-07.
                return self.async_update_and_abort(
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
        self._picked_accounts: list[dict[str, Any]] = []
        # Per-BAN import toggle from import_history_choice; maps BAN -> bool.
        self._import_choice: dict[str, bool] = {}

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

        self._picked_accounts = [
            account
            for account in self._available_accounts
            if account[CONF_BUSINESS_AGREEMENT_NUMBER] in selected
        ]
        return await self.async_step_import_history_choice()

    async def async_step_import_history_choice(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """
        Show one toggle per picked BAN to ask whether to backfill history.

        Mirrors the main-flow step of the same name. On submit, if any toggle
        is on the flow advances to ``async_step_import_options``; if all are
        off it finishes immediately with import-history-off defaults applied.
        """
        if user_input is not None:
            self._import_choice = {
                account[CONF_BUSINESS_AGREEMENT_NUMBER]: user_input.get(
                    f"ban_{i}", {}
                ).get(CONF_IMPORT_HISTORY, False)
                for i, account in enumerate(self._picked_accounts)
            }
            if any(self._import_choice.values()):
                return await self.async_step_import_options()

            enriched = _apply_import_defaults(self._picked_accounts)
            return self._finish_subentry_add(enriched)

        schema, placeholders = _build_import_history_choice_schema(
            self._picked_accounts
        )
        return self.async_show_form(
            step_id="import_history_choice",
            data_schema=schema,
            description_placeholders=placeholders,
        )

    async def async_step_import_options(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """
        Present per-BAN detail sections for BANs with import_history toggled on.

        Mirrors the main-flow step of the same name. Only toggled-on BANs appear;
        toggled-off BANs are merged back in with import-history-off defaults on
        submit.

        Before rendering, the contracts endpoint is queried for each opted-in
        BAN (with ``include_inactive=True``) so the energy-type selector is
        filtered to the divisions that BAN actually carries. A failed fetch for
        any BAN falls back to showing all three options for that BAN.
        """
        opted_in = [
            account
            for account in self._picked_accounts
            if self._import_choice.get(account[CONF_BUSINESS_AGREEMENT_NUMBER], False)
        ]
        opted_out = [
            account
            for account in self._picked_accounts
            if not self._import_choice.get(
                account[CONF_BUSINESS_AGREEMENT_NUMBER], False
            )
        ]

        if user_input is not None:
            enriched_in = _apply_import_options(opted_in, user_input)
            enriched_out = _apply_import_defaults(opted_out)
            ban_to_data: dict[str, dict[str, Any]] = {
                account[CONF_BUSINESS_AGREEMENT_NUMBER]: account
                for account in enriched_in + enriched_out
            }
            enriched = [
                ban_to_data[acct[CONF_BUSINESS_AGREEMENT_NUMBER]]
                for acct in self._picked_accounts
            ]
            return self._finish_subentry_add(enriched)

        entry = self._get_entry()
        divisions_by_ban = await _fetch_divisions_for_opted_in(
            self.hass,
            entry.data.get(CONF_ACCESS_TOKEN),
            entry.data.get(CONF_REFRESH_TOKEN),
            opted_in,
        )
        schema, placeholders = _build_import_options_schema(
            opted_in, divisions_by_ban=divisions_by_ban
        )
        return self.async_show_form(
            step_id="import_options",
            data_schema=schema,
            description_placeholders=placeholders,
        )

    def _finish_subentry_add(
        self,
        picked: list[dict[str, Any]],
    ) -> SubentryFlowResult:
        """
        Create subentry/subentries from the final enriched account dicts.

        Mirrors the logic that existed in ``async_step_user`` before the
        import-options step was introduced. Programmatically adds every
        pick after the first, gates the reload listener via
        ``pending_subentry_target``, and finishes the first pick through
        the framework's normal ``async_create_entry`` path.
        """
        entry = self._get_entry()

        # Programmatically add every pick after the first as a subentry on
        # the parent entry. The first pick is returned via async_create_entry
        # so the framework persists it via the standard ConfigSubentryFlow
        # finish path.
        #
        # Each ``async_add_subentry`` (and the framework's finish-path add for
        # the first pick) schedules this integration's ``async_reload_entry``
        # listener, so a naive multi-pick would reload N times. We arm the
        # one-shot ``pending_subentry_target`` gate with the *final* expected
        # set of business-agreement numbers (BANs) up front; the listener
        # suppresses every reload until that full BAN set is present, then
        # reloads exactly once. The gate keys on BANs (``unique_id``) rather
        # than subentry ids because the first pick's id is generated by the
        # framework finish path and is not known here. The gate is only set
        # when runtime_data exists (the picker can run before
        # async_setup_entry finishes; in that case no listener is registered
        # yet and there is nothing to debounce).
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None:
            existing_bans = {
                sub.unique_id
                for sub in entry.subentries.values()
                if sub.subentry_type == SUBENTRY_TYPE_BUSINESS_AGREEMENT
                and sub.unique_id
            }
            picked_bans = {
                account[CONF_BUSINESS_AGREEMENT_NUMBER] for account in picked
            }
            runtime.pending_subentry_target = existing_bans | picked_bans
            for extra in picked[1:]:
                self.hass.config_entries.async_add_subentry(
                    entry,
                    ConfigSubentry(
                        data=MappingProxyType(extra),
                        subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                        title=_subentry_title(extra),
                        unique_id=extra[CONF_BUSINESS_AGREEMENT_NUMBER],
                    ),
                )
            first = picked[0]
            return self.async_create_entry(
                title=_subentry_title(first),
                data=first,
                unique_id=first[CONF_BUSINESS_AGREEMENT_NUMBER],
            )

        # No runtime_data yet: add extras directly (no listener to debounce)
        # and finish the first pick through the framework.
        for extra in picked[1:]:
            self.hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(extra),
                    subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                    title=_subentry_title(extra),
                    unique_id=extra[CONF_BUSINESS_AGREEMENT_NUMBER],
                ),
            )

        first = picked[0]
        return self.async_create_entry(
            title=_subentry_title(first),
            data=first,
            unique_id=first[CONF_BUSINESS_AGREEMENT_NUMBER],
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
            client_id=DEFAULT_CLIENT_ID,
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
                value=account[CONF_BUSINESS_AGREEMENT_NUMBER],
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
    Collect every BAN already claimed by an existing subentry.

    For each business-agreement subentry on ``entry`` this gathers the
    subentry's ``unique_id`` plus its stored
    ``CONF_BUSINESS_AGREEMENT_NUMBER``. The returned set is what the
    picker uses to decide whether a candidate from the relations
    payload is already configured.

    A subentry that somehow lacks the BAN field (partial picker run,
    manual edit) is tolerated and silently skipped so the picker stays
    functional.
    """
    configured: set[str] = set()
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_BUSINESS_AGREEMENT:
            continue
        if subentry.unique_id:
            configured.add(subentry.unique_id)
        data = subentry.data or {}
        stored_ban = data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if stored_ban:
            configured.add(stored_ban)
    return configured


def _candidates_excluding_configured(
    relations: dict[str, Any],
    already_configured: set[str],
) -> list[dict[str, Any]]:
    """
    Return BAN-keyed picker candidates not already attached to the entry.

    Walks the relations payload via :func:`extract_business_agreements`
    (one entry per active BAN) and filters out any candidate whose BAN
    is already present in ``already_configured`` (see
    :func:`_collect_configured_identifiers`).
    """
    return [
        agreement
        for agreement in extract_business_agreements(relations)
        if agreement[CONF_BUSINESS_AGREEMENT_NUMBER] not in already_configured
    ]


async def _fetch_divisions_for_opted_in(
    hass: HomeAssistant,
    access_token: str | None,
    refresh_token: str | None,
    opted_in: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """
    Build an API client from the given tokens and fetch division sets per BAN.

    Wraps ``_fetch_ban_divisions`` so both the initial-setup flow and the
    subentry-add flow share one call site for client construction and BAN
    extraction. Neither caller needs the ``EngieBeApiClient`` afterwards,
    so it stays scoped to this helper.
    """
    client = EngieBeApiClient(
        session=async_get_clientsession(hass),
        client_id=DEFAULT_CLIENT_ID,
        access_token=access_token,
        refresh_token=refresh_token,
    )
    bans = [account[CONF_BUSINESS_AGREEMENT_NUMBER] for account in opted_in]
    return await _fetch_ban_divisions(client, bans)


async def _fetch_ban_divisions(
    client: EngieBeApiClient,
    bans: list[str],
) -> dict[str, set[str]]:
    """
    Fetch the set of energy divisions present on each BAN, in parallel.

    Queries the energy-contracts endpoint with ``include_inactive=True`` for
    each BAN in ``bans``. Returns a dict mapping BAN -> set of division strings
    (``"ELECTRICITY"``, ``"GAS"``). A failed fetch for any individual BAN is
    logged at DEBUG level and that BAN is omitted from the result, leaving the
    caller to apply a fail-open fallback (show all options). If all fetches
    fail a WARNING is logged.
    """
    if not bans:
        return {}

    async def _fetch_one(ban: str) -> tuple[str, set[str]] | None:
        try:
            payload = await client.async_get_energy_contracts(
                ban, include_inactive=True
            )
        except EngieBeApiClientError as exc:
            LOGGER.debug(
                "Contracts fetch failed for BAN %s, falling back to all options: %s",
                mask_identifier(ban),
                exc,
            )
            return None
        items = payload.get("items") if isinstance(payload, dict) else None
        divisions: set[str] = set()
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    division = item.get("division")
                    if isinstance(division, str) and division:
                        divisions.add(division)
        return (ban, divisions) if divisions else None

    results = await asyncio.gather(*(_fetch_one(ban) for ban in bans))
    divisions_by_ban: dict[str, set[str]] = {}
    for result in results:
        if result is not None:
            ban, divs = result
            divisions_by_ban[ban] = divs

    if not divisions_by_ban and bans:
        LOGGER.warning(
            "Contracts fetch failed for all %d opted-in BAN(s); "
            "showing all energy-type options as fallback",
            len(bans),
        )

    return divisions_by_ban


def _energy_types_for_divisions(divisions: set[str]) -> list[str]:
    """
    Return the energy-type option list restricted to the given set of divisions.

    ``"ELECTRICITY"`` maps to ``[consumption, injection]``;
    ``"GAS"`` maps to ``[gas]``. Unknown division strings are ignored.
    """
    types: list[str] = []
    if "ELECTRICITY" in divisions:
        types.extend([ENERGY_TYPE_CONSUMPTION, ENERGY_TYPE_INJECTION])
    if "GAS" in divisions:
        types.append(ENERGY_TYPE_GAS)
    # Preserve the canonical ENERGY_TYPE_OPTIONS ordering: consumption,
    # injection, gas. The extension order above already matches it.
    return types


def _import_section_schema(  # noqa: PLR0913
    *,
    default_import: bool = False,
    default_energy_types: list[str] | None = None,
    default_include_costs: bool = False,
    default_start_date: str | None = None,
    default_end_date: str | None = None,
    include_history_toggle: bool = True,
    available_energy_types: list[str] | None = None,
) -> section:
    """
    Build the voluptuous section for a single BAN's import options.

    Pass ``include_history_toggle=False`` to omit the ``import_history`` field.
    This is used by the ``import_options`` step (which follows the dedicated
    ``import_history_choice`` step) so the user is not asked to confirm the
    toggle a second time.

    Pass ``available_energy_types`` to restrict the selector to a subset of
    the full ``ENERGY_TYPE_OPTIONS`` list. The default value for the field is
    also narrowed to that subset (unless ``default_energy_types`` is provided
    explicitly, in which case that takes precedence). When ``None`` (the
    default) all three options are shown.
    """
    # Determine which options to expose in the selector.
    shown_options = (
        available_energy_types
        if available_energy_types is not None
        else list(ENERGY_TYPE_OPTIONS)
    )
    # Determine the pre-selected default. If the caller supplied an explicit
    # default list, use it (clamped to the shown options so Voluptuous does
    # not reject a stored value that is no longer available). If no explicit
    # default was given, pre-select all shown options.
    if default_energy_types is not None:
        effective_default = [t for t in default_energy_types if t in shown_options]
    else:
        effective_default = list(shown_options)

    schema_dict: dict[Any, Any] = {}
    if include_history_toggle:
        schema_dict[
            vol.Required(
                CONF_IMPORT_HISTORY,
                default=default_import,
            )
        ] = bool
    schema_dict[
        vol.Required(
            CONF_IMPORT_ENERGY_TYPES,
            default=effective_default,
        )
    ] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=shown_options,
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
            translation_key="energy_type",
        ),
    )
    schema_dict[
        vol.Required(
            CONF_IMPORT_INCLUDE_COSTS,
            default=default_include_costs,
        )
    ] = bool
    # Build date fields separately so we can conditionally attach the
    # default only when a stored value exists. DateSelector validates via
    # cv.date which rejects None, so passing default=None would break schema
    # instantiation if the user has never set a date.
    _start_key = (
        vol.Optional(CONF_IMPORT_START_DATE, default=default_start_date)
        if default_start_date is not None
        else vol.Optional(CONF_IMPORT_START_DATE)
    )
    _end_key = (
        vol.Optional(CONF_IMPORT_END_DATE, default=default_end_date)
        if default_end_date is not None
        else vol.Optional(CONF_IMPORT_END_DATE)
    )
    schema_dict[_start_key] = selector.DateSelector()
    schema_dict[_end_key] = selector.DateSelector()
    return section(
        vol.Schema(schema_dict),
        SectionConfig(collapsed=False),
    )


# Hassfest rejects hardcoded URLs in strings.json descriptions and asks
# integrations to inject them via description_placeholders instead. The
# ``{readme_url}`` placeholder is referenced by every import step description
# in strings.json and populated by the schema builders below.
_README_URL = (
    "https://github.com/DaanVervacke/hass-engie-be/blob/"
    "main/README.md#add-to-the-energy-dashboard"
)

_BLUEPRINT_IMPORT_URL = (
    "https://my.home-assistant.io/redirect/blueprint_import/"
    "?blueprint_url=https%3A%2F%2Fgithub.com%2FDaanVervacke%2Fhass-engie-be"
    "%2Fblob%2Fmain%2Fblueprints"
    "%2Fautomation%2FDaanVervacke%2Fengie_be_daily_history_sync.yaml"
)


def _build_import_options_schema(
    accounts: list[dict[str, Any]],
    *,
    divisions_by_ban: dict[str, set[str]] | None = None,
) -> tuple[vol.Schema, dict[str, str]]:
    """
    Build a per-BAN sectioned schema for the import_options flow step.

    Each business agreement gets its own collapsed section keyed by a
    positional identifier (``ban_0``, ``ban_1``, ...) so strings.json can
    provide a ``name`` template for each slot. The consumption address is
    returned as step-level ``description_placeholders`` under the keys
    ``title_0``, ``title_1``, ... so the frontend substitutes the address
    into the section name (defined in strings.json as ``{title_0}``,
    ``{title_1}``, etc.). The ``readme_url`` placeholder is also injected so
    the step description's markdown link can point at the docs anchor without
    hardcoding a URL in strings.json.

    When ``divisions_by_ban`` is provided, the energy-type selector for each
    BAN is restricted to the divisions present in that BAN's contracts
    (``"ELECTRICITY"`` -> consumption + injection; ``"GAS"`` -> gas). A BAN
    absent from the map (fetch failed) falls back to all three options.

    Returns a ``(schema, placeholders)`` tuple.
    """
    schema_fields: dict[Any, Any] = {}
    placeholders: dict[str, str] = {
        "readme_url": _README_URL,
        "blueprint_import_url": _BLUEPRINT_IMPORT_URL,
    }
    for i, account in enumerate(accounts):
        key = f"ban_{i}"
        title = subentry_title(account)
        placeholders[f"title_{i}"] = title
        ban = account[CONF_BUSINESS_AGREEMENT_NUMBER]
        divisions = (divisions_by_ban or {}).get(ban)
        available = _energy_types_for_divisions(divisions) if divisions else None
        schema_fields[vol.Required(key)] = _import_section_schema(
            default_energy_types=account.get(CONF_IMPORT_ENERGY_TYPES),
            default_include_costs=account.get(CONF_IMPORT_INCLUDE_COSTS, False),
            default_start_date=account.get(CONF_IMPORT_START_DATE),
            default_end_date=account.get(CONF_IMPORT_END_DATE),
            include_history_toggle=False,
            available_energy_types=available,
        )
    return vol.Schema(schema_fields), placeholders


def _build_import_history_choice_schema(
    accounts: list[dict[str, Any]],
) -> tuple[vol.Schema, dict[str, str]]:
    """
    Build a per-BAN sectioned schema for the import_history_choice flow step.

    Each business agreement gets its own collapsed section keyed by a positional
    identifier (``ban_0``, ``ban_1``, ...) with a single ``import_history``
    boolean field. The consumption address is returned as step-level
    ``description_placeholders`` under the keys ``title_0``, ``title_1``, ...

    Returns a ``(schema, placeholders)`` tuple.
    """
    schema_fields: dict[Any, Any] = {}
    placeholders: dict[str, str] = {"readme_url": _README_URL}
    for i, account in enumerate(accounts):
        key = f"ban_{i}"
        title = subentry_title(account)
        placeholders[f"title_{i}"] = title
        schema_fields[vol.Required(key)] = section(
            vol.Schema(
                {
                    vol.Required(
                        CONF_IMPORT_HISTORY,
                        default=False,
                    ): bool,
                }
            ),
            SectionConfig(collapsed=False),
        )
    return vol.Schema(schema_fields), placeholders


def _apply_import_defaults(
    accounts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Apply import-history-off defaults to a list of account dicts.

    Used for accounts whose ``import_history`` toggle was off in the
    ``import_history_choice`` step, or when the choice step finishes with all
    toggles off.
    """
    return [
        {
            **account,
            CONF_IMPORT_HISTORY: False,
            CONF_IMPORT_ENERGY_TYPES: list(ENERGY_TYPE_OPTIONS),
            CONF_IMPORT_INCLUDE_COSTS: False,
            CONF_IMPORT_START_DATE: None,
            CONF_IMPORT_END_DATE: None,
        }
        for account in accounts
    ]


def _apply_import_options(
    accounts: list[dict[str, Any]],
    user_input: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Merge per-BAN import options from ``user_input`` into account dicts.

    The schema uses positional section keys (``ban_0``, ``ban_1``, ...) so
    this function maps position back to account using the same index.
    """
    enriched: list[dict[str, Any]] = []
    for i, account in enumerate(accounts):
        section_data = user_input.get(f"ban_{i}", {})
        enriched.append(
            {
                **account,
                CONF_IMPORT_HISTORY: True,
                CONF_IMPORT_ENERGY_TYPES: section_data.get(
                    CONF_IMPORT_ENERGY_TYPES, list(ENERGY_TYPE_OPTIONS)
                ),
                CONF_IMPORT_INCLUDE_COSTS: section_data.get(
                    CONF_IMPORT_INCLUDE_COSTS, False
                ),
                CONF_IMPORT_START_DATE: section_data.get(CONF_IMPORT_START_DATE),
                CONF_IMPORT_END_DATE: section_data.get(CONF_IMPORT_END_DATE),
            }
        )
    return enriched


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
                    vol.Optional(
                        CONF_EXPOSE_ALL_ENTITIES,
                        default=self.config_entry.options.get(
                            CONF_EXPOSE_ALL_ENTITIES, False
                        ),
                    ): selector.BooleanSelector(),
                },
            ),
        )
