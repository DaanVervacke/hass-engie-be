"""
Tests for defensive type-guard branches in the per-subentry coordinator.

These branches protect against malformed payloads, missing runtime data,
auth-failure escalation in the peaks-with-fallback path, the relations
backfill no-op-when-match-unchanged path, the device-rename no-op, and
the EPEX parser's per-slot/per-publication-time malformed-row skips.
They are reached by HA at runtime only when ENGIE returns an unexpected
shape, so each test pokes the internal method directly with a crafted
payload rather than driving a full refresh.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.api import (
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CLIENT_ID,
    CONF_CONSUMPTION_ADDRESS,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.coordinator import (
    EngieBeDataUpdateCoordinator,
    _find_history_fallback,
    _parse_epex_response,
)
from custom_components.engie_be.data import EngieBeData, EngieBeSubentryData

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

_FIXTURES = Path(__file__).parent / "fixtures"
_RELATIONS_FIXTURE = _FIXTURES / "customer_account_relations_sample.json"
_FIXTURE_BAN = "002200000001"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_entry(
    hass: HomeAssistant,
    *,
    business_agreement_number: str = "B-0001",
    subentry_data_overrides: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Build a v5 entry with one business-agreement subentry."""
    overrides = subentry_data_overrides or {}
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        title="user@example.com",
        unique_id="user_example_com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "stored-access",
            CONF_REFRESH_TOKEN: "stored-refresh",
        },
        options={"update_interval": 60},
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                title="placeholder",
                unique_id=business_agreement_number,
                data={
                    CONF_BUSINESS_AGREEMENT_NUMBER: business_agreement_number,
                    CONF_PREMISES_NUMBER: f"P-{business_agreement_number}",
                    CONF_CONSUMPTION_ADDRESS: "Test 1, 1000 Brussels",
                    **overrides,
                },
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


def _only_subentry(entry: MockConfigEntry) -> ConfigSubentry:
    """Return the single subentry."""
    return next(iter(entry.subentries.values()))


def _coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> EngieBeDataUpdateCoordinator:
    """Build the per-subentry coordinator under test."""
    return EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=_only_subentry(entry),
    )


def _wire_runtime(
    entry: MockConfigEntry,
    coordinator: EngieBeDataUpdateCoordinator,
    *,
    with_subentry_data: bool = True,
    peaks_store: Any = None,
    happy_hours_store: Any = None,
) -> EngieBeSubentryData | None:
    """Attach a runtime with optional per-subentry data + stores."""
    subentry_data: EngieBeSubentryData | None = None
    subentry_data_map: dict[str, EngieBeSubentryData] = {}
    if with_subentry_data:
        subentry_data = EngieBeSubentryData(coordinator=coordinator)
        if peaks_store is not None:
            subentry_data.peaks_store = peaks_store
        if happy_hours_store is not None:
            subentry_data.happy_hours_store = happy_hours_store
        subentry_data_map[coordinator.subentry.subentry_id] = subentry_data
    entry.runtime_data = EngieBeData(
        client=MagicMock(),
        epex_coordinator=MagicMock(),
        subentry_data=subentry_data_map,
        authenticated=True,
        last_options=dict(entry.options),
    )
    return subentry_data


# ---------------------------------------------------------------------------
# _read_cached_enrollment + _async_apply_enrollment runtime-None guards
# (coordinator.py L251, L322-323)
# ---------------------------------------------------------------------------


async def test_read_cached_enrollment_returns_none_when_no_runtime(
    hass: HomeAssistant,
) -> None:
    """When ``runtime_data`` is missing the cache lookup short-circuits."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    # No runtime_data attribute attached.

    assert coordinator._read_cached_enrollment() is None


async def test_apply_enrollment_no_op_when_runtime_missing(
    hass: HomeAssistant,
) -> None:
    """``_async_apply_enrollment`` returns silently if runtime is absent."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    # No runtime_data: should not raise, should not schedule a reload.

    coordinator._async_apply_enrollment(
        previous_enrolled=False,
        new_enrolled=True,
    )


async def test_apply_enrollment_no_op_when_subentry_data_missing(
    hass: HomeAssistant,
) -> None:
    """``_async_apply_enrollment`` returns silently if subentry slot is absent."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    # Wire runtime but with an empty subentry_data map.
    _wire_runtime(entry, coordinator, with_subentry_data=False)

    coordinator._async_apply_enrollment(
        previous_enrolled=False,
        new_enrolled=True,
    )


# ---------------------------------------------------------------------------
# _async_fetch_happy_hour error branches
# (coordinator.py L387-397)
# ---------------------------------------------------------------------------


async def test_fetch_happy_hour_auth_failure_escalates_to_reauth(
    hass: HomeAssistant,
) -> None:
    """Auth errors must raise ``ConfigEntryAuthFailed``."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    client = MagicMock()
    client.async_get_happy_hour_event = AsyncMock(
        side_effect=EngieBeApiClientAuthenticationError("expired"),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_fetch_happy_hour(
            client,
            "B-0001",
            previous_wrapper={"data": {}},
        )


async def test_fetch_happy_hour_generic_error_returns_previous_wrapper(
    hass: HomeAssistant,
) -> None:
    """Transient errors must preserve the last-known wrapper."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    client = MagicMock()
    client.async_get_happy_hour_event = AsyncMock(
        side_effect=EngieBeApiClientError("boom"),
    )
    previous = {"data": {"tomorrow": {"startTime": "x", "endTime": "y"}}}

    result = await coordinator._async_fetch_happy_hour(
        client,
        "B-0001",
        previous_wrapper=previous,
    )

    assert result is previous


# ---------------------------------------------------------------------------
# _record_peak_history defensive branches
# (coordinator.py L417, L442, L446-454)
# ---------------------------------------------------------------------------


async def test_record_peak_history_skips_when_payload_is_fallback(
    hass: HomeAssistant,
) -> None:
    """Fallback months must not be persisted to history."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    store = MagicMock()
    _wire_runtime(entry, coordinator, peaks_store=store)

    coordinator._record_peak_history({"is_fallback": True})

    store.upsert.assert_not_called()


async def test_record_peak_history_skips_when_store_missing(
    hass: HomeAssistant,
) -> None:
    """Without a peaks store the recorder returns silently."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    _wire_runtime(entry, coordinator)  # no peaks_store

    # Should not raise even though the wrapper is otherwise valid.
    coordinator._record_peak_history(
        {
            "data": {"peakOfTheMonth": {"start": "x", "end": "y"}},
            "year": 2026,
            "month": 5,
            "is_fallback": False,
        },
    )


async def test_record_peak_history_skips_when_payload_not_dict(
    hass: HomeAssistant,
) -> None:
    """Non-dict ``data`` payload must be skipped."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    store = MagicMock()
    _wire_runtime(entry, coordinator, peaks_store=store)

    coordinator._record_peak_history({"data": "not-a-dict", "is_fallback": False})

    store.upsert.assert_not_called()


async def test_record_peak_history_skips_when_monthly_not_dict(
    hass: HomeAssistant,
) -> None:
    """Non-dict ``peakOfTheMonth`` must be skipped."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    store = MagicMock()
    _wire_runtime(entry, coordinator, peaks_store=store)

    coordinator._record_peak_history(
        {"data": {"peakOfTheMonth": None}, "is_fallback": False},
    )

    store.upsert.assert_not_called()


async def test_record_peak_history_skips_when_start_end_not_strings(
    hass: HomeAssistant,
) -> None:
    """Non-string start/end must be skipped."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    store = MagicMock()
    _wire_runtime(entry, coordinator, peaks_store=store)

    coordinator._record_peak_history(
        {
            "data": {"peakOfTheMonth": {"start": 1, "end": 2}},
            "year": 2026,
            "month": 5,
            "is_fallback": False,
        },
    )

    store.upsert.assert_not_called()


async def test_record_peak_history_skips_when_year_month_not_ints(
    hass: HomeAssistant,
) -> None:
    """Non-int year/month must be skipped."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    store = MagicMock()
    _wire_runtime(entry, coordinator, peaks_store=store)

    coordinator._record_peak_history(
        {
            "data": {"peakOfTheMonth": {"start": "a", "end": "b"}},
            "year": "2026",
            "month": "05",
            "is_fallback": False,
        },
    )

    store.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# _record_happy_hour_history defensive branches
# (coordinator.py L474-478, L489-496, L523)
# ---------------------------------------------------------------------------


async def test_record_happy_hour_history_skips_when_payload_not_dict(
    hass: HomeAssistant,
) -> None:
    """Non-dict ``data`` payload must be skipped."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    store = MagicMock()
    _wire_runtime(entry, coordinator, happy_hours_store=store)

    coordinator._record_happy_hour_history({"data": None})

    store.upsert.assert_not_called()


async def test_record_happy_hour_history_skips_when_start_end_not_strings(
    hass: HomeAssistant,
) -> None:
    """Non-string startTime/endTime must be skipped."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    store = MagicMock()
    _wire_runtime(entry, coordinator, happy_hours_store=store)

    coordinator._record_happy_hour_history(
        {"data": {"tomorrow": {"startTime": 1, "endTime": 2}}},
    )

    store.upsert.assert_not_called()


async def test_record_happy_hour_history_logs_when_window_already_present(
    hass: HomeAssistant,
) -> None:
    """An idempotent upsert that returns False must log without raising."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    store = MagicMock()
    store.upsert = MagicMock(return_value=False)  # already present
    _wire_runtime(entry, coordinator, happy_hours_store=store)

    coordinator._record_happy_hour_history(
        {
            "data": {
                "tomorrow": {
                    "startTime": "2026-05-22T10:00:00+02:00",
                    "endTime": "2026-05-22T11:00:00+02:00",
                },
            },
        },
    )

    store.upsert.assert_called_once()


# ---------------------------------------------------------------------------
# _async_try_backfill_subentry no-op when match is unchanged
# (coordinator.py L575)
# ---------------------------------------------------------------------------


@pytest.mark.backfill
async def test_backfill_no_op_when_all_fields_already_match_relations(
    hass: HomeAssistant,
) -> None:
    """
    Backfill must be a no-op when every field already matches the payload.

    When every backfillable field already matches the relations payload, the
    helper must return without re-writing the subentry (L575 ``return`` after
    the dict-equality check).
    """
    relations = json.loads(_RELATIONS_FIXTURE.read_text())
    # Pre-populate the subentry with the exact values from the fixture so the
    # backfill computes ``updated == existing`` and short-circuits.
    entry = _build_entry(
        hass,
        business_agreement_number=_FIXTURE_BAN,
        subentry_data_overrides={
            CONF_PREMISES_NUMBER: "5100000001",
            "account_holder_name": "Test Customer One",
            CONF_CONSUMPTION_ADDRESS: "TESTSTRAAT 1, 1000 BRUSSELS",
        },
    )
    coordinator = _coordinator(hass, entry)
    coordinator._needs_relations_backfill = True
    _wire_runtime(entry, coordinator)
    client = MagicMock()
    client.async_get_customer_account_relations = AsyncMock(return_value=relations)

    update_calls: list[Any] = []
    original_update = hass.config_entries.async_update_subentry

    def _track_update(*args: Any, **kwargs: Any) -> Any:
        update_calls.append((args, kwargs))
        return original_update(*args, **kwargs)

    hass.config_entries.async_update_subentry = _track_update  # type: ignore[method-assign]
    try:
        await coordinator._async_try_backfill_subentry(client)
    finally:
        hass.config_entries.async_update_subentry = original_update  # type: ignore[method-assign]

    assert update_calls == []


# ---------------------------------------------------------------------------
# _async_rename_subentry_device no-op branches
# (coordinator.py L613)
# ---------------------------------------------------------------------------


async def test_rename_subentry_device_no_op_when_name_unchanged(
    hass: HomeAssistant,
) -> None:
    """If the device name already matches, no registry update is issued."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    _wire_runtime(entry, coordinator)

    # Seed a device with the expected name.
    device_reg = dr.async_get(hass)
    device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.subentry.subentry_id)},
        name="Already correct",
    )

    # Should not raise; the name matches so update is skipped.
    coordinator._async_rename_subentry_device("Already correct")


# ---------------------------------------------------------------------------
# _async_fetch_peaks_with_fallback auth-failure escalation
# (coordinator.py L668)
# ---------------------------------------------------------------------------


async def test_fetch_peaks_with_fallback_previous_month_auth_failure(
    hass: HomeAssistant,
) -> None:
    """
    Auth failure on the previous-month fallback call must escalate to reauth.

    Covered by raising auth on the *second* ``async_get_monthly_peaks`` call;
    the first returns an empty payload so the helper falls through to the
    previous-month branch.
    """
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    client = MagicMock()
    client.async_get_monthly_peaks = AsyncMock(
        side_effect=[
            {"peakOfTheMonth": None},  # current month: empty -> fallback
            EngieBeApiClientAuthenticationError("expired"),
        ],
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_fetch_peaks_with_fallback(
            client,
            "B-0001",
            year=2026,
            month=5,
            previous_wrapper=None,
        )


async def test_fetch_peaks_with_fallback_previous_month_wraps_january(
    hass: HomeAssistant,
) -> None:
    """January (month == 1) must fall back to December of the previous year."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    client = MagicMock()
    client.async_get_monthly_peaks = AsyncMock(
        side_effect=[
            {"peakOfTheMonth": None},  # current month (Jan)
            {  # December fallback
                "peakOfTheMonth": {
                    "start": "2025-12-15T10:00:00",
                    "end": "2025-12-15T10:15:00",
                    "peakKW": 4.2,
                },
            },
        ],
    )

    result = await coordinator._async_fetch_peaks_with_fallback(
        client,
        "B-0001",
        year=2026,
        month=1,
        previous_wrapper=None,
    )

    assert result is not None
    assert result["is_fallback"] is True
    assert result["year"] == 2025
    assert result["month"] == 12
    # Confirm December was requested (the wraparound math).
    second_call = client.async_get_monthly_peaks.call_args_list[1]
    assert second_call.args == ("B-0001", 2025, 12)


# ---------------------------------------------------------------------------
# _parse_epex_response per-slot/per-publication-time malformed branches
# (coordinator.py L824-825, L835-836, L842, L846, L850-852)
# ---------------------------------------------------------------------------


def test_parse_epex_ignores_unparseable_publication_time() -> None:
    """An unparseable ``publicationTime`` must be ignored, not raise."""
    payload = _parse_epex_response(
        {
            "publicationTime": "definitely-not-iso",
            "marketDate": "2026-05-22",
            "timeSeries": [],
        },
    )
    assert payload.publication_time is None
    assert payload.market_date == "2026-05-22"


def test_parse_epex_raises_when_time_series_is_not_a_list() -> None:
    """A non-list ``timeSeries`` must raise ``TypeError``."""
    with pytest.raises(TypeError, match="EPEX timeSeries must be a list"):
        _parse_epex_response(
            {
                "publicationTime": "2026-05-22T11:00:00+02:00",
                "marketDate": "2026-05-22",
                "timeSeries": "not-a-list",
            },
        )


def test_parse_epex_skips_non_dict_slot_entries() -> None:
    """Slots that aren't dicts must be silently dropped."""
    payload = _parse_epex_response(
        {
            "marketDate": "2026-05-22",
            "timeSeries": [
                "garbage-string",  # skipped
                42,  # skipped
                {  # kept
                    "period": "2026-05-22T10:00:00+02:00",
                    "value": 50.0,
                },
            ],
        },
    )
    assert len(payload.slots) == 1


def test_parse_epex_skips_slot_with_missing_period_or_value() -> None:
    """Slots missing ``period`` (non-string) or ``value`` (None) are dropped."""
    payload = _parse_epex_response(
        {
            "marketDate": "2026-05-22",
            "timeSeries": [
                {"period": None, "value": 10.0},  # skipped (period not str)
                {"period": "2026-05-22T10:00:00+02:00", "value": None},  # skipped
                {  # kept
                    "period": "2026-05-22T11:00:00+02:00",
                    "value": 25.0,
                },
            ],
        },
    )
    assert len(payload.slots) == 1


def test_parse_epex_skips_slot_with_unparseable_period_or_value() -> None:
    """
    Skip the EPEX slot when its period or value can't be parsed.

    Slots with an unparseable timestamp or non-numeric value must be skipped
    via the ``(TypeError, ValueError)`` except clause.
    """
    payload = _parse_epex_response(
        {
            "marketDate": "2026-05-22",
            "timeSeries": [
                {"period": "not-iso", "value": 10.0},  # ValueError on fromisoformat
                {  # ValueError on float()
                    "period": "2026-05-22T10:00:00+02:00",
                    "value": "not-a-number",
                },
                {  # kept
                    "period": "2026-05-22T11:00:00+02:00",
                    "value": 25.0,
                },
            ],
        },
    )
    assert len(payload.slots) == 1


# ---------------------------------------------------------------------------
# _async_fetch_happy_hour: payload is not a dict (L417 else debug)
# ---------------------------------------------------------------------------


async def test_fetch_happy_hour_logs_when_payload_not_a_dict(
    hass: HomeAssistant,
) -> None:
    """A non-dict payload from the API hits the ``else`` debug branch."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    client = MagicMock()
    client.async_get_happy_hour_event = AsyncMock(return_value=["nope"])

    result = await coordinator._async_fetch_happy_hour(
        client,
        "B-0001",
        previous_wrapper=None,
    )

    assert result == {"data": None}


# ---------------------------------------------------------------------------
# _record_peak_history happy path (L454: store.upsert is actually called)
# ---------------------------------------------------------------------------


async def test_record_peak_history_persists_valid_payload(
    hass: HomeAssistant,
) -> None:
    """A fully-valid payload reaches ``store.upsert`` with all fields."""
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    store = MagicMock()
    _wire_runtime(entry, coordinator, peaks_store=store)

    coordinator._record_peak_history(
        {
            "data": {
                "peakOfTheMonth": {
                    "start": "2026-05-15T10:00:00",
                    "end": "2026-05-15T10:15:00",
                    "peakKW": 4.2,
                    "peakKWh": 1.05,
                },
            },
            "year": 2026,
            "month": 5,
            "is_fallback": False,
        },
    )

    store.upsert.assert_called_once()


# ---------------------------------------------------------------------------
# _async_update_data: previous-wrapper extraction when self.data exists
# (coordinator.py L220-222)
# ---------------------------------------------------------------------------


async def test_update_data_passes_previous_happy_hour_wrapper_when_present(
    hass: HomeAssistant,
) -> None:
    """
    Preserve the previous Happy Hours wrapper on a transient fetch error.

    On a refresh where ``self.data["happy_hour"]`` is already a dict,
    that wrapper must be passed as ``previous_wrapper`` so a transient API
    failure keeps the last-known window.
    """
    entry = _build_entry(hass)
    coordinator = _coordinator(hass, entry)
    _wire_runtime(entry, coordinator)

    seeded_wrapper = {"data": {"tomorrow": {"startTime": "x", "endTime": "y"}}}
    coordinator.data = {"happy_hour": seeded_wrapper, "items": []}
    subentry_data = entry.runtime_data.subentry_data[coordinator.subentry.subentry_id]
    subentry_data.is_happy_hour_enrolled = True

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value={"items": []})
    client.async_get_monthly_peaks = AsyncMock(
        return_value={"peakOfTheMonth": None, "dailyPeaks": []},
    )
    client.async_get_feature_flags = AsyncMock(
        return_value={"happy-hours-service-enabled": {"value": True}},
    )
    client.async_get_happy_hour_event = AsyncMock(
        side_effect=EngieBeApiClientError("transient"),
    )
    client.async_get_month_report = AsyncMock(return_value={})
    entry.runtime_data.client = client

    result = await coordinator._async_update_data()

    assert result["happy_hour"] is seeded_wrapper


# ---------------------------------------------------------------------------
# _find_history_fallback unit tests
# ---------------------------------------------------------------------------


def test_find_history_fallback_returns_none_for_non_list() -> None:
    """Non-list history (None, dict, str) yields None."""
    assert _find_history_fallback(None, "BAN-XXX") is None
    assert _find_history_fallback({"yearMonth": "2026-06"}, "BAN-XXX") is None
    assert _find_history_fallback("2026-06", "BAN-XXX") is None


def test_find_history_fallback_returns_none_for_empty_list() -> None:
    """Empty history list yields None."""
    assert _find_history_fallback([], "BAN-XXX") is None


def test_find_history_fallback_returns_none_when_no_happy_hour_dict() -> None:
    """History entries without a dict happyHour are skipped; None returned."""
    history = [
        {"yearMonth": "2026-05"},
        {"yearMonth": "2026-06", "happyHour": None},
        {"yearMonth": "2026-07", "happyHour": "bad"},
        {"notYearMonth": "ignored"},
        "not-a-dict",
    ]
    assert _find_history_fallback(history, "BAN-XXX") is None


def test_find_history_fallback_picks_most_recent_entry() -> None:
    """The entry with the lexicographically greatest yearMonth is chosen."""
    history = [
        {
            "yearMonth": "2026-05",
            "happyHour": {"rewardEuros": 1.0},
        },
        {
            "yearMonth": "2026-06",
            "happyHour": {"rewardEuros": 6.0},
        },
        {
            "yearMonth": "2026-04",
            "happyHour": {"rewardEuros": 0.5},
        },
    ]
    result = _find_history_fallback(history, "BAN-XXX")
    assert result is not None
    assert result["year"] == 2026
    assert result["month"] == 6
    assert result["is_fallback"] is True
    assert result["data"]["month"]["happyHour"]["rewardEuros"] == 6.0


def test_find_history_fallback_wraps_data_under_month_key() -> None:
    """Returned data has shape {"month": {"happyHour": ...}} for sensor paths."""
    history = [{"yearMonth": "2026-03", "happyHour": {"consumptionKWh": 7.7}}]
    result = _find_history_fallback(history, "BAN-XXX")
    assert result is not None
    assert list(result["data"].keys()) == ["month"]
    assert result["data"]["month"]["happyHour"]["consumptionKWh"] == 7.7


def test_find_history_fallback_skips_malformed_year_month() -> None:
    """An entry with an unparseable yearMonth string is skipped gracefully."""
    history = [
        {"yearMonth": "not-a-date", "happyHour": {"rewardEuros": 5.0}},
        {"yearMonth": "2026-06", "happyHour": {"rewardEuros": 6.0}},
    ]
    result = _find_history_fallback(history, "BAN-XXX")
    assert result is not None
    assert result["year"] == 2026
    assert result["month"] == 6


def test_find_history_fallback_returns_none_for_only_malformed_year_month() -> None:
    """When only malformed yearMonth entries exist with happyHour, return None."""
    history = [{"yearMonth": "bad", "happyHour": {"rewardEuros": 5.0}}]
    result = _find_history_fallback(history, "BAN-XXX")
    assert result is None
