"""Tests for the energy-contracts payload helpers."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.engie_be._contracts import (
    energy_products_by_ean,
    is_account_dynamic,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    """Load a JSON fixture by file name."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# is_account_dynamic
# ---------------------------------------------------------------------------


def test_is_account_dynamic_true_for_dynamic_elec_only() -> None:
    """A single active dynamic electricity contract must be detected."""
    payload = _load("energy_contracts_dynamic_elec_only.json")
    assert is_account_dynamic(payload) is True


def test_is_account_dynamic_false_for_fixed_dual_fuel() -> None:
    """Fixed (EASY) elec + fixed gas must not be flagged dynamic."""
    payload = _load("energy_contracts_fixed_dual_fuel.json")
    assert is_account_dynamic(payload) is False


def test_is_account_dynamic_true_for_dynamic_elec_plus_fixed_gas() -> None:
    """The original b6 bug: dynamic elec + fixed gas must still be detected."""
    payload = _load("energy_contracts_dynamic_plus_fixed_gas.json")
    assert is_account_dynamic(payload) is True


def test_is_account_dynamic_false_for_empty_items() -> None:
    """An empty contracts list cannot be classified as dynamic."""
    assert is_account_dynamic(_load("energy_contracts_empty.json")) is False


def test_is_account_dynamic_ignores_inactive_contracts() -> None:
    """A non-ACTIVE dynamic electricity contract must not flip the flag."""
    payload = {
        "items": [
            {
                "division": "ELECTRICITY",
                "status": "TERMINATED",
                "servicePointNumber": "541448820000000001_ID1",
                "productConfiguration": {"energyProduct": "DYNAMIC"},
            },
        ],
    }
    assert is_account_dynamic(payload) is False


def test_is_account_dynamic_ignores_dynamic_gas() -> None:
    """A dynamic-product GAS contract must never flag the account."""
    payload = {
        "items": [
            {
                "division": "GAS",
                "status": "ACTIVE",
                "servicePointNumber": "541448820000000002_ID2",
                "productConfiguration": {"energyProduct": "DYNAMIC"},
            },
        ],
    }
    assert is_account_dynamic(payload) is False


def test_is_account_dynamic_handles_malformed_items() -> None:
    """Non-dict items and missing keys must be silently skipped."""
    payload = {
        "items": [
            None,
            "not-a-dict",
            {},
            {"division": "ELECTRICITY", "status": "ACTIVE"},
            {
                "division": "ELECTRICITY",
                "status": "ACTIVE",
                "productConfiguration": "not-a-dict",
            },
            {
                "division": "ELECTRICITY",
                "status": "ACTIVE",
                "productConfiguration": {"energyProduct": "DYNAMIC"},
            },
        ],
    }
    assert is_account_dynamic(payload) is True


def test_is_account_dynamic_handles_non_dict_payload() -> None:
    """Non-dict payloads (e.g. None, list) must return False, not raise."""
    assert is_account_dynamic(None) is False
    assert is_account_dynamic([]) is False
    assert is_account_dynamic("oops") is False


def test_is_account_dynamic_missing_items_key() -> None:
    """A dict without ``items`` must return False."""
    assert is_account_dynamic({}) is False


# ---------------------------------------------------------------------------
# energy_products_by_ean
# ---------------------------------------------------------------------------


def test_energy_products_by_ean_maps_active_contracts() -> None:
    """Mapping must include one entry per active contract keyed by EAN."""
    payload = _load("energy_contracts_dynamic_plus_fixed_gas.json")
    mapping = energy_products_by_ean(payload)
    assert mapping == {
        "541448820000000001_ID1": "DYNAMIC",
        "541448820000000002_ID2": "EASY",
    }


def test_energy_products_by_ean_skips_inactive_contracts() -> None:
    """Inactive contracts must be excluded from the mapping."""
    payload = {
        "items": [
            {
                "division": "ELECTRICITY",
                "status": "TERMINATED",
                "servicePointNumber": "541448820000000001_ID1",
                "productConfiguration": {"energyProduct": "DYNAMIC"},
            },
            {
                "division": "GAS",
                "status": "ACTIVE",
                "servicePointNumber": "541448820000000002_ID2",
                "productConfiguration": {"energyProduct": "EASY"},
            },
        ],
    }
    assert energy_products_by_ean(payload) == {
        "541448820000000002_ID2": "EASY",
    }


def test_energy_products_by_ean_empty_payload() -> None:
    """Empty payloads must return an empty mapping, not raise."""
    assert energy_products_by_ean(_load("energy_contracts_empty.json")) == {}
    assert energy_products_by_ean(None) == {}
    assert energy_products_by_ean({}) == {}


def test_energy_products_by_ean_skips_items_missing_ean_or_product() -> None:
    """Items missing EAN or product code must be skipped silently."""
    payload = {
        "items": [
            {
                "status": "ACTIVE",
                "productConfiguration": {"energyProduct": "DYNAMIC"},
            },
            {
                "status": "ACTIVE",
                "servicePointNumber": "541448820000000003_ID3",
                "productConfiguration": {},
            },
            {
                "status": "ACTIVE",
                "servicePointNumber": "541448820000000004_ID4",
                "productConfiguration": {"energyProduct": "EASY"},
            },
        ],
    }
    assert energy_products_by_ean(payload) == {
        "541448820000000004_ID4": "EASY",
    }
