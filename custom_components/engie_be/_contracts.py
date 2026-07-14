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
            result[bare_ean(ean)] = energy_product
    return result


def service_points_by_ean(payload: Any) -> dict[str, str]:
    """
    Return a mapping of EAN to division for active contracts.

    The energy-contracts payload carries a division per active contract
    regardless of tariff type, unlike the supplier-energy-prices
    endpoint which returns no items for pure dynamic-tariff accounts.
    Used to fill in service_points for accounts the prices-based
    lookup misses. Items missing an EAN or division are skipped.
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
    """
    Strip a trailing delivery-point suffix (``_ID1``) from an EAN.

    ENGIE's supplier-energy-prices and TOU-schedules endpoints return
    EANs with a delivery-point suffix; ``service_points`` and every
    user-facing EAN key are stored bare. Returns ``ean`` unchanged when
    it carries no suffix.
    """
    return ean.split("_", maxsplit=1)[0] if "_" in ean else ean


def ean_with_delivery_point_suffix(ean: str) -> str:
    """
    Append the delivery-point suffix ENGIE's per-EAN endpoints expect.

    ENGIE delivery-point IDs observed in the wild are always
    ``{EAN}_ID1``. Multi-panel installations may expose ``_ID2``/
    ``_ID3`` but no service-points endpoint currently surfaces them;
    extend this mapping when a real multi-ID sample appears.
    """
    return f"{ean}{DELIVERY_POINT_SUFFIX}"
