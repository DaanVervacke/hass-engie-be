"""Tests for the ENGIE Belgium diagnostics platform."""

from __future__ import annotations

import json
from datetime import timedelta
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
    CONF_CLIENT_ID,
    CONF_CONSUMPTION_ADDRESS,
    CONF_CUSTOMER_NUMBER,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
)
from custom_components.engie_be.data import EngieBeData, EngieBeSubentryData
from custom_components.engie_be.diagnostics import (
    EAN_HASH_LENGTH,
    TITLE_HASH_LENGTH,
    TO_REDACT,
    _hash_ean,
    _redacted_title,
    async_get_config_entry_diagnostics,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

REDACTED_MARKER = "**REDACTED**"
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "prices_sample.json"
_TEST_SUBENTRY_TITLE = "Rue de la Loi 16, 1000 Brussels"
_TEST_HOLDER = "John Doe"


def _build_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a v3 MockConfigEntry with one customer subentry + runtime data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        title="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "eyJfake.access.token",
            CONF_REFRESH_TOKEN: "v1.fake_refresh_token",
        },
        options={"update_interval": 60},
        unique_id="user_example_com",
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
                title=_TEST_SUBENTRY_TITLE,
                unique_id="000000000000",
                data={
                    CONF_CUSTOMER_NUMBER: "000000000000",
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


async def test_redacts_pii_on_subentry_data(hass: HomeAssistant) -> None:
    """Subentry PII fields (customer/agreement/premises/holder/address) are redacted."""
    entry = _build_entry(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    subentries = diag["subentries"]
    assert len(subentries) == 1
    sub = next(iter(subentries.values()))
    sub_data = sub["data"]
    for key in (
        CONF_CUSTOMER_NUMBER,
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

    # CONF_CLIENT_ID is benign (a constant identifier) but already in TO_REDACT;
    # every other key on entry.data / subentry.data must also be redacted.
    missing = sensitive_keys - TO_REDACT
    assert missing == set(), f"TO_REDACT is missing: {missing!r}"


async def test_payload_structure_and_ean_hashing(hass: HomeAssistant) -> None:
    """Top-level structure exposes only privacy-preserving summaries."""
    entry = _build_entry(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert set(diag.keys()) == {"entry", "runtime", "epex_coordinator", "subentries"}
    assert diag["entry"]["version"] == 3
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
    assert "000000000000" not in serialised
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
    """Build a v3 entry without runtime_data attached (early-failure shape)."""
    entry: MockConfigEntry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        title="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "stored-access",
            CONF_REFRESH_TOKEN: "stored-refresh",
        },
        options={"update_interval": 60},
        unique_id="user_example_com",
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
                title=_TEST_SUBENTRY_TITLE,
                unique_id="000000000000",
                data={CONF_CUSTOMER_NUMBER: "000000000000"},
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
