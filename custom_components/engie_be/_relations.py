"""
Helpers for parsing ENGIE customer-account-relations responses.

Schema v5: one ``ConfigSubentry`` per active ``businessAgreement``, keyed
by the 12-digit BAN. The CAN is not stored.
"""

from __future__ import annotations

from typing import Any

from .const import (
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CONSUMPTION_ADDRESS,
    CONF_PREMISES_NUMBER,
)

# The BAN is the subentry identity and is set at creation, not backfilled.
RELATIONS_BACKFILLABLE_KEYS: tuple[str, ...] = (
    CONF_PREMISES_NUMBER,
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_CONSUMPTION_ADDRESS,
)


def extract_business_agreements(
    relations: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Flatten a customer-account-relations response into per-active-BAN dicts.

    Inactive agreements are skipped (prices/peaks return empty for them).
    Partial records missing a CAN or BAN are dropped without raising.
    """
    rows: list[dict[str, Any]] = []
    for item in relations.get("items", []):
        customer_account = item.get("customerAccount") or {}
        if not customer_account.get("customerAccountNumber"):
            continue
        holder_name = customer_account.get("name")
        for agreement in customer_account.get("businessAgreements") or []:
            if not agreement.get("active"):
                continue
            ban = agreement.get("businessAgreementNumber")
            if not ban:
                continue
            address = agreement.get("consumptionAddress") or {}
            rows.append(
                {
                    CONF_BUSINESS_AGREEMENT_NUMBER: ban,
                    CONF_PREMISES_NUMBER: address.get("premisesNumber"),
                    CONF_ACCOUNT_HOLDER_NAME: holder_name,
                    CONF_CONSUMPTION_ADDRESS: format_address(address),
                },
            )
    return rows


def find_agreement_for_ban(
    relations: dict[str, Any],
    ban: str,
) -> dict[str, Any] | None:
    """Return the flattened per-BAN row matching ``ban``, or ``None``."""
    if not ban:
        return None
    for row in extract_business_agreements(relations):
        if row.get(CONF_BUSINESS_AGREEMENT_NUMBER) == ban:
            return row
    return None


def subentry_title(account: dict[str, Any]) -> str:
    """Build a subentry title, falling back from address to holder name to BAN."""
    address = account.get(CONF_CONSUMPTION_ADDRESS)
    if isinstance(address, str) and address:
        return address
    holder = account.get(CONF_ACCOUNT_HOLDER_NAME)
    if isinstance(holder, str) and holder:
        return holder
    return str(account[CONF_BUSINESS_AGREEMENT_NUMBER])


def format_address(address: dict[str, Any]) -> str:
    """Format a consumption address as ``street houseNumber, postalCode city``."""
    if not address:
        return ""
    street = address.get("street") or ""
    house_number = address.get("houseNumber") or ""
    postal_code = address.get("postalCode") or ""
    city = address.get("city") or ""
    line1 = " ".join(part for part in (street, house_number) if part).strip()
    line2 = " ".join(part for part in (postal_code, city) if part).strip()
    return ", ".join(part for part in (line1, line2) if part)
