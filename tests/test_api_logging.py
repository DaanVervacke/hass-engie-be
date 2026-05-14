"""
Tests for structured DEBUG-level request/response logging in ``api.py``.

Two layers:

1. **Redaction helper unit tests** -- exercise the pure functions
   (``_redact_text``, ``_redact_mapping``, ``_redact_url``,
   ``_redact_body``) directly to lock the masking contract.
2. **End-to-end DEBUG capture** -- run requests through
   ``_api_wrapper`` / ``async_get_epex_prices`` / ``async_refresh_token``
   with ``caplog`` capturing the ``custom_components.engie_be`` logger
   at DEBUG and assert that the ``→`` / ``←`` / ``✗`` lines appear,
   share a ``req_id``, and never leak secrets.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.engie_be.api import (
    _HTML_PREVIEW_MAX,
    _PARTIAL_MASK_BODY_KEYS,
    _REDACT_BODY_KEYS,
    _REDACT_HEADER_KEYS,
    _REDACTED,
    EngieBeApiClient,
    EngieBeApiClientCommunicationError,
    EpexNotPublishedError,
    _redact_body,
    _redact_mapping,
    _redact_text,
    _redact_url,
)

_LOGGER_NAME = "custom_components.engie_be"


# ---------------------------------------------------------------------------
# Redaction helper unit tests
# ---------------------------------------------------------------------------


class TestRedactText:
    """``_redact_text`` masks all but the trailing characters."""

    def test_none_renders_placeholder(self) -> None:
        """``None`` becomes the ``<none>`` sentinel."""
        assert _redact_text(None) == "<none>"

    def test_empty_string_passes_through(self) -> None:
        """Empty input is returned unchanged."""
        assert _redact_text("") == ""

    def test_short_value_fully_redacted(self) -> None:
        """``len(value) <= keep`` collapses to ``***`` (no leak)."""
        # Leaking 3 chars of a 4-char secret still leaks too much.
        assert _redact_text("abcd", keep=4) == _REDACTED

    def test_long_value_keeps_tail(self) -> None:
        """Tail of the configured length is preserved."""
        masked = _redact_text("user@example.com", keep=4)
        assert masked == f"{_REDACTED}.com"

    def test_keep_parameter_respected(self) -> None:
        """The ``keep`` argument controls how many tail chars survive."""
        masked = _redact_text("541448XXXXXXXX1234", keep=4)
        assert masked.endswith("1234")
        assert "541448" not in masked


class TestRedactMapping:
    """``_redact_mapping`` recursively masks listed keys."""

    def test_top_level_redaction(self) -> None:
        """Top-level keys named in the redact set are masked."""
        out = _redact_mapping({"password": "hunter2", "user": "x"}, _REDACT_BODY_KEYS)
        assert out == {"password": _REDACTED, "user": "x"}

    def test_case_insensitive(self) -> None:
        """Header-style mixed-case keys are matched case-insensitively."""
        out = _redact_mapping({"Authorization": "Bearer eyJ..."}, _REDACT_HEADER_KEYS)
        assert out == {"Authorization": _REDACTED}

    def test_nested_dict_recursed(self) -> None:
        """Nested mappings are walked and redacted in place."""
        out = _redact_mapping(
            {"outer": {"refresh_token": "v0.x", "ok": True}},
            _REDACT_BODY_KEYS,
        )
        assert out == {"outer": {"refresh_token": _REDACTED, "ok": True}}

    def test_list_of_mappings_walked(self) -> None:
        """Lists of dicts are walked element-wise."""
        out = _redact_mapping(
            {"items": [{"otp": "123456"}, {"otp": "654321"}]},
            _REDACT_BODY_KEYS,
        )
        assert out == {"items": [{"otp": _REDACTED}, {"otp": _REDACTED}]}

    def test_non_mapping_returns_empty(self) -> None:
        """Non-mapping inputs degrade to ``{}`` rather than raising."""
        assert _redact_mapping("not a mapping", _REDACT_BODY_KEYS) == {}  # type: ignore[arg-type]

    def test_partial_mask_string_keeps_tail(self) -> None:
        """PII keys are partially masked, preserving last 4 chars."""
        out = _redact_mapping(
            {"ean": "541448820000010001", "other": "x"},
            _REDACT_BODY_KEYS,
            _PARTIAL_MASK_BODY_KEYS,
        )
        assert out == {"ean": f"{_REDACTED}0001", "other": "x"}

    def test_partial_mask_case_insensitive(self) -> None:
        """PII key matching is case-insensitive (matches camelCase API keys)."""
        out = _redact_mapping(
            {"customerAccountNumber": "1234567890", "ok": True},
            _REDACT_BODY_KEYS,
            _PARTIAL_MASK_BODY_KEYS,
        )
        assert out == {"customerAccountNumber": f"{_REDACTED}7890", "ok": True}

    def test_partial_mask_numeric_value(self) -> None:
        """Numeric PII values are stringified then partially masked."""
        out = _redact_mapping(
            {"customerAccountNumber": 1234567890},
            _REDACT_BODY_KEYS,
            _PARTIAL_MASK_BODY_KEYS,
        )
        assert out == {"customerAccountNumber": f"{_REDACTED}7890"}

    def test_partial_mask_recurses_into_nested_pii(self) -> None:
        """A PII key holding a dict (e.g. address) recurses, not collapses."""
        out = _redact_mapping(
            {
                "address": {
                    "street": "Rue de la Loi",
                    "city": "Brussels",
                    "houseNumber": "200",
                }
            },
            _REDACT_BODY_KEYS,
            _PARTIAL_MASK_BODY_KEYS,
        )
        # ``address`` itself is not in the partial set, but its children are.
        assert out == {
            "address": {
                "street": f"{_REDACTED} Loi",
                "city": f"{_REDACTED}sels",
                "houseNumber": f"{_REDACTED}",  # 3-char value -> fully masked
            }
        }

    def test_full_mask_takes_precedence_over_partial(self) -> None:
        """A key in both sets is fully masked (credentials win over PII)."""
        # Construct an artificial overlap to assert the precedence rule.
        out = _redact_mapping(
            {"password": "long-secret-value", "ean": "9999"},
            _REDACT_BODY_KEYS,
            _PARTIAL_MASK_BODY_KEYS | {"password"},
        )
        assert out["password"] == _REDACTED
        # Short value (<= 4 chars) in partial set still collapses to ``***``.
        assert out["ean"] == _REDACTED

    def test_partial_mask_in_list_of_strings(self) -> None:
        """Lists of PII strings under a partial-mask key are walked."""
        out = _redact_mapping(
            {"email": ["a@example.com", "bb@example.com"]},
            _REDACT_BODY_KEYS,
            _PARTIAL_MASK_BODY_KEYS,
        )
        assert out == {
            "email": [f"{_REDACTED}.com", f"{_REDACTED}.com"],
        }


class TestRedactUrl:
    """``_redact_url`` only rewrites the query string."""

    def test_no_query_passthrough(self) -> None:
        """URLs without a query string are returned unchanged."""
        url = "https://api.engie.be/v1/foo"
        assert _redact_url(url) == url

    def test_query_redacted(self) -> None:
        """Sensitive query params get ``***`` (URL-encoded)."""
        url = "https://auth.engie.be/authorize?state=secret-state&ui_locales=nl"
        out = _redact_url(url)
        assert "secret-state" not in out
        assert "state=%2A%2A%2A" in out  # _REDACTED url-encoded
        assert "ui_locales=nl" in out

    def test_path_and_host_preserved(self) -> None:
        """Host and path survive verbatim; only the query is rewritten."""
        url = (
            "https://auth.engie.be/u/login/identifier?state=abc&code=xyz&ui_locales=nl"
        )
        out = _redact_url(url)
        assert out.startswith("https://auth.engie.be/u/login/identifier")
        assert "abc" not in out
        assert "xyz" not in out

    def test_unknown_query_keys_kept(self) -> None:
        """Non-sensitive query params (e.g. ``maxGranularity``) are untouched."""
        url = "https://api.engie.be/v1/foo?maxGranularity=MONTHLY"
        assert _redact_url(url) == url


class TestRedactBody:
    """``_redact_body`` handles JSON, form, HTML, and plain text shapes."""

    def test_empty_renders_placeholder(self) -> None:
        """Empty / ``None`` bodies render as ``<empty>``."""
        assert _redact_body(None, None) == "<empty>"
        assert _redact_body("", None) == "<empty>"
        assert _redact_body(b"", None) == "<empty>"

    def test_dict_input_serialised_and_redacted(self) -> None:
        """Dict input is JSON-serialised with credential keys masked."""
        out = _redact_body({"refresh_token": "v0.x", "ok": True}, "application/json")
        parsed = json.loads(out)
        assert parsed == {"refresh_token": _REDACTED, "ok": True}

    def test_json_string_parsed_and_redacted(self) -> None:
        """JSON-string bodies are reparsed, redacted, and re-serialised."""
        body = json.dumps({"access_token": "eyJ...", "scope": "openid"})
        out = _redact_body(body, "application/json")
        parsed = json.loads(out)
        assert parsed == {"access_token": _REDACTED, "scope": "openid"}

    def test_form_urlencoded_redacted(self) -> None:
        """Form-encoded bodies have credential fields masked in place."""
        body = "refresh_token=v0.secret&grant_type=refresh_token&client_id=cid"
        out = _redact_body(body, "application/x-www-form-urlencoded")
        assert "v0.secret" not in out
        assert "refresh_token=%2A%2A%2A" in out
        assert "grant_type=refresh_token" in out
        assert "client_id=cid" in out

    def test_html_truncated_and_summarised(self) -> None:
        """HTML bodies are summarised to a length + short preview."""
        big = "<!DOCTYPE html><html>" + ("a" * 5000) + "</html>"
        out = _redact_body(big, "text/html; charset=utf-8")
        assert out.startswith("<html len=")
        # The ``preview=...`` repr of a 120-char window plus framing.
        assert "preview=" in out
        # Crude upper bound: framing + 120-char preview + repr quotes.
        assert len(out) < _HTML_PREVIEW_MAX + 80

    def test_html_detected_by_shape_when_ct_missing(self) -> None:
        """Bodies starting with ``<html`` are summarised even without a CT."""
        out = _redact_body("<html><body>hi</body></html>", None)
        assert out.startswith("<html len=")

    def test_bytes_decoded_then_redacted(self) -> None:
        """``bytes`` input is decoded as UTF-8 before redaction."""
        body = json.dumps({"password": "x"}).encode("utf-8")
        out = _redact_body(body, "application/json")
        parsed = json.loads(out)
        assert parsed == {"password": _REDACTED}

    def test_plain_text_passthrough(self) -> None:
        """Plain-text bodies are returned verbatim."""
        out = _redact_body("plain status text", "text/plain")
        assert out == "plain status text"

    def test_invalid_json_string_passes_through(self) -> None:
        """Malformed JSON returns the raw string rather than raising."""
        out = _redact_body("not really json {{{", "application/json")
        assert out == "not really json {{{"

    def test_relations_response_pii_partially_masked(self) -> None:
        """ENGIE relations payload: PII keys are partial-masked end-to-end."""
        # Mirrors the shape of ``customer_account_relations_sample.json``
        # well enough to lock the contract that no full PII value reaches
        # the log line.
        body = json.dumps(
            {
                "customerAccountNumber": "1234567890",
                "name": "Jane Doe",
                "emailAddress": "jane.doe@example.com",
                "premises": [
                    {
                        "premisesNumber": "PR-99887766",
                        "address": {
                            "street": "Rue de la Loi",
                            "houseNumber": "200",
                            "postalCode": "1000",
                            "city": "Brussels",
                        },
                        "ean": "541448820000010001",
                    }
                ],
            }
        )
        out = _redact_body(body, "application/json")
        # Hard contract: no raw PII value appears in the logged string.
        assert "1234567890" not in out
        assert "Jane Doe" not in out
        assert "jane.doe@example.com" not in out
        assert "PR-99887766" not in out
        assert "Rue de la Loi" not in out
        assert "Brussels" not in out
        assert "541448820000010001" not in out
        # And the masked tails are present (greppability invariant).
        parsed = json.loads(out)
        assert parsed["customerAccountNumber"] == f"{_REDACTED}7890"
        assert parsed["emailAddress"].endswith(".com")
        assert parsed["premises"][0]["ean"] == f"{_REDACTED}0001"
        assert parsed["premises"][0]["address"]["city"] == f"{_REDACTED}sels"

    def test_auth0_login_body_masks_username_and_state(self) -> None:
        """Auth0 login POST body masks ``username`` and ``state``."""
        # Regression for the PII leak observed in field-supplied DEBUG
        # logs where the form body of the login POST printed
        # ``"username": "user@example.com"`` and ``"state":
        # "<auth0-opaque>"`` verbatim because neither key was in any
        # redaction set.
        body = {
            "state": "hKFo2SBCX0xlN2JEWXJ1VVpaRk9nbjRRUE05NTluUXRhdGxOUKFur3VuaXZl",
            "allow-passkeys": "true",
            "username": "user.example@gmail.com",
            "js-available": "true",
        }
        out = _redact_body(body, "application/json")
        # Hard contracts: neither raw value appears in the logged string.
        assert "user.example@gmail.com" not in out
        assert "hKFo2SBC" not in out
        parsed = json.loads(out)
        # state -> fully masked (credential class wins over no class).
        assert parsed["state"] == _REDACTED
        # username -> partial-masked, last-4 preserved (".com").
        assert parsed["username"].endswith(".com")
        assert parsed["username"].startswith(_REDACTED)
        # Non-sensitive fields untouched.
        assert parsed["allow-passkeys"] == "true"
        assert parsed["js-available"] == "true"

    def test_auth0_login_body_form_encoded_masks_username_and_state(self) -> None:
        """Same regression as JSON path but for form-encoded bodies."""
        # Form-encoded bodies take a different code path inside
        # ``_redact_body`` (parsed via ``parse_qsl`` then re-encoded),
        # so we lock the contract on that path too.
        body = "state=hKFo2SBC.opaque&username=user.example%40gmail.com&action=default"
        out = _redact_body(body, "application/x-www-form-urlencoded")
        assert "hKFo2SBC.opaque" not in out
        assert "user.example" not in out
        assert "user.example%40gmail.com" not in out
        # state fully masked (URL-encoded ``***``).
        assert "state=%2A%2A%2A" in out
        # username partial-masked: tail (".com") preserved, body masked.
        assert "username=" in out
        assert ".com" in out
        # Non-sensitive field untouched.
        assert "action=default" in out


# ---------------------------------------------------------------------------
# E2E: ``_api_wrapper`` (via ``async_refresh_token``)
# ---------------------------------------------------------------------------


def _make_session(response: MagicMock) -> MagicMock:
    """Build a stub ``aiohttp.ClientSession`` whose request returns *response*."""
    session = MagicMock()
    session.request = AsyncMock(return_value=response)
    return session


def _make_response(
    *,
    status: int,
    json_body: Any | None = None,
    text_body: str | None = None,
    content_type: str = "application/json",
) -> MagicMock:
    """Stub aiohttp response usable by both ``.json()`` and ``.text()``."""
    response = MagicMock()
    response.status = status
    response.headers = {"Content-Type": content_type}
    if json_body is not None:
        response.json = AsyncMock(return_value=json_body)
        response.text = AsyncMock(return_value=json.dumps(json_body))
    else:
        response.json = AsyncMock(return_value=None)
        response.text = AsyncMock(return_value=text_body or "")
    response.raise_for_status = MagicMock()
    return response


def _arrows_for(records: list[logging.LogRecord]) -> list[str]:
    """Return the rendered messages, dropping non-arrow noise."""
    return [
        r.getMessage() for r in records if r.getMessage().startswith(("→", "←", "✗"))
    ]


def _extract_req_id(message: str) -> str | None:
    match = re.search(r"req_id=([0-9a-f]+)", message)
    return match.group(1) if match else None


async def test_api_wrapper_logs_request_and_response_with_correlated_req_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A successful request emits a paired → / ← with the same req_id."""
    response = _make_response(
        status=200,
        json_body={
            "access_token": "eyJabc.def",
            "refresh_token": "v0.new",
            "expires_in": 120,
        },
    )
    session = _make_session(response)
    client = EngieBeApiClient(
        session=session,
        client_id="client-1",
        refresh_token="v0.original",  # noqa: S106
    )

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        await client.async_refresh_token()

    arrows = _arrows_for(caplog.records)
    # At least one → and one ← (refresh_token call).  Refresh-token
    # rotation breadcrumb is separate and not arrow-prefixed.
    request_lines = [m for m in arrows if m.startswith("→")]
    response_lines = [m for m in arrows if m.startswith("←")]
    assert request_lines, "expected at least one '→' debug line"
    assert response_lines, "expected at least one '←' debug line"

    req_id = _extract_req_id(request_lines[0])
    resp_id = _extract_req_id(response_lines[0])
    assert req_id is not None
    assert req_id == resp_id, "→ and ← lines must share the same req_id"


async def test_api_wrapper_redacts_secrets_in_request_and_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tokens, passwords, and OAuth state are masked in DEBUG logs."""
    response = _make_response(
        status=200,
        json_body={"access_token": "eyJSECRETabc", "refresh_token": "v0.SECRETnew"},
    )
    session = _make_session(response)
    client = EngieBeApiClient(
        session=session,
        client_id="client-1",
        refresh_token="v0.SECRETold",  # noqa: S106
    )

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        await client.async_refresh_token()

    blob = "\n".join(r.getMessage() for r in caplog.records)
    # Refresh-token bodies + OAuth response tokens are all masked.
    assert "v0.SECRETold" not in blob
    assert "v0.SECRETnew" not in blob
    assert "eyJSECRETabc" not in blob
    # The rotation breadcrumb leaks only the redacted tail.
    assert "Token refresh: rotated refresh_token" in blob


async def test_api_wrapper_status_and_timing_present(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ← line carries status and a millisecond timing field."""
    response = _make_response(
        status=200, json_body={"access_token": "a", "refresh_token": "b"}
    )
    session = _make_session(response)
    client = EngieBeApiClient(
        session=session,
        client_id="client-1",
        refresh_token="v0.x",  # noqa: S106
    )

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        await client.async_refresh_token()

    response_lines = [m for m in _arrows_for(caplog.records) if m.startswith("←")]
    assert response_lines
    line = response_lines[0]
    assert "status=200" in line
    assert re.search(r"in \d+ms", line), f"missing timing in {line!r}"


async def test_api_wrapper_logs_failure_on_5xx(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 5xx response logs a ✗ line with status + timing and raises."""
    response = _make_response(
        status=503, text_body="Service Unavailable", content_type="text/plain"
    )
    response.raise_for_status = MagicMock(
        side_effect=aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=503
        )
    )
    session = _make_session(response)
    client = EngieBeApiClient(
        session=session,
        client_id="client-1",
        refresh_token="v0.x",  # noqa: S106
    )

    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME),
        pytest.raises(EngieBeApiClientCommunicationError),
    ):
        await client.async_refresh_token()

    error_lines = [m for m in _arrows_for(caplog.records) if m.startswith("✗")]
    assert error_lines, "expected a ✗ debug line on 5xx"
    assert "status=503" in error_lines[0]
    assert re.search(r"in \d+ms", error_lines[0])


async def test_api_wrapper_no_debug_lines_when_level_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With DEBUG disabled, no arrow log lines are emitted."""
    response = _make_response(
        status=200, json_body={"access_token": "a", "refresh_token": "b"}
    )
    session = _make_session(response)
    client = EngieBeApiClient(
        session=session,
        client_id="client-1",
        refresh_token="v0.x",  # noqa: S106
    )

    # WARNING is well above DEBUG -- redaction-gated branches must short-circuit.
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        await client.async_refresh_token()

    assert _arrows_for(caplog.records) == []


async def test_api_wrapper_unexpected_exception_logs_with_exc_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    The bare-``Exception`` branch in ``_api_wrapper`` attaches a stack trace.

    Triggers an unexpected exception type (``RuntimeError``) from
    ``session.request`` so the catch-all branch fires; asserts the
    emitted DEBUG record carries ``exc_info`` (i.e. a traceback is
    rendered for operator debugging).
    """
    session = MagicMock()
    session.request = AsyncMock(side_effect=RuntimeError("kaboom"))
    client = EngieBeApiClient(
        session=session,
        client_id="client-1",
        refresh_token="v0.x",  # noqa: S106
    )

    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME),
        pytest.raises(Exception, match="Unexpected error communicating"),
    ):
        await client.async_refresh_token()

    error_records = [
        r
        for r in caplog.records
        if r.getMessage().startswith("✗") and "RuntimeError" in r.getMessage()
    ]
    assert error_records, "expected ✗ DEBUG record for the unexpected branch"
    assert error_records[0].exc_info is not None, (
        "bare-Exception branch must pass exc_info=True so the traceback is logged"
    )


# ---------------------------------------------------------------------------
# E2E: EPEX inline path
# ---------------------------------------------------------------------------


_EPEX_FROM = datetime(2026, 5, 4, 0, 0, 0, tzinfo=UTC)
_EPEX_TO = datetime(2026, 5, 5, 0, 0, 0, tzinfo=UTC)


async def test_epex_logs_paired_request_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """EPEX happy path emits its own → / ← pair with shared req_id."""
    payload = {"timeSeries": [{"start": "x", "value": 1.23}]}
    response = _make_response(status=200, json_body=payload)
    session = _make_session(response)
    client = EngieBeApiClient(session=session, client_id="c", access_token="t")  # noqa: S106

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        result = await client.async_get_epex_prices(_EPEX_FROM, _EPEX_TO)

    assert result == payload
    arrows = _arrows_for(caplog.records)
    request_lines = [m for m in arrows if m.startswith("→")]
    response_lines = [m for m in arrows if m.startswith("←")]
    assert request_lines
    assert response_lines
    assert _extract_req_id(request_lines[0]) == _extract_req_id(response_lines[0])


async def test_epex_404_logs_failure_and_raises_not_published(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """404 logs a ✗ line and raises ``EpexNotPublishedError``."""
    response = _make_response(
        status=404,
        text_body='{"detail":"No prices found"}',
        content_type="application/problem+json",
    )
    session = _make_session(response)
    client = EngieBeApiClient(session=session, client_id="c", access_token="t")  # noqa: S106

    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME),
        pytest.raises(EpexNotPublishedError),
    ):
        await client.async_get_epex_prices(_EPEX_FROM, _EPEX_TO)

    error_lines = [m for m in _arrows_for(caplog.records) if m.startswith("✗")]
    assert error_lines
    assert "status=404" in error_lines[0]


async def test_epex_500_logs_failure_and_raises_communication_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """5xx on EPEX logs a ✗ line and raises a communication error."""
    response = _make_response(
        status=500,
        text_body="boom",
        content_type="text/plain",
    )
    session = _make_session(response)
    client = EngieBeApiClient(session=session, client_id="c", access_token="t")  # noqa: S106

    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME),
        pytest.raises(EngieBeApiClientCommunicationError),
    ):
        await client.async_get_epex_prices(_EPEX_FROM, _EPEX_TO)

    error_lines = [m for m in _arrows_for(caplog.records) if m.startswith("✗")]
    assert error_lines
    assert "status=500" in error_lines[0]


# ---------------------------------------------------------------------------
# Cross-cutting: no token/cookie/password leaks anywhere
# ---------------------------------------------------------------------------


async def test_authorization_header_redacted_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    ``authorization: Bearer ...`` headers must be masked in → lines.

    Uses ``async_get_prices`` (an authenticated endpoint that sends a
    ``Bearer`` header through ``_api_wrapper``) rather than EPEX, which
    is unauthenticated.
    """
    response = _make_response(status=200, json_body={"items": []})
    session = _make_session(response)
    client = EngieBeApiClient(
        session=session,
        client_id="c",
        access_token="eyJSUPER_SECRET_BEARER_TOKEN",  # noqa: S106
    )

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        await client.async_get_prices("123456789012")

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "eyJSUPER_SECRET_BEARER_TOKEN" not in blob
    # The header key is preserved (so logs remain greppable) but value masked.
    assert "authorization" in blob.lower()
    assert _REDACTED in blob
