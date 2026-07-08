"""Tests for the Energy dashboard solar-forecast hook."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.energy.websocket_api import (
    async_get_energy_platforms,
)
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CONSUMPTION_ADDRESS,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.energy import async_get_solar_forecast

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_ENTRY_ID = "engie_entry_1"
_EAN_A = "541448820070000001"
_EAN_B = "541448820070000002"


def _forecast_payload(iso: str, value_kwh: float) -> dict:
    """Wrap a single hourly slot into the ENGIE ``forecasts`` shape."""
    return {
        "forecastDate": iso.split("T", 1)[0],
        "level": "HIGH_SURPLUS",
        "details": [
            {"startTime": iso, "value": value_kwh, "level": "HIGH_SURPLUS"},
        ],
    }


def _wire(
    hass: HomeAssistant,
    *,
    subentries: dict[str, tuple[bool, dict | None]],
) -> None:
    """Attach a MagicMock runtime with per-subentry has_solar + coordinator data."""
    entry = MagicMock()
    entry.entry_id = _ENTRY_ID
    subentry_data: dict[str, MagicMock] = {}
    for sub_id, (has_solar, data) in subentries.items():
        coord = MagicMock()
        coord.data = data
        sub = MagicMock()
        sub.has_solar = has_solar
        sub.coordinator = coord
        subentry_data[sub_id] = sub
    runtime = MagicMock()
    runtime.subentry_data = subentry_data
    entry.runtime_data = runtime
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)


def _build_real_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a v5 MockConfigEntry with one business-agreement subentry."""
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
                title="Test Account",
                unique_id="B-0001",
                data={
                    CONF_BUSINESS_AGREEMENT_NUMBER: "B-0001",
                    CONF_PREMISES_NUMBER: "P-0001",
                    CONF_CONSUMPTION_ADDRESS: "Test 1, 1000 Brussels",
                },
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


def _stub_client() -> MagicMock:
    """Minimal API client stub so async_setup_entry completes."""
    client = MagicMock()

    async def _refresh_and_update() -> tuple[str, str]:
        client.refresh_token = "fresh-refresh"  # noqa: S105
        return ("fresh-access", "fresh-refresh")

    client.async_refresh_token = AsyncMock(side_effect=_refresh_and_update)
    client.async_get_prices = AsyncMock(return_value={"items": []})
    client.async_get_service_point = AsyncMock(
        return_value={"division": "ELECTRICITY"},
    )
    client.async_get_monthly_peaks = AsyncMock(
        return_value={"peakOfTheMonth": None, "dailyPeaks": []},
    )
    client.async_get_happy_hour_event = AsyncMock(return_value={})
    client.async_get_happy_hours_service_enabled_flag = AsyncMock(return_value={})
    client.async_get_solar_surplus_shown_dashboard_flag = AsyncMock(return_value={})
    client.async_get_solar_surplus_forecasts = AsyncMock(return_value={"forecasts": []})
    client.async_get_energy_contracts = AsyncMock(return_value={"items": []})
    client.async_get_epex_prices = AsyncMock(return_value={"timeSeries": []})
    return client


async def _setup_entry_with_stubs(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> None:
    """Set up the entry, bypassing coordinator first-refresh side effects."""
    client = _stub_client()
    with (
        patch(
            "custom_components.engie_be.EngieBeApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeDataUpdateCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.engie_be.coordinator.EngieBeEpexCoordinator"
            ".async_config_entry_first_refresh",
            new=AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()


async def test_returns_none_when_entry_missing(hass: HomeAssistant) -> None:
    """A missing config entry yields None (not an exception)."""
    hass.config_entries.async_get_entry = MagicMock(return_value=None)
    assert await async_get_solar_forecast(hass, _ENTRY_ID) is None


async def test_returns_none_when_runtime_data_missing(hass: HomeAssistant) -> None:
    """A loaded entry without runtime_data yields None."""
    entry = MagicMock()
    entry.runtime_data = None
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    assert await async_get_solar_forecast(hass, _ENTRY_ID) is None


async def test_skips_subentries_without_has_solar(hass: HomeAssistant) -> None:
    """Non-solar subentries are ignored even if they have a wrapper."""
    _wire(
        hass,
        subentries={
            "sub_gas": (
                False,
                {
                    "solar_surplus": {
                        "data": {
                            _EAN_A: [
                                _forecast_payload("2026-07-08T12:00:00+02:00", 1.0)
                            ]
                        }
                    }
                },
            ),
        },
    )
    assert await async_get_solar_forecast(hass, _ENTRY_ID) is None


async def test_converts_kwh_to_wh(hass: HomeAssistant) -> None:
    """Values are multiplied by 1000 to satisfy the Wh contract."""
    _wire(
        hass,
        subentries={
            "sub_a": (
                True,
                {
                    "solar_surplus": {
                        "data": {
                            _EAN_A: [
                                _forecast_payload("2026-07-08T12:00:00+02:00", 2.5)
                            ]
                        }
                    }
                },
            ),
        },
    )
    result = await async_get_solar_forecast(hass, _ENTRY_ID)
    assert result == {"wh_hours": {"2026-07-08T12:00:00+02:00": 2500.0}}


async def test_aggregates_across_eans_and_subentries(hass: HomeAssistant) -> None:
    """Slots at the same instant across EANs and subentries are summed."""
    ts = "2026-07-08T12:00:00+02:00"
    _wire(
        hass,
        subentries={
            "sub_a": (
                True,
                {
                    "solar_surplus": {
                        "data": {
                            _EAN_A: [_forecast_payload(ts, 1.0)],
                            _EAN_B: [_forecast_payload(ts, 2.0)],
                        }
                    }
                },
            ),
            "sub_b": (
                True,
                {
                    "solar_surplus": {
                        "data": {
                            _EAN_A: [_forecast_payload(ts, 0.5)],
                        }
                    }
                },
            ),
        },
    )
    result = await async_get_solar_forecast(hass, _ENTRY_ID)
    assert result == {"wh_hours": {ts: 3500.0}}


async def test_skips_zero_and_negative_values(hass: HomeAssistant) -> None:
    """NO_DATA/NO_SURPLUS slots carrying value=0 are omitted from the payload."""
    _wire(
        hass,
        subentries={
            "sub_a": (
                True,
                {
                    "solar_surplus": {
                        "data": {
                            _EAN_A: [
                                {
                                    "forecastDate": "2026-07-08",
                                    "level": "NO_DATA",
                                    "details": [
                                        {
                                            "startTime": "2026-07-08T06:00:00+02:00",
                                            "value": 0,
                                            "level": "NO_DATA",
                                        },
                                        {
                                            "startTime": "2026-07-08T12:00:00+02:00",
                                            "value": 1.0,
                                            "level": "HIGH_SURPLUS",
                                        },
                                    ],
                                }
                            ]
                        }
                    }
                },
            ),
        },
    )
    result = await async_get_solar_forecast(hass, _ENTRY_ID)
    assert result == {"wh_hours": {"2026-07-08T12:00:00+02:00": 1000.0}}


async def test_skips_malformed_slots(hass: HomeAssistant) -> None:
    """Missing/unparseable startTime or non-numeric value are dropped."""
    _wire(
        hass,
        subentries={
            "sub_a": (
                True,
                {
                    "solar_surplus": {
                        "data": {
                            _EAN_A: [
                                {
                                    "forecastDate": "2026-07-08",
                                    "level": "LOW_SURPLUS",
                                    "details": [
                                        {"startTime": None, "value": 1.0},
                                        {"startTime": "not-a-date", "value": 1.0},
                                        {
                                            "startTime": "2026-07-08T13:00:00",
                                            "value": 1.0,
                                        },  # no tz
                                        {
                                            "startTime": "2026-07-08T14:00:00+02:00",
                                            "value": "bogus",
                                        },
                                        {
                                            "startTime": "2026-07-08T15:00:00+02:00",
                                            "value": 0.7,
                                        },
                                    ],
                                }
                            ]
                        }
                    }
                },
            ),
        },
    )
    result = await async_get_solar_forecast(hass, _ENTRY_ID)
    assert result == {"wh_hours": {"2026-07-08T15:00:00+02:00": 700.0}}


async def test_ignores_wrapper_shape_variations(hass: HomeAssistant) -> None:
    """Non-dict data / wrapper / per_ean and non-list forecasts are all skipped."""
    ts = "2026-07-08T12:00:00+02:00"
    _wire(
        hass,
        subentries={
            "sub_non_dict_data": (True, "not a dict"),
            "sub_missing_wrapper": (True, {"other_key": {}}),
            "sub_non_dict_wrapper": (True, {"solar_surplus": "oops"}),
            "sub_non_dict_per_ean": (True, {"solar_surplus": {"data": "nope"}}),
            "sub_non_list_forecasts": (
                True,
                {"solar_surplus": {"data": {_EAN_A: "not a list"}}},
            ),
            "sub_non_dict_day": (True, {"solar_surplus": {"data": {_EAN_A: ["nope"]}}}),
            "sub_non_list_details": (
                True,
                {
                    "solar_surplus": {
                        "data": {
                            _EAN_A: [{"forecastDate": "2026-07-08", "details": "x"}]
                        }
                    }
                },
            ),
            "sub_non_dict_slot": (
                True,
                {
                    "solar_surplus": {
                        "data": {
                            _EAN_A: [
                                {"forecastDate": "2026-07-08", "details": ["nope"]}
                            ]
                        }
                    }
                },
            ),
            "sub_real": (
                True,
                {
                    "solar_surplus": {
                        "data": {_EAN_A: [_forecast_payload(ts, 1.0)]},
                    },
                },
            ),
        },
    )
    result = await async_get_solar_forecast(hass, _ENTRY_ID)
    assert result == {"wh_hours": {ts: 1000.0}}


async def test_returns_sorted_timestamps(hass: HomeAssistant) -> None:
    """The wh_hours mapping is ordered by ISO timestamp."""
    _wire(
        hass,
        subentries={
            "sub_a": (
                True,
                {
                    "solar_surplus": {
                        "data": {
                            _EAN_A: [
                                _forecast_payload("2026-07-09T12:00:00+02:00", 1.0),
                                _forecast_payload("2026-07-08T12:00:00+02:00", 2.0),
                            ]
                        }
                    }
                },
            ),
        },
    )
    result = await async_get_solar_forecast(hass, _ENTRY_ID)
    assert result is not None
    keys = list(result["wh_hours"].keys())
    assert keys == sorted(keys)


async def test_hook_is_discovered_by_ha_energy_platform(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """HA's async_get_energy_platforms discovers our hook by domain."""
    entry = _build_real_entry(hass)
    await _setup_entry_with_stubs(hass, entry)

    platforms = await async_get_energy_platforms(hass)

    assert DOMAIN in platforms
    hook = platforms[DOMAIN]
    # The discovered reference must be our own function, not a bound method
    # of some other module - assert identity against the import path.
    assert hook is async_get_solar_forecast


async def test_hook_returns_none_for_setup_entry_without_solar(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    End-to-end: real entry, no has_solar payload -> hook returns None.

    Complements the unit tests that mock hass.config_entries.async_get_entry
    directly; this one goes through the real HA lookup + our hook body.
    """
    entry = _build_real_entry(hass)
    await _setup_entry_with_stubs(hass, entry)

    result = await async_get_solar_forecast(hass, entry.entry_id)

    assert result is None
