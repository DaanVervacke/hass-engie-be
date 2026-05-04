"""Tests for the dynamic-tariff (EPEX) branch of the coordinator."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.api import (
    EngieBeApiClientCommunicationError,
    EpexNotPublishedError,
)
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CUSTOMER_NUMBER,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    EPEX_TZ,
    KEY_EPEX,
    KEY_IS_DYNAMIC,
)
from custom_components.engie_be.coordinator import EngieBeDataUpdateCoordinator
from custom_components.engie_be.data import EngieBeData, EpexPayload, EpexSlot

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_FIXTURES = Path(__file__).parent / "fixtures"
_PRICES_FIXTURE = _FIXTURES / "prices_sample.json"
_PEAKS_FIXTURE = _FIXTURES / "peaks_2026_04.json"
_EPEX_24H_FIXTURE = _FIXTURES / "epex_24h.json"
_EPEX_48H_FIXTURE = _FIXTURES / "epex_48h.json"

_BRUSSELS = ZoneInfo(EPEX_TZ)

# Anchor "now" inside the 48h fixture window.  2026-05-04 15:30 Brussels =
# inside the 15:00 slot (value 25.65 EUR/MWh).
_NOW_BRUSSELS = datetime(2026, 5, 4, 15, 30, 0, tzinfo=_BRUSSELS)
_NOW_UTC = _NOW_BRUSSELS.astimezone(UTC)


def _build_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a config entry with credentials and test runtime placeholder."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="user@example.com",
        unique_id="user_example_com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_CUSTOMER_NUMBER: "000000000000",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "stored-access",
            CONF_REFRESH_TOKEN: "stored-refresh",
        },
        options={"update_interval": 60},
    )
    entry.add_to_hass(hass)
    return entry


def _attach_runtime(entry: MockConfigEntry, client: MagicMock) -> None:
    """Attach an EngieBeData runtime stub with the given mocked client."""
    entry.runtime_data = EngieBeData(
        client=client,
        coordinator=MagicMock(),
        last_options=dict(entry.options),
    )


def _build_dynamic_client() -> MagicMock:
    """
    Build a mocked client whose prices endpoint returns ``items=[]`` (dynamic).

    ``async_get_monthly_peaks`` returns the standard peaks fixture so
    the peaks branch of ``_async_update_data`` succeeds and doesn't
    interfere with what we're actually testing.  The EPEX call is
    configured per-test.
    """
    peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))
    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value={"items": []})
    client.async_get_monthly_peaks = AsyncMock(return_value=peaks)
    client.async_get_epex_prices = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Detection: is_dynamic flag
# ---------------------------------------------------------------------------


async def test_non_dynamic_account_does_not_call_epex_endpoint(
    hass: HomeAssistant,
) -> None:
    """
    A populated ``items`` list must NOT trigger an EPEX fetch.

    Detection is account-level via ``items==[]``.  A fixed-tariff
    account has rates to expose, so calling the EPEX endpoint would be
    wasted bandwidth and would risk surfacing irrelevant data.
    """
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(return_value=peaks)
    client.async_get_epex_prices = AsyncMock()
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    result = await coordinator._async_update_data()

    assert result[KEY_IS_DYNAMIC] is False
    assert result[KEY_EPEX] is None
    client.async_get_epex_prices.assert_not_called()


async def test_dynamic_account_triggers_epex_fetch_and_parses_payload(
    hass: HomeAssistant,
) -> None:
    """``items==[]`` must trigger a 2-day Brussels-local EPEX window fetch."""
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_48H_FIXTURE.read_text(encoding="utf-8"))

    client = _build_dynamic_client()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        result = await coordinator._async_update_data()

    assert result[KEY_IS_DYNAMIC] is True
    epex = result[KEY_EPEX]
    assert isinstance(epex, EpexPayload)
    # 48 hourly slots in the fixture.
    assert len(epex.slots) == 48
    # Slots are normalised to Brussels-local during parsing.
    assert all(slot.start.tzinfo is not None for slot in epex.slots)
    assert all(
        slot.start.utcoffset() == _BRUSSELS.utcoffset(slot.start) for slot in epex.slots
    )
    # Wholesale EUR/MWh divided by 1000 -> EUR/kWh.
    # First slot in fixture: value 110.42 EUR/MWh -> 0.11042 EUR/kWh.
    assert epex.slots[0].value_eur_per_kwh == pytest.approx(0.11042)
    # Slot duration is exposed for forward-compat with 15-min publication.
    assert epex.slots[0].duration_minutes == 60
    assert epex.slots[0].end - epex.slots[0].start == timedelta(hours=1)
    # Publication metadata round-trips.
    assert epex.market_date == "2026-05-05"
    assert epex.publication_time is not None


async def test_dynamic_branch_requests_two_full_brussels_days(
    hass: HomeAssistant,
) -> None:
    """
    The EPEX window must be ``[today_00:00, today+2d_00:00)`` Brussels-local.

    This guarantees we always cover today + tomorrow regardless of which
    side of the 13:15 publication tick we're polling, and stays
    DST-safe by using local-midnight rather than fixed UTC offsets.
    """
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_48H_FIXTURE.read_text(encoding="utf-8"))

    client = _build_dynamic_client()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        await coordinator._async_update_data()

    call = client.async_get_epex_prices.await_args
    assert call is not None
    start_dt, end_dt = call.args
    expected_start = datetime(2026, 5, 4, 0, 0, 0, tzinfo=_BRUSSELS)
    expected_end = datetime(2026, 5, 6, 0, 0, 0, tzinfo=_BRUSSELS)
    assert start_dt == expected_start
    assert end_dt == expected_end


# ---------------------------------------------------------------------------
# Last-known fallback
# ---------------------------------------------------------------------------


async def test_epex_404_keeps_last_known_payload(hass: HomeAssistant) -> None:
    """
    ``EpexNotPublishedError`` must NOT clobber the last-known payload.

    Tomorrow's slate isn't published until ~13:15 Brussels.  Until
    then, the API returns 404 -- but yesterday's data (which still
    contains today's slots) is perfectly valid and must remain visible
    on the sensors.
    """
    entry = _build_entry(hass)
    client = _build_dynamic_client()
    client.async_get_epex_prices = AsyncMock(
        side_effect=EpexNotPublishedError("not yet"),
    )
    _attach_runtime(entry, client)

    # Seed coordinator.data with a previous EPEX payload (one slot is enough).
    seeded_slot = EpexSlot(
        start=datetime(2026, 5, 4, 12, 0, tzinfo=_BRUSSELS),
        end=datetime(2026, 5, 4, 13, 0, tzinfo=_BRUSSELS),
        value_eur_per_kwh=0.12345,
        duration_minutes=60,
    )
    seeded_payload = EpexPayload(
        slots=(seeded_slot,),
        publication_time=None,
        market_date="2026-05-04",
    )

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    coordinator.data = {KEY_IS_DYNAMIC: True, KEY_EPEX: seeded_payload}

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        result = await coordinator._async_update_data()

    assert result[KEY_IS_DYNAMIC] is True
    # Same payload object: no parse, no clobber.
    assert result[KEY_EPEX] is seeded_payload


async def test_epex_transient_error_keeps_last_known_payload(
    hass: HomeAssistant,
) -> None:
    """
    A transient comms error must also fall back to the last-known payload.

    Network blips and 5xx must not produce ``unavailable`` on the
    sensor; users would lose visibility of today's prices for a hiccup.
    """
    entry = _build_entry(hass)
    client = _build_dynamic_client()
    client.async_get_epex_prices = AsyncMock(
        side_effect=EngieBeApiClientCommunicationError("502"),
    )
    _attach_runtime(entry, client)

    seeded_payload = EpexPayload(slots=(), publication_time=None, market_date=None)
    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    coordinator.data = {KEY_IS_DYNAMIC: True, KEY_EPEX: seeded_payload}

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        result = await coordinator._async_update_data()

    assert result[KEY_EPEX] is seeded_payload


async def test_epex_404_with_no_previous_payload_returns_none(
    hass: HomeAssistant,
) -> None:
    """
    First-ever poll hitting a 404: ``epex`` is ``None``, sensors unavailable.

    No previous payload means we have nothing to fall back to.  The
    coordinator returns successfully with ``epex=None`` and the
    sensors' ``available`` property handles the rest.
    """
    entry = _build_entry(hass)
    client = _build_dynamic_client()
    client.async_get_epex_prices = AsyncMock(
        side_effect=EpexNotPublishedError("not yet"),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        result = await coordinator._async_update_data()

    assert result[KEY_IS_DYNAMIC] is True
    assert result[KEY_EPEX] is None


# ---------------------------------------------------------------------------
# Tariff-type transitions
# ---------------------------------------------------------------------------


async def test_dynamic_to_non_dynamic_clears_epex_payload(
    hass: HomeAssistant,
) -> None:
    """
    A contract switch from dynamic to fixed must drop the cached EPEX.

    Otherwise sensors on a now-fixed account would keep showing
    stale wholesale prices indefinitely.
    """
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))

    # Account has just transitioned to fixed: items is now non-empty.
    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(return_value=peaks)
    client.async_get_epex_prices = AsyncMock()
    _attach_runtime(entry, client)

    # Pretend a previous poll cached an EPEX payload.
    seeded_payload = EpexPayload(slots=(), publication_time=None, market_date="x")
    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    coordinator.data = {KEY_IS_DYNAMIC: True, KEY_EPEX: seeded_payload}

    result = await coordinator._async_update_data()

    assert result[KEY_IS_DYNAMIC] is False
    assert result[KEY_EPEX] is None
    client.async_get_epex_prices.assert_not_called()


# ---------------------------------------------------------------------------
# Fixture round-trip parsing sanity
# ---------------------------------------------------------------------------


async def test_24h_fixture_parses_to_24_hourly_slots(hass: HomeAssistant) -> None:
    """
    The 24h fixture must produce exactly 24 hourly Brussels-local slots.

    Defends against silent regressions in ``_parse_epex_response``
    (e.g. accidentally dropping rows with ``value=0`` or negative
    floats) by exercising a known-good payload end-to-end.
    """
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_24H_FIXTURE.read_text(encoding="utf-8"))

    client = _build_dynamic_client()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        result = await coordinator._async_update_data()

    epex = result[KEY_EPEX]
    assert isinstance(epex, EpexPayload)
    assert len(epex.slots) == 24
    # Negative wholesale prices are valid (over-supply) and must be preserved.
    negative_slots = [s for s in epex.slots if s.value_eur_per_kwh < 0]
    assert len(negative_slots) >= 1


# Last-fetched is stamped via dt_util.utcnow() in the coordinator.  This
# is a sanity test against a regression where the EPEX branch could
# return early and skip the timestamp.
async def test_last_successful_fetch_stamped_on_dynamic_account(
    hass: HomeAssistant,
) -> None:
    """``last_successful_fetch`` must be set even on the dynamic branch."""
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_48H_FIXTURE.read_text(encoding="utf-8"))

    client = _build_dynamic_client()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(hass=hass, config_entry=entry)
    assert coordinator.last_successful_fetch is None

    with (
        patch(
            "custom_components.engie_be.coordinator.dt_util.now",
            return_value=_NOW_BRUSSELS,
        ),
        patch(
            "custom_components.engie_be.coordinator.dt_util.utcnow",
            return_value=_NOW_UTC,
        ),
    ):
        await coordinator._async_update_data()

    assert coordinator.last_successful_fetch == _NOW_UTC
