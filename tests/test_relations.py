"""
Tests for the customer-account-relations payload parsers.

Covers :func:`extract_business_agreements` (used by the picker config
flow to fan out one row per active business agreement) and
:func:`find_agreement_for_ban` (used by the coordinator backfill to
re-hydrate a stored BAN's address / holder name after a relations
refresh).
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.engie_be._relations import (
    extract_business_agreements,
    find_agreement_for_ban,
    subentry_title,
)
from custom_components.engie_be.const import (
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CONSUMPTION_ADDRESS,
    CONF_PREMISES_NUMBER,
)

_RELATIONS_FIXTURE = (
    Path(__file__).parent / "fixtures" / "customer_account_relations_sample.json"
)
_MULTI_BAN_FIXTURE = (
    Path(__file__).parent / "fixtures" / "relations_multi_ban_single_can.json"
)


def _load_relations() -> dict:
    """Load the bundled customer-account-relations sample fixture."""
    return json.loads(_RELATIONS_FIXTURE.read_text())


def _load_multi_ban_relations() -> dict:
    """Load the multi-BAN single-CAN fixture (regression for the multi-address bug)."""
    return json.loads(_MULTI_BAN_FIXTURE.read_text())


# ---------------------------------------------------------------------------
# extract_business_agreements: per-active-BAN fan-out
# ---------------------------------------------------------------------------
#
# v5 stores one ``ConfigSubentry`` per active business agreement. The
# returned dict shape matches what the subentry's ``data`` will hold:
# BAN as the sole identifier (no separate CAN), plus premises number,
# account-holder name (carried from the parent customerAccount), and a
# pre-formatted consumption address used as the picker label.


def test_extract_business_agreements_fans_out_per_active_ban() -> None:
    """One active BAN -> one entry. Inactive BANs are skipped."""
    agreements = extract_business_agreements(_load_multi_ban_relations())
    bans = [a[CONF_BUSINESS_AGREEMENT_NUMBER] for a in agreements]
    # The fixture has two active BANs and one inactive BAN under a single
    # CAN. Only the actives surface.
    assert bans == ["002200005001", "002200005002"]


def test_extract_business_agreements_emits_v5_shape() -> None:
    """Each entry carries exactly the fields a v5 subentry will store."""
    agreements = extract_business_agreements(_load_multi_ban_relations())
    expected_keys = {
        CONF_BUSINESS_AGREEMENT_NUMBER,
        CONF_PREMISES_NUMBER,
        CONF_ACCOUNT_HOLDER_NAME,
        CONF_CONSUMPTION_ADDRESS,
    }
    for entry in agreements:
        assert set(entry.keys()) == expected_keys
        # BAN is always 12 digits in production payloads.
        assert len(entry[CONF_BUSINESS_AGREEMENT_NUMBER]) == 12


def test_extract_business_agreements_surfaces_per_ban_address() -> None:
    """Each BAN entry carries its own consumptionAddress, not the CAN's first."""
    agreements = extract_business_agreements(_load_multi_ban_relations())
    by_ban = {a[CONF_BUSINESS_AGREEMENT_NUMBER]: a for a in agreements}

    first = by_ban["002200005001"]
    assert "FIRSTSTRAAT 1" in first[CONF_CONSUMPTION_ADDRESS]
    assert "1000 BRUSSELS" in first[CONF_CONSUMPTION_ADDRESS]
    assert first[CONF_PREMISES_NUMBER] == "5100005001"

    second = by_ban["002200005002"]
    assert "SECONDLAAN 2" in second[CONF_CONSUMPTION_ADDRESS]
    assert "2000 ANTWERP" in second[CONF_CONSUMPTION_ADDRESS]
    assert second[CONF_PREMISES_NUMBER] == "5100005002"


def test_extract_business_agreements_keeps_account_holder_name_from_can() -> None:
    """The customerAccount's ``name`` propagates to every BAN entry."""
    agreements = extract_business_agreements(_load_multi_ban_relations())
    for entry in agreements:
        assert entry[CONF_ACCOUNT_HOLDER_NAME] == "Multi BAN Customer"


def test_extract_business_agreements_handles_single_active_ban() -> None:
    """The bundled single-CAN fixture produces one entry per active BAN."""
    agreements = extract_business_agreements(_load_relations())
    bans = sorted(a[CONF_BUSINESS_AGREEMENT_NUMBER] for a in agreements)
    assert bans == ["002200000001", "002200000002"]


def test_extract_business_agreements_skips_accounts_without_can() -> None:
    """Customer accounts missing a CAN are dropped entirely."""
    payload = {
        "items": [
            {
                "customerAccount": {
                    "customerAccountNumber": None,
                    "businessAgreements": [
                        {"businessAgreementNumber": "002200009999", "active": True},
                    ],
                }
            },
        ],
    }
    assert extract_business_agreements(payload) == []


def test_extract_business_agreements_skips_bans_without_number() -> None:
    """Active BANs missing their own number are dropped."""
    payload = {
        "items": [
            {
                "customerAccount": {
                    "customerAccountNumber": "1500000050",
                    "name": "Edge Case",
                    "businessAgreements": [
                        {"businessAgreementNumber": "", "active": True},
                        {"businessAgreementNumber": None, "active": True},
                        {"businessAgreementNumber": "002200005050", "active": True},
                    ],
                }
            },
        ],
    }
    agreements = extract_business_agreements(payload)
    assert [a[CONF_BUSINESS_AGREEMENT_NUMBER] for a in agreements] == [
        "002200005050",
    ]


def test_extract_business_agreements_handles_account_with_no_active_ban() -> None:
    """
    A customer account whose every BAN is inactive yields no entries.

    Rationale: an inactive BAN cannot be polled (the prices/peaks
    endpoints return empty) so creating a subentry for it would surface
    a permanently-stale device. If ENGIE later re-activates the BAN the
    user can re-run the picker.
    """
    payload = {
        "items": [
            {
                "customerAccount": {
                    "customerAccountNumber": "1500000060",
                    "name": "All Inactive",
                    "businessAgreements": [
                        {"businessAgreementNumber": "002200006001", "active": False},
                        {"businessAgreementNumber": "002200006002", "active": False},
                    ],
                }
            },
        ],
    }
    assert extract_business_agreements(payload) == []


def test_extract_business_agreements_handles_empty_payload() -> None:
    """Empty/items-less payload yields an empty list, not a crash."""
    assert extract_business_agreements({}) == []
    assert extract_business_agreements({"items": []}) == []


def test_extract_business_agreements_yields_titles_that_disambiguate() -> None:
    """
    Each entry must carry an address that ``subentry_title`` will render.

    The two active BANs share a CAN, account-holder name, and language;
    the only field that disambiguates them in the picker UI is the
    consumptionAddress. If we ever flatten that to the CAN's first
    address the user will see two identical rows and pick blind.
    """
    agreements = extract_business_agreements(_load_multi_ban_relations())
    addresses = {a[CONF_CONSUMPTION_ADDRESS] for a in agreements}
    assert len(addresses) == len(agreements)  # all unique


# ---------------------------------------------------------------------------
# find_agreement_for_ban: coordinator backfill
# ---------------------------------------------------------------------------
#
# Stored subentries carry a BAN; the coordinator re-hydrates address /
# holder name on every relations refresh by looking up that BAN in the
# fresh payload. The helper is a thin filter over
# ``extract_business_agreements``.


def test_find_agreement_for_ban_returns_matching_row() -> None:
    """A known active BAN resolves to its flattened row."""
    match = find_agreement_for_ban(_load_relations(), "002200000001")
    assert match is not None
    assert match[CONF_BUSINESS_AGREEMENT_NUMBER] == "002200000001"
    assert match[CONF_ACCOUNT_HOLDER_NAME] == "Test Customer One"


def test_find_agreement_for_ban_returns_none_for_unknown_ban() -> None:
    """Unknown BANs yield ``None``, not a crash."""
    assert find_agreement_for_ban(_load_relations(), "002200999999") is None


def test_find_agreement_for_ban_returns_none_for_empty_ban() -> None:
    """Empty stored BAN short-circuits to ``None``."""
    assert find_agreement_for_ban(_load_relations(), "") is None


def test_find_agreement_for_ban_skips_inactive() -> None:
    """An inactive BAN is invisible to backfill (extract drops it first)."""
    # The multi-BAN fixture has one inactive BAN, "002200005003".
    assert find_agreement_for_ban(_load_multi_ban_relations(), "002200005003") is None


def test_find_agreement_for_ban_handles_empty_payload() -> None:
    """Empty payload yields ``None`` cleanly."""
    assert find_agreement_for_ban({}, "002200000001") is None
    assert find_agreement_for_ban({"items": []}, "002200000001") is None


# ---------------------------------------------------------------------------
# subentry_title: address -> holder -> BAN fallback chain
# ---------------------------------------------------------------------------
#
# The picker / device title prefers a human-readable address, falls back
# to the account-holder name, and finally to the BAN so a subentry always
# renders something the user can recognise.


def test_subentry_title_prefers_consumption_address() -> None:
    """When an address is present it wins over holder and BAN."""
    account = {
        CONF_CONSUMPTION_ADDRESS: "FIRSTSTRAAT 1, 1000 BRUSSELS",
        CONF_ACCOUNT_HOLDER_NAME: "Jane Doe",
        CONF_BUSINESS_AGREEMENT_NUMBER: "002200000001",
    }
    assert subentry_title(account) == "FIRSTSTRAAT 1, 1000 BRUSSELS"


def test_subentry_title_falls_back_to_holder_when_address_blank() -> None:
    """A blank/absent address falls back to the account-holder name."""
    account = {
        CONF_CONSUMPTION_ADDRESS: "",
        CONF_ACCOUNT_HOLDER_NAME: "Jane Doe",
        CONF_BUSINESS_AGREEMENT_NUMBER: "002200000001",
    }
    assert subentry_title(account) == "Jane Doe"


def test_subentry_title_falls_back_to_ban_when_address_and_holder_blank() -> None:
    """Address and holder both blank fall through to the BAN."""
    account = {
        CONF_CONSUMPTION_ADDRESS: None,
        CONF_ACCOUNT_HOLDER_NAME: "",
        CONF_BUSINESS_AGREEMENT_NUMBER: "002200000001",
    }
    assert subentry_title(account) == "002200000001"
