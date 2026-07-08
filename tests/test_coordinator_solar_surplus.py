"""Tests for coordinator-driven solar-surplus forecast fetching."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.engie_be.api import (
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
from custom_components.engie_be.coordinator import _derive_has_solar

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

_FIXTURES = Path(__file__).parent / "fixtures"
_PRICES = _FIXTURES / "prices_sample.json"
_PEAKS = _FIXTURES / "peaks_2026_04.json"
_FLAGS_NOT_ENROLLED = _FIXTURES / "feature_flags_not_enrolled.json"
_SOLAR_NO_DATA = _FIXTURES / "solar_surplus_no_data.json"
_SOLAR_HIGH = _FIXTURES / "solar_surplus_high.json"

_EAN = "541448820070414088"

pytestmark = pytest.mark.solar_surplus


def _load(path: Path) -> dict:
    """Return a fresh copy of a JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def _make_client(
    *,
    solar_payload: dict | Exception,
    solar_flag: dict | Exception | None = None,
    happy_hour_enrolled: bool = False,
    happy_hour_event: dict | None = None,
) -> MagicMock:
    """Build a client mock primed for a full coordinator refresh."""
    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=_load(_PRICES))
    client.async_get_monthly_peaks = AsyncMock(return_value=_load(_PEAKS))
    if happy_hour_enrolled:
        client.async_get_happy_hours_service_enabled_flag = AsyncMock(
            return_value={"value": True},
        )
    else:
        client.async_get_happy_hours_service_enabled_flag = AsyncMock(
            return_value=_load(_FLAGS_NOT_ENROLLED),
        )
    client.async_get_happy_hour_event = AsyncMock(
        return_value=happy_hour_event if happy_hour_event is not None else {},
    )
    client.async_get_month_report = AsyncMock(return_value={})
    flag_value = solar_flag if solar_flag is not None else {"value": True}
    if isinstance(flag_value, Exception):
        client.async_get_solar_surplus_shown_dashboard_flag = AsyncMock(
            side_effect=flag_value,
        )
    else:
        client.async_get_solar_surplus_shown_dashboard_flag = AsyncMock(
            return_value=flag_value,
        )
    if isinstance(solar_payload, Exception):
        client.async_get_solar_surplus_forecasts = AsyncMock(side_effect=solar_payload)
    else:
        client.async_get_solar_surplus_forecasts = AsyncMock(
            return_value=solar_payload,
        )
    return client


async def test_no_data_response_marks_has_solar_false(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """An all-NO_DATA response stores the wrapper and sets has_solar to False."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=_load(_SOLAR_NO_DATA))
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    result = await coord._async_update_data()

    assert "solar_surplus" in result
    assert _EAN in result["solar_surplus"]["data"]
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is False
    client.async_get_solar_surplus_forecasts.assert_awaited_once_with(
        "B-0001",
        f"{_EAN}_ID1",
    )


async def test_non_no_data_response_marks_has_solar_true(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """Any non-NO_DATA slot flips has_solar to True."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=_load(_SOLAR_HIGH))
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    await coord._async_update_data()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is True


async def test_no_electricity_ean_skips_fetch(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """With no electricity service points the fetch is skipped entirely."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=_load(_SOLAR_HIGH))
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord, service_points={"5414ZZ": "GAS"})

    await coord._async_update_data()

    client.async_get_solar_surplus_forecasts.assert_not_awaited()
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is None


async def test_transient_error_keeps_previous_wrapper(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """A transient API error preserves the last-known wrapper."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=EngieBeApiClientError("boom"))
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    previous_wrapper = {
        "data": {_EAN: _load(_SOLAR_HIGH)["forecasts"]},
        "fetched_at": "2026-07-07T00:00:00+00:00",
    }
    coord.data = {"solar_surplus": previous_wrapper}

    result = await coord._async_update_data()

    assert result["solar_surplus"] is previous_wrapper
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is True


async def test_auth_error_escalates(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """Auth failures on the solar endpoint escalate to reauth."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        solar_payload=EngieBeApiClientAuthenticationError("nope"),
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_flag_off_skips_fetch_and_marks_no_solar(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """A False feature flag skips the forecasts endpoint entirely."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        solar_payload=_load(_SOLAR_HIGH),
        solar_flag={"value": False},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    result = await coord._async_update_data()

    assert "solar_surplus" not in result
    client.async_get_solar_surplus_forecasts.assert_not_awaited()
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is False


async def test_flag_probe_error_soft_fails_to_enabled(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """A transient flag probe error keeps us in the ``try to fetch`` branch."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        solar_payload=_load(_SOLAR_HIGH),
        solar_flag=EngieBeApiClientError("boom"),
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    result = await coord._async_update_data()

    assert "solar_surplus" in result
    client.async_get_solar_surplus_forecasts.assert_awaited_once()
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is True


async def test_flag_auth_error_escalates(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """Auth failures on the feature-flag endpoint escalate to reauth."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        solar_payload=_load(_SOLAR_HIGH),
        solar_flag=EngieBeApiClientAuthenticationError("nope"),
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


def test_derive_has_solar_returns_false_for_empty_per_ean() -> None:
    """
    Wrapper present but no per-EAN forecasts returns False, not None.

    An empty per_ean dict is a valid 'no solar' shape. The helper must
    return False so callers can reconcile entity presence, not None
    (which means 'no signal').
    """
    assert _derive_has_solar({"data": {}, "fetched_at": "x"}) is False


def test_derive_has_solar_returns_none_for_non_dict_wrapper() -> None:
    """A non-dict wrapper (or None) is the 'no signal' case."""
    assert _derive_has_solar(None) is None
    assert _derive_has_solar([]) is None  # type: ignore[arg-type]


async def test_first_has_solar_observation_seeds_cache_without_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """First refresh (previous_has_solar=None) must NOT schedule a reload."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=_load(_SOLAR_HIGH))
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is True
    assert entry.runtime_data.reload_pending is False
    reload_mock.assert_not_awaited()


async def test_has_solar_true_to_false_flip_schedules_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """True->False flip must set reload_pending and call async_reload once."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=_load(_SOLAR_NO_DATA))
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    # Seed: cache says the customer HAS solar; new refresh returns all NO_DATA.
    entry.runtime_data.subentry_data[subentry.subentry_id].has_solar = True

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()
    await hass.async_block_till_done()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is False
    assert entry.runtime_data.reload_pending is True
    reload_mock.assert_awaited_once_with(entry.entry_id)


async def test_has_solar_no_flip_does_not_schedule_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """Same value on consecutive refreshes -> no reload, reload_pending stays False."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=_load(_SOLAR_HIGH))
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    # Seed: cache says True; new refresh also returns True (has data).
    entry.runtime_data.subentry_data[subentry.subentry_id].has_solar = True

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.has_solar is True
    assert entry.runtime_data.reload_pending is False
    reload_mock.assert_not_awaited()


async def test_simultaneous_happy_hour_and_solar_flips_debounce_to_one_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """
    Happy-hour + solar flip in the same refresh must schedule exactly one reload.

    Both handlers share ``EngieBeData.reload_pending`` -- whichever fires first
    sets the flag; the second sees it and skips its own reload. The test
    verifies the debounce holds under both flips happening in one cycle.
    """
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        solar_payload=_load(_SOLAR_HIGH),
        happy_hour_enrolled=True,
        happy_hour_event={},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    # Seed BOTH caches at the pre-flip state so this refresh causes two flips.
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    sub_data.is_happy_hour_enrolled = False  # will flip to True
    sub_data.has_solar = False  # will flip to True

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()
    await hass.async_block_till_done()

    # Both flags flipped in the runtime cache.
    assert sub_data.is_happy_hour_enrolled is True
    assert sub_data.has_solar is True
    # But only ONE reload was scheduled.
    assert entry.runtime_data.reload_pending is True
    reload_mock.assert_awaited_once_with(entry.entry_id)


async def test_reload_pending_blocks_second_flip_from_re_scheduling(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """
    If ``reload_pending`` is already True, a fresh solar flip must not reschedule.

    Simulates an earlier reload already queued (flag is already True); verifies
    the solar flip handler sees it and skips scheduling its own reload.
    """
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(solar_payload=_load(_SOLAR_HIGH))
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    sub_data.has_solar = False  # will flip to True
    entry.runtime_data.reload_pending = True  # simulate earlier reload queued

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()
    await hass.async_block_till_done()

    # Cache still updates.
    assert sub_data.has_solar is True
    # But no new reload was scheduled -- the debounce holds.
    reload_mock.assert_not_awaited()
