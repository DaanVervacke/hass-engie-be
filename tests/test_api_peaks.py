"""Tests for ``EngieBeApiClient.async_get_monthly_peaks``."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.engie_be.api import (
    EngieBeApiClient,
    EngieBeApiClientError,
)
from custom_components.engie_be.const import PEAKS_BASE_URL

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "peaks_2026_04.json"
_CUSTOMER = "000000000000"
_YEAR = 2026
_MONTH = 4


def _build_client() -> EngieBeApiClient:
    """Build a client with a stub session and a stored access token."""
    return EngieBeApiClient(
        session=MagicMock(),
        client_id="test-client",
        access_token="test-access-token",  # noqa: S106
    )


async def test_async_get_monthly_peaks_returns_payload() -> None:
    """A successful API call returns the parsed payload as a dict."""
    client = _build_client()
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        result = await client.async_get_monthly_peaks(_CUSTOMER, _YEAR, _MONTH)

    assert result == payload
    mocked.assert_awaited_once()
    call_kwargs = mocked.await_args.kwargs
    expected_url = (
        f"{PEAKS_BASE_URL}/private/customers/me/contract-accounts/"
        f"{_CUSTOMER}/energy-insights/peaks"
    )
    assert call_kwargs["url"] == expected_url
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["params"] == {"year": "2026", "month": "4"}
    assert call_kwargs["json_response"] is True
    headers = call_kwargs["headers"]
    assert headers["authorization"] == "Bearer test-access-token"
    assert "x-trace-id" in headers
    # x-trace-id must be a valid UUID4 string.
    assert uuid.UUID(headers["x-trace-id"]).version == 4


async def test_async_get_monthly_peaks_strips_whitespace_in_customer_number() -> None:
    """Customer numbers with whitespace are normalised in the request URL."""
    client = _build_client()
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        await client.async_get_monthly_peaks("0000 0000 0000", _YEAR, _MONTH)

    call_kwargs = mocked.await_args.kwargs
    assert _CUSTOMER in call_kwargs["url"]
    assert " " not in call_kwargs["url"]


async def test_async_get_monthly_peaks_propagates_api_errors() -> None:
    """Underlying API errors propagate unchanged for the coordinator to handle."""
    client = _build_client()
    original = EngieBeApiClientError("boom")

    with (
        patch.object(client, "_api_wrapper", AsyncMock(side_effect=original)),
        pytest.raises(EngieBeApiClientError) as exc_info,
    ):
        await client.async_get_monthly_peaks(_CUSTOMER, _YEAR, _MONTH)

    assert exc_info.value is original


async def test_async_get_monthly_peaks_sends_unique_trace_id_per_call() -> None:
    """A fresh ``x-trace-id`` header is generated on every request."""
    client = _build_client()
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        await client.async_get_monthly_peaks(_CUSTOMER, _YEAR, _MONTH)
        await client.async_get_monthly_peaks(_CUSTOMER, _YEAR, _MONTH)

    first_trace = mocked.await_args_list[0].kwargs["headers"]["x-trace-id"]
    second_trace = mocked.await_args_list[1].kwargs["headers"]["x-trace-id"]
    assert first_trace != second_trace
