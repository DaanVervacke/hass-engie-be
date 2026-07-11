"""Tests for the parent-entry-level EPEX day-ahead coordinator."""

from __future__ import annotations

import json
import logging
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
    CONF_REFRESH_TOKEN,
    DOMAIN,
    EPEX_TZ,
)
from custom_components.engie_be.coordinator import (
    EngieBeEpexCoordinator,
    EngieBeEpexQuarterHourCoordinator,
)
from custom_components.engie_be.data import EngieBeData, EpexPayload, EpexSlot

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_FIXTURES = Path(__file__).parent / "fixtures"
_EPEX_24H_FIXTURE = _FIXTURES / "epex_24h.json"
_EPEX_48H_FIXTURE = _FIXTURES / "epex_48h.json"
_EPEX_96H_FIXTURE = _FIXTURES / "epex_96h.json"

_BRUSSELS = ZoneInfo(EPEX_TZ)

# Anchor "now" inside the 48h fixture window. 2026-05-04 15:30 Brussels =
# inside the 15:00 slot (value 25.65 EUR/MWh).
_NOW_BRUSSELS = datetime(2026, 5, 4, 15, 30, 0, tzinfo=_BRUSSELS)
_NOW_UTC = _NOW_BRUSSELS.astimezone(UTC)

# Anchor "now" inside the 96h (quarter-hourly) fixture window.
# 2026-05-04 15:30 Brussels is aligned with a 15-minute slot boundary.
_NOW_BRUSSELS_QH = datetime(2026, 5, 4, 15, 30, 0, tzinfo=_BRUSSELS)
_NOW_UTC_QH = _NOW_BRUSSELS_QH.astimezone(UTC)


def _build_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a v4 parent ConfigEntry. EPEX coord doesn't need subentries."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        title="user@example.com",
        unique_id="user_example_com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
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
        epex_coordinator=MagicMock(),
        subentry_data={},
        authenticated=True,
        last_options=dict(entry.options),
    )


# ---------------------------------------------------------------------------
# Successful fetch + parse
# ---------------------------------------------------------------------------


async def test_fetch_parses_48h_payload_to_brussels_local_slots(
    hass: HomeAssistant,
) -> None:
    """A successful fetch returns an :class:`EpexPayload` with parsed slots."""
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_48H_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        epex = await coordinator._async_update_data()

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
    assert epex.slots[0].end - epex.slots[0].start == timedelta(hours=1)
    # Publication metadata round-trips.
    assert epex.market_date == "2026-05-05"
    assert epex.publication_time is not None


async def test_fetch_requests_two_full_brussels_days(hass: HomeAssistant) -> None:
    """
    The EPEX window must be ``[today_00:00, today+2d_00:00)`` Brussels-local.

    This guarantees we always cover today + tomorrow regardless of which
    side of the 13:15 publication tick we're polling, and stays
    DST-safe by using local-midnight rather than fixed UTC offsets.
    """
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_48H_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)

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


async def test_24h_fixture_parses_to_24_hourly_slots(hass: HomeAssistant) -> None:
    """
    The 24h fixture must produce exactly 24 hourly Brussels-local slots.

    Defends against silent regressions in ``_parse_epex_response``
    (e.g. accidentally dropping rows with ``value=0`` or negative
    floats) by exercising a known-good payload end-to-end.
    """
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_24H_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        epex = await coordinator._async_update_data()

    assert isinstance(epex, EpexPayload)
    assert len(epex.slots) == 24
    # Negative wholesale prices are valid (over-supply) and must be preserved.
    negative_slots = [s for s in epex.slots if s.value_eur_per_kwh < 0]
    assert len(negative_slots) >= 1


# ---------------------------------------------------------------------------
# Last-known fallback
# ---------------------------------------------------------------------------


async def test_404_keeps_last_known_payload(hass: HomeAssistant) -> None:
    """
    ``EpexNotPublishedError`` must NOT clobber the last-known payload.

    Tomorrow's slate isn't published until ~13:15 Brussels. Until
    then, the API returns 404 -- but yesterday's data (which still
    contains today's slots) is perfectly valid and must remain visible
    on the sensors.
    """
    entry = _build_entry(hass)
    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(
        side_effect=EpexNotPublishedError("not yet"),
    )
    _attach_runtime(entry, client)

    seeded_slot = EpexSlot(
        start=datetime(2026, 5, 4, 12, 0, tzinfo=_BRUSSELS),
        end=datetime(2026, 5, 4, 13, 0, tzinfo=_BRUSSELS),
        value_eur_per_kwh=0.12345,
    )
    seeded_payload = EpexPayload(
        slots=(seeded_slot,),
        publication_time=None,
        market_date="2026-05-04",
    )

    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)
    coordinator.data = seeded_payload

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        result = await coordinator._async_update_data()

    # Same payload object: no parse, no clobber.
    assert result is seeded_payload


async def test_transient_error_keeps_last_known_payload(
    hass: HomeAssistant,
) -> None:
    """
    A transient comms error must also fall back to the last-known payload.

    Network blips and 5xx must not produce ``unavailable`` on the
    sensor; users would lose visibility of today's prices for a hiccup.
    """
    entry = _build_entry(hass)
    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(
        side_effect=EngieBeApiClientCommunicationError("502"),
    )
    _attach_runtime(entry, client)

    seeded_payload = EpexPayload(slots=(), publication_time=None, market_date=None)
    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)
    coordinator.data = seeded_payload

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        result = await coordinator._async_update_data()

    assert result is seeded_payload


async def test_404_with_no_previous_payload_returns_none(
    hass: HomeAssistant,
) -> None:
    """
    First-ever poll hitting a 404: returns ``None``, sensors unavailable.

    No previous payload means we have nothing to fall back to. The
    coordinator returns ``None`` and the sensors' ``available`` property
    handles the rest.
    """
    entry = _build_entry(hass)
    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(
        side_effect=EpexNotPublishedError("not yet"),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        result = await coordinator._async_update_data()

    assert result is None


async def test_parse_failure_keeps_last_known_payload(
    hass: HomeAssistant,
) -> None:
    """
    A malformed upstream payload must not clobber a previously good one.

    ``_parse_epex_response`` raises on structural problems; the
    coordinator catches and returns the previous payload instead.
    """
    entry = _build_entry(hass)
    client = MagicMock()
    # Garbage that ``_parse_epex_response`` will reject.
    client.async_get_epex_prices = AsyncMock(return_value="not a dict")
    _attach_runtime(entry, client)

    seeded_payload = EpexPayload(
        slots=(),
        publication_time=None,
        market_date="2026-05-04",
    )
    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)
    coordinator.data = seeded_payload

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS,
    ):
        result = await coordinator._async_update_data()

    assert result is seeded_payload


# ---------------------------------------------------------------------------
# Sanity: coordinator construction
# ---------------------------------------------------------------------------


def test_epex_coordinator_uses_options_update_interval(
    hass: HomeAssistant,
) -> None:
    """The EPEX coordinator must honour the parent entry's update_interval option."""
    entry = _build_entry(hass)
    _attach_runtime(entry, MagicMock())

    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)

    assert coordinator.update_interval == timedelta(minutes=60)


async def test_last_successful_fetch_metadata_via_coordinator_state(
    hass: HomeAssistant,
) -> None:
    """
    A successful EPEX refresh marks the coordinator as last-update-success.

    Since EPEX is a separate ``DataUpdateCoordinator``, success/failure
    is observable through the standard ``last_update_success`` attribute
    rather than through a custom timestamp on the coordinator.
    """
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_48H_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)

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
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert isinstance(coordinator.data, EpexPayload)


# ---------------------------------------------------------------------------
# log-when-unavailable: warn once per outage, info once on recovery
# ---------------------------------------------------------------------------


async def test_comms_outage_logs_warning_once_then_debug(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A sustained comms outage must warn exactly once, then fall to debug.

    The EPEX coordinator keeps serving the last-known payload instead of
    raising ``UpdateFailed``, so it owns the ``log-when-unavailable``
    contract manually: one WARNING on the transition into the outage,
    DEBUG for every subsequent failed poll while it persists.
    """
    entry = _build_entry(hass)
    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(
        side_effect=EngieBeApiClientCommunicationError("502"),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)

    msg = "Failed to fetch EPEX prices"
    with (
        caplog.at_level(logging.DEBUG, logger="custom_components.engie_be"),
        patch(
            "custom_components.engie_be.coordinator.dt_util.now",
            return_value=_NOW_BRUSSELS,
        ),
    ):
        for _ in range(3):
            await coordinator._async_update_data()

    fetch_records = [r for r in caplog.records if msg in r.getMessage()]
    warnings = [r for r in fetch_records if r.levelno == logging.WARNING]
    debugs = [r for r in fetch_records if r.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert len(debugs) == 2
    assert coordinator._unavailable_logged is True


async def test_recovery_logs_info_once_and_rearms(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Recovery emits one INFO and re-arms the warning for the next outage.

    Sequence: fail -> succeed -> fail. We expect a WARNING on the first
    failure, an INFO on recovery, and a *second* WARNING on the next
    failure (proving the flag reset on recovery).
    """
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_48H_FIXTURE.read_text(encoding="utf-8"))
    err = EngieBeApiClientCommunicationError("502")

    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(side_effect=[err, epex_raw, err])
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)

    with (
        caplog.at_level(logging.DEBUG, logger="custom_components.engie_be"),
        patch(
            "custom_components.engie_be.coordinator.dt_util.now",
            return_value=_NOW_BRUSSELS,
        ),
        patch(
            "custom_components.engie_be.coordinator.dt_util.utcnow",
            return_value=_NOW_UTC,
        ),
    ):
        await coordinator._async_update_data()  # fail -> warn
        assert coordinator._unavailable_logged is True
        await coordinator._async_update_data()  # success -> recovered
        assert coordinator._unavailable_logged is False
        await coordinator._async_update_data()  # fail -> warn again

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and "Failed to fetch EPEX prices" in r.getMessage()
    ]
    recovered = [r for r in caplog.records if "recovered" in r.getMessage()]
    assert len(warnings) == 2
    assert len(recovered) == 1
    assert recovered[0].levelno == logging.INFO


async def test_parse_failure_logs_warning_once(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A sustained parse failure also warns once, then drops to debug."""
    entry = _build_entry(hass)
    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(return_value="not a dict")
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)

    msg = "Failed to parse EPEX response"
    with (
        caplog.at_level(logging.DEBUG, logger="custom_components.engie_be"),
        patch(
            "custom_components.engie_be.coordinator.dt_util.now",
            return_value=_NOW_BRUSSELS,
        ),
    ):
        await coordinator._async_update_data()
        await coordinator._async_update_data()

    parse_records = [r for r in caplog.records if msg in r.getMessage()]
    warnings = [r for r in parse_records if r.levelno == logging.WARNING]
    debugs = [r for r in parse_records if r.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert len(debugs) == 1


# ---------------------------------------------------------------------------
# Quarter-hourly coordinator tests
# ---------------------------------------------------------------------------


async def test_qh_fetch_parses_96_slots_to_brussels_local(
    hass: HomeAssistant,
) -> None:
    """A successful QH fetch returns an EpexPayload with 96 parsed 15-min slots."""
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_96H_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexQuarterHourCoordinator(hass=hass, config_entry=entry)

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS_QH,
    ):
        epex = await coordinator._async_update_data()

    assert isinstance(epex, EpexPayload)
    # 96 quarter-hourly slots in the fixture (1 day * 24 hours * 4 quarters)
    assert len(epex.slots) == 96
    # Slots are normalised to Brussels-local during parsing
    assert all(slot.start.tzinfo is not None for slot in epex.slots)
    assert all(
        slot.start.utcoffset() == _BRUSSELS.utcoffset(slot.start) for slot in epex.slots
    )
    # Wholesale EUR/MWh divided by 1000 -> EUR/kWh
    assert epex.slots[0].value_eur_per_kwh == pytest.approx(0.11042)
    # Verify slot duration is 15 minutes
    assert epex.slot_duration == timedelta(minutes=15)
    assert epex.slots[0].end - epex.slots[0].start == timedelta(minutes=15)
    # Publication metadata round-trips
    assert epex.market_date == "2026-05-05"
    assert epex.publication_time is not None


async def test_qh_fetch_requests_quarter_hourly_granularity(
    hass: HomeAssistant,
) -> None:
    """The QH coordinator must request granularity=QUARTER_HOURLY from the API."""
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_96H_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexQuarterHourCoordinator(hass=hass, config_entry=entry)

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS_QH,
    ):
        await coordinator._async_update_data()

    call = client.async_get_epex_prices.await_args
    assert call is not None
    # Verify the granularity parameter was passed
    assert call.kwargs.get("granularity") == "QUARTER_HOURLY"


async def test_qh_fetch_requests_two_full_brussels_days(
    hass: HomeAssistant,
) -> None:
    """
    The QH EPEX window must be [today_00:00, today+2d_00:00) Brussels-local.

    Same as hourly - this guarantees we always cover today + tomorrow regardless
    of which side of the 13:15 publication tick we're polling, and stays
    DST-safe by using local-midnight rather than fixed UTC offsets.
    """
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_96H_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexQuarterHourCoordinator(hass=hass, config_entry=entry)

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS_QH,
    ):
        await coordinator._async_update_data()

    call = client.async_get_epex_prices.await_args
    assert call is not None
    start_dt, end_dt = call.args
    expected_start = datetime(2026, 5, 4, 0, 0, 0, tzinfo=_BRUSSELS)
    expected_end = datetime(2026, 5, 6, 0, 0, 0, tzinfo=_BRUSSELS)
    assert start_dt == expected_start
    assert end_dt == expected_end


async def test_qh_404_keeps_last_known_payload(hass: HomeAssistant) -> None:
    """
    EpexNotPublishedError must NOT clobber the last-known QH payload.
    """
    entry = _build_entry(hass)
    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(
        side_effect=EpexNotPublishedError("not yet"),
    )
    _attach_runtime(entry, client)

    seeded_slot = EpexSlot(
        start=datetime(2026, 5, 4, 12, 0, tzinfo=_BRUSSELS),
        end=datetime(2026, 5, 4, 12, 15, tzinfo=_BRUSSELS),
        value_eur_per_kwh=0.12345,
    )
    seeded_payload = EpexPayload(
        slots=(seeded_slot,),
        publication_time=None,
        market_date="2026-05-04",
        slot_duration=timedelta(minutes=15),
    )

    coordinator = EngieBeEpexQuarterHourCoordinator(hass=hass, config_entry=entry)
    coordinator.data = seeded_payload

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS_QH,
    ):
        result = await coordinator._async_update_data()

    # Same payload object: no parse, no clobber
    assert result is seeded_payload


async def test_qh_transient_error_keeps_last_known_payload(
    hass: HomeAssistant,
) -> None:
    """A transient comms error must also fall back to the last-known QH payload."""
    entry = _build_entry(hass)
    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(
        side_effect=EngieBeApiClientCommunicationError("502"),
    )
    _attach_runtime(entry, client)

    seeded_payload = EpexPayload(
        slots=(),
        publication_time=None,
        market_date=None,
        slot_duration=timedelta(minutes=15),
    )
    coordinator = EngieBeEpexQuarterHourCoordinator(hass=hass, config_entry=entry)
    coordinator.data = seeded_payload

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS_QH,
    ):
        result = await coordinator._async_update_data()

    assert result is seeded_payload


async def test_qh_parse_failure_keeps_last_known_payload(
    hass: HomeAssistant,
) -> None:
    """A malformed upstream QH payload must not clobber a previously good one."""
    entry = _build_entry(hass)
    client = MagicMock()
    # Garbage that _parse_epex_response will reject
    client.async_get_epex_prices = AsyncMock(return_value="not a dict")
    _attach_runtime(entry, client)

    seeded_payload = EpexPayload(
        slots=(),
        publication_time=None,
        market_date="2026-05-04",
        slot_duration=timedelta(minutes=15),
    )
    coordinator = EngieBeEpexQuarterHourCoordinator(hass=hass, config_entry=entry)
    coordinator.data = seeded_payload

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS_QH,
    ):
        result = await coordinator._async_update_data()

    assert result is seeded_payload


async def test_qh_404_with_no_previous_payload_returns_none(
    hass: HomeAssistant,
) -> None:
    """First-ever poll hitting a 404: returns None, sensors unavailable."""
    entry = _build_entry(hass)
    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(
        side_effect=EpexNotPublishedError("not yet"),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexQuarterHourCoordinator(hass=hass, config_entry=entry)

    with patch(
        "custom_components.engie_be.coordinator.dt_util.now",
        return_value=_NOW_BRUSSELS_QH,
    ):
        result = await coordinator._async_update_data()

    assert result is None


def test_qh_epex_coordinator_uses_options_update_interval(
    hass: HomeAssistant,
) -> None:
    """The QH EPEX coordinator must honour the parent entry's update_interval option."""
    entry = _build_entry(hass)
    _attach_runtime(entry, MagicMock())

    coordinator = EngieBeEpexQuarterHourCoordinator(hass=hass, config_entry=entry)

    assert coordinator.update_interval == timedelta(minutes=60)


async def test_qh_last_successful_fetch_metadata_via_coordinator_state(
    hass: HomeAssistant,
) -> None:
    """A successful QH EPEX refresh marks the coordinator as last-update-success."""
    entry = _build_entry(hass)
    epex_raw = json.loads(_EPEX_96H_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(return_value=epex_raw)
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexQuarterHourCoordinator(hass=hass, config_entry=entry)

    with (
        patch(
            "custom_components.engie_be.coordinator.dt_util.now",
            return_value=_NOW_BRUSSELS_QH,
        ),
        patch(
            "custom_components.engie_be.coordinator.dt_util.utcnow",
            return_value=_NOW_UTC_QH,
        ),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert isinstance(coordinator.data, EpexPayload)
    assert coordinator.data.slot_duration == timedelta(minutes=15)


async def test_qh_comms_outage_logs_warning_once_then_debug(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """QH coordinator: warn once on first comms failure, debug on subsequent."""
    entry = _build_entry(hass)
    client = MagicMock()
    client.async_get_epex_prices = AsyncMock(
        side_effect=EngieBeApiClientCommunicationError("502"),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexQuarterHourCoordinator(hass=hass, config_entry=entry)

    msg = "Failed to fetch EPEX QH prices"
    with (
        caplog.at_level(logging.DEBUG, logger="custom_components.engie_be"),
        patch(
            "custom_components.engie_be.coordinator.dt_util.now",
            return_value=_NOW_BRUSSELS_QH,
        ),
    ):
        await coordinator._async_update_data()
        await coordinator._async_update_data()

    comms_records = [r for r in caplog.records if msg in r.getMessage()]
    warnings = [r for r in comms_records if r.levelno == logging.WARNING]
    debugs = [r for r in comms_records if r.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert len(debugs) == 1


async def test_qh_recovery_logs_info_once_and_rearms(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """QH coordinator: info on recovery, then warn again on next failure."""
    entry = _build_entry(hass)
    client = MagicMock()

    # First call: error
    client.async_get_epex_prices = AsyncMock(
        side_effect=EngieBeApiClientCommunicationError("502"),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeEpexQuarterHourCoordinator(hass=hass, config_entry=entry)

    msg_outage = "Failed to fetch EPEX QH prices"
    msg_recovery = "EPEX QH prices fetch recovered"

    with (
        caplog.at_level(logging.DEBUG, logger="custom_components.engie_be"),
        patch(
            "custom_components.engie_be.coordinator.dt_util.now",
            return_value=_NOW_BRUSSELS_QH,
        ),
        patch(
            "custom_components.engie_be.coordinator.dt_util.utcnow",
            return_value=_NOW_UTC_QH,
        ),
    ):
        # First call: outage
        await coordinator._async_update_data()
        # Reset mock to return valid data
        client.async_get_epex_prices = AsyncMock(
            return_value=json.loads(_EPEX_96H_FIXTURE.read_text(encoding="utf-8"))
        )
        # Second call: recovery
        await coordinator._async_update_data()
        # Third call: another outage
        client.async_get_epex_prices = AsyncMock(
            side_effect=EngieBeApiClientCommunicationError("502"),
        )
        await coordinator._async_update_data()

    outage_records = [r for r in caplog.records if msg_outage in r.getMessage()]
    recovery_records = [r for r in caplog.records if msg_recovery in r.getMessage()]

    warnings = [r for r in outage_records if r.levelno == logging.WARNING]
    debugs = [r for r in outage_records if r.levelno == logging.DEBUG]
    infos = [r for r in recovery_records if r.levelno == logging.INFO]

    assert len(warnings) == 2  # Warning on first and third call
    assert len(debugs) == 0  # No debugs for outage in this scenario
    assert len(infos) == 1
