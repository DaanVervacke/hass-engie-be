"""Tests for coordinator-driven solar-surplus forecast fetching."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

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
_SOLAR_NO_DATA = _FIXTURES / "solar_surplus_no_data.json"
_SOLAR_HIGH = _FIXTURES / "solar_surplus_high.json"

_EAN = "541448820070414088"

pytestmark = pytest.mark.solar_surplus


def _load(path: Path) -> dict:
    """Return a fresh copy of a JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


async def test_no_data_response_marks_has_solar_false(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """An all-NO_DATA response stores the wrapper and sets has_solar to False."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=_load(_SOLAR_NO_DATA),
        solar_flag={"value": True},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    result = await coord._async_update_data()

    assert "solar_surplus" in result
    assert _EAN in result["solar_surplus"]["data"]
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.solar is False
    client.async_get_solar_surplus_forecasts.assert_awaited_once_with(
        "B-0001",
        f"{_EAN}_ID1",
    )


async def test_non_no_data_response_marks_has_solar_true(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """Any non-NO_DATA slot flips has_solar to True."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=_load(_SOLAR_HIGH),
        solar_flag={"value": True},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    await coord._async_update_data()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.solar is True


async def test_no_electricity_ean_skips_fetch(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """With no electricity service points the fetch is skipped entirely."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=_load(_SOLAR_HIGH),
        solar_flag={"value": True},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord, service_points={"5414ZZ": "GAS"})

    await coord._async_update_data()

    client.async_get_solar_surplus_forecasts.assert_not_awaited()
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.solar is None


async def test_transient_error_keeps_previous_wrapper(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """A transient API error preserves the last-known wrapper."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=EngieBeApiClientError("boom"),
        solar_flag={"value": True},
    )
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
    assert sub_data.feature_flags.solar is True


async def test_auth_error_escalates(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """Auth failures on the solar endpoint escalate to reauth."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=EngieBeApiClientAuthenticationError("nope"),
        solar_flag={"value": True},
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
    engie_client_baseline: Callable,
) -> None:
    """A False feature flag skips the forecasts endpoint entirely."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=_load(_SOLAR_HIGH),
        solar_flag={"value": False},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    result = await coord._async_update_data()

    assert "solar_surplus" not in result
    client.async_get_solar_surplus_forecasts.assert_not_awaited()
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.solar is False


async def test_flag_probe_error_soft_fails_to_enabled(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """A transient flag probe error keeps us in the ``try to fetch`` branch."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=_load(_SOLAR_HIGH),
        solar_flag=EngieBeApiClientError("boom"),
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    result = await coord._async_update_data()

    assert "solar_surplus" in result
    client.async_get_solar_surplus_forecasts.assert_awaited_once()
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.solar is True


async def test_flag_auth_error_escalates(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """Auth failures on the feature-flag endpoint escalate to reauth."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=_load(_SOLAR_HIGH),
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


async def test_first_has_solar_observation_seeds_cache_without_reload(  # noqa: PLR0913
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """First refresh (previous_has_solar=None) must NOT schedule a reload."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=_load(_SOLAR_HIGH),
        solar_flag={"value": True},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.solar is True
    assert entry.runtime_data.reload_pending is False
    reload_mock.assert_not_awaited()


async def test_has_solar_true_to_false_flip_schedules_reload(  # noqa: PLR0913
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """True->False flip must set reload_pending and call async_reload once."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=_load(_SOLAR_NO_DATA),
        solar_flag={"value": True},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    # Seed: cache says the customer HAS solar; new refresh returns all NO_DATA.
    entry.runtime_data.subentry_data[subentry.subentry_id].feature_flags.solar = True

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()
    await hass.async_block_till_done()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.solar is False
    assert entry.runtime_data.reload_pending is True
    reload_mock.assert_awaited_once_with(entry.entry_id)


async def test_has_solar_no_flip_does_not_schedule_reload(  # noqa: PLR0913
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """Same value on consecutive refreshes -> no reload, reload_pending stays False."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=_load(_SOLAR_HIGH),
        solar_flag={"value": True},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    # Seed: cache says True; new refresh also returns True (has data).
    entry.runtime_data.subentry_data[subentry.subentry_id].feature_flags.solar = True

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.solar is True
    assert entry.runtime_data.reload_pending is False
    reload_mock.assert_not_awaited()


async def test_simultaneous_happy_hour_and_solar_flips_debounce_to_one_reload(  # noqa: PLR0913
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """
    Happy-hour + solar flip in the same refresh must schedule exactly one reload.

    Both handlers share ``EngieBeData.reload_pending`` -- whichever fires first
    sets the flag; the second sees it and skips its own reload. The test
    verifies the debounce holds under both flips happening in one cycle.
    """
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=_load(_SOLAR_HIGH),
        solar_flag={"value": True},
        happy_hours_flag={"value": True},
        happy_hour_event={},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    # Seed BOTH caches at the pre-flip state so this refresh causes two flips.
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    sub_data.feature_flags.happy_hour_enrolled = False  # will flip to True
    sub_data.feature_flags.solar = False  # will flip to True

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()
    await hass.async_block_till_done()

    # Both flags flipped in the runtime cache.
    assert sub_data.feature_flags.happy_hour_enrolled is True
    assert sub_data.feature_flags.solar is True
    # But only ONE reload was scheduled.
    assert entry.runtime_data.reload_pending is True
    reload_mock.assert_awaited_once_with(entry.entry_id)


async def test_reload_pending_blocks_second_flip_from_re_scheduling(  # noqa: PLR0913
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
    engie_client_baseline: Callable,
) -> None:
    """
    If ``reload_pending`` is already True, a fresh solar flip must not reschedule.

    Simulates an earlier reload already queued (flag is already True); verifies
    the solar flip handler sees it and skips scheduling its own reload.
    """
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = engie_client_baseline(
        solar_forecasts=_load(_SOLAR_HIGH),
        solar_flag={"value": True},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    sub_data.feature_flags.solar = False  # will flip to True
    entry.runtime_data.reload_pending = True  # simulate earlier reload queued

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()
    await hass.async_block_till_done()

    # Cache still updates.
    assert sub_data.feature_flags.solar is True
    # But no new reload was scheduled -- the debounce holds.
    reload_mock.assert_not_awaited()
