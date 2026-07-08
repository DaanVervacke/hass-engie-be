"""Tests for ``EngieBeApiClient.async_get_account_balance``."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.engie_be.api import EngieBeApiClient
from custom_components.engie_be.const import BILLING_BASE_URL

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "billing_open_debit.json"
_BAN = "000000000000"


def _build_client() -> EngieBeApiClient:
    """Build a client with a stub session and a stored access token."""
    return EngieBeApiClient(
        session=MagicMock(),
        client_id="test-client",
        access_token="test-access-token",  # noqa: S106
    )


async def test_async_get_account_balance_returns_payload() -> None:
    """A successful API call returns the parsed payload as a dict."""
    client = _build_client()
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        result = await client.async_get_account_balance(_BAN)

    assert result == payload
    mocked.assert_awaited_once()
    call_kwargs = mocked.await_args.kwargs
    expected_url = f"{BILLING_BASE_URL}/business-agreements/{_BAN}/account-balance"
    assert call_kwargs["url"] == expected_url
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["json_response"] is True
    headers = call_kwargs["headers"]
    assert headers["authorization"] == "Bearer test-access-token"
    assert "x-trace-id" in headers
    assert uuid.UUID(headers["x-trace-id"]).version == 4


async def test_async_get_account_balance_strips_ban_whitespace() -> None:
    """Whitespace in the BAN is removed before building the URL."""
    client = _build_client()

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value={}),
    ) as mocked:
        await client.async_get_account_balance("000 000 000000")

    call_kwargs = mocked.await_args.kwargs
    assert "000000000000" in call_kwargs["url"]
    assert " " not in call_kwargs["url"]
