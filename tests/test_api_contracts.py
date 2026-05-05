"""Tests for ``EngieBeApiClient.async_get_energy_contracts``."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.engie_be.api import (
    EngieBeApiClient,
    EngieBeApiClientError,
)
from custom_components.engie_be.const import BUSINESS_AGREEMENTS_BASE_URL

_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "energy_contracts_dynamic_plus_fixed_gas.json"
)
_BAN = "002200000001"


def _build_client() -> EngieBeApiClient:
    """Build a client with a stub session and a stored access token."""
    return EngieBeApiClient(
        session=MagicMock(),
        client_id="test-client",
        access_token="test-access-token",  # noqa: S106
    )


async def test_async_get_energy_contracts_returns_payload() -> None:
    """A successful API call returns the parsed payload as a dict."""
    client = _build_client()
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value=payload),
    ) as mocked:
        result = await client.async_get_energy_contracts(_BAN)

    assert result == payload
    mocked.assert_awaited_once()
    call_kwargs = mocked.await_args.kwargs
    expected_url = (
        f"{BUSINESS_AGREEMENTS_BASE_URL}/business-agreements/{_BAN}/energy-contracts"
    )
    assert call_kwargs["url"] == expected_url
    assert call_kwargs["method"] == "GET"
    # The smart-app filter trio is required for ``productConfiguration`` to
    # be present in the response. Without these query parameters the
    # endpoint returns a slimmer payload that breaks dynamic detection.
    assert call_kwargs["params"] == {
        "filter": "ONLY_ACTIVE_ENERGY_CONTRACTS",
        "includeActions": "true",
        "includeSapData": "true",
    }
    assert call_kwargs["json_response"] is True
    headers = call_kwargs["headers"]
    assert headers["authorization"] == "Bearer test-access-token"


async def test_async_get_energy_contracts_strips_whitespace_in_ban() -> None:
    """BAN values with whitespace are normalised in the request URL."""
    client = _build_client()

    with patch.object(
        client,
        "_api_wrapper",
        AsyncMock(return_value={"items": []}),
    ) as mocked:
        await client.async_get_energy_contracts("0022 0000 0001")

    call_kwargs = mocked.await_args.kwargs
    assert _BAN in call_kwargs["url"]
    assert " " not in call_kwargs["url"]


async def test_async_get_energy_contracts_propagates_api_errors() -> None:
    """Underlying API errors propagate unchanged for the caller to handle."""
    client = _build_client()
    original = EngieBeApiClientError("contracts unavailable")

    with (
        patch.object(client, "_api_wrapper", AsyncMock(side_effect=original)),
        pytest.raises(EngieBeApiClientError) as exc_info,
    ):
        await client.async_get_energy_contracts(_BAN)

    assert exc_info.value is original
