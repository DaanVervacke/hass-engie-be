"""
Tests for the per-subentry coordinator's one-shot relations backfill.

When a customer-account ``ConfigSubentry`` is missing one or more of the
display fields normally populated from the customer-account-relations
endpoint (account holder name, consumption address, business agreement
number, premises number), the coordinator attempts to fill them in from
the relations endpoint on its first successful refresh. The attempt
runs at most once per Home Assistant process, even if the relations
call fails or returns no data for this customer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.api import EngieBeApiClientError
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
from custom_components.engie_be.coordinator import EngieBeDataUpdateCoordinator
from custom_components.engie_be.data import EngieBeData

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

# All tests in this module exercise the relations-backfill side effect; opt
# out of the autouse stub in conftest that no-ops backfill for unrelated
# coordinator tests.
pytestmark = pytest.mark.backfill

_RELATIONS_FIXTURE = (
    Path(__file__).parent / "fixtures" / "customer_account_relations_sample.json"
)


def _build_entry_with_subentry(
    hass: HomeAssistant,
    *,
    customer_number: str,
    subentry_data: dict[str, Any],
) -> MockConfigEntry:
    """Build a v3 entry with a single customer-account subentry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        title="user@example.com",
        unique_id="user_example_com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "stored-access",
            CONF_REFRESH_TOKEN: "stored-refresh",
        },
        options={"update_interval": 60},
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
                title="placeholder",
                unique_id=customer_number,
                data={CONF_CUSTOMER_NUMBER: customer_number, **subentry_data},
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


def _only_subentry(entry: MockConfigEntry) -> ConfigSubentry:
    """Return the single customer-account subentry on the test entry."""
    return next(iter(entry.subentries.values()))


def _make_client(
    *,
    relations_return: dict[str, Any] | None = None,
    relations_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock API client for the coordinator under test."""
    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value={"items": []})
    client.async_get_monthly_peaks = AsyncMock(
        return_value={"peakOfTheMonth": None, "dailyPeaks": []},
    )
    if relations_side_effect is not None:
        client.async_get_customer_account_relations = AsyncMock(
            side_effect=relations_side_effect,
        )
    else:
        client.async_get_customer_account_relations = AsyncMock(
            return_value=relations_return or {"items": []},
        )
    return client


def _attach_runtime(entry: MockConfigEntry, client: MagicMock) -> None:
    """Attach a minimal EngieBeData runtime onto the test entry."""
    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=MagicMock(),
        subentry_data={},
        authenticated=True,
        last_options=dict(entry.options),
    )


async def test_backfill_populates_missing_fields_on_first_refresh(
    hass: HomeAssistant,
) -> None:
    """First refresh must fill empty subentry fields from relations data."""
    relations = json.loads(_RELATIONS_FIXTURE.read_text())
    entry = _build_entry_with_subentry(
        hass,
        customer_number="1500000001",
        subentry_data={},  # no display fields yet
    )
    subentry = _only_subentry(entry)
    client = _make_client(relations_return=relations)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=subentry,
    )
    assert coordinator._needs_relations_backfill is True

    await coordinator._async_update_data()

    # Relations endpoint was hit exactly once.
    client.async_get_customer_account_relations.assert_awaited_once_with()

    # Subentry data now carries the relations-derived fields.
    refreshed = entry.subentries[subentry.subentry_id]
    assert refreshed.data[CONF_BUSINESS_AGREEMENT_NUMBER] == "002200000001"
    assert refreshed.data[CONF_PREMISES_NUMBER] == "5100000001"
    assert refreshed.data[CONF_ACCOUNT_HOLDER_NAME] == "Test Customer One"
    assert "TESTSTRAAT 1" in refreshed.data[CONF_CONSUMPTION_ADDRESS]
    assert "1000 BRUSSELS" in refreshed.data[CONF_CONSUMPTION_ADDRESS]

    # Flag is cleared so the next refresh skips the relations call.
    assert coordinator._needs_relations_backfill is False


async def test_backfill_skipped_when_all_fields_already_present(
    hass: HomeAssistant,
) -> None:
    """Coordinator must not call relations when nothing is missing."""
    entry = _build_entry_with_subentry(
        hass,
        customer_number="1500000001",
        subentry_data={
            CONF_BUSINESS_AGREEMENT_NUMBER: "B-EXISTING",
            CONF_PREMISES_NUMBER: "P-EXISTING",
            CONF_ACCOUNT_HOLDER_NAME: "Existing Name",
            CONF_CONSUMPTION_ADDRESS: "Existing Address 1, 1000 City",
        },
    )
    subentry = _only_subentry(entry)
    client = _make_client(
        relations_return=json.loads(_RELATIONS_FIXTURE.read_text()),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=subentry,
    )
    assert coordinator._needs_relations_backfill is False

    await coordinator._async_update_data()

    client.async_get_customer_account_relations.assert_not_called()

    # Existing values are preserved (not overwritten by upstream data).
    refreshed = entry.subentries[subentry.subentry_id]
    assert refreshed.data[CONF_BUSINESS_AGREEMENT_NUMBER] == "B-EXISTING"
    assert refreshed.data[CONF_ACCOUNT_HOLDER_NAME] == "Existing Name"


async def test_backfill_runs_only_once_even_across_multiple_refreshes(
    hass: HomeAssistant,
) -> None:
    """The flag must be cleared after the first attempt."""
    relations = json.loads(_RELATIONS_FIXTURE.read_text())
    entry = _build_entry_with_subentry(
        hass,
        customer_number="1500000001",
        subentry_data={},
    )
    subentry = _only_subentry(entry)
    client = _make_client(relations_return=relations)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=subentry,
    )

    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await coordinator._async_update_data()

    # Relations endpoint was hit on the first refresh only.
    assert client.async_get_customer_account_relations.await_count == 1


async def test_backfill_failure_is_swallowed_and_flag_cleared(
    hass: HomeAssistant,
) -> None:
    """A failing relations call must not raise and must clear the flag."""
    entry = _build_entry_with_subentry(
        hass,
        customer_number="1500000001",
        subentry_data={},
    )
    subentry = _only_subentry(entry)
    original_data = dict(subentry.data)
    client = _make_client(
        relations_side_effect=EngieBeApiClientError("relations 502"),
    )
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=subentry,
    )

    # Must not raise even though the relations call fails.
    await coordinator._async_update_data()

    client.async_get_customer_account_relations.assert_awaited_once_with()
    refreshed = entry.subentries[subentry.subentry_id]
    assert dict(refreshed.data) == original_data
    assert coordinator._needs_relations_backfill is False


async def test_backfill_no_match_for_customer_leaves_subentry_untouched(
    hass: HomeAssistant,
) -> None:
    """When relations has no entry for our customer, do nothing."""
    relations = json.loads(_RELATIONS_FIXTURE.read_text())
    entry = _build_entry_with_subentry(
        hass,
        customer_number="9999999999",  # not present in fixture
        subentry_data={},
    )
    subentry = _only_subentry(entry)
    original_data = dict(subentry.data)
    client = _make_client(relations_return=relations)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=subentry,
    )

    await coordinator._async_update_data()

    client.async_get_customer_account_relations.assert_awaited_once_with()
    refreshed = entry.subentries[subentry.subentry_id]
    assert dict(refreshed.data) == original_data
    assert coordinator._needs_relations_backfill is False


async def test_backfill_only_fills_missing_keys(
    hass: HomeAssistant,
) -> None:
    """Existing user-edited fields must not be overwritten by relations data."""
    relations = json.loads(_RELATIONS_FIXTURE.read_text())
    entry = _build_entry_with_subentry(
        hass,
        customer_number="1500000001",
        subentry_data={
            CONF_ACCOUNT_HOLDER_NAME: "User Edited Name",
            # business agreement, premises, address are missing
        },
    )
    subentry = _only_subentry(entry)
    client = _make_client(relations_return=relations)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=subentry,
    )

    await coordinator._async_update_data()

    refreshed = entry.subentries[subentry.subentry_id]
    # Edited field preserved.
    assert refreshed.data[CONF_ACCOUNT_HOLDER_NAME] == "User Edited Name"
    # Missing fields filled.
    assert refreshed.data[CONF_BUSINESS_AGREEMENT_NUMBER] == "002200000001"
    assert refreshed.data[CONF_PREMISES_NUMBER] == "5100000001"
    assert "TESTSTRAAT 1" in refreshed.data[CONF_CONSUMPTION_ADDRESS]


async def test_backfill_matches_legacy_business_agreement_number(
    hass: HomeAssistant,
) -> None:
    """
    Legacy v2 entries stored businessAgreementNumber as customer_number.

    The coordinator backfill must still locate the owning customer
    account via the businessAgreements walk so the address and holder
    name can be filled in. The stored ``customer_number`` is left
    untouched (the prices/peaks endpoints accept either identifier),
    only the display fields are populated.
    """
    relations = json.loads(_RELATIONS_FIXTURE.read_text())
    legacy_ban = "002200000001"  # 12-digit BAN, not a CAN
    entry = _build_entry_with_subentry(
        hass,
        customer_number=legacy_ban,
        subentry_data={},
    )
    subentry = _only_subentry(entry)
    client = _make_client(relations_return=relations)
    _attach_runtime(entry, client)

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=subentry,
    )

    await coordinator._async_update_data()

    refreshed = entry.subentries[subentry.subentry_id]
    # The originally-stored identifier is preserved (used for API calls).
    assert refreshed.data[CONF_CUSTOMER_NUMBER] == legacy_ban
    # Display fields are populated from the matched customer account.
    assert refreshed.data[CONF_BUSINESS_AGREEMENT_NUMBER] == "002200000001"
    assert refreshed.data[CONF_PREMISES_NUMBER] == "5100000001"
    assert refreshed.data[CONF_ACCOUNT_HOLDER_NAME] == "Test Customer One"
    assert "TESTSTRAAT 1" in refreshed.data[CONF_CONSUMPTION_ADDRESS]


async def test_backfill_refreshes_subentry_title_and_device_name(
    hass: HomeAssistant,
) -> None:
    """
    Successful backfill must update the subentry title and rename the device.

    Before backfill the subentry title is the bare customer number (the
    fallback used when no relations data is available at creation time).
    After backfill the title falls through to the consumption address,
    and the matching device must be renamed in place so the registry
    reflects the new label without requiring the user to remove and
    re-add the entry.
    """
    relations = json.loads(_RELATIONS_FIXTURE.read_text())
    entry = _build_entry_with_subentry(
        hass,
        customer_number="1500000001",
        subentry_data={},
    )
    subentry = _only_subentry(entry)
    client = _make_client(relations_return=relations)
    _attach_runtime(entry, client)

    # Pre-create the device the way the entity platform would, so the
    # coordinator has a real device to rename.
    device_reg = dr.async_get(hass)
    device = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry.subentry_id,
        identifiers={(DOMAIN, subentry.subentry_id)},
        manufacturer="ENGIE Belgium",
        name=subentry.title,
    )
    assert device.name == "placeholder"

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=subentry,
    )

    await coordinator._async_update_data()

    refreshed_subentry = entry.subentries[subentry.subentry_id]
    new_title = refreshed_subentry.title
    # Title now reflects the consumption address rather than the placeholder.
    assert new_title != "placeholder"
    assert "TESTSTRAAT 1" in new_title

    refreshed_device = device_reg.async_get(device.id)
    assert refreshed_device is not None
    assert refreshed_device.name == new_title


async def test_backfill_preserves_user_named_device(
    hass: HomeAssistant,
) -> None:
    """A user-customised device name must not be clobbered by backfill."""
    relations = json.loads(_RELATIONS_FIXTURE.read_text())
    entry = _build_entry_with_subentry(
        hass,
        customer_number="1500000001",
        subentry_data={},
    )
    subentry = _only_subentry(entry)
    client = _make_client(relations_return=relations)
    _attach_runtime(entry, client)

    device_reg = dr.async_get(hass)
    device = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry.subentry_id,
        identifiers={(DOMAIN, subentry.subentry_id)},
        manufacturer="ENGIE Belgium",
        name=subentry.title,
    )
    device_reg.async_update_device(device.id, name_by_user="My Custom Label")

    coordinator = EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=subentry,
    )

    await coordinator._async_update_data()

    refreshed_device = device_reg.async_get(device.id)
    assert refreshed_device is not None
    # The user-set label wins regardless of what backfill writes to ``name``.
    assert refreshed_device.name_by_user == "My Custom Label"
