"""
Tests for the customer-account-relations payload parsers.

Covers :func:`extract_accounts` (used by the picker config flow) and
:func:`find_account_for_customer_number` (used by both the migration
and the coordinator backfill to repair legacy v2 entries that stored a
``businessAgreementNumber`` rather than a ``customerAccountNumber``).
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.engie_be._relations import (
    extract_accounts,
    find_account_for_customer_number,
)
from custom_components.engie_be.const import (
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CONSUMPTION_ADDRESS,
    CONF_CUSTOMER_NUMBER,
    CONF_PREMISES_NUMBER,
)

_RELATIONS_FIXTURE = (
    Path(__file__).parent / "fixtures" / "customer_account_relations_sample.json"
)


def _load_relations() -> dict:
    """Load the bundled customer-account-relations sample fixture."""
    return json.loads(_RELATIONS_FIXTURE.read_text())


def test_extract_accounts_flattens_each_customer_account() -> None:
    """``extract_accounts`` returns one dict per customerAccount item."""
    accounts = extract_accounts(_load_relations())
    numbers = [account[CONF_CUSTOMER_NUMBER] for account in accounts]
    assert "1500000001" in numbers
    assert all(account.get(CONF_BUSINESS_AGREEMENT_NUMBER) for account in accounts)


def test_extract_accounts_picks_active_agreement_address() -> None:
    """The active business agreement's consumption address is surfaced."""
    accounts = extract_accounts(_load_relations())
    first = next(a for a in accounts if a[CONF_CUSTOMER_NUMBER] == "1500000001")
    assert first[CONF_BUSINESS_AGREEMENT_NUMBER] == "002200000001"
    assert first[CONF_PREMISES_NUMBER] == "5100000001"
    assert first[CONF_ACCOUNT_HOLDER_NAME] == "Test Customer One"
    assert "TESTSTRAAT 1" in first[CONF_CONSUMPTION_ADDRESS]
    assert "1000 BRUSSELS" in first[CONF_CONSUMPTION_ADDRESS]


def test_find_account_matches_by_customer_account_number() -> None:
    """Stored customerAccountNumber resolves to the same flattened record."""
    match = find_account_for_customer_number(_load_relations(), "1500000001")
    assert match is not None
    assert match[CONF_CUSTOMER_NUMBER] == "1500000001"
    assert match[CONF_BUSINESS_AGREEMENT_NUMBER] == "002200000001"


def test_find_account_matches_by_business_agreement_number() -> None:
    """
    Legacy v2 entries stored a businessAgreementNumber as customer_number.

    The matcher must walk every account's ``businessAgreements`` array
    and surface the owning customerAccount so backfill can recover the
    address and holder name without forcing the user to re-pair.
    """
    match = find_account_for_customer_number(_load_relations(), "002200000001")
    assert match is not None
    # The returned record reflects the canonical customerAccountNumber from
    # the API, not the originally-stored business agreement number. Callers
    # that want to keep the stored value must merge explicitly.
    assert match[CONF_CUSTOMER_NUMBER] == "1500000001"
    assert match[CONF_BUSINESS_AGREEMENT_NUMBER] == "002200000001"
    assert match[CONF_ACCOUNT_HOLDER_NAME] == "Test Customer One"


def test_find_account_returns_none_when_no_match() -> None:
    """Unknown identifiers must yield ``None``, not raise."""
    assert find_account_for_customer_number(_load_relations(), "9999999999") is None


def test_find_account_returns_none_for_empty_input() -> None:
    """Empty stored number short-circuits to ``None`` without walking the payload."""
    assert find_account_for_customer_number(_load_relations(), "") is None


def test_find_account_handles_empty_payload() -> None:
    """Empty/items-less payload must yield ``None`` cleanly."""
    assert find_account_for_customer_number({}, "1500000001") is None
    assert find_account_for_customer_number({"items": []}, "1500000001") is None
