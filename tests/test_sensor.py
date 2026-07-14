"""Unit tests for ENGIE Belgium sensor helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.const import EntityCategory

from custom_components.engie_be.const import SUBENTRY_TYPE_BUSINESS_AGREEMENT
from custom_components.engie_be.sensor import (
    _CAPTAR_MONTHLY_PEAK_END,
    _CAPTAR_MONTHLY_PEAK_ENERGY,
    _CAPTAR_MONTHLY_PEAK_START,
    _EPEX_HIGH_TODAY,
    _EPEX_LOW_TODAY,
    EngieBeEnergySensor,
    _build_peak_sensors,
    _build_sensor_descriptions,
    _detect_energy_type,
    _find_current_price,
    _normalize_slot_code,
    _slot_suffixes,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture from the tests/fixtures directory."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# _normalize_slot_code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TOTAL_HOURS", "TOTAL_HOURS"),
        ("PEAK", "PEAK"),
        ("OFFPEAK", "OFFPEAK"),
        ("SUPEROFFPEAK", "SUPEROFFPEAK"),
        ("S_TOU1_OFFTAKE_PEAK", "PEAK"),
        ("S_TOU2_INJECTION_OFFPEAK", "OFFPEAK"),
        ("S_TOU3_OFFTAKE_SUPEROFFPEAK", "SUPEROFFPEAK"),
        ("UNKNOWN_CODE", "UNKNOWN_CODE"),
        ("EN", "EN"),
    ],
)
def test_normalize_slot_code(raw: str, expected: str) -> None:
    """Normalising slot codes strips direction prefixes and keeps the rate part."""
    assert _normalize_slot_code(raw) == expected


# ---------------------------------------------------------------------------
# _slot_suffixes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("TOTAL_HOURS", ("", "")),
        ("PEAK", ("_peak", "_peak")),
        ("OFFPEAK", ("_offpeak", "_offpeak")),
        ("SUPEROFFPEAK", ("_superoffpeak", "_superoffpeak")),
        ("EN", None),
        ("S_TOU1_OFFTAKE_PEAK", ("_peak", "_peak")),
        ("S_TOU1_INJECTION_OFFPEAK", ("_offpeak", "_offpeak")),
        ("WEIRD", ("_weird", "_weird")),
    ],
)
def test_slot_suffixes(code: str, expected: tuple[str, str] | None) -> None:
    """Slot suffix mapping returns expected key/translation tuples or None."""
    assert _slot_suffixes(code) == expected


# ---------------------------------------------------------------------------
# _find_current_price
# ---------------------------------------------------------------------------


def test_find_current_price_returns_matching_window() -> None:
    """Returns the price entry whose date range covers today."""
    today = datetime.now(tz=UTC).date()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)
    next_week = today + timedelta(days=7)

    prices = [
        {"from": yesterday.isoformat(), "to": tomorrow.isoformat(), "id": "current"},
        {"from": tomorrow.isoformat(), "to": next_week.isoformat(), "id": "future"},
    ]

    result = _find_current_price(prices)
    assert result is not None
    assert result["id"] == "current"


def test_find_current_price_falls_back_to_last() -> None:
    """When no entry covers today, the last entry is returned as fallback."""
    today = datetime.now(tz=UTC).date()
    long_ago = today - timedelta(days=60)
    less_long_ago = today - timedelta(days=30)
    recent_past = today - timedelta(days=1)

    prices = [
        {"from": long_ago.isoformat(), "to": less_long_ago.isoformat(), "id": "old"},
        {
            "from": less_long_ago.isoformat(),
            "to": recent_past.isoformat(),
            "id": "fallback",
        },
    ]

    result = _find_current_price(prices)
    assert result is not None
    assert result["id"] == "fallback"


def test_find_current_price_empty_returns_none() -> None:
    """Empty list returns None."""
    assert _find_current_price([]) is None


def test_find_current_price_uses_brussels_date_at_month_boundary() -> None:
    """Brussels civil date must win at a UTC/Brussels month boundary."""
    prices = [
        {"from": "2026-04-01", "to": "2026-05-01", "id": "april"},
        {"from": "2026-05-01", "to": "2026-06-01", "id": "may"},
    ]
    brussels = ZoneInfo("Europe/Brussels")
    boundary_brussels = datetime(2026, 5, 1, 0, 30, 0, tzinfo=brussels)
    with patch(
        "custom_components.engie_be.sensor.dt_util.now",
        return_value=boundary_brussels,
    ):
        result = _find_current_price(prices)
    assert result is not None
    assert result["id"] == "may"


# ---------------------------------------------------------------------------
# _detect_energy_type
# ---------------------------------------------------------------------------


def test_detect_energy_type_electricity() -> None:
    """ELECTRICITY division maps to 'Electricity'."""
    assert _detect_energy_type("ean1", {"ean1": "ELECTRICITY"}) == "Electricity"


def test_detect_energy_type_gas() -> None:
    """GAS division maps to 'Gas'."""
    assert _detect_energy_type("ean2", {"ean2": "GAS"}) == "Gas"


def test_detect_energy_type_unknown_falls_back() -> None:
    """Unknown EAN or unmapped division falls back to 'Energy'."""
    assert _detect_energy_type("missing", {}) == "Energy"
    assert _detect_energy_type("ean3", {"ean3": "WEIRD"}) == "Energy"


# ---------------------------------------------------------------------------
# _build_sensor_descriptions
# ---------------------------------------------------------------------------


def test_build_sensor_descriptions_skips_blended_en_slots() -> None:
    """Blended 'EN' slot codes must produce no sensor descriptions."""
    data = _load_fixture("prices_sample.json")
    service_points = _load_fixture("service_points_sample.json")

    # Force _find_current_price to return our static fixture entry rather than
    # depending on real time-of-day matching.
    with patch(
        "custom_components.engie_be.sensor._find_current_price",
        side_effect=lambda prices: prices[0] if prices else None,
    ):
        descriptions = _build_sensor_descriptions(data, service_points)

    slot_codes = [slot for _, _, _, slot in descriptions]
    assert "EN" not in slot_codes


def test_build_sensor_descriptions_emits_excl_vat_pair() -> None:
    """Each direction/slot must yield both an incl-VAT and excl-VAT sensor."""
    data = _load_fixture("prices_sample.json")
    service_points = _load_fixture("service_points_sample.json")

    with patch(
        "custom_components.engie_be.sensor._find_current_price",
        side_effect=lambda prices: prices[0] if prices else None,
    ):
        descriptions = _build_sensor_descriptions(data, service_points)

    keys = [desc.key for desc, *_ in descriptions]
    # Sanity: every base key must have a matching `_excl_vat` sibling.
    excl_keys = {k for k in keys if k.endswith("_excl_vat")}
    base_keys = {k for k in keys if not k.endswith("_excl_vat")}
    assert excl_keys, "Expected at least one excl-VAT description"
    for base in base_keys:
        assert f"{base}_excl_vat" in excl_keys, (
            f"Missing excl-VAT counterpart for {base}"
        )


def test_build_sensor_descriptions_uses_translation_keys_per_energy_type() -> None:
    """Electricity and gas EANs must produce different translation key prefixes."""
    data = _load_fixture("prices_sample.json")
    service_points = _load_fixture("service_points_sample.json")

    with patch(
        "custom_components.engie_be.sensor._find_current_price",
        side_effect=lambda prices: prices[0] if prices else None,
    ):
        descriptions = _build_sensor_descriptions(data, service_points)

    translation_keys = {desc.translation_key for desc, *_ in descriptions}
    assert any(k.startswith("electricity_") for k in translation_keys)
    assert any(k.startswith("gas_") for k in translation_keys)


# ---------------------------------------------------------------------------
# EngieBeEnergySensor unique_id shape
# ---------------------------------------------------------------------------


def test_energy_sensor_unique_id_includes_subentry_segment() -> None:
    """
    Energy price unique_ids must carry the subentry segment.

    All v3 customer-account entities (peaks, calendar, EPEX) follow the
    ``{entry_id}_{subentry_id}_{key}`` shape. Energy price sensors used
    to omit the subentry segment, which made the platform produce a
    different unique_id than the v2->v3 migration helper had registered
    for the same sensor, resulting in duplicated entities after upgrades
    from 0.7.x. This regression test pins the platform to the canonical
    shape so that mismatch cannot return.
    """
    coordinator = MagicMock()
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"

    subentry = MagicMock()
    subentry.subentry_id = "sub_xyz"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {}

    description = SensorEntityDescription(
        key="541448820000000001_offtake",
        translation_key="electricity_offtake_price_eur_per_kwh",
    )

    sensor = EngieBeEnergySensor(
        coordinator=coordinator,
        subentry=subentry,
        entity_description=description,
        ean="541448820000000001_ID1",
        value_key="ELE_OFFTAKE",
        slot_code="STD",
    )

    assert sensor.unique_id == "test_entry_id_sub_xyz_541448820000000001_offtake"


def test_energy_sensor_ean_attribute_strips_delivery_point_suffix() -> None:
    """
    The ``ean`` extra-state-attribute must not leak the delivery-point suffix.

    ``self._ean`` itself must stay raw (it's matched against
    ``coordinator.data["items"]``'s unmodified ``ean`` field to find the
    current price entry), but the value shown to the user in
    ``extra_state_attributes`` should be the clean EAN.
    """
    coordinator = MagicMock()
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    coordinator.data = {"items": []}
    coordinator.last_successful_fetch = None

    subentry = MagicMock()
    subentry.subentry_id = "sub_xyz"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {}

    description = SensorEntityDescription(
        key="541448820000000001_offtake",
        translation_key="electricity_offtake_price_eur_per_kwh",
    )

    sensor = EngieBeEnergySensor(
        coordinator=coordinator,
        subentry=subentry,
        entity_description=description,
        ean="541448820000000001_ID1",
        value_key="ELE_OFFTAKE",
        slot_code="STD",
    )

    assert sensor.extra_state_attributes["ean"] == "541448820000000001"
    # The internal, unstripped value is unchanged - needed to match
    # coordinator.data["items"] price lookups.
    assert sensor._ean == "541448820000000001_ID1"


# ---------------------------------------------------------------------------
# entity-disabled-by-default: verify disabled-by-default flags on descriptions
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_prices() -> dict[str, Any]:
    """Return the prices_sample fixture for description-generation tests."""
    return _load_fixture("prices_sample.json")


def test_excl_vat_sensors_disabled_by_default(
    sample_prices: dict[str, Any],
) -> None:
    """Price-excl-VAT sensors must be disabled by default."""
    descs = _build_sensor_descriptions(sample_prices, {})
    for desc, _ean, value_key, _slot in descs:
        is_excl_vat = value_key.endswith("ExclVAT")
        if is_excl_vat:
            assert desc.entity_registry_enabled_default is False, (
                f"{desc.key}: excl_vat sensor must be disabled by default"
            )
        else:
            assert desc.entity_registry_enabled_default is not False, (
                f"{desc.key}: incl_vat sensor must be enabled by default"
            )


def test_captar_peak_energy_and_timestamps_disabled_by_default() -> None:
    """Captar peak energy and timestamp sensors must be disabled by default."""
    assert _CAPTAR_MONTHLY_PEAK_ENERGY.entity_registry_enabled_default is False
    assert _CAPTAR_MONTHLY_PEAK_START.entity_registry_enabled_default is False
    assert _CAPTAR_MONTHLY_PEAK_END.entity_registry_enabled_default is False


def test_captar_peak_timestamps_are_diagnostic() -> None:
    """Peak start/end timestamps are contextual diagnostic detail."""
    assert _CAPTAR_MONTHLY_PEAK_START.entity_category is EntityCategory.DIAGNOSTIC
    assert _CAPTAR_MONTHLY_PEAK_END.entity_category is EntityCategory.DIAGNOSTIC


def test_epex_extrema_sensors_enabled_by_default() -> None:
    """EPEX high/low sensors are primary data for dynamic-tariff users."""
    assert _EPEX_LOW_TODAY.entity_registry_enabled_default is not False
    assert _EPEX_HIGH_TODAY.entity_registry_enabled_default is not False


# ---------------------------------------------------------------------------
# expose_all_entities toggle
# ---------------------------------------------------------------------------


def test_build_sensor_descriptions_expose_all_enables_excl_vat() -> None:
    """Expose-all forces excl-VAT sensor descriptions to enabled-by-default."""
    data = _load_fixture("prices_sample.json")
    service_points = _load_fixture("service_points_sample.json")

    with patch(
        "custom_components.engie_be.sensor._find_current_price",
        side_effect=lambda prices: prices[0] if prices else None,
    ):
        descriptions = _build_sensor_descriptions(data, service_points, expose_all=True)

    excl_vat_descs = [
        desc for desc, _ean, vk, _slot in descriptions if vk.endswith("ExclVAT")
    ]
    assert excl_vat_descs, "Expected at least one excl-VAT description"
    for desc in excl_vat_descs:
        assert desc.entity_registry_enabled_default is True, (
            f"{desc.key}: expose_all should force excl-VAT to enabled-by-default"
        )


def test_build_sensor_descriptions_default_keeps_excl_vat_disabled() -> None:
    """Without expose-all, excl-VAT sensors stay disabled-by-default."""
    data = _load_fixture("prices_sample.json")
    service_points = _load_fixture("service_points_sample.json")

    with patch(
        "custom_components.engie_be.sensor._find_current_price",
        side_effect=lambda prices: prices[0] if prices else None,
    ):
        descriptions = _build_sensor_descriptions(data, service_points)

    excl_vat_descs = [
        desc for desc, _ean, vk, _slot in descriptions if vk.endswith("ExclVAT")
    ]
    assert excl_vat_descs, "Expected at least one excl-VAT description"
    for desc in excl_vat_descs:
        assert desc.entity_registry_enabled_default is False, (
            f"{desc.key}: excl-VAT should remain disabled-by-default"
        )


def test_build_peak_sensors_expose_all_enables_disabled_descriptions() -> None:
    """Expose-all forces captar peak energy/start/end to enabled-by-default."""
    coordinator = MagicMock()
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"

    subentry = MagicMock()
    subentry.subentry_id = "sub_xyz"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {}

    sensors = _build_peak_sensors(coordinator, subentry, expose_all=True)
    disabled_keys = {
        "captar_monthly_peak_energy",
        "captar_monthly_peak_start",
        "captar_monthly_peak_end",
    }
    for sensor in sensors:
        desc = sensor.entity_description
        if desc.key in disabled_keys:
            assert desc.entity_registry_enabled_default is True, (
                f"{desc.key}: expose_all should force to enabled-by-default"
            )
