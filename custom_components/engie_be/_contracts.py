"""Pure helpers for parsing the energy-contracts payload."""

from __future__ import annotations

from typing import Any

from .const import DYNAMIC_ENERGY_PRODUCTS

CONTRACT_STATUS_ACTIVE = "ACTIVE"
DIVISION_ELECTRICITY = "ELECTRICITY"


def is_account_dynamic(payload: Any) -> bool:
    """Return True iff payload has an active dynamic-electricity contract."""
    if not isinstance(payload, dict):
        return False
    items = payload.get("items")
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") != CONTRACT_STATUS_ACTIVE:
            continue
        if item.get("division") != DIVISION_ELECTRICITY:
            continue
        product_configuration = item.get("productConfiguration")
        if not isinstance(product_configuration, dict):
            continue
        energy_product = product_configuration.get("energyProduct")
        if (
            isinstance(energy_product, str)
            and energy_product in DYNAMIC_ENERGY_PRODUCTS
        ):
            return True
    return False


def energy_products_by_ean(payload: Any) -> dict[str, str]:
    """Return a mapping of EAN to ``energyProduct`` for active contracts."""
    result: dict[str, str] = {}
    if not isinstance(payload, dict):
        return result
    items = payload.get("items")
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") != CONTRACT_STATUS_ACTIVE:
            continue
        ean = item.get("servicePointNumber")
        product_configuration = item.get("productConfiguration")
        if not isinstance(product_configuration, dict):
            continue
        energy_product = product_configuration.get("energyProduct")
        if isinstance(ean, str) and ean and isinstance(energy_product, str):
            result[bare_ean(ean)] = energy_product
    return result


def service_points_by_ean(payload: Any) -> dict[str, str]:
    """
    Return a mapping of EAN to division for active contracts.

    Fills in service_points for pure dynamic-tariff accounts, where the
    supplier-energy-prices endpoint returns no items.
    """
    result: dict[str, str] = {}
    if not isinstance(payload, dict):
        return result
    items = payload.get("items")
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") != CONTRACT_STATUS_ACTIVE:
            continue
        ean = item.get("servicePointNumber")
        division = item.get("division")
        if isinstance(ean, str) and ean and isinstance(division, str) and division:
            result[ean] = division
    return result


DELIVERY_POINT_SUFFIX = "_ID1"


def bare_ean(ean: str) -> str:
    """Strip a trailing delivery-point suffix (``_ID1``) from an EAN."""
    return ean.split("_", maxsplit=1)[0] if "_" in ean else ean


def ean_with_delivery_point_suffix(ean: str) -> str:
    """Append the delivery-point suffix ENGIE's per-EAN endpoints expect."""
    return f"{ean}{DELIVERY_POINT_SUFFIX}"
