"""Unit tests for ENGIE Belgium sensor helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.engie_be.sensor import (
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
