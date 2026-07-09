"""Tests for the Happy Hours month-report API getter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.engie_be.api import (
    EngieBeApiClient,
    EngieBeApiClientError,
)
from custom_components.engie_be.const import HAPPY_HOUR_BASE_URL, USER_AGENT_NATIVE

_BAN = "002200000001"


def _build_client() -> EngieBeApiClient:
    """Build a client stub with a stored access token."""
    return EngieBeApiClient(
        session=MagicMock(),
        client_id="test-client",
        access_token="test-access-token",  # noqa: S106
    )


async def test_async_get_month_report_builds_request() -> None:
    """The month-report getter targets the correct BAN-scoped URL."""
    client = _build_client()
    payload = {"month": {"happyHour": {"consumptionKWh": 12.3}}}

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        result = await client.async_get_month_report(_BAN, 2026, 7)

    assert result == payload
    call_kwargs = mocked.await_args.kwargs
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["url"] == (
        f"{HAPPY_HOUR_BASE_URL}/business-agreements/{_BAN}/month-report/2026-07"
    )
    assert call_kwargs["json_response"] is True
    headers = call_kwargs["headers"]
    assert headers["User-Agent"] == USER_AGENT_NATIVE
    assert headers["authorization"] == "Bearer test-access-token"


async def test_async_get_month_report_formats_date_with_leading_zero() -> None:
    """Single-digit months are zero-padded in the URL path."""
    client = _build_client()

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value={}),
    ) as mocked:
        await client.async_get_month_report(_BAN, 2026, 1)

    url = mocked.await_args.kwargs["url"]
    assert "/2026-01" in url


async def test_async_get_month_report_strips_whitespace_in_ban() -> None:
    """Whitespace in the BAN is removed from the URL path."""
    client = _build_client()

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value={}),
    ) as mocked:
        await client.async_get_month_report("0022 0000 0001", 2026, 7)

    url = mocked.await_args.kwargs["url"]
    assert _BAN in url
    assert " " not in url


async def test_async_get_month_report_propagates_api_errors() -> None:
    """Underlying API errors propagate unchanged."""
    client = _build_client()
    original = EngieBeApiClientError("network timeout")

    with (
        patch.object(client, "_api_wrapper", AsyncMock(side_effect=original)),
        pytest.raises(EngieBeApiClientError) as exc_info,
    ):
        await client.async_get_month_report(_BAN, 2026, 7)

    assert exc_info.value is original
