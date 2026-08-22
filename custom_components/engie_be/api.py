"""ENGIE Belgium API client implementing OAuth2/PKCE with MFA (SMS or email)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import socket
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, date, datetime
from http import HTTPStatus
from typing import Any, Literal, NoReturn, overload

import aiohttp

from ._api_logging import RequestLogger, _redact_text
from .const import (
    ACCOUNTS_BASE_URL,
    API_BASE_URL,
    AUTH_BASE_URL,
    BILLING_BASE_URL,
    BOOLEAN_FEATURE_FLAG_BASE_URL,
    BUSINESS_AGREEMENTS_BASE_URL,
    ENERGY_INSIGHTS_V2_BASE_URL,
    EPEX_BASE_URL,
    HAPPY_HOUR_BASE_URL,
    HAPPY_HOURS_SERVICE_ENABLED_KEY,
    LOGGER,
    MFA_METHOD_SMS,
    OAUTH_AUDIENCE,
    OAUTH_SCOPES,
    PEAKS_BASE_URL,
    PREMISES_BASE_URL,
    REDIRECT_URI,
    SOLAR_SURPLUS_SHOWN_DASHBOARD_KEY,
    TOU_FLAG_KEY,
    USER_AGENT_BROWSER,
    USER_AGENT_NATIVE,
)


class EngieBeApiClientError(Exception):
    """Base exception for ENGIE Belgium API client errors."""


class EngieBeApiClientCommunicationError(EngieBeApiClientError):
    """Exception for communication errors (timeout, network)."""


class EngieBeApiClientAuthenticationError(EngieBeApiClientError):
    """Exception for authentication errors (bad credentials, expired token)."""


class EngieBeApiClientMfaError(EngieBeApiClientError):
    """Exception for MFA-related errors (invalid code)."""


class EpexNotPublishedError(EngieBeApiClientError):
    """Raised when the EPEX endpoint returns 404 (prices not yet published)."""


def _raise_auth_error(status: int) -> NoReturn:
    """Raise an authentication error tagged with the offending HTTP status."""
    msg = f"Authentication failed ({status})"
    raise EngieBeApiClientAuthenticationError(msg)


@dataclass
class AuthFlowState:
    """Intermediate state kept between config-flow steps."""

    session: aiohttp.ClientSession
    authorize_state: str
    login_state: str
    mfa_challenge_state: str
    code_verifier: str


_BROWSER_HEADERS: dict[str, str] = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "User-Agent": USER_AGENT_BROWSER,
    "sec-ch-ua": ('"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"'),
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
}


def _base64url(data: bytes) -> str:
    """Encode bytes to a Base64-URL string (no padding)."""
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_pkce() -> tuple[str, str, str, str]:
    """Return (state, nonce, code_verifier, code_challenge)."""
    state = os.urandom(16).hex()
    nonce = os.urandom(16).hex()
    code_verifier = _base64url(os.urandom(32))
    code_challenge = _base64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
    return state, nonce, code_verifier, code_challenge


def _extract_from_body(body: str, pattern: str) -> str | None:
    """Extract a value from an HTML body using a regex pattern."""
    match = re.search(pattern, body)
    return match.group(1) if match else None


# Public re-export so non-HTTP modules mask identifiers with the same scheme
# as the HTTP layer. Keep in sync with ``_PARTIAL_MASK_BODY_KEYS`` semantics.
mask_identifier = _redact_text


class EngieBeApiClient:
    """ENGIE Belgium API client (OAuth2/PKCE + MFA, token refresh, data fetch)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: str,
        access_token: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        """Initialise the client."""
        self._session = session
        self._client_id = client_id
        self.access_token = access_token
        self.refresh_token = refresh_token
        # Serialises async_refresh_token against itself. ENGIE rotates the
        # refresh token on every call, so concurrent refreshes would consume
        # the same token twice and 400 the second caller.
        self._token_lock = asyncio.Lock()
        self._req_logger = RequestLogger()

    async def async_start_authentication(
        self,
        username: str,
        password: str,
        mfa_method: str = MFA_METHOD_SMS,
    ) -> AuthFlowState:
        """Execute auth steps 1-7 (trigger SMS or email MFA), return flow state."""
        auth_session = aiohttp.ClientSession()
        try:
            return await self._run_auth_steps_1_to_7(
                auth_session, username, password, mfa_method
            )
        except Exception:
            await auth_session.close()
            raise

    async def async_complete_authentication(
        self,
        flow_state: AuthFlowState,
        mfa_code: str,
        mfa_method: str = MFA_METHOD_SMS,
    ) -> tuple[str, str]:
        """
        Submit the MFA code and exchange the auth code for (access, refresh).

        Keeps the temporary auth session open on ``EngieBeApiClientMfaError``
        so the caller can retry with a corrected code.
        """
        try:
            access_token, refresh_token = await self._run_auth_steps_8_to_13(
                flow_state, mfa_code, mfa_method=mfa_method
            )
        except EngieBeApiClientMfaError:
            raise
        except BaseException:
            await flow_state.session.close()
            raise
        else:
            await flow_state.session.close()
            self.access_token = access_token
            self.refresh_token = refresh_token
            return access_token, refresh_token

    async def async_refresh_token(self) -> tuple[str, str]:
        """
        Refresh the access token, returning the rotated (access, refresh) pair.

        Serialised against concurrent callers: if another caller already
        rotated while this one was waiting on the lock, returns the fresh
        pair without re-issuing the request.
        """
        if not self.refresh_token:
            msg = "No refresh token available"
            raise EngieBeApiClientAuthenticationError(msg)

        # Snapshot before awaiting the lock to detect a racing refresh.
        refresh_at_entry = self.refresh_token

        async with self._token_lock:
            if self.refresh_token != refresh_at_entry:
                racing_access_token = self.access_token
                if racing_access_token is None:
                    msg = "Racing token refresh left access_token unset"
                    raise EngieBeApiClientAuthenticationError(msg)
                LOGGER.debug(
                    "Token refresh: racing caller already rotated tokens; "
                    "returning fresh pair without re-issuing request"
                )
                return racing_access_token, self.refresh_token

            data = {
                "refresh_token": self.refresh_token,
                "audience": OAUTH_AUDIENCE,
                "grant_type": "refresh_token",
                "scope": OAUTH_SCOPES,
                "redirect_uri": REDIRECT_URI,
                "client_id": self._client_id,
            }
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT_NATIVE,
            }

            old_refresh_tail = _redact_text(self.refresh_token)

            result = await self._api_wrapper(
                session=self._session,
                method="POST",
                url=f"{AUTH_BASE_URL}/oauth/token",
                data=data,
                headers=headers,
                json_response=True,
            )

            self.access_token = result["access_token"]
            self.refresh_token = result["refresh_token"]

            LOGGER.debug(
                "Token refresh: rotated refresh_token %s -> %s, "
                "access_expires_in=%s refresh_expires_in=%s",
                old_refresh_tail,
                _redact_text(self.refresh_token),
                result.get("expires_in"),
                result.get("refresh_token_expires_in"),
            )

            return self.access_token, self.refresh_token

    async def async_get_prices(self, business_agreement_number: str) -> dict[str, Any]:
        """Fetch energy prices for a 12-digit BAN."""
        url = (
            f"{API_BASE_URL}/business-agreements/"
            f"{business_agreement_number.replace(' ', '')}/supplier-energy-prices"
        )
        headers = {
            "User-Agent": USER_AGENT_BROWSER,
            "Accept": "application/json, application/problem+json",
            "authorization": f"Bearer {self.access_token}",
        }
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            params={"maxGranularity": "MONTHLY"},
            json_response=True,
        )

    async def async_get_energy_contracts(
        self,
        business_agreement_number: str,
        *,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        """
        Fetch energy contracts for a BAN.

        ``include_inactive=True`` widens the ENGIE filter to include
        historical contracts. The ``includeActions`` and
        ``includeSapData`` query flags are required for ENGIE to return
        the ``productConfiguration`` block.
        """
        url = (
            f"{BUSINESS_AGREEMENTS_BASE_URL}/business-agreements/"
            f"{business_agreement_number.replace(' ', '')}/energy-contracts"
        )
        headers = self._authenticated_headers(user_agent=USER_AGENT_BROWSER)
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            params={
                "filter": (
                    "ALL_ENERGY_CONTRACTS"
                    if include_inactive
                    else "ONLY_ACTIVE_ENERGY_CONTRACTS"
                ),
                "includeActions": "true",
                "includeSapData": "true",
            },
            json_response=True,
        )

    async def async_get_service_point(self, ean: str) -> dict[str, Any]:
        """Fetch service-point metadata for a single EAN."""
        url = f"{PREMISES_BASE_URL}/service-points/{ean}"
        headers = {
            "User-Agent": USER_AGENT_BROWSER,
            "Accept": "application/json, application/problem+json",
            "authorization": f"Bearer {self.access_token}",
        }
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            json_response=True,
        )

    async def async_get_customer_account_relations(self) -> dict[str, Any]:
        """
        Fetch customer accounts accessible to the logged-in user.

        The ``withBusinessAgreements=SMART_APP`` query flag is required
        for ENGIE to include the active business agreement inline.
        """
        url = f"{ACCOUNTS_BASE_URL}/customer-account-relations"
        headers = self._authenticated_headers()
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            params={"withBusinessAgreements": "SMART_APP"},
            json_response=True,
        )

    async def async_get_monthly_peaks(
        self,
        business_agreement_number: str,
        year: int,
        month: int,
    ) -> dict[str, Any]:
        """
        Fetch capacity-tariff (captar) peaks for a given month.

        Despite the ``contract-accounts`` path segment, ENGIE expects a
        BAN here, not a CAN (a CAN returns HTTP 500).
        """
        url = (
            f"{PEAKS_BASE_URL}/private/customers/me/contract-accounts/"
            f"{business_agreement_number.replace(' ', '')}/energy-insights/peaks"
        )
        headers = self._authenticated_headers()
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            params={"year": str(year), "month": str(month)},
            json_response=True,
        )

    async def async_get_happy_hour_event(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """
        Fetch the upcoming Happy Hours event for a BAN.

        Response is ``{}`` when no event is scheduled, otherwise carries
        ``today`` and/or ``tomorrow`` keys.
        """
        url = (
            f"{HAPPY_HOUR_BASE_URL}/business-agreements/"
            f"{business_agreement_number.replace(' ', '')}/happy-hour-event"
        )
        headers = self._authenticated_headers()
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            json_response=True,
        )

    async def async_get_month_report(
        self,
        business_agreement_number: str,
        year: int,
        month: int,
    ) -> dict[str, Any]:
        """Fetch the Happy Hours month report for a BAN."""
        ban = business_agreement_number.replace(" ", "")
        url = (
            f"{HAPPY_HOUR_BASE_URL}/business-agreements/"
            f"{ban}/month-report/{year:04d}-{month:02d}"
        )
        headers = self._authenticated_headers()
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            json_response=True,
        )

    async def async_get_usage_details(
        self,
        business_agreement_number: str,
        start_date: date,
        end_date: date,
        granularity: str = "HOURLY",
        *,
        include_simulation: bool = False,
    ) -> dict[str, Any]:
        """
        Fetch historical usage details for a BAN over ``[start_date, end_date)``.

        ``include_simulation`` defaults to ``False`` so projected values
        never enter long-term statistics.
        """
        ban = business_agreement_number.replace(" ", "")
        url = f"{ENERGY_INSIGHTS_V2_BASE_URL}/business-agreements/{ban}/usage-details"
        headers = self._authenticated_headers()
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            params={
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "granularity": granularity,
                "includeSimulation": "true" if include_simulation else "false",
            },
            json_response=True,
        )

    async def async_get_solar_surplus_forecasts(
        self,
        business_agreement_number: str,
        delivery_point_id: str,
    ) -> dict[str, Any]:
        """
        Fetch Solar Surplus injection forecasts for a delivery point.

        ``delivery_point_id`` is typically ``{EAN}_ID1``. Customers with
        no installation get ``level: NO_DATA`` slots.
        """
        ban = business_agreement_number.replace(" ", "")
        url = (
            f"{HAPPY_HOUR_BASE_URL}/business-agreements/"
            f"{ban}/solar-surplus/{delivery_point_id}/forecasts"
        )
        headers = self._authenticated_headers()
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            json_response=True,
        )

    async def async_get_happy_hours_service_enabled_flag(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """
        Fetch the ``happy-hours-service-enabled`` boolean feature flag for a BAN.

        Authoritative signal for enrolment: ``/happy-hour-event`` returns
        ``{}`` for both un-enrolled and enrolled-without-window states.
        """
        return await self._async_query_boolean_feature_flag(
            HAPPY_HOURS_SERVICE_ENABLED_KEY,
            business_agreement_number,
        )

    async def async_get_solar_surplus_shown_dashboard_flag(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """Fetch the ``solar-surplus-shown-dashboard`` boolean feature flag."""
        return await self._async_query_boolean_feature_flag(
            SOLAR_SURPLUS_SHOWN_DASHBOARD_KEY,
            business_agreement_number,
        )

    async def async_get_tou_schedules(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """
        Fetch the time-of-use tariff schedules for a BAN.

        Endpoint responds regardless of the ``tou-is-active`` flag.
        """
        ban = business_agreement_number.replace(" ", "")
        url = f"{BILLING_BASE_URL}/business-agreements/{ban}/tou-schedules"
        headers = self._authenticated_headers()
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            json_response=True,
        )

    async def async_get_account_balance(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """Fetch the current account balance for a BAN."""
        ban = business_agreement_number.replace(" ", "")
        url = f"{BILLING_BASE_URL}/business-agreements/{ban}/account-balance"
        headers = self._authenticated_headers()
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            json_response=True,
        )

    async def async_get_tou_is_active_flag(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """Fetch the ``tou-is-active`` boolean feature flag for a BAN."""
        return await self._async_query_boolean_feature_flag(
            TOU_FLAG_KEY,
            business_agreement_number,
        )

    async def _async_query_boolean_feature_flag(
        self,
        flag_name: str,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """Query a single boolean feature flag for a BAN."""
        headers = self._authenticated_headers(
            extra={"Content-Type": "application/json"},
        )
        body = {
            "name": flag_name,
            "additionalContext": {
                "contractAccountId": business_agreement_number.replace(" ", ""),
                "platform": "android",
                "platformVersion": "16",
                "appVersion": "4.19.0.703",
            },
        }
        return await self._api_wrapper(
            session=self._session,
            method="POST",
            url=BOOLEAN_FEATURE_FLAG_BASE_URL,
            headers=headers,
            json_body=body,
            json_response=True,
        )

    async def async_get_epex_prices(
        self,
        from_dt: datetime,
        to_dt: datetime,
        *,
        granularity: str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch EPEX day-ahead market prices for the given UTC window.

        ``granularity`` accepts ``"HOURLY"`` (default) or ``"QUARTER_HOURLY"``.
        Raises :class:`EpexNotPublishedError` on 404 so callers can treat
        it as a soft state.
        """

        def _iso_ms_z(value: datetime) -> str:
            """Render a datetime as ISO-8601 UTC with ms precision + ``Z``."""
            utc_value = value.astimezone(UTC)
            iso = utc_value.isoformat(timespec="milliseconds").removesuffix("+00:00")
            return f"{iso}Z"

        params = {"from": _iso_ms_z(from_dt), "to": _iso_ms_z(to_dt)}
        if granularity is not None:
            params["granularity"] = granularity

        # 404 means "not yet published" here, handle it specially below.
        try:
            headers = self._authenticated_headers(user_agent=USER_AGENT_BROWSER)
            return await self._api_wrapper(
                session=self._session,
                method="GET",
                url=EPEX_BASE_URL,
                headers=headers,
                params=params,
                json_response=True,
            )
        except EngieBeApiClientCommunicationError as err:
            if (
                isinstance(err.__cause__, aiohttp.ClientResponseError)
                and err.__cause__.status == HTTPStatus.NOT_FOUND
            ):
                msg = (
                    "EPEX prices not yet published for "
                    f"{params['from']}..{params['to']}"
                )
                raise EpexNotPublishedError(msg) from err
            raise

    async def _run_auth_steps_1_to_7(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        mfa_method: str,
    ) -> AuthFlowState:
        """Run auth steps 1-7 (authorize, then trigger SMS or email MFA)."""
        state, nonce, code_verifier, code_challenge = _generate_pkce()

        # Step 1: GET /authorize
        authorize_params = {
            "redirect_uri": REDIRECT_URI,
            "client_id": self._client_id,
            "response_type": "code",
            "ui_locales": "nl",
            "state": state,
            "nonce": nonce,
            "scope": OAUTH_SCOPES,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "audience": OAUTH_AUDIENCE,
            "app_scheme": "be-engie-smart",
            "cancel_redirect": "be-engie-smart://cancel-registration-redirect",
        }
        body = await self._api_wrapper(
            session=session,
            method="GET",
            url=f"{AUTH_BASE_URL}/authorize",
            params=authorize_params,
            headers=_BROWSER_HEADERS,
            allow_redirects=False,
        )
        authorize_state = _extract_from_body(body, r"state=([a-zA-Z0-9_-]+)")
        if not authorize_state:
            msg = "Failed to extract authorize state from response"
            raise EngieBeApiClientAuthenticationError(msg)

        LOGGER.debug("Auth step 1 complete: got authorizeState")

        # Step 2: GET /u/login/identifier (load login page)
        await self._api_wrapper(
            session=session,
            method="GET",
            url=f"{AUTH_BASE_URL}/u/login/identifier",
            params={"state": authorize_state, "ui_locales": "nl"},
            headers=_BROWSER_HEADERS,
            allow_redirects=False,
        )
        LOGGER.debug("Auth step 2 complete: loaded login page")

        # Step 3: POST /u/login/identifier (submit username)
        await self._api_wrapper(
            session=session,
            method="POST",
            url=f"{AUTH_BASE_URL}/u/login/identifier",
            params={"state": authorize_state, "ui_locales": "nl"},
            headers=_BROWSER_HEADERS,
            data={
                "state": authorize_state,
                "allow-passkeys": "true",
                "username": username,
                "js-available": "true",
                "webauthn-available": "true",
                "is-brave": "false",
                "webauthn-platform-available": "true",
                "ulp-remember-me-present": "true",
                "ulp-remember-me": "on",
            },
            allow_redirects=False,
        )
        LOGGER.debug("Auth step 3 complete: submitted username")

        # Step 4: GET /u/login/password (load password page)
        await self._api_wrapper(
            session=session,
            method="GET",
            url=f"{AUTH_BASE_URL}/u/login/password",
            params={"state": authorize_state, "ui_locales": "nl"},
            headers=_BROWSER_HEADERS,
            allow_redirects=False,
        )
        LOGGER.debug("Auth step 4 complete: loaded password page")

        # Step 5: POST /u/login/password (submit credentials)
        body = await self._api_wrapper(
            session=session,
            method="POST",
            url=f"{AUTH_BASE_URL}/u/login/password",
            params={"state": authorize_state, "ui_locales": "nl"},
            headers=_BROWSER_HEADERS,
            data={
                "state": authorize_state,
                "username": username,
                "password": password,
                "js-available": "true",
                "webauthn-available": "true",
                "is-brave": "false",
                "webauthn-platform-available": "true",
            },
            allow_redirects=False,
        )
        login_state = _extract_from_body(body, r"state=([a-zA-Z0-9_-]+)")
        if not login_state:
            msg = "Login failed: could not extract login state (bad credentials?)"
            raise EngieBeApiClientAuthenticationError(msg)

        LOGGER.debug("Auth step 5 complete: got loginState")

        # Step 6: GET /authorize/resume (triggers MFA)
        body = await self._api_wrapper(
            session=session,
            method="GET",
            url=f"{AUTH_BASE_URL}/authorize/resume",
            params={"state": login_state},
            headers=_BROWSER_HEADERS,
            allow_redirects=False,
        )
        mfa_challenge_state = _extract_from_body(body, r"state=([a-zA-Z0-9_-]+)")
        if not mfa_challenge_state:
            msg = "Failed to extract MFA challenge state"
            raise EngieBeApiClientAuthenticationError(msg)

        LOGGER.debug("Auth step 6 complete: got mfaChallengeState")

        if mfa_method == MFA_METHOD_SMS:
            # Step 7: GET /u/mfa-sms-challenge (triggers SMS send)
            await self._api_wrapper(
                session=session,
                method="GET",
                url=f"{AUTH_BASE_URL}/u/mfa-sms-challenge",
                params={"state": mfa_challenge_state, "ui_locales": "nl"},
                headers=_BROWSER_HEADERS,
                allow_redirects=False,
            )
            LOGGER.debug("Auth step 7 complete: SMS sent to user")
        else:
            # Email MFA: switch authenticator via ALT steps 1-4, skipping step 7.
            await self._switch_to_email_mfa(session, mfa_challenge_state)

        return AuthFlowState(
            session=session,
            authorize_state=authorize_state,
            login_state=login_state,
            mfa_challenge_state=mfa_challenge_state,
            code_verifier=code_verifier,
        )

    async def _run_auth_steps_8_to_13(
        self,
        flow_state: AuthFlowState,
        mfa_code: str,
        *,
        mfa_method: str = MFA_METHOD_SMS,
    ) -> tuple[str, str]:
        """Run auth steps 8-13 (submit MFA -> get tokens)."""
        session = flow_state.session

        if mfa_method == MFA_METHOD_SMS:
            body = await self._submit_sms_mfa(flow_state, mfa_code)
        else:
            body = await self._submit_email_mfa(flow_state, mfa_code)

        # Missing state means the code was wrong (server re-served the form).
        another_state = _extract_from_body(body, r"state=([a-zA-Z0-9_-]+)")
        if not another_state:
            msg = "Invalid MFA code or failed to proceed after MFA submission"
            raise EngieBeApiClientMfaError(msg)

        LOGGER.debug("Auth step 8 complete: MFA code accepted")

        # Step 9: GET /authorize/resume (post-MFA). Auth0 either short-circuits
        # to the callback URI with the code in Location (outcome A) or
        # redirects to a passkey-enrollment interstitial (outcome B). For A
        # the body ``state`` is the OAuth state, not a passKeyState, so the
        # Location header is the authoritative source.
        body, resp_headers = await self._api_wrapper(
            session=session,
            method="GET",
            url=f"{AUTH_BASE_URL}/authorize/resume",
            params={"state": flow_state.login_state},
            headers=_BROWSER_HEADERS,
            allow_redirects=False,
            include_headers=True,
        )

        location = resp_headers.get("Location", "")
        if location.startswith(REDIRECT_URI):
            # Outcome A: Auth code is already in the Location header.
            auth_code = _extract_from_body(location, r"code=([a-zA-Z0-9_-]+)")
            if not auth_code:
                msg = (
                    "Step 9 redirected to the native callback URI but the "
                    "authorization code was missing"
                )
                raise EngieBeApiClientAuthenticationError(msg)
            LOGGER.debug(
                "Auth step 9 complete: got authorization code from callback "
                "Location header (no passkey enrollment)"
            )
        else:
            # Outcome B: Passkey-enrollment interstitial.
            passkey_state = _extract_from_body(body, r"state=([a-zA-Z0-9_-]+)")
            if not passkey_state:
                msg = "Failed to extract passkey enrollment state"
                raise EngieBeApiClientAuthenticationError(msg)

            LOGGER.debug("Auth step 9 complete: got passKeyState")

            # Step 10: GET /u/passkey-enrollment (load passkey page)
            await self._api_wrapper(
                session=session,
                method="GET",
                url=f"{AUTH_BASE_URL}/u/passkey-enrollment",
                params={"state": passkey_state, "ui_locales": "nl"},
                headers=_BROWSER_HEADERS,
                allow_redirects=False,
            )
            LOGGER.debug("Auth step 10 complete: loaded passkey page")

            # Step 11: abort enrollment. Redirect target uses app-scheme
            # which aiohttp cannot follow, so allow_redirects=False.
            await self._api_wrapper(
                session=session,
                method="POST",
                url=f"{AUTH_BASE_URL}/u/passkey-enrollment",
                params={"state": passkey_state, "ui_locales": "nl"},
                headers=_BROWSER_HEADERS,
                data={
                    "state": passkey_state,
                    "action": "abort-passkey-enrollment",
                },
                allow_redirects=False,
            )
            LOGGER.debug("Auth step 11 complete: passkey enrollment aborted")

            # Step 12: extract auth code (uses loginState, not passKeyState).
            # Some responses return the code only in the Location header.
            body, resp_headers = await self._api_wrapper(
                session=session,
                method="GET",
                url=f"{AUTH_BASE_URL}/authorize/resume",
                params={"state": flow_state.login_state},
                headers=_BROWSER_HEADERS,
                allow_redirects=False,
                include_headers=True,
            )
            auth_code = _extract_from_body(body, r"code=([a-zA-Z0-9_-]+)")
            if auth_code:
                LOGGER.debug("Auth step 12 complete: got authorization code from body")
            else:
                location = resp_headers.get("Location", "")
                auth_code = _extract_from_body(location, r"code=([a-zA-Z0-9_-]+)")
                if auth_code:
                    LOGGER.debug(
                        "Auth step 12 complete: got authorization code from "
                        "Location header"
                    )
                else:
                    msg = "Failed to extract auth code from body and Location header"
                    raise EngieBeApiClientAuthenticationError(msg)

        # Step 13: POST /oauth/token (exchange code for tokens)
        token_result = await self._api_wrapper(
            session=session,
            method="POST",
            url=f"{AUTH_BASE_URL}/oauth/token",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT_NATIVE,
            },
            data={
                "code": auth_code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
                "code_verifier": flow_state.code_verifier,
                "client_id": self._client_id,
            },
            json_response=True,
            allow_redirects=False,
        )

        access_token: str = token_result["access_token"]
        refresh_token: str = token_result["refresh_token"]

        LOGGER.debug("Auth step 13 complete: tokens obtained")
        return access_token, refresh_token

    async def _submit_sms_mfa(
        self,
        flow_state: AuthFlowState,
        mfa_code: str,
    ) -> str:
        """Submit an SMS MFA code (auth step 8)."""
        # Suppress automatic error handling so a wrong-code 400 becomes an MfaError.
        return await self._api_wrapper(
            session=flow_state.session,
            method="POST",
            url=f"{AUTH_BASE_URL}/u/mfa-sms-challenge",
            params={
                "state": flow_state.mfa_challenge_state,
                "ui_locales": "nl",
            },
            headers=_BROWSER_HEADERS,
            data={
                "state": flow_state.mfa_challenge_state,
                "code": mfa_code,
            },
            allow_redirects=False,
            raise_on_error=False,
        )

    async def _submit_email_mfa(
        self,
        flow_state: AuthFlowState,
        mfa_code: str,
    ) -> str:
        """Submit an email MFA code (auth step 8.ALT-5)."""
        return await self._api_wrapper(
            session=flow_state.session,
            method="POST",
            url=f"{AUTH_BASE_URL}/u/mfa-email-challenge",
            params={
                "state": flow_state.mfa_challenge_state,
                "ui_locales": "nl",
            },
            headers=_BROWSER_HEADERS,
            data={
                "state": flow_state.mfa_challenge_state,
                "code": mfa_code,
                "action": "default",
            },
            allow_redirects=False,
            raise_on_error=False,
        )

    async def _switch_to_email_mfa(
        self,
        session: aiohttp.ClientSession,
        challenge_state: str,
    ) -> None:
        """Run the authenticator-switching detour (auth ALT steps 1-4)."""
        # ALT-1: POST /u/mfa-sms-challenge with action=pick-authenticator
        await self._api_wrapper(
            session=session,
            method="POST",
            url=f"{AUTH_BASE_URL}/u/mfa-sms-challenge",
            params={"state": challenge_state, "ui_locales": "nl"},
            headers=_BROWSER_HEADERS,
            data={
                "state": challenge_state,
                "action": "pick-authenticator",
            },
            allow_redirects=False,
        )
        LOGGER.debug("Auth ALT-1 complete: picked authenticator")

        # ALT-2: GET /u/mfa-login-options (load MFA method selection)
        await self._api_wrapper(
            session=session,
            method="GET",
            url=f"{AUTH_BASE_URL}/u/mfa-login-options",
            params={"state": challenge_state, "ui_locales": "nl"},
            headers=_BROWSER_HEADERS,
            allow_redirects=False,
        )
        LOGGER.debug("Auth ALT-2 complete: loaded login options")

        # ALT-3: POST /u/mfa-login-options with action=email::1
        await self._api_wrapper(
            session=session,
            method="POST",
            url=f"{AUTH_BASE_URL}/u/mfa-login-options",
            params={"state": challenge_state, "ui_locales": "nl"},
            headers=_BROWSER_HEADERS,
            data={
                "state": challenge_state,
                "action": "email::1",
            },
            allow_redirects=False,
        )
        LOGGER.debug("Auth ALT-3 complete: selected email MFA")

        # ALT-4: GET /u/mfa-email-challenge (triggers email send)
        await self._api_wrapper(
            session=session,
            method="GET",
            url=f"{AUTH_BASE_URL}/u/mfa-email-challenge",
            params={"state": challenge_state, "ui_locales": "nl"},
            headers=_BROWSER_HEADERS,
            allow_redirects=False,
        )
        LOGGER.debug("Auth ALT-4 complete: email challenge triggered")

    def _authenticated_headers(
        self,
        user_agent: str = USER_AGENT_NATIVE,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Return the standard authenticated JSON header dict, merging ``extra``."""
        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, application/problem+json",
            "authorization": f"Bearer {self.access_token}",
        }
        if extra:
            headers.update(extra)
        return headers

    @overload
    async def _api_wrapper(
        self,
        *,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        json_response: Literal[True],
        allow_redirects: bool = False,
        raise_on_error: bool = True,
        include_headers: Literal[False] = False,
    ) -> dict[str, Any]: ...

    @overload
    async def _api_wrapper(
        self,
        *,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        json_response: Literal[False] = False,
        allow_redirects: bool = False,
        raise_on_error: bool = True,
        include_headers: Literal[True],
    ) -> tuple[str, dict[str, str]]: ...

    @overload
    async def _api_wrapper(
        self,
        *,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        json_response: Literal[False] = False,
        allow_redirects: bool = False,
        raise_on_error: bool = True,
        include_headers: Literal[False] = False,
    ) -> str: ...

    async def _api_wrapper(  # noqa: PLR0912, PLR0913
        self,
        *,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        json_response: bool = False,
        allow_redirects: bool = False,
        raise_on_error: bool = True,
        include_headers: bool = False,
    ) -> Any:
        """
        Execute an HTTP request with error handling and DEBUG-level tracing.

        With ``raise_on_error=False`` the caller interprets non-success
        codes. With ``include_headers=True`` returns ``(body, headers)``.
        """
        ctx = self._req_logger.new_context(method, url)

        if ctx is not None:
            self._req_logger.request(
                ctx,
                params=params,
                headers=headers,
                body=data if data is not None else json_body,
            )

        try:
            async with asyncio.timeout(30):
                response = await session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=data,
                    json=json_body,
                    params=params,
                    allow_redirects=allow_redirects,
                )
                if raise_on_error:
                    if response.status in (
                        HTTPStatus.UNAUTHORIZED,
                        HTTPStatus.FORBIDDEN,
                    ):
                        if ctx is not None:
                            self._req_logger.error(ctx, status=response.status)
                        _raise_auth_error(response.status)

                    # Do not raise on 3xx: redirects are handled manually.
                    if response.status >= HTTPStatus.BAD_REQUEST:
                        if ctx is not None:
                            self._req_logger.error(ctx, status=response.status)
                        response.raise_for_status()

                if json_response:
                    result = await response.json()
                else:
                    result = await response.text()

                if ctx is not None:
                    resp_ct = (
                        response.headers.get("Content-Type")
                        if hasattr(response, "headers")
                        else None
                    )
                    self._req_logger.response(
                        ctx, status=response.status, ct=resp_ct, body=result
                    )

                if include_headers:
                    return result, dict(response.headers)
                return result

        except EngieBeApiClientError:
            raise
        except TimeoutError as exception:
            if ctx is not None:
                self._req_logger.error(ctx, exc_name="timeout")  # noqa: TRY400
            msg = (
                f"Timeout communicating with Engie API ({exception.__class__.__name__})"
            )
            raise EngieBeApiClientCommunicationError(msg) from exception
        except (aiohttp.ClientError, socket.gaierror) as exception:
            if ctx is not None:
                self._req_logger.error(  # noqa: TRY400
                    ctx, exc_name=exception.__class__.__name__
                )
            msg = f"Error communicating with Engie API ({exception.__class__.__name__})"
            raise EngieBeApiClientCommunicationError(msg) from exception
        except Exception as exception:
            if ctx is not None:
                self._req_logger.error(  # noqa: G201
                    ctx, exc_name=exception.__class__.__name__, exc_info=True
                )
            msg = (
                "Unexpected error communicating with Engie API "
                f"({exception.__class__.__name__})"
            )
            raise EngieBeApiClientError(msg) from exception
