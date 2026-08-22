"""Structured DEBUG-level request/response logging with redaction."""

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

_REDACTED = "***"

# Header keys whose values must never be logged verbatim (case-insensitive).
_REDACT_HEADER_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-csrf-token",
    }
)

# Body keys carrying credentials, tokens, OAuth secrets, or PKCE material.
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
        "client_id",
        # Auth0 opaque flow-state token, replayable from a log if leaked.
        "state",
    }
)

# Body keys carrying account-identifying PII, partial-masked via
# ``_redact_text`` so log lines stay greppable.
_PARTIAL_MASK_BODY_KEYS: frozenset[str] = frozenset(
    {
        # Identifiers
        "ean",
        "customeraccountnumber",
        "businessagreementnumber",
        "ban",
        "contractaccountid",
        "premisesnumber",
        "servicepointnumber",
        "eanwithsuffix",
        "invoicestructuredcommunication",
        # Contact / personal
        "name",
        "firstname",
        "lastname",
        "email",
        "emailaddress",
        # Auth0 form field carrying the user's email verbatim.
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

# Query-string keys carrying OAuth/PKCE state or secrets.
_REDACT_QUERY_KEYS: frozenset[str] = frozenset(
    {
        "code",
        "state",
        "code_verifier",
        "code_challenge",
        "nonce",
    }
)

# URL-path collection segments whose next segment is a PII identifier.
# Any new endpoint interpolating a BAN/EAN into the path must add its
# collection prefix here or DEBUG logs will leak the identifier.
_REDACT_PATH_PREFIXES: frozenset[str] = frozenset(
    {
        "business-agreements",
        "service-points",
        "contract-accounts",
        "solar-surplus",
    }
)

# Auth-flow HTML responses carry live CSRF tokens: never log in full.
_HTML_PREVIEW_MAX = 120


def _redact_text(value: str | None, keep: int = 4) -> str:
    """Mask all but the trailing *keep* characters of *value*."""
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
    """Return a copy of *data* with sensitive values masked (recurses)."""
    if not isinstance(data, Mapping):
        return {}
    partial = partial_keys or frozenset()
    result: dict[str, Any] = {}
    for key, value in data.items():
        key_lc = key.lower()
        if key_lc in keys:
            result[key] = _REDACTED
        elif key_lc in partial:
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
    """Return *url* with sensitive query-string and path segments redacted."""
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
    """Render *body* for DEBUG logs with credentials masked."""
    # Do NOT collapse to ``body in {b"", ""}``: body may be an unhashable
    # dict/list. ruff PLR1714 disagrees but is wrong here.
    if body is None or body == b"" or body == "":  # noqa: PLR1714
        return "<empty>"

    ct = (content_type or "").lower()

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
        except TypeError, ValueError:
            return repr(body)

    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - defensive; shouldn't happen with errors="replace"
            return f"<{len(body)} bytes binary>"

    if not isinstance(body, str):
        return repr(body)

    if "html" in ct or body.lstrip().startswith(("<!DOCTYPE", "<html", "<HTML")):
        preview = body[:_HTML_PREVIEW_MAX]
        return f"<html len={len(body)} preview={preview!r}>"

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

    # Form-encoded: partial-mask must apply here too because the Auth0
    # login POST sends ``username`` alongside ``password`` and ``state``.
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

    return body


def _new_req_id() -> str:
    """Return an 8-char correlation ID for one request/response pair."""
    return uuid.uuid4().hex[:8]


def _emit_request(  # noqa: PLR0913
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
    """Emit the ``✗`` line for any error path."""
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


@dataclass(frozen=True)
class RequestContext:
    """One outgoing HTTP request's correlation state."""

    req_id: str
    method: str
    url: str
    started: float


class RequestLogger:
    """Structured DEBUG-level request/response logging with redaction."""

    def new_context(self, method: str, url: str) -> RequestContext | None:
        """Return a fresh context if DEBUG is enabled, else None."""
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
