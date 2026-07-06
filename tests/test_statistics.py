"""Tests for the pure usage-items -> StatisticData converter and orchestrator."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.engie_be._statistics import (
    STREAM_CONSUMPTION,
    STREAM_GAS,
    STREAM_INJECTION,
    async_clear_usage_history,
    async_import_usage_history,
    earliest_contract_start_date,
    statistic_id,
    streams_for_fuels,
    usage_items_to_statistics,
)
from custom_components.engie_be.api import EngieBeApiClientCommunicationError
from custom_components.engie_be.button import (
    _CONSUMPTION_DESCRIPTION,
    _CONSUMPTION_STREAMS,
    _GAS_DESCRIPTION,
    _GAS_STREAMS,
    _INJECTION_DESCRIPTION,
    _INJECTION_STREAMS,
    EngieBeImportHistoryButton,
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "usage_details_hourly.json"


def _load_items() -> list[dict]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))["items"]


def test_statistic_id_format() -> None:
    """External statistic IDs are ``engie_be:{ban}_{stream}`` with dashes normalised."""
    assert (
        statistic_id("000000000000", STREAM_CONSUMPTION)
        == "engie_be:000000000000_consumption"
    )
    assert statistic_id("00 22 09", STREAM_GAS) == "engie_be:002209_gas"
    assert statistic_id("abc-def", STREAM_INJECTION) == "engie_be:abc_def_injection"


def test_converter_produces_row_per_hour_per_stream() -> None:
    """Every non-partial input row yields exactly one row per stream."""
    items = _load_items()
    per_stream = usage_items_to_statistics(
        items, initial_sums={}, last_stats_time_utc=None
    )

    assert set(per_stream) == {STREAM_CONSUMPTION, STREAM_INJECTION, STREAM_GAS}
    for stream in per_stream:
        assert len(per_stream[stream]) == len(items)


def test_converter_totals_match_engie_totals() -> None:
    """Final running sum for each stream equals the ENGIE-reported total."""
    items = _load_items()
    per_stream = usage_items_to_statistics(
        items, initial_sums={}, last_stats_time_utc=None
    )

    consumption_total = per_stream[STREAM_CONSUMPTION][-1]["sum"]
    injection_total = per_stream[STREAM_INJECTION][-1]["sum"]
    gas_total = per_stream[STREAM_GAS][-1]["sum"]

    # Values pulled from the ``total`` block in the fixture.
    assert consumption_total == pytest.approx(0.003)
    assert injection_total == pytest.approx(2.986)
    assert gas_total == pytest.approx(1.776)


def test_converter_running_sum_is_monotonic() -> None:
    """Cumulative ``sum`` never decreases (all streams are non-negative)."""
    items = _load_items()
    per_stream = usage_items_to_statistics(
        items, initial_sums={}, last_stats_time_utc=None
    )

    for rows in per_stream.values():
        sums = [row["sum"] for row in rows]
        assert sums == sorted(sums)


def test_converter_normalises_timestamps_to_utc() -> None:
    """Brussels-local ``+02:00`` starts are converted to UTC before storage."""
    items = _load_items()
    per_stream = usage_items_to_statistics(
        items, initial_sums={}, last_stats_time_utc=None
    )

    first_utc = per_stream[STREAM_CONSUMPTION][0]["start"]
    # 2026-07-03T00:00:00+02:00 == 2026-07-02T22:00:00 UTC
    assert first_utc == datetime(2026, 7, 2, 22, 0, tzinfo=UTC)


def test_converter_seeds_from_initial_sums() -> None:
    """Running sums start from ``initial_sums`` (continuation across imports)."""
    items = _load_items()
    per_stream = usage_items_to_statistics(
        items,
        initial_sums={
            STREAM_CONSUMPTION: 100.0,
            STREAM_INJECTION: 200.0,
            STREAM_GAS: 50.0,
        },
        last_stats_time_utc=None,
    )

    assert per_stream[STREAM_CONSUMPTION][-1]["sum"] == pytest.approx(100.003)
    assert per_stream[STREAM_INJECTION][-1]["sum"] == pytest.approx(202.986)
    assert per_stream[STREAM_GAS][-1]["sum"] == pytest.approx(51.776)


def test_converter_drops_partial_data_rows() -> None:
    """A ``partialData: true`` row is skipped so in-progress hours never persist."""
    items = _load_items()
    poisoned = [
        *items,
        {
            "start": "2026-07-03T23:00:00+02:00",
            "end": "2026-07-04T00:00:00+02:00",
            "partialData": True,
            "energy": {
                "electricity": {
                    "offtake": {"kWhSum": 999},
                    "injection": {"kWhSum": 999},
                },
                "gas": {"kWh": 999},
            },
        },
    ]
    per_stream = usage_items_to_statistics(
        poisoned, initial_sums={}, last_stats_time_utc=None
    )

    # Same row count as the clean fixture; the poisoned row was dropped.
    assert len(per_stream[STREAM_CONSUMPTION]) == len(items)
    assert per_stream[STREAM_CONSUMPTION][-1]["sum"] == pytest.approx(0.003)


def test_converter_skips_rows_at_or_before_last_stats_time() -> None:
    """Rows whose ``start`` <= last recorded timestamp are dropped."""
    items = _load_items()
    # Second row in the fixture starts at 2026-07-03T01:00:00+02:00 == 23:00 UTC.
    cutoff = datetime(2026, 7, 2, 23, 0, tzinfo=UTC)
    per_stream = usage_items_to_statistics(
        items, initial_sums={}, last_stats_time_utc=cutoff
    )

    # First two rows are at or before the cutoff; only the last two remain.
    assert len(per_stream[STREAM_CONSUMPTION]) == len(items) - 2


def test_converter_tolerates_malformed_rows() -> None:
    """Missing/mistyped fields degrade to a zero contribution, not a crash."""
    items = [
        {"start": "not-a-date", "partialData": False, "energy": {}},
        {"partialData": False, "energy": {}},  # no start
        {
            "start": "2026-07-03T05:00:00+02:00",
            "partialData": False,
            "energy": {"electricity": {"offtake": {"kWhSum": "oops"}}},
        },
    ]
    per_stream = usage_items_to_statistics(
        items, initial_sums={}, last_stats_time_utc=None
    )

    # Only the third row survives; its bad kWhSum is coerced to 0.
    assert len(per_stream[STREAM_CONSUMPTION]) == 1
    assert per_stream[STREAM_CONSUMPTION][0]["sum"] == 0.0


def _mock_subentry(ban: str = "000000000000") -> MagicMock:
    subentry = MagicMock()
    subentry.data = {"business_agreement_number": ban}
    return subentry


async def test_orchestrator_first_import_writes_three_streams(hass) -> None:  # noqa: ANN001
    """First-time import: three streams written, chunked across ~3y window."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = MagicMock()
    client.async_get_usage_details = AsyncMock(return_value=payload)
    # No contracts returned -> orchestrator falls back to HISTORY_BACKFILL_YEARS.
    client.async_get_energy_contracts = AsyncMock(return_value={"items": []})

    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(return_value={})
    with (
        patch(
            "custom_components.engie_be._statistics.get_instance",
            return_value=recorder,
        ),
        patch(
            "custom_components.engie_be._statistics.async_add_external_statistics",
        ) as mocked_add,
    ):
        count = await async_import_usage_history(hass, client, _mock_subentry())

    # Three streams * N rows each (N = fixture rows * chunks walked back over 3y).
    assert count > 0
    assert count % 3 == 0
    # Per-chunk persistence: one add_external_statistics call per stream
    # per chunk, so total calls == 3 * number_of_chunks.
    n_chunks = client.async_get_usage_details.await_count
    assert n_chunks > 1
    assert mocked_add.call_count == 3 * n_chunks
    written_ids = {
        call.args[1].get("statistic_id") for call in mocked_add.call_args_list
    }
    assert written_ids == {
        "engie_be:000000000000_consumption",
        "engie_be:000000000000_injection",
        "engie_be:000000000000_gas",
    }


async def test_orchestrator_incremental_seeds_from_last_stats(hass) -> None:  # noqa: ANN001
    """Subsequent import: running sums continue from the last recorded sum."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = MagicMock()
    client.async_get_usage_details = AsyncMock(return_value=payload)

    # Last stats say we've imported through 2026-07-02 22:00 UTC (== the
    # first row's start) with a running consumption sum of 100.0.
    last_stats_ts = datetime(2026, 7, 2, 22, 0, tzinfo=UTC).timestamp()
    fake_last = {
        "engie_be:000000000000_consumption": [{"start": last_stats_ts, "sum": 100.0}],
        "engie_be:000000000000_injection": [{"start": last_stats_ts, "sum": 200.0}],
        "engie_be:000000000000_gas": [{"start": last_stats_ts, "sum": 50.0}],
    }

    recorder = MagicMock()

    async def _fake_executor(_fn, _hass, _n, sid, _c, _t):  # noqa: ANN001, ANN202
        return {sid: fake_last[sid]}

    recorder.async_add_executor_job = _fake_executor
    with (
        patch(
            "custom_components.engie_be._statistics.get_instance",
            return_value=recorder,
        ),
        patch(
            "custom_components.engie_be._statistics.async_add_external_statistics",
        ) as mocked_add,
    ):
        count = await async_import_usage_history(hass, client, _mock_subentry())

    # First fixture row is at the cutoff (<=), so it's dropped. 3 remain per stream.
    assert count == 9
    # Running sums continue from the seeded values. Per-chunk persistence
    # writes one call per stream per chunk; only one chunk is fetched in
    # the incremental case (window is small). Read the last consumption
    # write and check its tail sum.
    consumption_calls = [
        call
        for call in mocked_add.call_args_list
        if call.args[1].get("statistic_id") == "engie_be:000000000000_consumption"
    ]
    written_rows = consumption_calls[-1].args[2]
    assert written_rows[-1]["sum"] == pytest.approx(100.002)


async def test_orchestrator_explicit_window_bypasses_cutoff(hass) -> None:  # noqa: ANN001
    """Explicit start/end dates: re-import overlapping hours, no cutoff drop."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = MagicMock()
    client.async_get_usage_details = AsyncMock(return_value=payload)

    # last_stats says we're already through 2027-01-01; explicit window is
    # in 2026. Without the cutoff bypass, everything would be dropped.
    later_ts = datetime(2027, 1, 1, 0, 0, tzinfo=UTC).timestamp()
    fake_last = {
        "engie_be:000000000000_consumption": [{"start": later_ts, "sum": 5.0}],
        "engie_be:000000000000_injection": [{"start": later_ts, "sum": 6.0}],
        "engie_be:000000000000_gas": [{"start": later_ts, "sum": 7.0}],
    }
    recorder = MagicMock()

    async def _fake_executor(_fn, _hass, _n, sid, _c, _t):  # noqa: ANN001, ANN202
        return {sid: fake_last[sid]}

    recorder.async_add_executor_job = _fake_executor
    with (
        patch(
            "custom_components.engie_be._statistics.get_instance",
            return_value=recorder,
        ),
        patch(
            "custom_components.engie_be._statistics.async_add_external_statistics",
        ) as mocked_add,
    ):
        count = await async_import_usage_history(
            hass,
            client,
            _mock_subentry(),
            start_date=date(2026, 7, 3),
            end_date=date(2026, 7, 4),
        )

    # Single 1-day chunk == one API call, four rows written per stream.
    assert client.async_get_usage_details.await_count == 1
    call_kwargs = client.async_get_usage_details.await_args.kwargs
    assert call_kwargs["start_date"] == date(2026, 7, 3)
    assert call_kwargs["end_date"] == date(2026, 7, 4)
    assert count == 12
    # Per-chunk persistence: 1 chunk * 3 streams == 3 writes.
    assert mocked_add.call_count == 3


def test_streams_for_fuels_maps_user_selectors() -> None:
    """Public helper maps user-facing fuels to internal stream keys."""
    assert streams_for_fuels(None) == frozenset(
        {STREAM_CONSUMPTION, STREAM_INJECTION, STREAM_GAS}
    )
    assert streams_for_fuels([]) == frozenset(
        {STREAM_CONSUMPTION, STREAM_INJECTION, STREAM_GAS}
    )
    assert streams_for_fuels(["consumption"]) == frozenset({STREAM_CONSUMPTION})
    assert streams_for_fuels(["injection"]) == frozenset({STREAM_INJECTION})
    assert streams_for_fuels(["gas"]) == frozenset({STREAM_GAS})
    assert streams_for_fuels(["consumption", "gas"]) == frozenset(
        {STREAM_CONSUMPTION, STREAM_GAS}
    )
    # Unknown fuel silently degrades to "all" so old service payloads
    # never explode after a future selector rename.
    assert streams_for_fuels(["district_heating"]) == frozenset(
        {STREAM_CONSUMPTION, STREAM_INJECTION, STREAM_GAS}
    )


def test_earliest_contract_start_date_picks_min_active_electricity() -> None:
    """Earliest legalContractStartDate wins for electricity streams."""
    payload = {
        "items": [
            {
                "status": "ACTIVE",
                "division": "ELECTRICITY",
                "legalContractStartDate": "2025-11-10",
            },
            {
                "status": "ACTIVE",
                "division": "ELECTRICITY",
                "legalContractStartDate": "2024-03-01",
            },
            # Gas contract must be ignored when filtering electricity only.
            {
                "status": "ACTIVE",
                "division": "GAS",
                "legalContractStartDate": "2021-01-01",
            },
            # Inactive contract must be ignored regardless of date.
            {
                "status": "TERMINATED",
                "division": "ELECTRICITY",
                "legalContractStartDate": "2019-01-01",
            },
        ]
    }
    got = earliest_contract_start_date(
        payload, frozenset({STREAM_CONSUMPTION, STREAM_INJECTION})
    )
    assert got == date(2024, 3, 1)


def test_earliest_contract_start_date_falls_back_to_startdate() -> None:
    """When legalContractStartDate is absent, startDate is used."""
    payload = {
        "items": [
            {
                "status": "ACTIVE",
                "division": "GAS",
                "startDate": "2022-05-01",
            }
        ]
    }
    got = earliest_contract_start_date(payload, frozenset({STREAM_GAS}))
    assert got == date(2022, 5, 1)


def test_earliest_contract_start_date_returns_none_when_no_match() -> None:
    """No matching active contracts -> None (caller falls back)."""
    assert earliest_contract_start_date(None, frozenset({STREAM_GAS})) is None
    assert earliest_contract_start_date({}, frozenset({STREAM_GAS})) is None
    assert (
        earliest_contract_start_date(
            {"items": [{"status": "ACTIVE", "division": "GAS"}]},
            frozenset({STREAM_GAS}),
        )
        is None
    )


async def test_orchestrator_uses_contract_start_when_no_prior_stats(hass) -> None:  # noqa: ANN001
    """First import uses the contract start date instead of the fixed default."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = MagicMock()
    client.async_get_usage_details = AsyncMock(return_value=payload)
    # Contract started 30 days ago -> orchestrator should walk from there,
    # not from 3 years back. That means dramatically fewer chunks.

    thirty_days_ago = (dt_util.now() - timedelta(days=30)).date().isoformat()
    client.async_get_energy_contracts = AsyncMock(
        return_value={
            "items": [
                {
                    "status": "ACTIVE",
                    "division": "GAS",
                    "legalContractStartDate": thirty_days_ago,
                }
            ]
        }
    )
    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(return_value={})

    with (
        patch(
            "custom_components.engie_be._statistics.get_instance",
            return_value=recorder,
        ),
        patch(
            "custom_components.engie_be._statistics.async_add_external_statistics",
        ),
    ):
        await async_import_usage_history(
            hass, client, _mock_subentry(), streams=frozenset({STREAM_GAS})
        )

    # 30-day window / 7-day chunks -> at most 5 requests. 3-year fallback
    # would have been ~156. Assert we stayed short.
    assert client.async_get_usage_details.await_count <= 6
    # Contracts endpoint was consulted exactly once.
    client.async_get_energy_contracts.assert_awaited_once()


async def test_orchestrator_persists_chunks_before_later_failure(hass) -> None:  # noqa: ANN001
    """A chunk failure preserves everything written by earlier chunks."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = MagicMock()
    # First two chunks succeed, third raises. Persistence-per-chunk means
    # the two successful chunks' rows must still be handed to
    # ``async_add_external_statistics`` before the exception propagates.
    client.async_get_usage_details = AsyncMock(
        side_effect=[
            payload,
            payload,
            EngieBeApiClientCommunicationError("timeout"),
        ]
    )
    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(return_value={})

    with (
        patch(
            "custom_components.engie_be._statistics.get_instance",
            return_value=recorder,
        ),
        patch(
            "custom_components.engie_be._statistics.async_add_external_statistics",
        ) as mocked_add,
        pytest.raises(EngieBeApiClientCommunicationError),
    ):
        await async_import_usage_history(
            hass,
            client,
            _mock_subentry(),
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 22),  # spans three 7-day chunks
        )

    # Two successful chunks * three streams == 6 statistics writes.
    assert mocked_add.call_count == 6


async def test_orchestrator_streams_filter_writes_only_selected(hass) -> None:  # noqa: ANN001
    """When ``streams`` is passed, only those streams are written."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = MagicMock()
    client.async_get_usage_details = AsyncMock(return_value=payload)

    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(return_value={})
    with (
        patch(
            "custom_components.engie_be._statistics.get_instance",
            return_value=recorder,
        ),
        patch(
            "custom_components.engie_be._statistics.async_add_external_statistics",
        ) as mocked_add,
    ):
        await async_import_usage_history(
            hass,
            client,
            _mock_subentry(),
            start_date=date(2026, 7, 3),
            end_date=date(2026, 7, 4),  # one-day window, one chunk
            streams=frozenset({STREAM_GAS}),
        )

    # One chunk * gas-only filter == exactly one write, to the gas stream.
    assert mocked_add.call_count == 1
    metadata = mocked_add.call_args_list[0].args[1]
    assert metadata.get("statistic_id") == "engie_be:000000000000_gas"


async def test_clear_usage_history_streams_filter(hass) -> None:  # noqa: ANN001
    """Clear helper only queues the requested streams."""
    recorder = MagicMock()

    with patch(
        "custom_components.engie_be._statistics.get_instance",
        return_value=recorder,
    ):
        cleared = await async_clear_usage_history(
            hass,
            "000000000000",
            streams=frozenset({STREAM_CONSUMPTION, STREAM_INJECTION}),
        )

    assert cleared == [
        "engie_be:000000000000_consumption",
        "engie_be:000000000000_injection",
    ]
    task = recorder.queue_task.call_args.args[0]
    assert task.statistic_ids == cleared


async def test_consumption_button_delegates_with_consumption_stream(hass) -> None:  # noqa: ANN001
    """The consumption button passes only the consumption stream filter."""
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.runtime_data.client = MagicMock()
    coordinator = MagicMock()
    subentry = _mock_subentry()
    subentry.subentry_id = "s1"
    subentry.title = "ENGIE 000000000000"

    button = EngieBeImportHistoryButton(
        coordinator,
        subentry,
        entry,
        description=_CONSUMPTION_DESCRIPTION,
        streams=_CONSUMPTION_STREAMS,
    )
    button.hass = hass

    with patch(
        "custom_components.engie_be.button.async_import_usage_history",
        AsyncMock(return_value=42),
    ) as mocked:
        await button.async_press()

    mocked.assert_awaited_once_with(
        hass,
        entry.runtime_data.client,
        subentry,
        streams=_CONSUMPTION_STREAMS,
    )
    assert button.unique_id == "e1_s1_import_consumption_history"
    assert (
        button.entity_id
        == "button.engie_belgium_000000000000_import_consumption_history"
    )


async def test_injection_button_delegates_with_injection_stream(hass) -> None:  # noqa: ANN001
    """The injection button passes only the injection stream filter."""
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.runtime_data.client = MagicMock()
    coordinator = MagicMock()
    subentry = _mock_subentry()
    subentry.subentry_id = "s1"
    subentry.title = "ENGIE 000000000000"

    button = EngieBeImportHistoryButton(
        coordinator,
        subentry,
        entry,
        description=_INJECTION_DESCRIPTION,
        streams=_INJECTION_STREAMS,
    )
    button.hass = hass

    with patch(
        "custom_components.engie_be.button.async_import_usage_history",
        AsyncMock(return_value=7),
    ) as mocked:
        await button.async_press()

    mocked.assert_awaited_once_with(
        hass,
        entry.runtime_data.client,
        subentry,
        streams=_INJECTION_STREAMS,
    )
    assert button.unique_id == "e1_s1_import_injection_history"
    assert (
        button.entity_id == "button.engie_belgium_000000000000_import_injection_history"
    )


async def test_gas_button_delegates_with_gas_streams(hass) -> None:  # noqa: ANN001
    """The gas button passes only the gas streams filter."""
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.runtime_data.client = MagicMock()
    coordinator = MagicMock()
    subentry = _mock_subentry()
    subentry.subentry_id = "s1"
    subentry.title = "ENGIE 000000000000"

    button = EngieBeImportHistoryButton(
        coordinator,
        subentry,
        entry,
        description=_GAS_DESCRIPTION,
        streams=_GAS_STREAMS,
    )
    button.hass = hass

    with patch(
        "custom_components.engie_be.button.async_import_usage_history",
        AsyncMock(return_value=7),
    ) as mocked:
        await button.async_press()

    assert mocked.await_args.kwargs["streams"] == _GAS_STREAMS
    assert button.entity_id == "button.engie_belgium_000000000000_import_gas_history"


async def test_button_press_wraps_api_error_as_hass_error(hass) -> None:  # noqa: ANN001
    """API failures surface as translated HomeAssistantError toasts."""
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.runtime_data.client = MagicMock()
    coordinator = MagicMock()
    subentry = _mock_subentry()
    subentry.subentry_id = "s1"
    subentry.title = "ENGIE 000000000000"

    button = EngieBeImportHistoryButton(
        coordinator,
        subentry,
        entry,
        description=_CONSUMPTION_DESCRIPTION,
        streams=_CONSUMPTION_STREAMS,
    )
    button.hass = hass

    with (
        patch(
            "custom_components.engie_be.button.async_import_usage_history",
            AsyncMock(side_effect=EngieBeApiClientCommunicationError("boom")),
        ),
        pytest.raises(HomeAssistantError) as excinfo,
    ):
        await button.async_press()

    assert excinfo.value.translation_key == "import_history_failed"
    assert excinfo.value.translation_domain == "engie_be"


async def test_clear_usage_history_deletes_three_streams(hass) -> None:  # noqa: ANN001
    """Clear helper queues a ClearStatisticsTask for the three per-BAN IDs."""
    recorder = MagicMock()

    with patch(
        "custom_components.engie_be._statistics.get_instance",
        return_value=recorder,
    ):
        cleared = await async_clear_usage_history(hass, "000000000000")

    assert cleared == [
        "engie_be:000000000000_consumption",
        "engie_be:000000000000_injection",
        "engie_be:000000000000_gas",
    ]
    recorder.queue_task.assert_called_once()
    task = recorder.queue_task.call_args.args[0]
    assert task.statistic_ids == cleared
    assert task.on_done is None


async def test_orchestrator_falls_back_to_3y_when_contracts_endpoint_fails(
    hass,  # noqa: ANN001
) -> None:
    """Contracts-endpoint failure triggers the full 3-year default backfill window."""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    client = MagicMock()
    client.async_get_usage_details = AsyncMock(return_value=payload)
    client.async_get_energy_contracts = AsyncMock(
        side_effect=EngieBeApiClientCommunicationError("500")
    )

    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(return_value={})
    with (
        patch(
            "custom_components.engie_be._statistics.get_instance",
            return_value=recorder,
        ),
        patch(
            "custom_components.engie_be._statistics.async_add_external_statistics",
        ),
    ):
        await async_import_usage_history(hass, client, _mock_subentry())

    # Contracts endpoint attempted exactly once.
    client.async_get_energy_contracts.assert_awaited_once()
    # Fallback is 3 years / 7-day chunks ~= 156+ requests; well above the
    # 30-day / 5-request count that a real contract start would produce.
    assert client.async_get_usage_details.await_count > 100


def test_converter_handles_spring_forward_missing_hour() -> None:
    """Brussels spring-forward: missing 02:00 still maps to consecutive UTC hours."""
    # 2026-03-29 clocks spring forward: 01:00+01:00 -> 03:00+02:00.
    # Consecutive UTC instants: 00:00 UTC and 01:00 UTC.
    items = [
        {
            "start": "2026-03-29T01:00:00+01:00",
            "partialData": False,
            "energy": {
                "electricity": {
                    "offtake": {"kWhSum": 1.0},
                    "injection": {"kWhSum": 0.0},
                },
                "gas": {"kWh": 0.0},
            },
        },
        {
            "start": "2026-03-29T03:00:00+02:00",
            "partialData": False,
            "energy": {
                "electricity": {
                    "offtake": {"kWhSum": 2.0},
                    "injection": {"kWhSum": 0.0},
                },
                "gas": {"kWh": 0.0},
            },
        },
    ]
    per_stream = usage_items_to_statistics(
        items, initial_sums={}, last_stats_time_utc=None
    )

    rows = per_stream[STREAM_CONSUMPTION]
    assert len(rows) == 2
    assert rows[0]["start"] == datetime(2026, 3, 29, 0, 0, tzinfo=UTC)
    assert rows[1]["start"] == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    assert rows[0]["sum"] == pytest.approx(1.0)
    assert rows[1]["sum"] == pytest.approx(3.0)


def test_converter_handles_fall_back_doubled_hour() -> None:
    """Brussels fall-back: doubled 02:00 hour maps to distinct UTC instants."""
    # 2026-10-25 clocks fall back: 02:00+02:00 == 00:00 UTC, 02:00+01:00 == 01:00 UTC.
    items = [
        {
            "start": "2026-10-25T02:00:00+02:00",
            "partialData": False,
            "energy": {
                "electricity": {
                    "offtake": {"kWhSum": 3.0},
                    "injection": {"kWhSum": 0.0},
                },
                "gas": {"kWh": 0.0},
            },
        },
        {
            "start": "2026-10-25T02:00:00+01:00",
            "partialData": False,
            "energy": {
                "electricity": {
                    "offtake": {"kWhSum": 4.0},
                    "injection": {"kWhSum": 0.0},
                },
                "gas": {"kWh": 0.0},
            },
        },
    ]
    per_stream = usage_items_to_statistics(
        items, initial_sums={}, last_stats_time_utc=None
    )

    rows = per_stream[STREAM_CONSUMPTION]
    assert len(rows) == 2
    assert rows[0]["start"] == datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    assert rows[1]["start"] == datetime(2026, 10, 25, 1, 0, tzinfo=UTC)
    assert rows[0]["sum"] == pytest.approx(3.0)
    assert rows[1]["sum"] == pytest.approx(7.0)
