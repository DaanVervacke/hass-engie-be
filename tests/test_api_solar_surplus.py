"""Tests for the solar-surplus API getter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.engie_be.api import EngieBeApiClient
from custom_components.engie_be.const import (
    BOOLEAN_FEATURE_FLAG_BASE_URL,
    HAPPY_HOUR_BASE_URL,
    SOLAR_SURPLUS_SHOWN_DASHBOARD_KEY,
)

_BAN = "000000000000"
_EAN = "541448820070414088"


def _build_client() -> EngieBeApiClient:
    """Build a client stub with a stored access token."""
    return EngieBeApiClient(
        session=MagicMock(),
        client_id="test-client",
        access_token="test-access-token",  # noqa: S106
    )


async def test_async_get_solar_surplus_builds_request() -> None:
    """The getter targets the correct BAN and delivery-point URL."""
    client = _build_client()
    payload = {"forecasts": []}

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        result = await client.async_get_solar_surplus_forecasts(
            _BAN,
            f"{_EAN}_ID1",
        )

    assert result == payload
    call_kwargs = mocked.await_args.kwargs
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["url"] == (
        f"{HAPPY_HOUR_BASE_URL}/business-agreements/{_BAN}/"
        f"solar-surplus/{_EAN}_ID1/forecasts"
    )
    assert call_kwargs["json_response"] is True
    headers = call_kwargs["headers"]
    assert headers["authorization"] == "Bearer test-access-token"


async def test_async_get_solar_surplus_strips_ban_whitespace() -> None:
    """Whitespace in the BAN is stripped from the URL path."""
    client = _build_client()

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value={}),
    ) as mocked:
        await client.async_get_solar_surplus_forecasts(
            "0000 0000 0000",
            f"{_EAN}_ID1",
        )

    url = mocked.await_args.kwargs["url"]
    assert _BAN in url
    assert " " not in url


async def test_async_get_solar_flag_posts_named_flag() -> None:
    """The flag getter POSTs the ``solar-surplus-shown-dashboard`` name."""
    client = _build_client()
    payload = {"value": True, "reason": "some_rule"}

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        result = await client.async_get_solar_surplus_shown_dashboard_flag(_BAN)

    assert result == payload
    call_kwargs = mocked.await_args.kwargs
    assert call_kwargs["method"] == "POST"
    assert call_kwargs["url"] == BOOLEAN_FEATURE_FLAG_BASE_URL
    body = call_kwargs["json_body"]
    assert body["name"] == SOLAR_SURPLUS_SHOWN_DASHBOARD_KEY
    assert body["additionalContext"]["contractAccountId"] == _BAN
