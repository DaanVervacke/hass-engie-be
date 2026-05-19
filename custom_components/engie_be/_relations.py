"""
Helpers for parsing ENGIE customer-account-relations responses.

Kept in a small module of its own so the config flow (which uses it
during the picker) and the coordinator (which uses it to backfill
missing subentry fields on first refresh) can share the parsing logic
without ``coordinator`` having to import ``config_flow``.

Schema (v5)
-----------

One ``ConfigSubentry`` per *active* ``businessAgreement``. The
12-digit BAN is the canonical identifier every downstream endpoint
keys on (prices, peaks, contracts, service-points); the CAN is no
longer stored. :func:`extract_business_agreements` fans the relations
response out to one row per active BAN; the picker dedups on BAN
alone.
"""

from __future__ import annotations

from typing import Any

from .const import (
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CONSUMPTION_ADDRESS,
    CONF_PREMISES_NUMBER,
)

# The set of subentry-data keys that the relations endpoint can
# populate during runtime backfill. The BAN itself is not included:
# it is the subentry's identity and is set at creation time.
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

    Walks every ``customerAccount`` in the payload and emits one dict
    for every *active* ``businessAgreement`` underneath it. The
    returned dict shape matches what a ``ConfigSubentry`` for this
    agreement would store.

    Inactive business agreements are skipped entirely: they cannot be
    polled (prices/peaks return empty), and surfacing them would create
    permanently-stale devices. If ENGIE later re-activates a BAN the
    user can re-run the picker.

    Customer accounts whose ``customerAccountNumber`` is missing, and
    BANs whose ``businessAgreementNumber`` is missing, are dropped
    without raising -- relations responses occasionally arrive with
    partial records during partner-side migrations.
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
    """
    Find the per-BAN row in a relations response matching ``ban``.

    Returns the same flattened dict shape as
    :func:`extract_business_agreements` for the matching active BAN, or
    ``None`` when no match is found. Used by the coordinator's one-shot
    backfill to fill in display fields for a subentry that was created
    without a complete relations payload.
    """
    if not ban:
        return None
    for row in extract_business_agreements(relations):
        if row.get(CONF_BUSINESS_AGREEMENT_NUMBER) == ban:
            return row
    return None


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

    Falls back from address to account holder name to BAN so the title
    always renders something useful.
    """
    address = account.get(CONF_CONSUMPTION_ADDRESS)
    if address:
        return address
    holder = account.get(CONF_ACCOUNT_HOLDER_NAME)
    if holder:
        return holder
    return account[CONF_BUSINESS_AGREEMENT_NUMBER]


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
