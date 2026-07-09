"""Tests for the simple bearer-authenticated getter endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.engie_be.api import (
    EngieBeApiClient,
    EngieBeApiClientError,
)
from custom_components.engie_be.const import (
    BOOLEAN_FEATURE_FLAG_BASE_URL,
    HAPPY_HOUR_BASE_URL,
    HAPPY_HOURS_SERVICE_ENABLED_KEY,
    PREMISES_BASE_URL,
    USER_AGENT_BROWSER,
    USER_AGENT_NATIVE,
)

_BAN = "002200000001"
_EAN = "541448000000000001"


def _build_client() -> EngieBeApiClient:
    """Build a client with a stub session and a stored access token."""
    return EngieBeApiClient(
        session=MagicMock(),
        client_id="test-client",
        access_token="test-access-token",  # noqa: S106
    )


# ---------------------------------------------------------------------------
# async_get_service_point
# ---------------------------------------------------------------------------


async def test_async_get_service_point_builds_request() -> None:
    """The service-point getter targets the premises endpoint by EAN."""
    client = _build_client()
    payload = {"division": "ELECTRICITY"}

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        result = await client.async_get_service_point(_EAN)

    assert result == payload
    mocked.assert_awaited_once()
    call_kwargs = mocked.await_args.kwargs
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["url"] == f"{PREMISES_BASE_URL}/service-points/{_EAN}"
    assert call_kwargs["json_response"] is True
    headers = call_kwargs["headers"]
    assert headers["User-Agent"] == USER_AGENT_BROWSER
    assert headers["authorization"] == "Bearer test-access-token"


async def test_async_get_service_point_propagates_api_errors() -> None:
    """Underlying API errors propagate unchanged for the caller to handle."""
    client = _build_client()
    original = EngieBeApiClientError("service point unavailable")

    with (
        patch.object(client, "_api_wrapper", AsyncMock(side_effect=original)),
        pytest.raises(EngieBeApiClientError) as exc_info,
    ):
        await client.async_get_service_point(_EAN)

    assert exc_info.value is original


# ---------------------------------------------------------------------------
# async_get_happy_hour_event
# ---------------------------------------------------------------------------


async def test_async_get_happy_hour_event_builds_request() -> None:
    """The happy-hour getter targets the BAN-scoped event endpoint."""
    client = _build_client()
    payload = {"tomorrow": {"startTime": "x", "endTime": "y"}}

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        result = await client.async_get_happy_hour_event(_BAN)

    assert result == payload
    call_kwargs = mocked.await_args.kwargs
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["url"] == (
        f"{HAPPY_HOUR_BASE_URL}/business-agreements/{_BAN}/happy-hour-event"
    )
    assert call_kwargs["json_response"] is True
    headers = call_kwargs["headers"]
    assert headers["User-Agent"] == USER_AGENT_NATIVE
    assert headers["authorization"] == "Bearer test-access-token"


async def test_async_get_happy_hour_event_strips_whitespace_in_ban() -> None:
    """BAN values with whitespace are normalised in the request URL."""
    client = _build_client()

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value={}),
    ) as mocked:
        await client.async_get_happy_hour_event("0022 0000 0001")

    url = mocked.await_args.kwargs["url"]
    assert _BAN in url
    assert " " not in url


# ---------------------------------------------------------------------------
# async_get_happy_hours_service_enabled_flag
# ---------------------------------------------------------------------------


async def test_async_get_happy_hours_service_enabled_flag_builds_request() -> None:
    """The flag getter POSTs the flag name and context to the boolean endpoint."""
    client = _build_client()
    payload = {"value": True, "reason": "HAPPY_HOUR_ACTIVE"}

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        result = await client.async_get_happy_hours_service_enabled_flag(_BAN)

    assert result == payload
    call_kwargs = mocked.await_args.kwargs
    assert call_kwargs["method"] == "POST"
    assert call_kwargs["url"] == BOOLEAN_FEATURE_FLAG_BASE_URL
    assert call_kwargs["json_response"] is True
    headers = call_kwargs["headers"]
    assert headers["User-Agent"] == USER_AGENT_NATIVE
    assert headers["Content-Type"] == "application/json"
    assert headers["authorization"] == "Bearer test-access-token"
    body = call_kwargs["json_body"]
    assert body["name"] == HAPPY_HOURS_SERVICE_ENABLED_KEY
    assert body["additionalContext"]["contractAccountId"] == _BAN


async def test_happy_hours_flag_strips_ban_whitespace() -> None:
    """BAN whitespace is normalised in the additionalContext payload."""
    client = _build_client()

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value={}),
    ) as mocked:
        await client.async_get_happy_hours_service_enabled_flag("0022 0000 0001")

    body = mocked.await_args.kwargs["json_body"]
    assert body["additionalContext"]["contractAccountId"] == _BAN
