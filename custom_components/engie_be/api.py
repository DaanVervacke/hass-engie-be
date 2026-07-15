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
from typing import Any, NoReturn

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

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EngieBeApiClientError(Exception):
    """Base exception for ENGIE Belgium API client errors."""


class EngieBeApiClientCommunicationError(EngieBeApiClientError):
    """Exception for communication errors (timeout, network)."""


class EngieBeApiClientAuthenticationError(EngieBeApiClientError):
    """Exception for authentication errors (bad credentials, expired token)."""


class EngieBeApiClientMfaError(EngieBeApiClientError):
    """Exception for MFA-related errors (invalid code)."""


class EpexNotPublishedError(EngieBeApiClientError):
    """
    The EPEX endpoint returned 404 for the requested window.

    ENGIE returns 404 (with body ``{"detail":"No prices found ..."}``)
    when day-ahead prices for the requested window have not yet been
    published.  Callers should treat this as a soft state (retry later)
    rather than an error worth surfacing to the user.
    """


def _raise_auth_error(status: int) -> NoReturn:
    """Raise an authentication error tagged with the offending HTTP status."""
    msg = f"Authentication failed ({status})"
    raise EngieBeApiClientAuthenticationError(msg)


# ---------------------------------------------------------------------------
# Auth flow intermediate state
# ---------------------------------------------------------------------------


@dataclass
class AuthFlowState:
    """Intermediate state kept between config-flow steps."""

    session: aiohttp.ClientSession
    authorize_state: str
    login_state: str
    mfa_challenge_state: str
    code_verifier: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """
    Generate PKCE parameters.

    Returns (state, nonce, code_verifier, code_challenge).
    """
    state = os.urandom(16).hex()
    nonce = os.urandom(16).hex()
    code_verifier = _base64url(os.urandom(32))
    code_challenge = _base64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
    return state, nonce, code_verifier, code_challenge


def _extract_from_body(body: str, pattern: str) -> str | None:
    """Extract a value from an HTML body using a regex pattern."""
    match = re.search(pattern, body)
    return match.group(1) if match else None


# Public re-export so non-HTTP modules (coordinator, platforms) can mask
# identifiers in their own log lines using the same scheme as the HTTP
# layer's body/URL redaction (``***NNNN`` with last-4 preserved).  Keep
# this in sync with ``_PARTIAL_MASK_BODY_KEYS`` semantics: any identifier
# masked there must also be masked when logged elsewhere.
mask_identifier = _redact_text


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------


class EngieBeApiClient:
    """
    ENGIE Belgium API client.

    Handles the full OAuth2/PKCE + MFA authentication flow (SMS or email)
    and subsequent token refresh / data retrieval.
    """

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
        # refresh token on every call, so two concurrent refreshes (e.g.
        # the periodic 60s timer firing while a coordinator's auth-failure
        # retry path also calls refresh) would consume the same refresh
        # token twice and the second caller would 400 -> spurious reauth.
        # The lock + "did someone else refresh while I was waiting?" check
        # inside async_refresh_token make refresh idempotent under racing
        # callers. See .opencode/audit-v0.10.0b1-prerelease.md CFG-4/CFG-5.
        self._token_lock = asyncio.Lock()
        self._req_logger = RequestLogger()

    # ------------------------------------------------------------------
    # Phase 1: start authentication (config-flow step 1 triggers this)
    # Runs auth steps 1-7, returns intermediate state so the config flow
    # can ask the user for the MFA code.
    # ------------------------------------------------------------------

    async def async_start_authentication(
        self,
        username: str,
        password: str,
        mfa_method: str = MFA_METHOD_SMS,
    ) -> AuthFlowState:
        """
        Execute auth steps 1-7 and return intermediate state.

        When *mfa_method* is ``sms`` (default) step 7 triggers an SMS.
        When it is ``email`` the ALT authenticator-switching detour runs
        instead (no SMS is sent).  The caller must then collect the code
        and pass it to ``async_complete_authentication``.
        """
        auth_session = aiohttp.ClientSession()
        try:
            return await self._run_auth_steps_1_to_7(
                auth_session, username, password, mfa_method
            )
        except Exception:
            await auth_session.close()
            raise

    # ------------------------------------------------------------------
    # Phase 2: complete authentication (config-flow step 2 triggers this)
    # Runs auth steps 8-13.
    # ------------------------------------------------------------------

    async def async_complete_authentication(
        self,
        flow_state: AuthFlowState,
        mfa_code: str,
        mfa_method: str = MFA_METHOD_SMS,
    ) -> tuple[str, str]:
        """
        Submit the MFA code and exchange the authorisation code for tokens.

        Returns (access_token, refresh_token).
        The temporary auth session is closed on success or on non-recoverable
        errors.  On ``EngieBeApiClientMfaError`` the session is kept open so
        the caller can retry with a corrected code.
        """
        try:
            access_token, refresh_token = await self._run_auth_steps_8_to_13(
                flow_state, mfa_code, mfa_method=mfa_method
            )
        except EngieBeApiClientMfaError:
            # Keep session open - user can retry with a new code
            raise
        except BaseException:
            await flow_state.session.close()
            raise
        else:
            await flow_state.session.close()
            self.access_token = access_token
            self.refresh_token = refresh_token
            return access_token, refresh_token

    # ------------------------------------------------------------------
    # Token refresh  (runs on a 60-second timer after setup)
    # ------------------------------------------------------------------

    async def async_refresh_token(self) -> tuple[str, str]:
        """
        Refresh the access token using the refresh token.

        Returns (new_access_token, new_refresh_token).
        The refresh token is rotated on every call.

        Serialised by self._token_lock against concurrent callers. If a
        racing caller already rotated the pair while we were waiting on
        the lock, we return the freshly-rotated pair without issuing a
        second refresh request (which would 400 on the now-consumed
        refresh token). See CFG-4/CFG-5 in the pre-release audit.
        """
        if not self.refresh_token:
            msg = "No refresh token available"
            raise EngieBeApiClientAuthenticationError(msg)

        # Snapshot before awaiting the lock so we can detect a racing
        # refresh that completed while we were queued.
        refresh_at_entry = self.refresh_token

        async with self._token_lock:
            if self.refresh_token != refresh_at_entry:
                # Another caller rotated the pair while we were waiting.
                # Return the fresh pair instead of replaying our (now
                # consumed) refresh token.
                LOGGER.debug(
                    "Token refresh: racing caller already rotated tokens; "
                    "returning fresh pair without re-issuing request"
                )
                return self.access_token, self.refresh_token

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

    # ------------------------------------------------------------------
    # Data retrieval
    # ------------------------------------------------------------------

    async def async_get_prices(self, business_agreement_number: str) -> Any:
        """
        Fetch energy prices for a business agreement.

        ``business_agreement_number`` is the 12-digit BAN (the
        ``businessAgreementNumber`` field returned by the customer-account
        relations endpoint, distinct from the shorter
        ``customerAccountNumber`` / CAN). The endpoint validates this
        path segment as exactly 12 characters and returns HTTP 400 for
        any other identifier.

        Returns the parsed JSON response.
        """
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
        Fetch energy contracts for a business agreement.

        Returns the parsed JSON response. Each ``items[]`` element
        carries a ``division`` (``"ELECTRICITY"`` / ``"GAS"``), a
        ``servicePointNumber`` (EAN), a ``status``
        (``"ACTIVE"`` / ``"INACTIVE"``), and a ``productConfiguration``
        block whose ``energyProduct`` field identifies the tariff
        product (e.g. ``"DYNAMIC"`` for the EPEX-indexed tariff,
        ``"EASY"`` for fixed). The integration uses ``energyProduct``
        to detect dynamic-tariff accounts in a way that survives
        mixed-fuel households (dynamic electricity + fixed gas), where
        the supplier-energy-prices payload alone is ambiguous.

        ``include_inactive=False`` (default) sends
        ``filter=ONLY_ACTIVE_ENERGY_CONTRACTS`` and returns only the
        contracts currently in force, matching the ENGIE smart app's
        request. ``include_inactive=True`` switches to
        ``filter=ALL_ENERGY_CONTRACTS`` so historical contracts
        (renewals, prior suppliers switched away from) are included
        too. Callers walking the customer's full contract history
        (for example the historical usage import) need the wider
        view; callers only interested in what's currently billed
        should leave the default.

        ``includeActions=true`` and ``includeSapData=true`` mirror the
        request the ENGIE smart app issues. Without them the response
        omits the ``productConfiguration`` block this integration
        relies on.
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
        """
        Fetch service-point metadata for a single EAN.

        Returns the parsed JSON response which includes a ``division``
        field (``"ELECTRICITY"`` or ``"GAS"``).
        """
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
        Fetch the list of customer accounts the logged-in user can access.

        The Auth0 access token is per-login, not per-customer-account, so
        a single ENGIE login can be linked to multiple ``customerAccountNumber``
        values (e.g. a person managing both their own household and a
        relative's account). This endpoint enumerates all such accounts
        together with the consumption address and contract metadata
        needed to present a meaningful picker in the config flow.

        Returns the parsed JSON response with the shape
        ``{"items": [{"customerAccount": {"customerAccountNumber": ...,
        "name": ..., "businessAgreements": [...]}}, ...]}``.

        The ``withBusinessAgreements=SMART_APP`` query parameter is
        required to make ENGIE include the active business agreement
        and its consumption address inline; without it the endpoint
        returns only bare customer-account identifiers.
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

        ``business_agreement_number`` is the 12-digit BAN. Despite the
        URL path naming it ``contract-accounts``, the endpoint expects
        a businessAgreementNumber (the same identifier accepted by
        :meth:`async_get_prices`); passing a ``customerAccountNumber``
        / CAN here returns HTTP 500.

        Returns the parsed JSON response which contains the monthly peak
        and an array of daily peaks for the requested month.
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
        Fetch the upcoming Happy Hour event for a business agreement.

        ``business_agreement_number`` is the 12-digit BAN. Passing a
        ``customerAccountNumber`` / CAN here returns HTTP 400.

        Returns the parsed JSON response. Shapes observed in production:

        * No event scheduled: ``{}`` (empty object).
        * Event scheduled: the upcoming window under a ``tomorrow`` key
          (announced the day before) and/or a ``today`` key (the same
          window, re-published once midnight passes), each shaped
          ``{"startTime": "...", "endTime": "..."}`` with ISO-8601 times
          carrying explicit offsets.

        The endpoint is not gated on dynamic-tariff status, so it is
        polled for every active business agreement.
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
        """
        Fetch the Happy Hours month report for a business agreement.

        ``business_agreement_number`` is the 12-digit BAN. The endpoint
        returns a ``month`` block with a ``happyHour`` sub-object carrying
        the current-month Happy Hours summary (consumption, number of
        eligible hours, reward, and comparison metrics).

        Only meaningful for BANs enrolled in the Happy Hours service; the
        endpoint may return empty or zero values for un-enrolled BANs.

        Returns the parsed JSON response.
        """
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
        Fetch historical usage details for a business agreement.

        Returns the parsed JSON response. Per-hour rows live under
        ``items[]`` with ``start`` / ``end`` ISO timestamps, per-stream
        ``energy.electricity.{offtake,injection}.kWhSum`` and
        ``energy.gas.kWh``, plus a ``partialData`` boolean that flags
        the current in-progress hour. A ``total`` block aggregates the
        whole requested window. ``start_date`` is inclusive and
        ``end_date`` is exclusive (civil-day boundaries).

        ``include_simulation`` defaults to ``False`` so ENGIE never
        returns projected numbers into the response; the caller writes
        the results into permanent long-term statistics where projected
        values would be misleading.
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
        Fetch solar-surplus injection forecasts for a delivery point.

        ``delivery_point_id`` is the ENGIE-formatted service-point ID,
        typically ``{EAN}_ID1``.

        Returns the parsed JSON response. Shape:
        ``{"forecasts": [{"forecastDate": "YYYY-MM-DD", "level": "...",
        "details": [{"startTime": "...", "value": <kWh>, "level": "..."}]}]}``.

        The endpoint sits behind the Smart App's
        ``solar-surplus-shown-dashboard`` feature flag. Customers without a
        solar installation receive a well-formed response whose per-slot
        ``level`` values are all ``NO_DATA``; callers infer availability
        from the response rather than a separate flag probe.
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

        Uses the targeted boolean-feature-flags endpoint, which returns a
        single flat object ``{"value": bool, "reason": str, ...}`` for the
        named flag rather than the group envelope keyed by flag name.

        The endpoint is the authoritative signal for Happy Hours enrolment:
        the ``/happy-hour-event`` endpoint returns ``{}`` both when the
        customer is not enrolled and when they are enrolled but no window is
        scheduled yet, so the event payload alone cannot distinguish the two
        states. The ``value`` field flips to ``true`` once the customer has
        signed the Happy Hours agreement.

        The ``customerAccountNumber`` field that the Smart App sends in
        ``additionalContext`` is accepted but not required; omitting it
        keeps the integration aligned with the v5 subentry schema which
        deliberately drops the CAN.

        Returns the parsed JSON response as a flat dict (top-level ``value``
        and ``reason`` keys).
        """
        return await self._async_query_boolean_feature_flag(
            HAPPY_HOURS_SERVICE_ENABLED_KEY,
            business_agreement_number,
        )

    async def async_get_solar_surplus_shown_dashboard_flag(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """
        Fetch the ``solar-surplus-shown-dashboard`` boolean feature flag for a BAN.

        Mirrors the Smart App's UI gate: ``value: true`` means the customer
        has a qualifying contract and delivery point for solar surplus, so
        it is safe to fetch per-EAN forecasts. ``value: false`` means the
        app hides the surplus tile and we skip the per-EAN fan-out to
        keep the refresh cycle lean.

        Returns the parsed JSON response as a flat dict (top-level ``value``
        and ``reason`` keys).
        """
        return await self._async_query_boolean_feature_flag(
            SOLAR_SURPLUS_SHOWN_DASHBOARD_KEY,
            business_agreement_number,
        )

    async def async_get_tou_schedules(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """
        Fetch the time-of-use tariff schedules for a business agreement.

        Returns the parsed JSON response. Shape:
        ``{"items": [{"eanWithSuffix": "..._ID1", "supplierSchedule": {...},
        "dgoTgoSchedule": {...}}]}`` where each schedule has per-direction
        ``offtake`` / ``injection`` maps of weekday -> list of
        ``{startTime, endTime, slotCode}`` slots. Endpoint responds even
        when the ``dgo-tou-is-active`` feature flag is off because the
        DGO/network schedule always applies to metered electricity.
        """
        ban = business_agreement_number.replace(" ", "")
        url = f"{HAPPY_HOUR_BASE_URL}/business-agreements/{ban}/tou-schedules"
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
        """
        Fetch the current account balance for a business agreement.

        Returns the parsed JSON response. Shape confirmed via spike capture
        on 2026-07-08. The response includes a ``status`` field
        (``"CLEAR"``, ``"OPEN_DEBIT"``, ``"OPEN_OVERDUE"``, etc.), an
        ``overview`` block with totals, a ``details`` block with
        ``financialTransactions`` (list of per-invoice rows with
        ``dueDate``, ``openAmount``, ``dueAmount``, and ``invoiceType``),
        and a ``refundBlocked`` flag. Amounts are in EUR. No customer
        name, IBAN, or address is returned by this endpoint.
        """
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

    async def async_get_dgo_tou_is_active_flag(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """
        Fetch the ``dgo-tou-is-active`` boolean feature flag for a BAN.

        Mirrors the Smart App's UI gate for the TOU tile. ``value: true``
        means the customer's supplier contract is TOU-billed and slot
        sensors are directly relevant to their bill. ``value: false``
        still allows displaying the network/DGO schedule since that
        applies to all digital-meter customers.

        Returns the parsed JSON response as a flat dict (top-level
        ``value`` and ``reason`` keys).
        """
        return await self._async_query_boolean_feature_flag(
            TOU_FLAG_KEY,
            business_agreement_number,
        )

    async def _async_query_boolean_feature_flag(
        self,
        flag_name: str,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """
        Query a single boolean feature flag for a business agreement.

        Shared plumbing for the targeted boolean-feature-flags endpoint,
        which returns a flat ``{"value": bool, "reason": str, ...}`` object
        for the named flag. Callers are the public wrappers that pin the
        specific flag name so the call site stays self-documenting.
        """
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

        ``from_dt``/``to_dt`` must be timezone-aware datetimes; they are
        normalised to UTC and rendered as ISO-8601 with millisecond
        precision and a literal ``Z`` suffix (the format the endpoint
        accepts; e.g. ``2026-05-04T00:00:00.000Z``).

        The endpoint requires authentication.  401/403 errors WILL
        trigger reauth via the standard OAuth flow through _api_wrapper.

        The optional ``granularity`` parameter controls the resolution of
        the returned time series:
        - ``None`` or ``"HOURLY"`` (default) -> 24 items/day, hourly slots
        - ``"QUARTER_HOURLY"`` -> 96 items/day, 15-minute slots

        On HTTP 404 (``{"detail":"No prices found ..."}``) this raises
        :class:`EpexNotPublishedError` so callers can treat it as a soft
        "not yet published" state rather than a real failure.
        """

        def _iso_ms_z(value: datetime) -> str:
            """Render a datetime as ISO-8601 UTC with ms precision + ``Z``."""
            utc_value = value.astimezone(UTC)
            # ``isoformat(timespec="milliseconds")`` keeps ms precision; we
            # then strip the ``+00:00`` offset and append ``Z`` to match the
            # exact shape the EPEX endpoint expects.
            iso = utc_value.isoformat(timespec="milliseconds").removesuffix("+00:00")
            return f"{iso}Z"

        params = {"from": _iso_ms_z(from_dt), "to": _iso_ms_z(to_dt)}
        if granularity is not None:
            params["granularity"] = granularity

        # Endpoint requires auth; use _api_wrapper which handles bearer
        # tokens and 401/403 reauth automatically. We still need to handle
        # 404 (not published yet) specially.
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
            # Check if this is a 404 by looking at the cause
            # The _api_wrapper wraps aiohttp.ClientResponseError which has status
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

    # ------------------------------------------------------------------
    # Internal: auth flow step implementations
    # ------------------------------------------------------------------

    async def _run_auth_steps_1_to_7(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        mfa_method: str,
    ) -> AuthFlowState:
        """
        Run auth steps 1-7 (authorize -> MFA triggered).

        When *mfa_method* is ``sms``, step 7 fires an SMS.  When it is
        ``email``, step 7 is skipped and the ALT authenticator-switching
        detour runs instead so the user receives an email code.
        """
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
            # Email MFA: run ALT steps 1-4 to switch authenticator and
            # trigger the email send (skips step 7 entirely so no SMS
            # is sent).
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

        # The response should contain a new state; if it doesn't the
        # code was most likely wrong (server returned 400 with the MFA
        # form again).
        another_state = _extract_from_body(body, r"state=([a-zA-Z0-9_-]+)")
        if not another_state:
            msg = "Invalid MFA code or failed to proceed after MFA submission"
            raise EngieBeApiClientMfaError(msg)

        LOGGER.debug("Auth step 8 complete: MFA code accepted")

        # Step 9: GET /authorize/resume (post-MFA).
        #
        # Auth0 has two possible outcomes here, and the choice is per
        # session/account (not configurable client-side):
        #
        #   A. **Callback short-circuit.** Auth0 finalizes the session
        #      immediately and redirects to the native callback URI
        #      ``be.engie.smart://login-callback/nl?code=...&state=...``.
        #      This is what older / passkey-dismissed accounts get. The
        #      auth code is already in the ``Location`` header; steps
        #      10-12 must be skipped because the Auth0 session is gone
        #      (a second ``/authorize/resume`` would return
        #      ``error=access_denied``).
        #
        #   B. **Passkey-enrollment interstitial.** Auth0 redirects to
        #      ``/u/passkey-enrollment?state=<passKeyState>`` so the
        #      user can register a passkey. The body contains a fresh
        #      state we must extract; we then load (step 10) and abort
        #      (step 11) enrollment, and re-resume (step 12) to get the
        #      code.
        #
        # We distinguish the two by inspecting the ``Location`` header
        # first. Falling back to body-only parsing the way the previous
        # implementation did is wrong for outcome A: the body's
        # ``state=`` is the *OAuth* state (the integration's own nonce
        # from step 1), not a ``passKeyState``. Using it as such
        # produces a stale-session error at step 12.
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

            # Step 11: POST /u/passkey-enrollment (abort enrollment).
            # The Auth0 flow uses followRedirects=true, but the redirect
            # chain ends at a non-HTTP app-scheme URL that aiohttp cannot
            # follow. We skip following redirects here since the code is
            # extracted in step 12.
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

            # Step 12: GET /authorize/resume (final - extract auth code).
            # Uses loginState (not passKeyState), exactly as in the API
            # auth flow. The response body contains the authorization
            # code, but some responses return it only in the Location
            # header.
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

    # ------------------------------------------------------------------
    # Internal: MFA submission methods (SMS vs email)
    # ------------------------------------------------------------------

    async def _submit_sms_mfa(
        self,
        flow_state: AuthFlowState,
        mfa_code: str,
    ) -> str:
        """Submit an SMS MFA code (auth step 8)."""
        # Step 8: POST /u/mfa-sms-challenge (submit SMS code)
        # A wrong code returns HTTP 400; we suppress the automatic error
        # handling so we can raise a specific MfaError instead.
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
        """
        Submit an email MFA code (auth step 8.ALT-5).

        The authenticator switch (ALT steps 1-4) has already been
        performed during ``_run_auth_steps_1_to_7`` so only the code
        POST is needed here.
        """
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

    # ------------------------------------------------------------------
    # Internal: email MFA authenticator switch (ALT steps 1-4)
    # ------------------------------------------------------------------

    async def _switch_to_email_mfa(
        self,
        session: aiohttp.ClientSession,
        challenge_state: str,
    ) -> None:
        """
        Run the authenticator-switching detour (auth ALT steps 1-4).

        This is called from ``_run_auth_steps_1_to_7`` when the user
        chose email MFA instead of SMS.  It navigates the Auth0 UI from
        the SMS challenge screen to the email challenge screen, which
        triggers the email send.
        """
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

    # ------------------------------------------------------------------
    # Authenticated header helper
    # ------------------------------------------------------------------

    def _authenticated_headers(
        self,
        user_agent: str = USER_AGENT_NATIVE,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        Return the standard authenticated JSON header dict.

        Used by every ENGIE endpoint that requires a Bearer token. Pass
        ``extra`` to merge per-endpoint headers (e.g.
        ``Content-Type: application/json`` on POST bodies). Auth-flow methods use
        custom header dicts and do not go through this helper.
        """
        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, application/problem+json",
            "authorization": f"Bearer {self.access_token}",
        }
        if extra:
            headers.update(extra)
        return headers

    # ------------------------------------------------------------------
    # Generic request wrapper
    # ------------------------------------------------------------------

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
        Execute an HTTP request with error handling.

        When *raise_on_error* is ``False`` the caller is responsible for
        interpreting non-success status codes (useful when a 400 has
        semantic meaning, e.g. an invalid MFA code).

        When *include_headers* is ``True`` the return value is a tuple
        of ``(body_or_json, response_headers)`` instead of just the body.

        At ``DEBUG`` log level this emits one ``→`` line before the
        request and one ``←`` (success) or ``✗`` (error) line after,
        correlated by an 8-char ``req_id``.  Tokens, credentials, OAuth
        state, and HTML auth-page bodies are masked / truncated; see the
        module-level redaction helpers.  When DEBUG is off, the cost is a
        single ``isEnabledFor`` check.
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

                    # For auth-flow HTML pages, non-200/302 is likely an
                    # error but we don't raise_for_status on 3xx since we
                    # handle redirects manually.
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
