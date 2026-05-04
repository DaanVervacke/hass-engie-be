"""
Helpers for parsing ENGIE customer-account-relations responses.

Kept in a small module of its own so the config flow (which uses it
during initial setup) and the coordinator (which uses it to backfill
missing subentry fields on first refresh) can share the parsing logic
without ``coordinator`` having to import ``config_flow``.
"""

from __future__ import annotations

from typing import Any

from .const import (
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CONSUMPTION_ADDRESS,
    CONF_CUSTOMER_NUMBER,
    CONF_PREMISES_NUMBER,
)

# The set of subentry-data keys that the relations endpoint can
# populate. The customer number itself is not included: it is the
# subentry's identity and is set at creation time.
RELATIONS_BACKFILLABLE_KEYS: tuple[str, ...] = (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_PREMISES_NUMBER,
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_CONSUMPTION_ADDRESS,
)


def extract_accounts(relations: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Flatten a customer-account-relations response into per-account dicts.

    Each returned dict carries the subset of fields stored in the
    corresponding ``ConfigSubentry``. Inactive business agreements are
    skipped; if no active agreement is present the account is still
    surfaced so the user can pick it (the address fields stay empty).
    """
    accounts: list[dict[str, Any]] = []
    for item in relations.get("items", []):
        customer_account = item.get("customerAccount") or {}
        customer_number = customer_account.get("customerAccountNumber")
        if not customer_number:
            continue

        agreement = pick_active_agreement(customer_account.get("businessAgreements"))
        address = (agreement or {}).get("consumptionAddress") or {}

        accounts.append(
            {
                CONF_CUSTOMER_NUMBER: customer_number,
                CONF_BUSINESS_AGREEMENT_NUMBER: (agreement or {}).get(
                    "businessAgreementNumber",
                ),
                CONF_PREMISES_NUMBER: address.get("premisesNumber"),
                CONF_ACCOUNT_HOLDER_NAME: customer_account.get("name"),
                CONF_CONSUMPTION_ADDRESS: format_address(address),
            },
        )
    return accounts


def pick_active_agreement(
    agreements: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Return the first active business agreement, or the first one available."""
    if not agreements:
        return None
    for agreement in agreements:
        if agreement.get("active"):
            return agreement
    return agreements[0]


def subentry_title(account: dict[str, Any]) -> str:
    """
    Build a user-friendly subentry title.

    Falls back from address to account holder name to customer number so the
    title always renders something useful.
    """
    address = account.get(CONF_CONSUMPTION_ADDRESS)
    if address:
        return address
    holder = account.get(CONF_ACCOUNT_HOLDER_NAME)
    if holder:
        return holder
    return account[CONF_CUSTOMER_NUMBER]


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
