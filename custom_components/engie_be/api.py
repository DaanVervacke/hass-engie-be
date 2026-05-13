"""ENGIE Belgium API client implementing OAuth2/PKCE with MFA (SMS or email)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import socket
import time
import uuid
from base64 import urlsafe_b64encode
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, NoReturn
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp

from .const import (
    ACCOUNTS_BASE_URL,
    API_BASE_URL,
    AUTH_BASE_URL,
    BUSINESS_AGREEMENTS_BASE_URL,
    EPEX_BASE_URL,
    LOGGER,
    MFA_METHOD_SMS,
    OAUTH_AUDIENCE,
    OAUTH_SCOPES,
    PEAKS_BASE_URL,
    PREMISES_BASE_URL,
    REDIRECT_URI,
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


# ---------------------------------------------------------------------------
# Debug-logging helpers
# ---------------------------------------------------------------------------
#
# Centralised redaction so DEBUG-level request/response logging can never
# leak tokens, credentials, or PII.  Everything below is gated behind
# ``LOGGER.isEnabledFor(logging.DEBUG)`` at the call sites so the cost on
# the hot token-refresh path is one branch when debug is off.
#
# We deliberately do NOT use ``homeassistant.components.diagnostics``
# ``async_redact_data`` even though it has overlapping recursive-walk
# semantics.  Two features we need that it lacks:
#   1. Case-insensitive key matching -- HTTP headers arrive with
#      arbitrary casing (``Authorization`` vs ``authorization``) and we
#      want a single key set to match both.
#   2. Partial masking with tail-preservation (``***0001``) -- the
#      ENGIE API returns EAN, customer-account, and similar identifiers
#      whose tail is the only useful greppable token in support logs.
#      ``async_redact_data`` only does full replacement
#      (``"**REDACTED**"``).
# Audit finding 2 documents this divergence; revisit if HA core grows
# either feature upstream.

_REDACTED = "***"

# Header keys whose values must never be logged verbatim.  Compared
# case-insensitively against header names actually sent on the wire.
_REDACT_HEADER_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-csrf-token",
    }
)

# JSON / form-body keys whose values are credentials, tokens, OAuth
# secrets, or PKCE material.  Fully masked (``***``) recursively in any
# nested dict.
_REDACT_BODY_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "code",
        "otp",
        "access_token",
        "refresh_token",
        "id_token",
        "code_verifier",
        "code_challenge",
        "client_secret",
    }
)

# JSON / form-body keys carrying account-identifying PII surfaced by
# the ENGIE API (relations, contracts, prices, peaks, service-points).
# These are partially masked via ``_redact_text`` so log lines stay
# greppable (e.g. ``***0001`` for an EAN tail) without leaking the full
# identifier.  Compared case-insensitively against the JSON keys
# returned on the wire.
_PARTIAL_MASK_BODY_KEYS: frozenset[str] = frozenset(
    {
        # Identifiers
        "ean",
        "customeraccountnumber",
        "businessagreementnumber",
        "premisesnumber",
        # Contact / personal
        "name",
        "firstname",
        "lastname",
        "email",
        "emailaddress",
        "phonenumber",
        "mobilephonenumber",
        # Address components
        "street",
        "housenumber",
        "postalcode",
        "city",
    }
)

# Query-string keys carrying OAuth/PKCE state worth masking.  ``state``
# is sensitive because it gates the auth flow; the verifier and
# challenge are PKCE secrets.
_REDACT_QUERY_KEYS: frozenset[str] = frozenset(
    {
        "code",
        "state",
        "code_verifier",
        "code_challenge",
        "nonce",
    }
)

# Maximum HTML preview length kept in DEBUG logs.  Auth-flow HTML
# responses are 50-200 KB and contain live CSRF tokens; we never log
# the body in full.
_HTML_PREVIEW_MAX = 120


def _redact_text(value: str | None, keep: int = 4) -> str:
    """
    Mask all but the trailing *keep* characters of *value*.

    Used for emails, EAN, customer-account numbers, and refresh-token
    tails so log lines remain greppable without leaking the secret.
    ``None`` and empty values are passed through unchanged.
    """
    if value is None:
        return "<none>"
    if not value:
        return ""
    if len(value) <= keep:
        return _REDACTED
    return f"{_REDACTED}{value[-keep:]}"


def _redact_mapping(
    data: Mapping[str, Any],
    keys: frozenset[str],
    partial_keys: frozenset[str] | None = None,
) -> dict[str, Any]:
    """
    Return a copy of *data* with sensitive values masked.

    Values whose key (case-insensitive) is in *keys* are replaced by
    ``***``.  Values whose key is in *partial_keys* are passed through
    ``_redact_text`` so the trailing 4 chars are kept for greppability
    (intended for account-identifying PII like EAN / customer numbers
    / addresses).  Dict values are recursed into; lists of dicts are
    walked too.  Non-mapping inputs are returned as ``{}``.
    """
    if not isinstance(data, Mapping):
        return {}
    partial = partial_keys or frozenset()
    result: dict[str, Any] = {}
    for key, value in data.items():
        key_lc = key.lower()
        if key_lc in keys:
            result[key] = _REDACTED
        elif key_lc in partial:
            # Partial-mask scalars; recurse into nested dicts/lists so
            # an "address" sub-dict's "street" still gets masked.
            if isinstance(value, str):
                result[key] = _redact_text(value)
            elif isinstance(value, (int, float)):
                result[key] = _redact_text(str(value))
            elif isinstance(value, Mapping):
                result[key] = _redact_mapping(value, keys, partial)
            elif isinstance(value, list):
                result[key] = [
                    _redact_mapping(item, keys, partial)
                    if isinstance(item, Mapping)
                    else (_redact_text(item) if isinstance(item, str) else item)
                    for item in value
                ]
            else:
                result[key] = _REDACTED
        elif isinstance(value, Mapping):
            result[key] = _redact_mapping(value, keys, partial)
        elif isinstance(value, list):
            result[key] = [
                _redact_mapping(item, keys, partial)
                if isinstance(item, Mapping)
                else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def _redact_url(url: str) -> str:
    """
    Return *url* with sensitive query-string parameters redacted.

    Path and host are left intact (they are needed to identify which
    endpoint was hit).  Only the query string is rewritten.
    """
    if "?" not in url:
        return url
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted_pairs = [
        (k, _REDACTED if k.lower() in _REDACT_QUERY_KEYS else v) for k, v in pairs
    ]
    return urlunsplit(parts._replace(query=urlencode(redacted_pairs)))


def _redact_body(body: Any, content_type: str | None) -> str:  # noqa: PLR0911, PLR0912
    """
    Render *body* for DEBUG logs with credentials masked.

    JSON bodies are parsed, then both the credential keys
    (``_REDACT_BODY_KEYS``, fully masked) and the PII keys
    (``_PARTIAL_MASK_BODY_KEYS``, last-4-chars preserved) are walked
    recursively before re-serialisation.
    ``application/x-www-form-urlencoded`` strings are parsed, redacted,
    and re-rendered.  ``text/html`` (or anything HTML-shaped) is
    truncated to ``_HTML_PREVIEW_MAX`` chars to avoid dumping live
    auth pages full of CSRF tokens.  Anything else is rendered with
    ``repr`` and returned as-is (no length cap; per integration debug
    policy non-HTML bodies are logged in full).
    """
    # NB: do NOT collapse to ``body in {b"", ""}`` -- ``body`` may be a
    # dict / list (unhashable) which raises TypeError. The empty-body
    # tests cover this; ruff PLR1714 disagrees but is wrong here.
    if body is None or body == b"" or body == "":  # noqa: PLR1714
        return "<empty>"

    ct = (content_type or "").lower()

    # JSON in / JSON out.
    if isinstance(body, (dict, list)):
        try:
            return (
                json.dumps(
                    _redact_mapping(body, _REDACT_BODY_KEYS, _PARTIAL_MASK_BODY_KEYS),
                    default=str,
                )
                if isinstance(body, Mapping)
                else json.dumps(
                    [
                        _redact_mapping(
                            item, _REDACT_BODY_KEYS, _PARTIAL_MASK_BODY_KEYS
                        )
                        if isinstance(item, Mapping)
                        else item
                        for item in body
                    ],
                    default=str,
                )
            )
        except (TypeError, ValueError):
            return repr(body)

    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - defensive; shouldn't happen with errors="replace"
            return f"<{len(body)} bytes binary>"

    if not isinstance(body, str):
        return repr(body)

    # HTML response: truncated preview only.
    if "html" in ct or body.lstrip().startswith(("<!DOCTYPE", "<html", "<HTML")):
        preview = body[:_HTML_PREVIEW_MAX]
        return f"<html len={len(body)} preview={preview!r}>"

    # JSON string: try to parse + redact.
    if "json" in ct or body.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(body)
        except ValueError:
            return body
        if isinstance(parsed, Mapping):
            return json.dumps(
                _redact_mapping(parsed, _REDACT_BODY_KEYS, _PARTIAL_MASK_BODY_KEYS),
                default=str,
            )
        if isinstance(parsed, list):
            return json.dumps(
                [
                    _redact_mapping(item, _REDACT_BODY_KEYS, _PARTIAL_MASK_BODY_KEYS)
                    if isinstance(item, Mapping)
                    else item
                    for item in parsed
                ],
                default=str,
            )
        return body

    # Form-encoded: parse + redact.  Only credential keys are masked
    # here (form bodies are used for OAuth / login posts, not for
    # PII-bearing API responses).
    if "form-urlencoded" in ct or ("=" in body and "&" in body and " " not in body):
        try:
            pairs = parse_qsl(body, keep_blank_values=True)
        except ValueError:
            return body
        if pairs:
            return urlencode(
                [
                    (k, _REDACTED if k.lower() in _REDACT_BODY_KEYS else v)
                    for k, v in pairs
                ]
            )

    # Plain text: passthrough.
    return body


def _new_req_id() -> str:
    """Return an 8-char correlation ID for one request/response pair."""
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Structured request / response logging helpers
# ---------------------------------------------------------------------------
#
# Both ``_api_wrapper`` and the inline EPEX path emit identical
# ``→ / ← / ✗`` lines.  Centralising them here avoids the two paths
# drifting (audit finding 5).  Callers gate on
# ``LOGGER.isEnabledFor(logging.DEBUG)`` themselves so that on the
# disabled-debug hot path we pay one branch, not three; the helpers
# therefore assume DEBUG is on and never re-check.


def _log_request(  # noqa: PLR0913
    req_id: str,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None,
    headers: Mapping[str, str] | None,
    body: Any,
) -> None:
    """Emit the ``→`` line for an outgoing request."""
    req_ct = (headers or {}).get("Content-Type") or (headers or {}).get("content-type")
    LOGGER.debug(
        "→ %s %s [req_id=%s] params=%s headers=%s body=%s",
        method,
        _redact_url(url),
        req_id,
        _redact_mapping(params or {}, _REDACT_QUERY_KEYS),
        _redact_mapping(headers or {}, _REDACT_HEADER_KEYS),
        _redact_body(body, req_ct) if body is not None else "<empty>",
    )


def _log_response(  # noqa: PLR0913
    req_id: str,
    method: str,
    url: str,
    *,
    status: int,
    started: float,
    ct: str | None,
    body: Any,
) -> None:
    """Emit the ``←`` line for a successful response."""
    LOGGER.debug(
        "← %s %s [req_id=%s] status=%d in %.0fms ct=%s body=%s",
        method,
        _redact_url(url),
        req_id,
        status,
        (time.monotonic() - started) * 1000,
        ct,
        _redact_body(body, ct),
    )


def _log_error(  # noqa: PLR0913
    req_id: str,
    method: str,
    url: str,
    started: float,
    *,
    status: int | None = None,
    body: Any = None,
    ct: str | None = None,
    exc_name: str | None = None,
    suffix: str | None = None,
    exc_info: bool = False,
) -> None:
    """
    Emit the ``✗`` line for any error path.

    The format is built dynamically from whichever of *status* /
    *exc_name* / *body* / *suffix* is supplied so a single helper
    covers HTTP-error, timeout, ClientError, EPEX-404, and bare-
    ``Exception`` variants without forcing each call site to assemble
    the format string.
    """
    parts = ["✗ %s %s [req_id=%s]"]
    args: list[Any] = [method, _redact_url(url), req_id]

    if status is not None:
        parts.append("status=%d")
        args.append(status)
    if exc_name is not None:
        parts.append("%s")
        args.append(exc_name)

    parts.append("in %.0fms")
    args.append((time.monotonic() - started) * 1000)

    if body is not None:
        parts.append("body=%s")
        args.append(_redact_body(body, ct))

    fmt = " ".join(parts)
    if suffix:
        fmt = f"{fmt} {suffix}"
    LOGGER.debug(fmt, *args, exc_info=exc_info)


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
        """
        if not self.refresh_token:
            msg = "No refresh token available"
            raise EngieBeApiClientAuthenticationError(msg)

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

        debug_on = LOGGER.isEnabledFor(logging.DEBUG)
        old_refresh_tail = _redact_text(self.refresh_token) if debug_on else ""

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

        if debug_on:
            LOGGER.debug(
                "Token refresh: rotated refresh_token %s → %s, expires_in=%s",
                old_refresh_tail,
                _redact_text(self.refresh_token),
                result.get("expires_in"),
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
    ) -> dict[str, Any]:
        """
        Fetch the active energy contracts for a business agreement.

        Returns the parsed JSON response. Each ``items[]`` element
        carries a ``division`` (``"ELECTRICITY"`` / ``"GAS"``), a
        ``servicePointNumber`` (EAN), and a ``productConfiguration``
        block whose ``energyProduct`` field identifies the tariff
        product (e.g. ``"DYNAMIC"`` for the EPEX-indexed tariff,
        ``"EASY"`` for fixed). The integration uses ``energyProduct``
        to detect dynamic-tariff accounts in a way that survives
        mixed-fuel households (dynamic electricity + fixed gas), where
        the supplier-energy-prices payload alone is ambiguous.

        ``filter=ONLY_ACTIVE_ENERGY_CONTRACTS`` plus
        ``includeActions=true`` and ``includeSapData=true`` mirror the
        request the ENGIE smart-app issues. Without them the response
        omits the ``productConfiguration`` block this integration relies
        on.
        """
        url = (
            f"{BUSINESS_AGREEMENTS_BASE_URL}/business-agreements/"
            f"{business_agreement_number.replace(' ', '')}/energy-contracts"
        )
        headers = {
            "User-Agent": USER_AGENT_BROWSER,
            "Accept": "application/json, application/problem+json",
            "authorization": f"Bearer {self.access_token}",
            "x-trace-id": str(uuid.uuid4()),
        }
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            params={
                "filter": "ONLY_ACTIVE_ENERGY_CONTRACTS",
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
        headers = {
            "User-Agent": USER_AGENT_NATIVE,
            "Accept": "application/json, application/problem+json",
            "authorization": f"Bearer {self.access_token}",
            "x-trace-id": str(uuid.uuid4()),
        }
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
        headers = {
            "User-Agent": USER_AGENT_NATIVE,
            "Accept": "application/json, application/problem+json",
            "authorization": f"Bearer {self.access_token}",
            "x-trace-id": str(uuid.uuid4()),
        }
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            params={"year": str(year), "month": str(month)},
            json_response=True,
        )

    async def async_get_epex_prices(
        self,
        from_dt: datetime,
        to_dt: datetime,
    ) -> dict[str, Any]:
        """
        Fetch EPEX day-ahead market prices for the given UTC window.

        ``from_dt``/``to_dt`` must be timezone-aware datetimes; they are
        normalised to UTC and rendered as ISO-8601 with millisecond
        precision and a literal ``Z`` suffix (the format the endpoint
        accepts; e.g. ``2026-05-04T00:00:00.000Z``).

        The endpoint is public, so no bearer is attached and a 401/403
        from this call must NOT trigger reauth of the user's session.
        Authentication-style status codes are coerced into a generic
        communication error instead.

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
        headers = {
            "User-Agent": USER_AGENT_BROWSER,
            "Accept": "application/json, application/problem+json",
        }

        # Use raise_on_error=False so 404 doesn't go through
        # raise_for_status (which would raise a generic ClientError) and
        # so 401/403 don't trip the auth-error branch.  We need raw
        # status visibility to distinguish 404 from real failures, so
        # call session.request directly here -- mirroring _api_wrapper's
        # error mapping but without its 401/403 handling.
        debug_on = LOGGER.isEnabledFor(logging.DEBUG)
        req_id = _new_req_id() if debug_on else ""
        started = time.monotonic() if debug_on else 0.0

        if debug_on:
            _log_request(
                req_id,
                "GET",
                EPEX_BASE_URL,
                params=params,
                headers=headers,
                body=None,
            )

        try:
            async with asyncio.timeout(30):
                response = await self._session.request(
                    method="GET",
                    url=EPEX_BASE_URL,
                    headers=headers,
                    params=params,
                    allow_redirects=False,
                )
                status = response.status
                if status == HTTPStatus.NOT_FOUND:
                    if debug_on:
                        _log_error(
                            req_id,
                            "GET",
                            EPEX_BASE_URL,
                            started,
                            status=status,
                            suffix="(EPEX not yet published)",
                        )
                    msg = (
                        "EPEX prices not yet published for "
                        f"{params['from']}..{params['to']}"
                    )
                    raise EpexNotPublishedError(msg)
                if status >= HTTPStatus.BAD_REQUEST:
                    body_preview = (await response.text())[:200]
                    if debug_on:
                        _log_error(
                            req_id,
                            "GET",
                            EPEX_BASE_URL,
                            started,
                            status=status,
                            body=body_preview,
                            ct=response.headers.get("Content-Type")
                            if hasattr(response, "headers")
                            else None,
                        )
                    msg = f"EPEX endpoint returned HTTP {status}: {body_preview}"
                    raise EngieBeApiClientCommunicationError(msg)
                result = await response.json()
                if debug_on:
                    resp_ct = (
                        response.headers.get("Content-Type")
                        if hasattr(response, "headers")
                        else None
                    )
                    _log_response(
                        req_id,
                        "GET",
                        EPEX_BASE_URL,
                        status=status,
                        started=started,
                        ct=resp_ct,
                        body=result,
                    )
                return result
        except EngieBeApiClientError:
            raise
        except TimeoutError as exception:
            if debug_on:
                _log_error(
                    req_id,
                    "GET",
                    EPEX_BASE_URL,
                    started,
                    exc_name="timeout",
                )
            msg = (
                "Timeout communicating with EPEX endpoint "
                f"({exception.__class__.__name__})"
            )
            raise EngieBeApiClientCommunicationError(msg) from exception
        except (aiohttp.ClientError, socket.gaierror) as exception:
            if debug_on:
                _log_error(
                    req_id,
                    "GET",
                    EPEX_BASE_URL,
                    started,
                    exc_name=exception.__class__.__name__,
                )
            msg = (
                f"Error communicating with EPEX endpoint "
                f"({exception.__class__.__name__})"
            )
            raise EngieBeApiClientCommunicationError(msg) from exception

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

        # Step 9: GET /authorize/resume (post-MFA)
        body = await self._api_wrapper(
            session=session,
            method="GET",
            url=f"{AUTH_BASE_URL}/authorize/resume",
            params={"state": flow_state.login_state},
            headers=_BROWSER_HEADERS,
            allow_redirects=False,
        )
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

        # Step 11: POST /u/passkey-enrollment (abort enrollment)
        # The Auth0 flow uses followRedirects=true, but the redirect chain
        # ends at a non-HTTP app-scheme URL that aiohttp cannot follow.
        # We skip following redirects here since the code is extracted in
        # step 12.
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

        # Step 12: GET /authorize/resume (final - extract auth code)
        # Uses loginState (not passKeyState), exactly as in the API
        # auth flow.  The response body contains the authorization code,
        # but some responses return it only in the Location header.
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
                    "Auth step 12 complete: got authorization code from Location header"
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
        debug_on = LOGGER.isEnabledFor(logging.DEBUG)
        req_id = _new_req_id() if debug_on else ""
        started = time.monotonic() if debug_on else 0.0

        if debug_on:
            _log_request(
                req_id,
                method,
                url,
                params=params,
                headers=headers,
                body=data,
            )

        try:
            async with asyncio.timeout(30):
                response = await session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=data,
                    params=params,
                    allow_redirects=allow_redirects,
                )
                if raise_on_error:
                    if response.status in (
                        HTTPStatus.UNAUTHORIZED,
                        HTTPStatus.FORBIDDEN,
                    ):
                        if debug_on:
                            _log_error(
                                req_id,
                                method,
                                url,
                                started,
                                status=response.status,
                            )
                        _raise_auth_error(response.status)

                    # For auth-flow HTML pages, non-200/302 is likely an
                    # error but we don't raise_for_status on 3xx since we
                    # handle redirects manually.
                    if response.status >= HTTPStatus.BAD_REQUEST:
                        if debug_on:
                            _log_error(
                                req_id,
                                method,
                                url,
                                started,
                                status=response.status,
                            )
                        response.raise_for_status()

                if json_response:
                    result = await response.json()
                else:
                    result = await response.text()

                if debug_on:
                    resp_ct = (
                        response.headers.get("Content-Type")
                        if hasattr(response, "headers")
                        else None
                    )
                    _log_response(
                        req_id,
                        method,
                        url,
                        status=response.status,
                        started=started,
                        ct=resp_ct,
                        body=result,
                    )

                if include_headers:
                    return result, dict(response.headers)
                return result

        except EngieBeApiClientError:
            raise
        except TimeoutError as exception:
            if debug_on:
                _log_error(
                    req_id,
                    method,
                    url,
                    started,
                    exc_name="timeout",
                )
            msg = (
                f"Timeout communicating with Engie API ({exception.__class__.__name__})"
            )
            raise EngieBeApiClientCommunicationError(msg) from exception
        except (aiohttp.ClientError, socket.gaierror) as exception:
            if debug_on:
                _log_error(
                    req_id,
                    method,
                    url,
                    started,
                    exc_name=exception.__class__.__name__,
                )
            msg = f"Error communicating with Engie API ({exception.__class__.__name__})"
            raise EngieBeApiClientCommunicationError(msg) from exception
        except Exception as exception:
            if debug_on:
                _log_error(
                    req_id,
                    method,
                    url,
                    started,
                    exc_name=exception.__class__.__name__,
                    exc_info=True,
                )
            msg = (
                "Unexpected error communicating with Engie API "
                f"({exception.__class__.__name__})"
            )
            raise EngieBeApiClientError(msg) from exception
