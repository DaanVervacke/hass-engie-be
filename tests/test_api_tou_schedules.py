"""Tests for the TOU schedules API getter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.engie_be.api import EngieBeApiClient
from custom_components.engie_be.const import (
    BOOLEAN_FEATURE_FLAG_BASE_URL,
    HAPPY_HOUR_BASE_URL,
    TOU_FLAG_KEY,
)

pytestmark = pytest.mark.tou

_BAN = "000000000000"


def _build_client() -> EngieBeApiClient:
    """Build a client stub with a stored access token."""
    return EngieBeApiClient(
        session=MagicMock(),
        client_id="test-client",
        access_token="test-access-token",  # noqa: S106
    )


async def test_async_get_tou_schedules_builds_request() -> None:
    """The getter targets the correct BAN URL."""
    client = _build_client()
    payload = {"items": []}

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        result = await client.async_get_tou_schedules(_BAN)

    assert result == payload
    call_kwargs = mocked.await_args.kwargs
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["url"] == (
        f"{HAPPY_HOUR_BASE_URL}/business-agreements/{_BAN}/tou-schedules"
    )
    assert call_kwargs["json_response"] is True
    headers = call_kwargs["headers"]
    assert headers["authorization"] == "Bearer test-access-token"


async def test_async_get_tou_schedules_strips_ban_whitespace() -> None:
    """Whitespace in the BAN is stripped from the URL path."""
    client = _build_client()

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value={}),
    ) as mocked:
        await client.async_get_tou_schedules("0000 0000 0000")

    url = mocked.await_args.kwargs["url"]
    assert _BAN in url
    assert " " not in url


async def test_async_get_dgo_tou_flag_posts_named_flag() -> None:
    """The flag getter POSTs the ``dgo-tou-is-active`` name."""
    client = _build_client()
    payload = {"value": True, "reason": "some_rule"}

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        result = await client.async_get_dgo_tou_is_active_flag(_BAN)

    assert result == payload
    call_kwargs = mocked.await_args.kwargs
    assert call_kwargs["method"] == "POST"
    assert call_kwargs["url"] == BOOLEAN_FEATURE_FLAG_BASE_URL
    body = call_kwargs["json_body"]
    assert body["name"] == TOU_FLAG_KEY
    assert body["additionalContext"]["contractAccountId"] == _BAN
