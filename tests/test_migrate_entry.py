"""Tests for the v2 to v3 ``async_migrate_entry`` path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be import async_migrate_entry
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
from custom_components.engie_be.store import EngieBePeaksStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_relations_fixture() -> dict[str, Any]:
    """Read the bundled customer-account-relations sample."""
    return json.loads(
        (_FIXTURE_DIR / "customer_account_relations_sample.json").read_text("utf-8"),
    )


def _build_v2_entry(
    hass: HomeAssistant,
    *,
    customer_number: str = "1500000001",
    with_tokens: bool = True,
) -> MockConfigEntry:
    """Build a v2-shaped MockConfigEntry (customer_number on entry.data)."""
    data: dict[str, Any] = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "hunter2",
        CONF_CUSTOMER_NUMBER: customer_number,
        CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
    }
    if with_tokens:
        data[CONF_ACCESS_TOKEN] = "stored-access"
        data[CONF_REFRESH_TOKEN] = "stored-refresh"
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="user@example.com",
        unique_id="user_example_com",
        data=data,
        options={"update_interval": 60},
    )
    entry.add_to_hass(hass)
    return entry


def _seed_v2_device_and_entities(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> tuple[str, dict[str, str]]:
    """
    Pre-create the v2 device and a sample of v2-keyed entities.

    Returns the device_id and a dict mapping logical labels to old unique_ids
    so tests can assert on rename outcomes without re-listing them.
    """
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    v2_device = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="ENGIE Belgium",
        name="ENGIE Belgium",
    )

    old_uids: dict[str, str] = {}

    # Subentry-scoped (must be renamed)
    for key in (
        "captar_monthly_peak_power",
        "captar_monthly_peak_energy",
        "captar_monthly_peak_start",
        "captar_monthly_peak_end",
        "epex_current",
        "epex_low_today",
        "epex_high_today",
    ):
        old_uid = f"{entry.entry_id}_{key}"
        old_uids[key] = old_uid
        entity_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            old_uid,
            suggested_object_id=f"engie_be_{key}",
            config_entry=entry,
            device_id=v2_device.id,
        )

    old_uids["epex_negative"] = f"{entry.entry_id}_epex_negative"
    entity_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        old_uids["epex_negative"],
        suggested_object_id="engie_be_epex_negative",
        config_entry=entry,
        device_id=v2_device.id,
    )
    old_uids["calendar"] = f"{entry.entry_id}_calendar"
    entity_reg.async_get_or_create(
        "calendar",
        DOMAIN,
        old_uids["calendar"],
        suggested_object_id="engie_be_calendar",
        config_entry=entry,
        device_id=v2_device.id,
    )

    # Untouched: energy sensor (EAN-embedded key) and auth binary sensor
    energy_uid = f"{entry.entry_id}_consumption_541448820000000001"
    old_uids["energy"] = energy_uid
    entity_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        energy_uid,
        suggested_object_id="engie_be_consumption",
        config_entry=entry,
        device_id=v2_device.id,
    )
    auth_uid = f"{entry.entry_id}_authentication"
    old_uids["auth"] = auth_uid
    entity_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        auth_uid,
        suggested_object_id="engie_be_authentication",
        config_entry=entry,
        device_id=v2_device.id,
    )

    return v2_device.id, old_uids


def _make_relations_client(
    relations: dict[str, Any] | None = None,
    refresh_side_effect: Exception | None = None,
    relations_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock client that satisfies the migration's backfill path."""
    client = MagicMock()
    if refresh_side_effect is not None:
        client.async_refresh_token = AsyncMock(side_effect=refresh_side_effect)
    else:
        client.async_refresh_token = AsyncMock(
            return_value=("fresh-access", "fresh-refresh"),
        )
    if relations_side_effect is not None:
        client.async_get_customer_account_relations = AsyncMock(
            side_effect=relations_side_effect,
        )
    else:
        client.async_get_customer_account_relations = AsyncMock(
            return_value=relations or {"items": []},
        )
    return client


# ---------------------------------------------------------------------------
# Happy path: relations backfill succeeds, all renames apply
# ---------------------------------------------------------------------------


async def test_migrate_v2_to_v3_creates_subentry_with_relations_backfill(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A v2 entry plus a successful relations fetch produces a populated subentry."""
    entry = _build_v2_entry(hass, customer_number="1500000001")
    relations = _load_relations_fixture()
    client = _make_relations_client(relations=relations)

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await async_migrate_entry(hass, entry)

    assert ok is True
    assert entry.version == 3
    assert CONF_CUSTOMER_NUMBER not in entry.data

    subentries = list(entry.subentries.values())
    assert len(subentries) == 1
    subentry = subentries[0]
    assert subentry.subentry_type == SUBENTRY_TYPE_CUSTOMER_ACCOUNT
    assert subentry.unique_id == "1500000001"
    # Address-derived title from the relations fixture (TESTSTRAAT 1, 1000 BRUSSELS).
    assert "TESTSTRAAT" in subentry.title
    assert "BRUSSELS" in subentry.title
    assert subentry.data[CONF_CUSTOMER_NUMBER] == "1500000001"
    assert subentry.data[CONF_BUSINESS_AGREEMENT_NUMBER] == "002200000001"
    assert subentry.data[CONF_PREMISES_NUMBER] == "5100000001"
    assert subentry.data[CONF_ACCOUNT_HOLDER_NAME] == "Test Customer One"
    assert CONF_CONSUMPTION_ADDRESS in subentry.data


# ---------------------------------------------------------------------------
# Backfill failure paths: missing tokens, network error, account not in relations
# ---------------------------------------------------------------------------


async def test_migrate_v2_to_v3_falls_back_to_customer_number_title_on_relations_error(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Network errors during backfill must not abort migration."""
    entry = _build_v2_entry(hass, customer_number="1500000099")
    client = _make_relations_client(
        relations_side_effect=EngieBeApiClientError("boom"),
    )

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await async_migrate_entry(hass, entry)

    assert ok is True
    assert entry.version == 3
    subentry = next(iter(entry.subentries.values()))
    # Title falls back to the bare customer number when no address is available.
    assert subentry.title == "1500000099"
    # Optional display fields stay absent rather than being persisted as None.
    assert CONF_BUSINESS_AGREEMENT_NUMBER not in subentry.data
    assert CONF_PREMISES_NUMBER not in subentry.data


async def test_migrate_v2_to_v3_skips_backfill_when_no_refresh_token(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Without a refresh token the migration must not even construct a client."""
    entry = _build_v2_entry(
        hass,
        customer_number="1500000001",
        with_tokens=False,
    )

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
    ) as client_cls:
        ok = await async_migrate_entry(hass, entry)

    assert ok is True
    assert entry.version == 3
    subentry = next(iter(entry.subentries.values()))
    assert subentry.title == "1500000001"
    # No client should ever be built when there is no refresh token to use.
    assert client_cls.call_count == 0


async def test_migrate_v2_to_v3_subentry_title_falls_back_when_account_absent(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A successful relations fetch that omits this customer falls back gracefully."""
    entry = _build_v2_entry(hass, customer_number="9999999999")
    relations = _load_relations_fixture()  # holds 1500000001 / 1500000002
    client = _make_relations_client(relations=relations)

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        ok = await async_migrate_entry(hass, entry)

    assert ok is True
    subentry = next(iter(entry.subentries.values()))
    assert subentry.title == "9999999999"


# ---------------------------------------------------------------------------
# Corrupt entry: missing customer_number must not loop on every restart
# ---------------------------------------------------------------------------


async def test_migrate_v2_to_v3_without_customer_number_just_bumps_version(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """An entry that lost its customer_number is bumped to v3 with no subentry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
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
    )
    entry.add_to_hass(hass)

    ok = await async_migrate_entry(hass, entry)

    assert ok is True
    assert entry.version == 3
    assert len(entry.subentries) == 0


# ---------------------------------------------------------------------------
# Device-registry mutation: identifiers swap, customisations preserved
# ---------------------------------------------------------------------------


async def test_migrate_v2_to_v3_mutates_device_identifiers_in_place(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """The v2 device must keep its device_id but switch to (DOMAIN, subentry_id)."""
    entry = _build_v2_entry(hass)
    v2_device_id, _ = _seed_v2_device_and_entities(hass, entry)

    # Tag the device with a user customisation so we can prove it survives.
    device_reg = dr.async_get(hass)
    device_reg.async_update_device(v2_device_id, name_by_user="My ENGIE Hub")

    relations = _load_relations_fixture()
    client = _make_relations_client(relations=relations)

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        await async_migrate_entry(hass, entry)

    subentry = next(iter(entry.subentries.values()))
    migrated_device = device_reg.async_get(v2_device_id)
    assert migrated_device is not None
    assert migrated_device.identifiers == {(DOMAIN, subentry.subentry_id)}
    assert migrated_device.name_by_user == "My ENGIE Hub"
    assert subentry.subentry_id in migrated_device.config_entries_subentries[
        entry.entry_id
    ]


# ---------------------------------------------------------------------------
# Entity registry: rename the 9 affected entities, leave energy + auth alone
# ---------------------------------------------------------------------------


async def test_migrate_v2_to_v3_renames_only_subentry_scoped_unique_ids(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Peak/EPEX/calendar unique_ids are rewritten; energy + auth stay put."""
    entry = _build_v2_entry(hass)
    _v2_device_id, old_uids = _seed_v2_device_and_entities(hass, entry)

    relations = _load_relations_fixture()
    client = _make_relations_client(relations=relations)

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        await async_migrate_entry(hass, entry)

    subentry = next(iter(entry.subentries.values()))
    entity_reg = er.async_get(hass)
    all_entries = er.async_entries_for_config_entry(entity_reg, entry.entry_id)
    by_uid = {e.unique_id: e for e in all_entries}

    # Renamed entities: old uid is gone, new uid is present, and the
    # subentry_id is associated.
    keys_to_rename = (
        "captar_monthly_peak_power",
        "captar_monthly_peak_energy",
        "captar_monthly_peak_start",
        "captar_monthly_peak_end",
        "epex_current",
        "epex_low_today",
        "epex_high_today",
        "epex_negative",
        "calendar",
    )
    for key in keys_to_rename:
        new_uid = f"{entry.entry_id}_{subentry.subentry_id}_{key}"
        assert old_uids[key] not in by_uid, f"old {key} unique_id should be gone"
        assert new_uid in by_uid, f"new {key} unique_id should exist"
        assert by_uid[new_uid].config_subentry_id == subentry.subentry_id

    # Energy and auth unique_ids are unchanged (no subentry_id segment added).
    assert old_uids["energy"] in by_uid
    assert old_uids["auth"] in by_uid


# ---------------------------------------------------------------------------
# Peaks store: persisted peaks must be readable under the new subentry key
# ---------------------------------------------------------------------------


async def test_migrate_v2_to_v3_carries_peaks_store_under_new_key(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Persisted v2 peaks must be loadable via the v3 subentry-keyed store.

    The migration writes through ``EngieBePeaksStore.upsert`` which schedules
    a delayed disk save (30s); asserting on the on-disk file would race the
    debounce. Instead we assert on the in-memory snapshot of a freshly built
    store, which is what the production code path itself relies on.
    """
    entry = _build_v2_entry(hass)

    # Pre-write a v2-shaped peaks store on disk via a transient Store so
    # the migration's "load via old key, save via new key" path has
    # something to copy.
    old_key = f"{DOMAIN}.peaks_history.{entry.entry_id}"
    seeded_peaks = [
        {
            "year": 2024,
            "month": 11,
            "start": "2024-11-12T18:15:00+01:00",
            "end": "2024-11-12T18:30:00+01:00",
            "peakKW": 4.3,
            "peakKWh": 1.075,
        },
        {
            "year": 2024,
            "month": 12,
            "start": "2024-12-05T19:00:00+01:00",
            "end": "2024-12-05T19:15:00+01:00",
            "peakKW": 5.1,
            "peakKWh": 1.275,
        },
    ]
    seed_store: Store[dict[str, Any]] = Store(hass, 1, old_key)
    await seed_store.async_save({"peaks": seeded_peaks})

    relations = _load_relations_fixture()
    client = _make_relations_client(relations=relations)

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        await async_migrate_entry(hass, entry)

    subentry = next(iter(entry.subentries.values()))

    # Old store is gone (async_remove was called explicitly by the migration).
    reloaded_old = await Store(hass, 1, old_key).async_load()
    assert reloaded_old is None

    # New store can be built and exposes the seeded peaks.
    new_store = EngieBePeaksStore(hass, subentry.subentry_id)
    await new_store.async_load()
    persisted_months = {(p["year"], p["month"]) for p in new_store.peaks}
    assert persisted_months == {(2024, 11), (2024, 12)}


# ---------------------------------------------------------------------------
# Idempotency: running migration on a v3 entry is a noop
# ---------------------------------------------------------------------------


async def test_migrate_v2_to_v3_is_idempotent_when_run_again(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A second migration call on a v3 entry must not re-create the subentry."""
    entry = _build_v2_entry(hass)
    relations = _load_relations_fixture()
    client = _make_relations_client(relations=relations)

    with patch(
        "custom_components.engie_be.EngieBeApiClient",
        return_value=client,
    ):
        await async_migrate_entry(hass, entry)
        # Snapshot post-migration shape.
        first_subentries = dict(entry.subentries)
        first_data = dict(entry.data)
        first_version = entry.version

        # Second call: version is already 3, so neither migration block fires.
        await async_migrate_entry(hass, entry)

    assert entry.version == first_version == 3
    assert dict(entry.data) == first_data
    assert dict(entry.subentries) == first_subentries
