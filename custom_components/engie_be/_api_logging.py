"""
Structured DEBUG-level request/response logging with redaction.

Owns the request/response/error log emission and the redaction rules
that keep tokens, credentials, OAuth state, and HTML auth-page bodies
out of the log. Extracted from ``api.py`` so endpoint additions and
redaction-rule tweaks land in different files.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .const import LOGGER

# ---------------------------------------------------------------------------
# Redaction constants
# ---------------------------------------------------------------------------

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
        # Auth0 opaque flow-state token. Sent as a query param on GETs
        # (already covered by ``_REDACT_QUERY_KEYS``) AND embedded in
        # the form body of every POST in the login flow -- both must
        # be masked or the login session can be replayed from a log.
        "state",
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
        # Auth0 form field on /u/login/identifier and /u/login/password
        # carries the user's email address verbatim. Partial-masked
        # (last-4) so support logs stay greppable without leaking the
        # full address.
        "username",
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

# URL-path collection segments that are immediately followed by an
# account-identifying PII segment (BAN, EAN, or ENGIE-formatted
# delivery-point ID such as ``{EAN}_ID1``) in ``api.py``.  The segment
# right after any of these prefixes is partial-masked by ``_redact_url``.
#
# Maintenance note: any new endpoint added to ``api.py`` that
# interpolates a BAN/EAN/customer-account-number (or similar
# identifier) directly into the URL path must add its collection
# prefix here, otherwise the identifier will be logged verbatim at
# DEBUG level.
_REDACT_PATH_PREFIXES: frozenset[str] = frozenset(
    {
        "business-agreements",
        "service-points",
        "contract-accounts",
        "solar-surplus",
    }
)

# Maximum HTML preview length kept in DEBUG logs.  Auth-flow HTML
# responses are 50-200 KB and contain live CSRF tokens; we never log
# the body in full.
_HTML_PREVIEW_MAX = 120


# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------


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
    Return *url* with sensitive query-string and path segments redacted.

    The host and most of the path are left intact (they are needed to
    identify which endpoint was hit).  The exception is any path
    segment that immediately follows one of the ``_REDACT_PATH_PREFIXES``
    collection names (e.g. ``business-agreements/<BAN>``,
    ``service-points/<EAN>``) -- those account identifiers are
    partial-masked via ``_redact_text`` so log lines stay greppable
    without leaking the full BAN/EAN. The query string is redacted the
    same way it always was.

    Maintenance note: adding a new endpoint whose URL path embeds a
    BAN/EAN/other identifier requires adding its collection prefix to
    ``_REDACT_PATH_PREFIXES`` above, or the identifier will leak into
    DEBUG logs verbatim.
    """
    parts = urlsplit(url)

    segments = parts.path.split("/")
    redacted_segments = list(segments)
    for i, segment in enumerate(segments[:-1]):
        if segment in _REDACT_PATH_PREFIXES:
            redacted_segments[i + 1] = _redact_text(segments[i + 1])
    redacted_path = "/".join(redacted_segments)

    if not parts.query:
        return urlunsplit(parts._replace(path=redacted_path))

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted_pairs = [
        (k, _REDACTED if k.lower() in _REDACT_QUERY_KEYS else v) for k, v in pairs
    ]
    return urlunsplit(
        parts._replace(path=redacted_path, query=urlencode(redacted_pairs))
    )


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

    # Form-encoded: parse + redact.  Both credential keys (fully
    # masked) and PII keys (partial-masked, last-4 preserved) are
    # walked -- the Auth0 login flow sends ``username`` (an email)
    # alongside ``password`` and ``state`` in the same form body, so
    # the partial-mask set must apply here too.
    if "form-urlencoded" in ct or ("=" in body and "&" in body and " " not in body):
        try:
            pairs = parse_qsl(body, keep_blank_values=True)
        except ValueError:
            return body
        if pairs:
            redacted_pairs: list[tuple[str, str]] = []
            for k, v in pairs:
                k_lc = k.lower()
                if k_lc in _REDACT_BODY_KEYS:
                    redacted_pairs.append((k, _REDACTED))
                elif k_lc in _PARTIAL_MASK_BODY_KEYS:
                    redacted_pairs.append((k, _redact_text(v)))
                else:
                    redacted_pairs.append((k, v))
            return urlencode(redacted_pairs)

    # Plain text: passthrough.
    return body


# ---------------------------------------------------------------------------
# Request ID generator
# ---------------------------------------------------------------------------


def _new_req_id() -> str:
    """Return an 8-char correlation ID for one request/response pair."""
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Emit helpers (implementation details of RequestLogger)
# ---------------------------------------------------------------------------


def _emit_request(  # noqa: PLR0913
    req_id: str,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None,
    headers: Mapping[str, str] | None,
    body: Any,
) -> None:
    """Emit the ``->`` line for an outgoing request."""
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


def _emit_response(  # noqa: PLR0913
    req_id: str,
    method: str,
    url: str,
    *,
    status: int,
    started: float,
    ct: str | None,
    body: Any,
) -> None:
    """Emit the ``<-`` line for a successful response."""
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


def _emit_error(  # noqa: PLR0913
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
    Emit the ``x`` line for any error path.

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
# Public API: RequestContext + RequestLogger
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestContext:
    """One outgoing HTTP request's correlation state."""

    req_id: str
    method: str
    url: str
    started: float


class RequestLogger:
    """
    Structured DEBUG-level request/response logging with redaction.

    Owns the request/response/error log emission and the redaction
    rules. Callers hold one instance per API client, obtain a
    RequestContext at the start of each request (returns None when
    DEBUG is off - the client uses this as the gate), then feed the
    request, response, and error frames back in.
    """

    def new_context(self, method: str, url: str) -> RequestContext | None:
        """
        Return a fresh context if DEBUG is enabled, else None.

        Captures ``time.monotonic()`` at the same logical point where
        the pre-refactor code did (immediately after the debug gate,
        before the request is dispatched). Elapsed-time calculations
        in ``response`` and ``error`` therefore stay byte-identical to
        pre-refactor output.
        """
        if LOGGER.isEnabledFor(logging.DEBUG):
            return RequestContext(
                req_id=_new_req_id(),
                method=method,
                url=url,
                started=time.monotonic(),
            )
        return None

    def request(
        self,
        ctx: RequestContext,
        *,
        params: Mapping[str, Any] | None,
        headers: Mapping[str, str] | None,
        body: Any,
    ) -> None:
        """Emit the -> line for the outgoing request."""
        _emit_request(
            ctx.req_id,
            ctx.method,
            ctx.url,
            params=params,
            headers=headers,
            body=body,
        )

    def response(
        self,
        ctx: RequestContext,
        *,
        status: int,
        ct: str | None,
        body: Any,
    ) -> None:
        """Emit the <- line for a successful response."""
        _emit_response(
            ctx.req_id,
            ctx.method,
            ctx.url,
            status=status,
            started=ctx.started,
            ct=ct,
            body=body,
        )

    def error(  # noqa: PLR0913
        self,
        ctx: RequestContext,
        *,
        status: int | None = None,
        body: Any = None,
        ct: str | None = None,
        exc_name: str | None = None,
        suffix: str | None = None,
        exc_info: bool = False,
    ) -> None:
        """Emit the x line for any error path."""
        _emit_error(
            ctx.req_id,
            ctx.method,
            ctx.url,
            ctx.started,
            status=status,
            body=body,
            ct=ct,
            exc_name=exc_name,
            suffix=suffix,
            exc_info=exc_info,
        )
