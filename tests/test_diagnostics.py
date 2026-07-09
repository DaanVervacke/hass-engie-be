"""Tests for the ENGIE Belgium diagnostics platform."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_HOLDER_NAME,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CONSUMPTION_ADDRESS,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.data import (
    EngieBeData,
    EngieBeSubentryData,
    EpexPayload,
    EpexSlot,
)
from custom_components.engie_be.diagnostics import (
    EAN_HASH_LENGTH,
    TITLE_HASH_LENGTH,
    TO_REDACT,
    _hash_ean,
    _redacted_title,
    _summarise_billing,
    _summarise_coordinator_data,
    _summarise_epex,
    _summarise_solar_surplus,
    async_get_config_entry_diagnostics,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

REDACTED_MARKER = "**REDACTED**"
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "prices_sample.json"
_TEST_SUBENTRY_TITLE = "Rue de la Loi 16, 1000 Brussels"
_TEST_HOLDER = "John Doe"


def _build_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a v5 entry with one business-agreement subentry and runtime data."""
    entry: MockConfigEntry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        title="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_ACCESS_TOKEN: "eyJfake.access.token",
            CONF_REFRESH_TOKEN: "v1.fake_refresh_token",
        },
        options={"update_interval": 60},
        unique_id="user_example_com",
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                title=_TEST_SUBENTRY_TITLE,
                unique_id="002200000999",
                data={
                    CONF_BUSINESS_AGREEMENT_NUMBER: "002200000999",
                    CONF_PREMISES_NUMBER: "5100009999",
                    CONF_ACCOUNT_HOLDER_NAME: _TEST_HOLDER,
                    CONF_CONSUMPTION_ADDRESS: _TEST_SUBENTRY_TITLE,
                },
            ),
        ],
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.data = json.loads(_FIXTURE_PATH.read_text())
    coordinator.last_update_success = True
    coordinator.update_interval = timedelta(minutes=60)

    epex_coordinator = MagicMock()
    epex_coordinator.data = None
    epex_coordinator.last_update_success = True
    epex_coordinator.update_interval = timedelta(hours=1)

    subentry_id = next(iter(entry.subentries))
    sub_data = EngieBeSubentryData(
        coordinator=coordinator,
        service_points={
            "541448820000000001_ID1": "ELECTRICITY",
            "541448820000000002_ID1": "GAS",
        },
        peaks_store=None,
    )
    runtime: EngieBeData = EngieBeData(
        client=MagicMock(),
        epex_coordinator=epex_coordinator,
        subentry_data={subentry_id: sub_data},
        authenticated=True,
        last_options={"update_interval": 60},
    )
    entry.runtime_data = runtime
    return entry


async def test_redacts_credentials_on_entry_data(hass: HomeAssistant) -> None:
    """All credential fields in TO_REDACT are replaced on entry.data."""
    entry = _build_entry(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    entry_data = diag["entry"]["data"]
    # The login-level credential fields all live on entry.data and must redact.
    for key in (CONF_USERNAME, CONF_PASSWORD, CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN):
        assert key in entry_data, f"Expected key {key!r} in entry.data"
        assert entry_data[key] == REDACTED_MARKER


async def test_redacts_id_token_if_persisted(hass: HomeAssistant) -> None:
    """A persisted OAuth id_token on entry.data is redacted from diagnostics."""
    entry = _build_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "id_token": "eyJfake.id.token"},
    )

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["entry"]["data"]["id_token"] == REDACTED_MARKER
    assert "eyJfake.id.token" not in json.dumps(diag)


async def test_redacts_pii_on_subentry_data(hass: HomeAssistant) -> None:
    """Subentry PII fields (customer/agreement/premises/holder/address) are redacted."""
    entry = _build_entry(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    subentries = diag["subentries"]
    assert len(subentries) == 1
    sub = next(iter(subentries.values()))
    sub_data = sub["data"]
    for key in (
        CONF_BUSINESS_AGREEMENT_NUMBER,
        CONF_PREMISES_NUMBER,
        CONF_ACCOUNT_HOLDER_NAME,
        CONF_CONSUMPTION_ADDRESS,
    ):
        assert key in sub_data, f"Expected key {key!r} in subentry.data"
        assert sub_data[key] == REDACTED_MARKER


async def test_to_redact_covers_every_credential_and_pii_key(
    hass: HomeAssistant,
) -> None:
    """TO_REDACT enumerates every key present in entry.data + subentry.data."""
    entry = _build_entry(hass)
    sensitive_keys: set[str] = set(entry.data.keys())
    for sub in entry.subentries.values():
        sensitive_keys.update(sub.data.keys())

    missing = sensitive_keys - TO_REDACT
    assert missing == set(), f"TO_REDACT is missing: {missing!r}"


async def test_payload_structure_and_ean_hashing(hass: HomeAssistant) -> None:
    """Top-level structure exposes only privacy-preserving summaries."""
    entry = _build_entry(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert set(diag.keys()) == {"entry", "runtime", "epex_coordinator", "subentries"}
    assert diag["entry"]["version"] == 5
    assert diag["entry"]["options"] == {"update_interval": 60}

    # Per-subentry coordinator + service points
    sub = next(iter(diag["subentries"].values()))
    assert sub["coordinator"]["last_update_success"] is True
    assert sub["coordinator"]["update_interval_seconds"] == 3600.0
    assert set(sub["service_points"].values()) == {"ELECTRICITY", "GAS"}
    for hashed_ean in sub["service_points"]:
        assert len(hashed_ean) == EAN_HASH_LENGTH
        assert all(c in "0123456789abcdef" for c in hashed_ean)

    data_summary = sub["coordinator"]["data_summary"]
    assert data_summary["item_count"] >= 1
    assert all(len(h) == EAN_HASH_LENGTH for h in data_summary["ean_hashes"])

    # Raw EANs and credentials must not appear anywhere in the serialised payload.
    serialised = json.dumps(diag)
    assert "541448820000000001_ID1" not in serialised
    assert "541448820000000002_ID1" not in serialised
    assert "hunter2" not in serialised
    assert "002200000999" not in serialised
    assert "v1.fake_refresh_token" not in serialised


async def test_subentry_and_entry_titles_are_redacted(hass: HomeAssistant) -> None:
    """Subentry and entry titles must be replaced with a redaction fingerprint."""
    entry = _build_entry(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    sub = next(iter(diag["subentries"].values()))
    sub_title = sub["title"]
    entry_title = diag["entry"]["title"]

    assert sub_title.startswith("**REDACTED")
    assert entry_title.startswith("**REDACTED")
    # Plain title text must never appear anywhere in the payload.
    serialised = json.dumps(diag)
    assert _TEST_SUBENTRY_TITLE not in serialised
    assert "user@example.com" not in serialised


def test_redacted_title_is_deterministic_and_short() -> None:
    """_redacted_title returns a stable fingerprint for the same input."""
    digest_a = _redacted_title("Some Address 1, 1000 Brussels")
    digest_b = _redacted_title("Some Address 1, 1000 Brussels")
    digest_c = _redacted_title("Other Address 9, 9999 Antwerp")
    assert digest_a == digest_b
    assert digest_a != digest_c
    # "**REDACTED:" + N hex + "**" = 12 + N characters.
    assert len(digest_a) == len("**REDACTED:") + TITLE_HASH_LENGTH + len("**")
    assert _redacted_title(None) == REDACTED_MARKER
    assert _redacted_title("") == REDACTED_MARKER


def test_hash_ean_is_deterministic_and_short() -> None:
    """_hash_ean returns a stable short hex digest for the same input."""
    ean = "541448820000000001_ID1"
    digest_a = _hash_ean(ean)
    digest_b = _hash_ean(ean)
    assert digest_a == digest_b
    assert len(digest_a) == EAN_HASH_LENGTH
    assert _hash_ean("different_ean") != digest_a


async def test_epex_coordinator_summary_present_when_no_payload(
    hass: HomeAssistant,
) -> None:
    """epex_coordinator section is always present, even with no cached payload."""
    entry = _build_entry(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["epex_coordinator"]["present"] is True
    assert diag["epex_coordinator"]["payload"] is None
    assert diag["epex_coordinator"]["last_update_success"] is True


def _build_entry_without_runtime(hass: HomeAssistant) -> MockConfigEntry:
    """Build a v5 entry without runtime_data attached (early-failure shape)."""
    entry: MockConfigEntry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        title="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_ACCESS_TOKEN: "stored-access",
            CONF_REFRESH_TOKEN: "stored-refresh",
        },
        options={"update_interval": 60},
        unique_id="user_example_com",
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                title=_TEST_SUBENTRY_TITLE,
                unique_id="002200000999",
                data={CONF_BUSINESS_AGREEMENT_NUMBER: "002200000999"},
            ),
        ],
    )
    entry.add_to_hass(hass)
    # Deliberately leave runtime_data absent.
    return entry


async def test_diagnostics_works_when_runtime_data_is_missing(
    hass: HomeAssistant,
) -> None:
    """Diagnostics must not raise when runtime_data is absent (e.g. failed setup)."""
    entry = _build_entry_without_runtime(hass)
    # MockConfigEntry initialises runtime_data to UNDEFINED; clear it explicitly.
    entry.runtime_data = None  # type: ignore[assignment]

    diag: dict[str, Any] = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["epex_coordinator"] == {"present": False}
    sub = next(iter(diag["subentries"].values()))
    assert sub["present"] is False


# ---------------------------------------------------------------------------
# Dynamic-detection diagnostics (b6)
#
# When ``_async_populate_dynamic_flags`` succeeds at setup,
# diagnostics must surface the override value, the source label
# (``"contract"`` vs ``"fallback"``), and the per-EAN energyProduct
# mapping with EANs hashed for privacy. When the contracts call
# fails (override stays None), diagnostics must report the
# fallback source so support bundles distinguish "we trusted the
# contracts API" from "we degraded to the legacy heuristic".
# ---------------------------------------------------------------------------


def _build_entry_with_contracts_payload(
    hass: HomeAssistant,
    *,
    is_dynamic_override: bool | None,
    energy_contracts_payload: dict[str, Any] | None,
) -> MockConfigEntry:
    """Build an entry whose subentry carries explicit dynamic-detection state."""
    entry = _build_entry(hass)
    sub_id = next(iter(entry.subentries))
    sub_data = entry.runtime_data.subentry_data[sub_id]
    sub_data.is_dynamic_override = is_dynamic_override
    sub_data.energy_contracts_payload = energy_contracts_payload
    return entry


async def test_diagnostics_reports_contract_source_when_override_set(
    hass: HomeAssistant,
) -> None:
    """An override populated from contracts must report ``is_dynamic_source='contract'``."""  # noqa: E501
    entry = _build_entry_with_contracts_payload(
        hass,
        is_dynamic_override=True,
        energy_contracts_payload={
            "items": [
                {
                    "servicePointNumber": "541448820000000001_ID1",
                    "division": "ELECTRICITY",
                    "status": "ACTIVE",
                    "productConfiguration": {"energyProduct": "DYNAMIC"},
                },
            ],
        },
    )

    diag = await async_get_config_entry_diagnostics(hass, entry)
    sub = next(iter(diag["subentries"].values()))

    assert sub["is_dynamic_override"] is True
    assert sub["is_dynamic_source"] == "contract"
    # EAN must be hashed; product code preserved verbatim.
    assert sub["energy_products"]
    for hashed_ean, product in sub["energy_products"].items():
        assert len(hashed_ean) == EAN_HASH_LENGTH
        assert all(c in "0123456789abcdef" for c in hashed_ean)
        assert product == "DYNAMIC"


async def test_diagnostics_reports_fallback_source_when_override_none(
    hass: HomeAssistant,
) -> None:
    """A None override must surface as ``is_dynamic_source='fallback'``."""
    entry = _build_entry_with_contracts_payload(
        hass,
        is_dynamic_override=None,
        energy_contracts_payload=None,
    )

    diag = await async_get_config_entry_diagnostics(hass, entry)
    sub = next(iter(diag["subentries"].values()))

    assert sub["is_dynamic_override"] is None
    assert sub["is_dynamic_source"] == "fallback"
    assert sub["energy_products"] == {}


async def test_diagnostics_does_not_leak_raw_eans_from_contracts(
    hass: HomeAssistant,
) -> None:
    """Raw EANs from the contracts payload must never appear serialised."""
    entry = _build_entry_with_contracts_payload(
        hass,
        is_dynamic_override=True,
        energy_contracts_payload={
            "items": [
                {
                    "servicePointNumber": "541448820000000001_ID1",
                    "division": "ELECTRICITY",
                    "status": "ACTIVE",
                    "productConfiguration": {"energyProduct": "DYNAMIC"},
                },
            ],
        },
    )

    diag = await async_get_config_entry_diagnostics(hass, entry)
    serialised = json.dumps(diag)
    assert "541448820000000001_ID1" not in serialised


# ---------------------------------------------------------------------------
# Private summariser branches
#
# These exercise the small helper functions directly so the rarer
# branches (non-dict coordinator data, peaks metadata, a populated
# EPEX payload) are covered without standing up a full coordinator.
# ---------------------------------------------------------------------------


def _make_coord(data: object) -> MagicMock:
    """Build a minimal mock coordinator for _summarise_coordinator_data tests."""
    coord = MagicMock()
    coord.data = data
    return coord


def test_summarise_coordinator_data_non_dict_returns_raw_type() -> None:
    """Non-dict coordinator data degrades to a type tag instead of raising."""
    assert _summarise_coordinator_data(_make_coord(None)) == {"raw_type": "NoneType"}
    assert _summarise_coordinator_data(_make_coord([1, 2, 3])) == {"raw_type": "list"}


def test_summarise_coordinator_data_includes_peaks_metadata() -> None:
    """A present peaks wrapper with int year/month yields a ``YYYY-MM`` label."""
    summary = _summarise_coordinator_data(
        _make_coord(
            {
                "items": [{"ean": "541448820000000001_ID1"}],
                "peaks": {
                    "data": {"peak": 3.5},
                    "year": 2026,
                    "month": 6,
                    "is_fallback": True,
                },
            }
        )
    )

    assert summary["item_count"] == 1
    assert summary["ean_hashes"] == [_hash_ean("541448820000000001_ID1")]
    assert summary["peaks_present"] is True
    assert summary["peaks_month"] == "2026-06"
    assert summary["peaks_is_fallback"] is True


def test_summarise_coordinator_data_peaks_present_without_valid_month() -> None:
    """A present peaks wrapper lacking int year/month reports a None month."""
    summary = _summarise_coordinator_data(
        _make_coord({"peaks": {"data": {"peak": 1.0}}})
    )

    assert summary["peaks_present"] is True
    assert summary["peaks_month"] is None
    assert summary["peaks_is_fallback"] is False


def test_summarise_epex_with_slots_returns_full_summary() -> None:
    """A populated EpexPayload is summarised with first/last slot boundaries."""
    start = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)
    slot_a = EpexSlot(
        start=start,
        end=start + timedelta(minutes=15),
        value_eur_per_kwh=0.05,
    )
    slot_b = EpexSlot(
        start=start + timedelta(minutes=15),
        end=start + timedelta(minutes=30),
        value_eur_per_kwh=0.06,
    )
    publication = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    payload = EpexPayload(
        slots=(slot_a, slot_b),
        publication_time=publication,
        market_date="2026-06-08",
    )

    summary = _summarise_epex(payload)

    assert summary == {
        "slot_count": 2,
        "slot_duration_minutes": 60,
        "first_slot_start": slot_a.start.isoformat(),
        "last_slot_end": slot_b.end.isoformat(),
        "publication_time": publication.isoformat(),
        "market_date": "2026-06-08",
    }


async def test_diagnostics_skips_non_business_agreement_subentries(
    hass: HomeAssistant,
) -> None:
    """Subentries whose type is not a business agreement are excluded entirely."""
    entry: MockConfigEntry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        title="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_ACCESS_TOKEN: "eyJfake.access.token",
            CONF_REFRESH_TOKEN: "v1.fake_refresh_token",
        },
        options={"update_interval": 60},
        unique_id="user_example_com",
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                title=_TEST_SUBENTRY_TITLE,
                unique_id="002200000999",
                data={CONF_BUSINESS_AGREEMENT_NUMBER: "002200000999"},
            ),
            ConfigSubentryData(
                subentry_type="some_other_type",
                title="Foreign Subentry",
                unique_id="foreign-1",
                data={"foo": "bar"},
            ),
        ],
    )
    entry.add_to_hass(hass)
    entry.runtime_data = None  # type: ignore[assignment]

    diag = await async_get_config_entry_diagnostics(hass, entry)

    foreign_ids = [
        sid
        for sid, sub in entry.subentries.items()
        if sub.subentry_type != SUBENTRY_TYPE_BUSINESS_AGREEMENT
    ]
    assert foreign_ids, "fixture must include a foreign subentry"
    for sid in foreign_ids:
        assert sid not in diag["subentries"]
    # The single business-agreement subentry is still summarised.
    assert len(diag["subentries"]) == 1


# ---------------------------------------------------------------------------
# Solar-surplus summariser
# ---------------------------------------------------------------------------


def _solar_coord_from_wrapper(wrapper: object) -> MagicMock:
    """Build a mock coordinator whose solar_surplus wrapper is the given object."""
    coord = MagicMock()
    coord.data = {"solar_surplus": wrapper}
    return coord


def test_summarise_solar_surplus_returns_none_for_missing_wrapper() -> None:
    """No wrapper -> None (so top-level key is present but empty)."""
    # coordinator.data is not a dict - solar_surplus_payload returns None
    coord_no_data = MagicMock()
    coord_no_data.data = None
    assert _summarise_solar_surplus(coord_no_data) is None
    coord_str_data = MagicMock()
    coord_str_data.data = "not a dict"
    assert _summarise_solar_surplus(coord_str_data) is None
    # solar_surplus key present but wrapper is empty dict - inner "data" missing
    assert _summarise_solar_surplus(_solar_coord_from_wrapper({})) is None
    # solar_surplus wrapper present but inner "data" is not a dict
    assert (
        _summarise_solar_surplus(_solar_coord_from_wrapper({"data": "not a dict"}))
        is None
    )


def test_summarise_solar_surplus_hashes_eans_and_counts_slots() -> None:
    """Wrapper with real payload yields hashed EAN keys and shape metadata."""
    ean = "541448820070414088"
    wrapper = {
        "data": {
            ean: [
                {
                    "forecastDate": "2026-07-08",
                    "level": "HIGH_SURPLUS",
                    "details": [
                        {
                            "startTime": "2026-07-08T10:00:00+02:00",
                            "value": 1.5,
                            "level": "LOW_SURPLUS",
                        },
                        {
                            "startTime": "2026-07-08T11:00:00+02:00",
                            "value": 3.2,
                            "level": "HIGH_SURPLUS",
                        },
                    ],
                },
                {
                    "forecastDate": "2026-07-09",
                    "level": "LOW_SURPLUS",
                    "details": [
                        {
                            "startTime": "2026-07-09T10:00:00+02:00",
                            "value": 2.0,
                            "level": "LOW_SURPLUS",
                        },
                    ],
                },
            ],
        },
        "fetched_at": "2026-07-08T10:00:00+00:00",
    }
    result = _summarise_solar_surplus(_solar_coord_from_wrapper(wrapper))
    assert result is not None
    assert result["ean_count"] == 1
    assert result["fetched_at"] == "2026-07-08T10:00:00+00:00"
    assert _hash_ean(ean) in result["per_ean"]
    per_ean_entry = result["per_ean"][_hash_ean(ean)]
    assert per_ean_entry["day_count"] == 2
    assert per_ean_entry["slot_count"] == 3
    assert per_ean_entry["levels_present"] == ["HIGH_SURPLUS", "LOW_SURPLUS"]
    # Raw EAN must NOT appear anywhere in the output.
    assert ean not in json.dumps(result)


def test_summarise_solar_surplus_survives_malformed_shape() -> None:
    """Non-dict days, non-list details are silently skipped."""
    wrapper = {
        "data": {
            "5414ZZ": [
                "not a dict",
                {"forecastDate": "2026-07-08", "details": "not a list"},
                {"forecastDate": "2026-07-09", "details": ["not a dict slot"]},
            ],
        },
        "fetched_at": None,
    }
    result = _summarise_solar_surplus(_solar_coord_from_wrapper(wrapper))
    assert result is not None
    assert result["ean_count"] == 1
    assert result["fetched_at"] is None
    entry = next(iter(result["per_ean"].values()))
    assert entry["day_count"] == 2  # the two dict-shaped days counted
    assert entry["slot_count"] == 0  # neither day yielded slot dicts


# ---------------------------------------------------------------------------
# Billing summariser
# ---------------------------------------------------------------------------

_BILLING_FIXTURES = Path(__file__).parent / "fixtures"


def _billing_coord(fixture_name: str) -> MagicMock:
    """Load a billing fixture and wrap it in a mock coordinator."""
    payload = json.loads(
        (_BILLING_FIXTURES / f"billing_{fixture_name}.json").read_text()
    )
    wrapper = {"data": payload, "fetched_at": "2026-07-20T10:00:00+00:00"}
    coord = MagicMock()
    coord.data = {"billing": wrapper}
    return coord


def _billing_coord_from_wrapper(wrapper: object) -> MagicMock:
    """Build a mock coordinator whose billing wrapper is the given object."""
    coord = MagicMock()
    coord.data = {"billing": wrapper}
    return coord


def test_summarise_billing_returns_none_when_no_billing_key() -> None:
    """_summarise_billing returns None when the coordinator has no billing wrapper."""
    coord = MagicMock()
    coord.data = {}
    assert _summarise_billing(coord) is None


def test_summarise_billing_returns_none_when_billing_not_dict() -> None:
    """_summarise_billing returns None when the billing wrapper is not a dict."""
    coord = MagicMock()
    coord.data = {"billing": None}
    assert _summarise_billing(coord) is None
    coord2 = MagicMock()
    coord2.data = {"billing": "not a dict"}
    assert _summarise_billing(coord2) is None


def test_summarise_billing_clear_fixture() -> None:
    """_summarise_billing on cleared fixture reports CLEAR status, 0 transactions."""
    result = _summarise_billing(_billing_coord("cleared"))
    assert result is not None
    assert result["has_data"] is True
    assert result["status"] == "CLEAR"
    assert result["transaction_count"] == 0
    assert result["fetched_at"] == "2026-07-20T10:00:00+00:00"


def test_summarise_billing_open_debit_fixture() -> None:
    """_summarise_billing on open_debit counts transactions and reports no overdue."""
    result = _summarise_billing(_billing_coord("open_debit"))
    assert result is not None
    assert result["has_data"] is True
    assert result["status"] == "OPEN_DEBIT"
    assert result["transaction_count"] == 1


def test_summarise_billing_does_not_leak_amounts_or_communication() -> None:
    """_summarise_billing never emits raw amounts or invoiceStructuredCommunication."""
    wrapper = {
        "data": {
            "status": "OPEN_OVERDUE",
            "overview": {"totalAmount": 50.0, "openAmount": 50.0, "dueAmount": 50.0},
            "details": {
                "invoiceStructuredCommunication": "+++111/2222/33333+++",
                "financialTransactions": [
                    {"openAmount": 50.0, "dueAmount": 50.0, "dueDate": "2026-06-01"},
                ],
            },
        },
        "fetched_at": "2026-07-20T10:00:00+00:00",
    }
    result = _summarise_billing(_billing_coord_from_wrapper(wrapper))
    serialised = json.dumps(result)
    # Raw field names and amounts must not appear in diagnostics output.
    for forbidden in ("openAmount", "dueAmount", "totalAmount"):
        assert forbidden not in serialised
    # invoiceStructuredCommunication must not appear
    assert "invoiceStructuredCommunication" not in serialised
    # Raw numeric amounts from the wrapper (50.0) must not appear
    assert "50.0" not in serialised


def test_summarise_billing_missing_data_returns_empty_shell() -> None:
    """A wrapper with no inner 'data' dict returns a has_data=False shell."""
    wrapper = {"fetched_at": "2026-07-20T10:00:00+00:00"}
    result = _summarise_billing(_billing_coord_from_wrapper(wrapper))
    assert result is not None
    assert result["has_data"] is False
    assert result["status"] is None
    assert result["transaction_count"] == 0
