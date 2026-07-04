"""
Tests for coordinator-driven Happy Hours enrolment detection.

Covers the feature-flags probe, the per-subentry enrolment cache, the
debounced reload-on-flip behaviour, and the soft-fail / auth-failure
edges. The Happy Hours event endpoint must only be polled when the BAN
is enrolled, and a stale ``data["happy_hour"]`` wrapper must be
dropped when enrolment flips off.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.api import (
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CONSUMPTION_ADDRESS,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.coordinator import EngieBeDataUpdateCoordinator
from custom_components.engie_be.data import EngieBeData, EngieBeSubentryData

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

_FIXTURES = Path(__file__).parent / "fixtures"
_PRICES_FIXTURE = _FIXTURES / "prices_sample.json"
_PEAKS_FIXTURE = _FIXTURES / "peaks_2026_04.json"
_FLAGS_ENROLLED = _FIXTURES / "feature_flags_enrolled.json"
_FLAGS_NOT_ENROLLED = _FIXTURES / "feature_flags_not_enrolled.json"


def _load(path: Path) -> dict:
    """Return a fresh copy of a JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def _build_entry(
    hass: HomeAssistant,
    *,
    business_agreement_numbers: list[str] | None = None,
) -> MockConfigEntry:
    """Build a v5 MockConfigEntry with one or more business-agreement subentries."""
    bans = business_agreement_numbers or ["B-0001"]
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
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                title=f"placeholder-{ban}",
                unique_id=ban,
                data={
                    CONF_BUSINESS_AGREEMENT_NUMBER: ban,
                    CONF_PREMISES_NUMBER: f"P-{ban}",
                    CONF_CONSUMPTION_ADDRESS: "Test 1, 1000 Brussels",
                },
            )
            for ban in bans
        ],
    )
    entry.add_to_hass(hass)
    return entry


def _subentries(entry: MockConfigEntry) -> list[ConfigSubentry]:
    """Return all business-agreement subentries in registration order."""
    return list(entry.subentries.values())


def _coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    subentry: ConfigSubentry,
) -> EngieBeDataUpdateCoordinator:
    """Build the per-subentry coordinator under test."""
    return EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=subentry,
    )


def _wire_runtime(
    entry: MockConfigEntry,
    client: MagicMock,
    coordinators: dict[str, EngieBeDataUpdateCoordinator],
) -> None:
    """
    Attach an ``EngieBeData`` runtime with one ``EngieBeSubentryData`` per coordinator.

    The enrolment cache lives on ``EngieBeSubentryData.is_happy_hour_enrolled``,
    so the runtime must hold a real dataclass instance per subentry (not a
    bare ``MagicMock``) for the read/write paths in
    ``_read_cached_enrollment`` and ``_async_apply_enrollment`` to behave
    like production.
    """
    subentry_data = {
        subentry_id: EngieBeSubentryData(coordinator=coord)
        for subentry_id, coord in coordinators.items()
    }
    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=MagicMock(),
        subentry_data=subentry_data,
        authenticated=True,
        last_options=dict(entry.options),
    )


def _make_client(
    *,
    flags: dict | Exception,
    happy_hour_payload: dict | Exception | None = None,
) -> MagicMock:
    """
    Build a mock API client primed for the coordinator refresh path.

    Prices/peaks are stubbed with the canonical fixtures so the refresh
    completes; only ``async_get_feature_flags`` and
    ``async_get_happy_hour_event`` carry per-test semantics.
    """
    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=_load(_PRICES_FIXTURE))
    client.async_get_monthly_peaks = AsyncMock(return_value=_load(_PEAKS_FIXTURE))

    if isinstance(flags, Exception):
        client.async_get_feature_flags = AsyncMock(side_effect=flags)
    else:
        client.async_get_feature_flags = AsyncMock(return_value=flags)

    if isinstance(happy_hour_payload, Exception):
        client.async_get_happy_hour_event = AsyncMock(side_effect=happy_hour_payload)
    else:
        client.async_get_happy_hour_event = AsyncMock(
            return_value=happy_hour_payload if happy_hour_payload is not None else {},
        )
    client.async_get_month_report = AsyncMock(return_value={})
    return client


# ---------------------------------------------------------------------------
# Enrolment cache + skip behaviour
# ---------------------------------------------------------------------------


async def test_un_enrolled_ban_skips_happy_hour_event_fetch(
    hass: HomeAssistant,
) -> None:
    """A False enrolment must skip the Happy Hours event endpoint entirely."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(flags=_load(_FLAGS_NOT_ENROLLED))
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    result = await coord._async_update_data()

    assert "happy_hour" not in result
    client.async_get_happy_hour_event.assert_not_awaited()
    assert (
        entry.runtime_data.subentry_data[subentry.subentry_id].is_happy_hour_enrolled
        is False
    )


async def test_enrolled_ban_polls_happy_hour_event(hass: HomeAssistant) -> None:
    """A True enrolment must poll the Happy Hours event endpoint."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(
        flags=_load(_FLAGS_ENROLLED),
        happy_hour_payload={"tomorrow": {"startTime": "x", "endTime": "y"}},
    )
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    result = await coord._async_update_data()

    assert result["happy_hour"]["data"] == {
        "tomorrow": {"startTime": "x", "endTime": "y"},
    }
    client.async_get_happy_hour_event.assert_awaited_once_with("B-0001")
    assert (
        entry.runtime_data.subentry_data[subentry.subentry_id].is_happy_hour_enrolled
        is True
    )


# ---------------------------------------------------------------------------
# First-observation: set cache, no reload
# ---------------------------------------------------------------------------


async def test_first_refresh_sets_cache_without_scheduling_reload(
    hass: HomeAssistant,
) -> None:
    """The very first observation must set the cache but not schedule a reload."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(flags=_load(_FLAGS_ENROLLED))
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    # Pre-state: no cached enrolment.
    assert (
        entry.runtime_data.subentry_data[subentry.subentry_id].is_happy_hour_enrolled
        is None
    )
    assert entry.runtime_data.reload_pending is False

    await coord._async_update_data()

    assert (
        entry.runtime_data.subentry_data[subentry.subentry_id].is_happy_hour_enrolled
        is True
    )
    # First observation must NOT mark a reload as pending.
    assert entry.runtime_data.reload_pending is False


# ---------------------------------------------------------------------------
# Flip-triggered reload
# ---------------------------------------------------------------------------


async def test_enrolment_flip_schedules_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A True -> False flip must mark reload_pending and call async_reload."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(flags=_load(_FLAGS_NOT_ENROLLED))
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    # Seed the cache with a previous "enrolled" observation.
    entry.runtime_data.subentry_data[subentry.subentry_id].is_happy_hour_enrolled = True

    reload_mock = AsyncMock()
    monkeypatch.setattr(
        hass.config_entries,
        "async_reload",
        reload_mock,
    )

    await coord._async_update_data()

    assert entry.runtime_data.reload_pending is True
    assert (
        entry.runtime_data.subentry_data[subentry.subentry_id].is_happy_hour_enrolled
        is False
    )
    # The reload is dispatched via ``hass.async_create_task``; await pending
    # tasks so the AsyncMock observes the call.
    await hass.async_block_till_done()
    reload_mock.assert_awaited_once_with(entry.entry_id)


async def test_multi_subentry_flip_debounces_to_single_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Two subentries flipping in the same tick must schedule exactly one reload.

    The debounce lives on ``EngieBeData.reload_pending``: the first
    subentry sets the flag and schedules; the second sees the flag set
    and short-circuits.
    """
    entry = _build_entry(hass, business_agreement_numbers=["B-0001", "B-0002"])
    sub_a, sub_b = _subentries(entry)
    client = _make_client(flags=_load(_FLAGS_ENROLLED))
    coord_a = _coordinator(hass, entry, sub_a)
    coord_b = _coordinator(hass, entry, sub_b)
    _wire_runtime(
        entry,
        client,
        {sub_a.subentry_id: coord_a, sub_b.subentry_id: coord_b},
    )

    # Seed both with the opposite-of-now state to force a flip on both.
    entry.runtime_data.subentry_data[sub_a.subentry_id].is_happy_hour_enrolled = False
    entry.runtime_data.subentry_data[sub_b.subentry_id].is_happy_hour_enrolled = False

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord_a._async_update_data()
    await coord_b._async_update_data()
    await hass.async_block_till_done()

    reload_mock.assert_awaited_once_with(entry.entry_id)
    assert entry.runtime_data.reload_pending is True


async def test_no_flip_does_not_schedule_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Steady-state (cached == new) must never schedule a reload."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(flags=_load(_FLAGS_ENROLLED))
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    # Seed with the SAME state we are about to observe.
    entry.runtime_data.subentry_data[subentry.subentry_id].is_happy_hour_enrolled = True

    reload_mock = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    await coord._async_update_data()
    await hass.async_block_till_done()

    reload_mock.assert_not_awaited()
    assert entry.runtime_data.reload_pending is False


# ---------------------------------------------------------------------------
# Soft-fail and auth-failure semantics
# ---------------------------------------------------------------------------


async def test_feature_flags_generic_error_soft_fails_to_cached(
    hass: HomeAssistant,
) -> None:
    """A non-auth API error must keep the previous enrolment value."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(flags=EngieBeApiClientError("upstream 500"))
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    # Pre-seed an enrolled state; transient error must preserve it.
    entry.runtime_data.subentry_data[subentry.subentry_id].is_happy_hour_enrolled = True

    await coord._async_update_data()

    assert (
        entry.runtime_data.subentry_data[subentry.subentry_id].is_happy_hour_enrolled
        is True
    )
    # Happy Hours endpoint MUST still be polled because the cached value
    # remains True.
    client.async_get_happy_hour_event.assert_awaited_once()


async def test_feature_flags_generic_error_with_no_cache_defaults_false(
    hass: HomeAssistant,
) -> None:
    """A non-auth error on the very first refresh must default to un-enrolled."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(flags=EngieBeApiClientError("upstream 500"))
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    await coord._async_update_data()

    assert (
        entry.runtime_data.subentry_data[subentry.subentry_id].is_happy_hour_enrolled
        is False
    )
    client.async_get_happy_hour_event.assert_not_awaited()


async def test_feature_flags_auth_error_raises_config_entry_auth_failed(
    hass: HomeAssistant,
) -> None:
    """Auth failures from the feature-flags endpoint must surface reauth."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    original = EngieBeApiClientAuthenticationError("token rejected")
    client = _make_client(flags=original)
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    with pytest.raises(ConfigEntryAuthFailed) as exc_info:
        await coord._async_update_data()

    assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# History persistence (_record_happy_hour_history)
# ---------------------------------------------------------------------------


async def test_enrolled_refresh_upserts_happy_hour_into_store(
    hass: HomeAssistant,
) -> None:
    """A populated ``tomorrow`` window is persisted to the per-subentry store."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(
        flags=_load(_FLAGS_ENROLLED),
        happy_hour_payload={
            "tomorrow": {
                "startTime": "2026-05-23T12:00:00+02:00",
                "endTime": "2026-05-23T15:00:00+02:00",
            },
        },
    )
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    store = MagicMock()
    store.upsert = MagicMock(return_value=True)
    entry.runtime_data.subentry_data[subentry.subentry_id].happy_hours_store = store

    await coord._async_update_data()

    store.upsert.assert_called_once_with(
        start="2026-05-23T12:00:00+02:00",
        end="2026-05-23T15:00:00+02:00",
    )


async def test_enrolled_refresh_with_empty_payload_does_not_upsert(
    hass: HomeAssistant,
) -> None:
    """An empty ``{}`` Happy Hours payload never reaches the store."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(
        flags=_load(_FLAGS_ENROLLED),
        happy_hour_payload={},
    )
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    store = MagicMock()
    store.upsert = MagicMock(return_value=False)
    entry.runtime_data.subentry_data[subentry.subentry_id].happy_hours_store = store

    await coord._async_update_data()

    store.upsert.assert_not_called()


async def test_history_record_tolerates_missing_store(
    hass: HomeAssistant,
) -> None:
    """A subentry without a happy_hours_store falls back to a no-op record."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(
        flags=_load(_FLAGS_ENROLLED),
        happy_hour_payload={
            "tomorrow": {
                "startTime": "2026-05-23T12:00:00+02:00",
                "endTime": "2026-05-23T15:00:00+02:00",
            },
        },
    )
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    # Explicit None mirrors a subentry whose store init failed.
    entry.runtime_data.subentry_data[subentry.subentry_id].happy_hours_store = None

    # Must not raise.
    result = await coord._async_update_data()
    assert "happy_hour" in result


async def test_enrolled_refresh_upserts_today_key_window(
    hass: HomeAssistant,
) -> None:
    """A post-midnight ``today``-only window is persisted (regression)."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(
        flags=_load(_FLAGS_ENROLLED),
        happy_hour_payload={
            "today": {
                "startTime": "2026-05-23T12:00:00+02:00",
                "endTime": "2026-05-23T15:00:00+02:00",
            },
        },
    )
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    store = MagicMock()
    store.upsert = MagicMock(return_value=True)
    entry.runtime_data.subentry_data[subentry.subentry_id].happy_hours_store = store

    await coord._async_update_data()

    store.upsert.assert_called_once_with(
        start="2026-05-23T12:00:00+02:00",
        end="2026-05-23T15:00:00+02:00",
    )


async def test_enrolled_refresh_upserts_both_today_and_tomorrow_windows(
    hass: HomeAssistant,
) -> None:
    """Both keys in one payload are each persisted (order-independent)."""
    entry = _build_entry(hass)
    subentry = _subentries(entry)[0]
    client = _make_client(
        flags=_load(_FLAGS_ENROLLED),
        happy_hour_payload={
            "today": {
                "startTime": "2026-05-23T12:00:00+02:00",
                "endTime": "2026-05-23T15:00:00+02:00",
            },
            "tomorrow": {
                "startTime": "2026-05-24T11:00:00+02:00",
                "endTime": "2026-05-24T14:00:00+02:00",
            },
        },
    )
    coord = _coordinator(hass, entry, subentry)
    _wire_runtime(entry, client, {subentry.subentry_id: coord})

    store = MagicMock()
    store.upsert = MagicMock(return_value=True)
    entry.runtime_data.subentry_data[subentry.subentry_id].happy_hours_store = store

    await coord._async_update_data()

    assert store.upsert.call_count == 2
    store.upsert.assert_any_call(
        start="2026-05-23T12:00:00+02:00",
        end="2026-05-23T15:00:00+02:00",
    )
    store.upsert.assert_any_call(
        start="2026-05-24T11:00:00+02:00",
        end="2026-05-24T14:00:00+02:00",
    )
