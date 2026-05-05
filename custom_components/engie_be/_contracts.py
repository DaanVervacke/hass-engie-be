"""Pure helpers for parsing the energy-contracts payload."""

from __future__ import annotations

from typing import Any

from .const import DYNAMIC_ENERGY_PRODUCTS

CONTRACT_STATUS_ACTIVE = "ACTIVE"
DIVISION_ELECTRICITY = "ELECTRICITY"


def is_account_dynamic(payload: Any) -> bool:
    """
    Return True when the payload describes a dynamic-electricity contract.

    The ENGIE energy-contracts endpoint returns one element per active
    contract on a business agreement. A customer is considered to be on
    a dynamic (EPEX-indexed) tariff when at least one such element is
    an active electricity contract whose
    ``productConfiguration.energyProduct`` identifies a dynamic product
    (see :data:`DYNAMIC_ENERGY_PRODUCTS`). Any item that fails to match
    is silently ignored so a malformed entry in an otherwise healthy
    payload never flips the result.

    Mixed-fuel households (dynamic electricity plus fixed gas) are
    handled correctly: the gas item is skipped because its ``division``
    is ``"GAS"``, and the electricity item alone determines the result.
    """
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
    """
    Return a mapping of EAN to ``energyProduct`` for active contracts.

    Used by diagnostics so support bundles surface the per-EAN product
    code that drove dynamic detection. Items missing an EAN or product
    code are skipped.
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
        product_configuration = item.get("productConfiguration")
        if not isinstance(product_configuration, dict):
            continue
        energy_product = product_configuration.get("energyProduct")
        if isinstance(ean, str) and ean and isinstance(energy_product, str):
            result[ean] = energy_product
    return result
