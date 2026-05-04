"""Tests for ``EngieBeApiClient.async_get_customer_account_relations``."""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.engie_be.api import (
    EngieBeApiClient,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientCommunicationError,
)
from custom_components.engie_be.const import ACCOUNTS_BASE_URL, USER_AGENT_NATIVE

_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "customer_account_relations_sample.json"
)


def _build_response(status: int, body: Any) -> MagicMock:
    """
    Construct a stub aiohttp response with the given status and body.

    For statuses >= 400 the response's ``raise_for_status`` is wired to
    raise ``aiohttp.ClientResponseError``, which mirrors real aiohttp
    behaviour and lets ``_api_wrapper``'s error mapping run.
    """
    response = MagicMock()
    response.status = status
    response.headers = {}
    if isinstance(body, (dict, list)):
        response.json = AsyncMock(return_value=body)
        response.text = AsyncMock(return_value=json.dumps(body))
    else:
        response.json = AsyncMock(return_value=body)
        response.text = AsyncMock(return_value=str(body))

    if status >= HTTPStatus.BAD_REQUEST:
        request_info = MagicMock()
        request_info.real_url = "https://example.invalid/"

        def _raise() -> None:
            raise aiohttp.ClientResponseError(
                request_info=request_info,
                history=(),
                status=status,
                message=f"HTTP {status}",
            )

        response.raise_for_status = MagicMock(side_effect=_raise)
    else:
        response.raise_for_status = MagicMock()
    return response


def _build_client(response: MagicMock) -> EngieBeApiClient:
    """Build a client whose session returns the supplied stub response."""
    session = MagicMock()
    session.request = AsyncMock(return_value=response)
    return EngieBeApiClient(
        session=session,
        client_id="test-client",
        access_token="test-access-token",  # noqa: S106
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_async_get_customer_account_relations_returns_payload() -> None:
    """A 200 response is returned to the caller verbatim as a dict."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = _build_client(_build_response(200, payload))

    result = await client.async_get_customer_account_relations()

    assert result == payload
    # Sanity: fixture has two accounts.
    assert len(result["items"]) == 2
    numbers = {
        item["customerAccount"]["customerAccountNumber"] for item in result["items"]
    }
    assert numbers == {"1500000001", "1500000002"}


async def test_async_get_customer_account_relations_uses_correct_url_and_params() -> (
    None
):
    """The endpoint URL is hit with the SMART_APP query param."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = _build_client(_build_response(200, payload))

    await client.async_get_customer_account_relations()

    call = client._session.request.await_args
    assert call.kwargs["method"] == "GET"
    assert call.kwargs["url"] == f"{ACCOUNTS_BASE_URL}/customer-account-relations"
    # The SMART_APP filter is what causes ENGIE to inline the
    # businessAgreements + consumptionAddress in the response.
    assert call.kwargs["params"] == {"withBusinessAgreements": "SMART_APP"}


async def test_async_get_customer_account_relations_attaches_bearer() -> None:
    """The bearer token from the client is attached to the request."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = _build_client(_build_response(200, payload))

    await client.async_get_customer_account_relations()

    call = client._session.request.await_args
    headers: dict[str, str] = call.kwargs["headers"]
    assert headers["authorization"] == "Bearer test-access-token"


async def test_async_get_customer_account_relations_sends_native_user_agent() -> None:
    """
    The native user-agent is sent.

    The api.engie.be host (as opposed to www.engie.be) is the mobile-app
    backend; sending the browser UA here regularly trips the WAF.
    """
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = _build_client(_build_response(200, payload))

    await client.async_get_customer_account_relations()

    call = client._session.request.await_args
    headers: dict[str, str] = call.kwargs["headers"]
    # Match the same UA family we send for the peaks endpoint, which
    # lives on the same host. This is the Dalvik/Android UA that mimics
    # the ENGIE Smart App; the WAF rejects browser UAs on api.engie.be.
    assert headers["User-Agent"] == USER_AGENT_NATIVE
    assert "Mozilla" not in headers["User-Agent"]


async def test_async_get_customer_account_relations_sends_unique_trace_id() -> None:
    """Each request gets a fresh ``x-trace-id`` UUID for ENGIE-side log lookup."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = _build_client(_build_response(200, payload))

    await client.async_get_customer_account_relations()
    first_call_headers = client._session.request.await_args.kwargs["headers"]
    first_trace_id = first_call_headers["x-trace-id"]

    await client.async_get_customer_account_relations()
    second_call_headers = client._session.request.await_args.kwargs["headers"]
    second_trace_id = second_call_headers["x-trace-id"]

    # Both must be present and they must differ.
    assert first_trace_id
    assert second_trace_id
    assert first_trace_id != second_trace_id


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
async def test_async_get_customer_account_relations_raises_auth_error(
    status: int,
) -> None:
    """
    401/403 must surface as an authentication error.

    The relations endpoint is bearer-authenticated and lives behind the
    user's ENGIE session. Auth-style failures here genuinely mean the
    session is invalid and must trigger reauth, unlike the public EPEX
    endpoint where the same status codes are coerced to comm errors.
    """
    client = _build_client(_build_response(status, "denied"))

    with pytest.raises(EngieBeApiClientAuthenticationError):
        await client.async_get_customer_account_relations()


@pytest.mark.parametrize("status", [400, 404, 500, 502, 503])
async def test_async_get_customer_account_relations_raises_comm_error(
    status: int,
) -> None:
    """Any other ``>=400`` status maps to the generic comms error."""
    client = _build_client(_build_response(status, "boom"))

    with pytest.raises(EngieBeApiClientCommunicationError):
        await client.async_get_customer_account_relations()
