"""Tests for ``EngieBeApiClient.async_get_epex_prices``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.engie_be.api import (
    EngieBeApiClient,
    EngieBeApiClientCommunicationError,
    EpexNotPublishedError,
)
from custom_components.engie_be.const import EPEX_BASE_URL

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "epex_24h.json"

# Anchor request window: full UTC day 2026-05-04.  Picked to match the
# api docstring's example output (``2026-05-04T00:00:00.000Z``).
_FROM = datetime(2026, 5, 4, 0, 0, 0, tzinfo=UTC)
_TO = datetime(2026, 5, 5, 0, 0, 0, tzinfo=UTC)


def _build_response(status: int, body: Any) -> MagicMock:
    """
    Construct a stub aiohttp response with the given status and body.

    ``body`` is returned from both ``.json()`` (parsed) and ``.text()``
    (stringified) so the tests work regardless of which path the
    production code uses for that status code.
    """
    response = MagicMock()
    response.status = status
    if isinstance(body, (dict, list)):
        response.json = AsyncMock(return_value=body)
        response.text = AsyncMock(return_value=json.dumps(body))
    else:
        response.json = AsyncMock(return_value=body)
        response.text = AsyncMock(return_value=str(body))
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


async def test_async_get_epex_prices_returns_payload() -> None:
    """A 200 response is returned to the caller verbatim as a dict."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = _build_client(_build_response(200, payload))

    result = await client.async_get_epex_prices(_FROM, _TO)

    assert result == payload
    # 24 hourly slots in the fixture; round-trip sanity check.
    assert len(result["timeSeries"]) == 24


async def test_async_get_epex_prices_uses_correct_url_and_querystring() -> None:
    """The endpoint URL is hit and from/to are formatted as ISO-8601 ms + Z."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    response = _build_response(200, payload)
    client = _build_client(response)

    await client.async_get_epex_prices(_FROM, _TO)

    call = client._session.request.await_args
    assert call.kwargs["method"] == "GET"
    assert call.kwargs["url"] == EPEX_BASE_URL
    # The exact wire format the public endpoint requires: millisecond
    # precision and a literal Z, no offset.
    assert call.kwargs["params"] == {
        "from": "2026-05-04T00:00:00.000Z",
        "to": "2026-05-05T00:00:00.000Z",
    }


async def test_async_get_epex_prices_normalises_non_utc_input() -> None:
    """
    Brussels-local inputs are converted to UTC before serialisation.

    ``2026-05-04T02:00:00+02:00`` is the same instant as
    ``2026-05-04T00:00:00Z`` -- the wire payload must reflect that.
    """
    brussels = ZoneInfo("Europe/Brussels")
    from_local = datetime(2026, 5, 4, 2, 0, 0, tzinfo=brussels)
    to_local = datetime(2026, 5, 5, 2, 0, 0, tzinfo=brussels)

    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    response = _build_response(200, payload)
    client = _build_client(response)

    await client.async_get_epex_prices(from_local, to_local)

    call = client._session.request.await_args
    assert call.kwargs["params"] == {
        "from": "2026-05-04T00:00:00.000Z",
        "to": "2026-05-05T00:00:00.000Z",
    }


async def test_async_get_epex_prices_does_not_attach_bearer() -> None:
    """
    The endpoint is public; no bearer token may be attached.

    Sending the user's bearer here would surface their identity to a
    third-party WAF for no benefit and would risk a 401 cascade if the
    token happens to be invalid.
    """
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    response = _build_response(200, payload)
    client = _build_client(response)

    await client.async_get_epex_prices(_FROM, _TO)

    call = client._session.request.await_args
    headers: dict[str, str] = call.kwargs["headers"]
    assert "authorization" not in {k.lower() for k in headers}
    assert "Authorization" not in headers


async def test_async_get_epex_prices_does_not_follow_redirects() -> None:
    """
    Imperva WAFs sometimes 302 to a challenge page; we must not follow.

    Following a redirect would either time out or return HTML, which the
    ``response.json()`` call would then fail to parse with a confusing
    ``ClientError``.  Surface the redirect as a non-2xx instead.
    """
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    response = _build_response(200, payload)
    client = _build_client(response)

    await client.async_get_epex_prices(_FROM, _TO)

    call = client._session.request.await_args
    assert call.kwargs["allow_redirects"] is False


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


async def test_async_get_epex_prices_raises_not_published_on_404() -> None:
    """
    404 from the endpoint maps to ``EpexNotPublishedError``.

    This is a soft "no prices yet" state that callers must not treat as
    a real failure -- they should keep the last-known payload and try
    again on the next poll.
    """
    body = {"detail": "No prices found for the requested period"}
    client = _build_client(_build_response(404, body))

    with pytest.raises(EpexNotPublishedError):
        await client.async_get_epex_prices(_FROM, _TO)


@pytest.mark.parametrize("status", [400, 401, 403, 500, 502, 503])
async def test_async_get_epex_prices_raises_communication_error_on_other_4xx_5xx(
    status: int,
) -> None:
    """
    Any other ``>=400`` status is mapped to the generic comms error.

    Crucially, 401/403 must NOT trigger a reauth flow -- this endpoint
    is anonymous, and an auth error here is unrelated to the user's
    ENGIE session.
    """
    client = _build_client(_build_response(status, "boom"))

    with pytest.raises(EngieBeApiClientCommunicationError):
        await client.async_get_epex_prices(_FROM, _TO)
