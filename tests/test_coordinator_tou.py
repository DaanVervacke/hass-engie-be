"""Tests for coordinator-driven TOU schedule fetching."""

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

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

_FIXTURES = Path(__file__).parent / "fixtures"
_PRICES = _FIXTURES / "prices_sample.json"
_PEAKS = _FIXTURES / "peaks_2026_04.json"
_FLAGS_NOT_ENROLLED = _FIXTURES / "feature_flags_not_enrolled.json"
_TOU_BIHORAIRE = _FIXTURES / "tou_schedules_bihoraire.json"

_EAN = "541448820070000000"

pytestmark = pytest.mark.tou


def _load(path: Path) -> dict:
    """Return a fresh copy of a JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def _make_client(
    *,
    tou_payload: dict | Exception,
    tou_flag: dict | Exception | None = None,
) -> MagicMock:
    """Build a client mock primed for a full coordinator refresh."""
    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=_load(_PRICES))
    client.async_get_monthly_peaks = AsyncMock(return_value=_load(_PEAKS))
    client.async_get_happy_hours_service_enabled_flag = AsyncMock(
        return_value=_load(_FLAGS_NOT_ENROLLED),
    )
    client.async_get_happy_hour_event = AsyncMock(return_value={})
    client.async_get_month_report = AsyncMock(return_value={})
    client.async_get_solar_surplus_shown_dashboard_flag = AsyncMock(
        return_value={"value": False},
    )
    client.async_get_solar_surplus_forecasts = AsyncMock(return_value={"forecasts": []})

    flag_value = tou_flag if tou_flag is not None else {"value": False}
    if isinstance(flag_value, Exception):
        client.async_get_dgo_tou_is_active_flag = AsyncMock(side_effect=flag_value)
    else:
        client.async_get_dgo_tou_is_active_flag = AsyncMock(return_value=flag_value)

    if isinstance(tou_payload, Exception):
        client.async_get_tou_schedules = AsyncMock(side_effect=tou_payload)
    else:
        client.async_get_tou_schedules = AsyncMock(return_value=tou_payload)

    return client


async def test_tou_flag_off_skips_fetch_and_marks_inactive(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """Flag off skips the schedules endpoint entirely and drops any wrapper."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        tou_payload=_load(_TOU_BIHORAIRE),
        tou_flag={"value": False},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    result = await coord._async_update_data()

    assert "tou_schedules" not in result
    client.async_get_tou_schedules.assert_not_awaited()
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.tou_active is False


async def test_tou_flag_on_stores_wrapper_marks_active(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """Flag on stores wrapper and sets is_tou_active=True."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        tou_payload=_load(_TOU_BIHORAIRE),
        tou_flag={"value": True},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    result = await coord._async_update_data()

    assert "tou_schedules" in result
    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.tou_active is True


async def test_transient_endpoint_error_preserves_previous_wrapper(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """A transient API error on the schedules endpoint preserves the last wrapper."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        tou_payload=EngieBeApiClientError("boom"),
        tou_flag={"value": True},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    previous_wrapper = {
        "data": {"items": []},
        "fetched_at": "2026-07-07T00:00:00+00:00",
    }
    coord.data = {"tou_schedules": previous_wrapper}

    result = await coord._async_update_data()

    assert result["tou_schedules"] is previous_wrapper


async def test_auth_error_on_schedules_escalates(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """Auth failures on the schedules endpoint escalate to reauth."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        tou_payload=EngieBeApiClientAuthenticationError("nope"),
        tou_flag={"value": True},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_auth_error_on_flag_escalates(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """Auth failures on the flag endpoint escalate to reauth."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        tou_payload=_load(_TOU_BIHORAIRE),
        tou_flag=EngieBeApiClientAuthenticationError("nope"),
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_flag_probe_error_soft_fails_to_enabled(
    hass: HomeAssistant,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """
    Transient flag-endpoint error soft-fails to True (fail-open).

    The per-EAN schedules fetch has its own soft-fail; a transient outage
    on the flag probe alone must not strip TOU entities from customers who
    are legitimately TOU-billed.  Matches solar-surplus discipline.
    """
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        tou_payload=_load(_TOU_BIHORAIRE),
        tou_flag=EngieBeApiClientError("flag endpoint unreachable"),
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    await coord._async_update_data()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.tou_active is True
    client.async_get_tou_schedules.assert_awaited_once()


async def test_flag_flip_true_to_false_schedules_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """True->False TOU flag flip schedules a config-entry reload."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        tou_payload=_load(_TOU_BIHORAIRE),
        tou_flag={"value": False},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    # Seed: TOU was active; this refresh returns False.
    ff = entry.runtime_data.subentry_data[subentry.subentry_id].feature_flags
    ff.tou_active = True

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()
    await hass.async_block_till_done()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.tou_active is False
    assert entry.runtime_data.reload_pending is True
    reload_mock.assert_awaited_once_with(entry.entry_id)


async def test_first_tou_observation_seeds_cache_without_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """First refresh (previous=None) must NOT schedule a reload."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        tou_payload=_load(_TOU_BIHORAIRE),
        tou_flag={"value": True},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.tou_active is True
    assert entry.runtime_data.reload_pending is False
    reload_mock.assert_not_awaited()


async def test_flag_no_flip_does_not_schedule_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    build_engie_entry: Callable,
    build_engie_coordinator: Callable,
    wire_engie_runtime: Callable,
) -> None:
    """Same value on consecutive refreshes -> no reload."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))
    client = _make_client(
        tou_payload=_load(_TOU_BIHORAIRE),
        tou_flag={"value": False},
    )
    coord = build_engie_coordinator(hass, entry, subentry)
    wire_engie_runtime(entry, client, subentry, coord)

    ff = entry.runtime_data.subentry_data[subentry.subentry_id].feature_flags
    ff.tou_active = False

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()

    sub_data = entry.runtime_data.subentry_data[subentry.subentry_id]
    assert sub_data.feature_flags.tou_active is False
    assert entry.runtime_data.reload_pending is False
    reload_mock.assert_not_awaited()
