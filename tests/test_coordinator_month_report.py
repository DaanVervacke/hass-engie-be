"""
Tests for coordinator-driven Happy Hours month-report fetching.

Covers the happy_hour_month_report wrapper storage, soft-fail on API
error, auth-failure escalation, and the gate that skips the fetch for
un-enrolled BANs.
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
    CONF_CLIENT_ID,
    CONF_CONSUMPTION_ADDRESS,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
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
_MONTH_REPORT_FIXTURE = _FIXTURES / "happy_hour_month_report.json"
_MONTH_REPORT_NO_CURRENT_FIXTURE = _FIXTURES / "happy_hour_month_report_no_current.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_entry(hass: HomeAssistant) -> MockConfigEntry:
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
                title="placeholder-B-0001",
                unique_id="B-0001",
                data={
                    CONF_BUSINESS_AGREEMENT_NUMBER: "B-0001",
                    CONF_PREMISES_NUMBER: "P-B-0001",
                    CONF_CONSUMPTION_ADDRESS: "Test 1, 1000 Brussels",
                },
            )
        ],
    )
    entry.add_to_hass(hass)
    return entry


def _subentry(entry: MockConfigEntry) -> ConfigSubentry:
    return next(iter(entry.subentries.values()))


def _coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    subentry: ConfigSubentry,
) -> EngieBeDataUpdateCoordinator:
    return EngieBeDataUpdateCoordinator(
        hass=hass, config_entry=entry, subentry=subentry
    )


def _wire_runtime(
    entry: MockConfigEntry,
    client: MagicMock,
    coordinator: EngieBeDataUpdateCoordinator,
    subentry: ConfigSubentry,
) -> None:
    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=MagicMock(),
        subentry_data={
            subentry.subentry_id: EngieBeSubentryData(coordinator=coordinator)
        },
        authenticated=True,
        last_options=dict(entry.options),
    )


def _make_client(
    *,
    flags: dict | Exception,
    month_report: dict | Exception | None = None,
) -> MagicMock:
    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=_load(_PRICES_FIXTURE))
    client.async_get_monthly_peaks = AsyncMock(return_value=_load(_PEAKS_FIXTURE))

    if isinstance(flags, Exception):
        client.async_get_feature_flags = AsyncMock(side_effect=flags)
    else:
        client.async_get_feature_flags = AsyncMock(return_value=flags)

    client.async_get_happy_hour_event = AsyncMock(return_value={})

    if isinstance(month_report, Exception):
        client.async_get_month_report = AsyncMock(side_effect=month_report)
    else:
        client.async_get_month_report = AsyncMock(
            return_value=month_report
            if month_report is not None
            else _load(_MONTH_REPORT_FIXTURE)
        )
    return client


# ---------------------------------------------------------------------------
# Gate: only enrolled BANs trigger the month-report fetch
# ---------------------------------------------------------------------------


async def test_un_enrolled_ban_skips_month_report_fetch(hass: HomeAssistant) -> None:
    """An un-enrolled BAN must never call async_get_month_report."""
    entry = _build_entry(hass)
    sub = _subentry(entry)
    client = _make_client(flags=_load(_FLAGS_NOT_ENROLLED))
    coord = _coordinator(hass, entry, sub)
    _wire_runtime(entry, client, coord, sub)

    result = await coord._async_update_data()

    assert "happy_hour_month_report" not in result
    client.async_get_month_report.assert_not_awaited()


async def test_enrolled_ban_fetches_month_report(hass: HomeAssistant) -> None:
    """An enrolled BAN must call async_get_month_report and store the wrapper."""
    entry = _build_entry(hass)
    sub = _subentry(entry)
    client = _make_client(flags=_load(_FLAGS_ENROLLED))
    coord = _coordinator(hass, entry, sub)
    _wire_runtime(entry, client, coord, sub)

    result = await coord._async_update_data()

    client.async_get_month_report.assert_awaited_once()
    assert "happy_hour_month_report" in result
    wrapper = result["happy_hour_month_report"]
    assert isinstance(wrapper["data"], dict)
    assert wrapper["year"] == result.get("happy_hour_month_report", {}).get("year")


# ---------------------------------------------------------------------------
# Soft-fail: API errors keep last-known wrapper
# ---------------------------------------------------------------------------


async def test_month_report_api_error_keeps_last_known(hass: HomeAssistant) -> None:
    """A transient API error keeps prior data with is_fallback=True."""
    entry = _build_entry(hass)
    sub = _subentry(entry)
    previous_data = {"month": {"happyHour": {"rewardEuros": 9.99}}}
    previous_wrapper = {
        "data": previous_data,
        "year": 2026,
        "month": 6,
        "is_fallback": False,
    }
    client = _make_client(
        flags=_load(_FLAGS_ENROLLED),
        month_report=EngieBeApiClientError("timeout"),
    )
    coord = _coordinator(hass, entry, sub)
    _wire_runtime(entry, client, coord, sub)
    # Seed the coordinator's data with the previous wrapper.
    coord.data = {"happy_hour_month_report": previous_wrapper}

    result = await coord._async_update_data()

    returned = result["happy_hour_month_report"]
    # The data and the ORIGINAL year/month are preserved unchanged.
    assert returned["data"] is previous_data
    assert returned["year"] == 2026
    assert returned["month"] == 6
    # The fallback flag is flipped to True so sensors can signal staleness.
    assert returned["is_fallback"] is True


async def test_month_report_wrapper_has_is_fallback_on_success(
    hass: HomeAssistant,
) -> None:
    """A successful fetch wraps is_fallback=False with current year/month."""
    entry = _build_entry(hass)
    sub = _subentry(entry)
    client = _make_client(flags=_load(_FLAGS_ENROLLED))
    coord = _coordinator(hass, entry, sub)
    _wire_runtime(entry, client, coord, sub)

    result = await coord._async_update_data()

    wrapper = result["happy_hour_month_report"]
    assert wrapper["is_fallback"] is False
    assert isinstance(wrapper["year"], int)
    assert isinstance(wrapper["month"], int)


async def test_month_report_api_error_with_no_prior_wrapper(
    hass: HomeAssistant,
) -> None:
    """When there is no prior wrapper and the API errors, the key is absent."""
    entry = _build_entry(hass)
    sub = _subentry(entry)
    client = _make_client(
        flags=_load(_FLAGS_ENROLLED),
        month_report=EngieBeApiClientError("timeout"),
    )
    coord = _coordinator(hass, entry, sub)
    _wire_runtime(entry, client, coord, sub)

    result = await coord._async_update_data()

    assert "happy_hour_month_report" not in result


# ---------------------------------------------------------------------------
# Auth-failure escalation
# ---------------------------------------------------------------------------


async def test_month_report_auth_failure_escalates(hass: HomeAssistant) -> None:
    """An auth error from the month-report endpoint raises ConfigEntryAuthFailed."""
    entry = _build_entry(hass)
    sub = _subentry(entry)
    client = _make_client(
        flags=_load(_FLAGS_ENROLLED),
        month_report=EngieBeApiClientAuthenticationError("token expired"),
    )
    coord = _coordinator(hass, entry, sub)
    _wire_runtime(entry, client, coord, sub)

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


# ---------------------------------------------------------------------------
# Non-dict API response
# ---------------------------------------------------------------------------


async def test_month_report_non_dict_response_stores_none(hass: HomeAssistant) -> None:
    """A non-dict API response stores data=None in the wrapper."""
    entry = _build_entry(hass)
    sub = _subentry(entry)
    client = _make_client(flags=_load(_FLAGS_ENROLLED), month_report=None)
    # Force the mock to return a non-dict value (list)
    client.async_get_month_report = AsyncMock(return_value=["unexpected"])
    coord = _coordinator(hass, entry, sub)
    _wire_runtime(entry, client, coord, sub)

    result = await coord._async_update_data()

    wrapper = result.get("happy_hour_month_report")
    assert wrapper is not None
    assert wrapper["data"] is None


# ---------------------------------------------------------------------------
# History fallback: current month absent, history carries prior data
# ---------------------------------------------------------------------------


async def test_current_month_present_is_fallback_false(hass: HomeAssistant) -> None:
    """When current month has happyHour data, wrapper is is_fallback=False."""
    entry = _build_entry(hass)
    sub = _subentry(entry)
    client = _make_client(flags=_load(_FLAGS_ENROLLED))
    coord = _coordinator(hass, entry, sub)
    _wire_runtime(entry, client, coord, sub)

    result = await coord._async_update_data()

    wrapper = result["happy_hour_month_report"]
    assert wrapper["is_fallback"] is False
    assert isinstance(wrapper["year"], int)
    assert isinstance(wrapper["month"], int)
    # data must carry the full API response so sensor paths resolve
    assert isinstance(wrapper["data"], dict)
    assert isinstance(wrapper["data"].get("month"), dict)


async def test_current_month_absent_history_has_fallback(hass: HomeAssistant) -> None:
    """When current month has no happyHour, the most-recent history entry is used."""
    entry = _build_entry(hass)
    sub = _subentry(entry)
    no_current = _load(_MONTH_REPORT_NO_CURRENT_FIXTURE)
    client = _make_client(flags=_load(_FLAGS_ENROLLED), month_report=no_current)
    coord = _coordinator(hass, entry, sub)
    _wire_runtime(entry, client, coord, sub)

    result = await coord._async_update_data()

    wrapper = result["happy_hour_month_report"]
    assert wrapper["is_fallback"] is True
    # Should pick the most-recent history entry: 2026-06
    assert wrapper["year"] == 2026
    assert wrapper["month"] == 6
    # data must be shaped so sensor paths resolve: {"month": {"happyHour": ...}}
    assert isinstance(wrapper["data"], dict)
    happy_hour = wrapper["data"]["month"]["happyHour"]
    assert happy_hour["consumptionKWh"] == 15.5
    assert happy_hour["numberOfEligibleHappyHours"] == 4
    assert happy_hour["rewardEuros"] == 6.0


async def test_current_month_absent_history_empty_no_happy_hour(
    hass: HomeAssistant,
) -> None:
    """When no history entry has happyHour data, the key is absent."""
    entry = _build_entry(hass)
    sub = _subentry(entry)
    # API returns a response where current month has no happyHour
    # and history entries also lack happyHour (e.g. very first month)
    no_data_response = {
        "month": {},
        "history": [
            {"yearMonth": "2026-05"},
            {"yearMonth": "2026-04", "happyHour": None},
        ],
    }
    client = _make_client(flags=_load(_FLAGS_ENROLLED), month_report=no_data_response)
    coord = _coordinator(hass, entry, sub)
    _wire_runtime(entry, client, coord, sub)

    result = await coord._async_update_data()

    assert "happy_hour_month_report" not in result


async def test_current_month_absent_history_completely_empty(
    hass: HomeAssistant,
) -> None:
    """When history is empty and current month has no happyHour, key is absent."""
    entry = _build_entry(hass)
    sub = _subentry(entry)
    no_data_response = {"month": {}, "history": []}
    client = _make_client(flags=_load(_FLAGS_ENROLLED), month_report=no_data_response)
    coord = _coordinator(hass, entry, sub)
    _wire_runtime(entry, client, coord, sub)

    result = await coord._async_update_data()

    assert "happy_hour_month_report" not in result
