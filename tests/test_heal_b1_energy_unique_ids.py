"""Tests for the b1->b3 energy unique_id heal pass."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be import _async_heal_b1_energy_unique_ids
from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
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

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _build_entry_with_subentries(
    hass: HomeAssistant,
    *,
    customer_numbers: tuple[str, ...] = ("1500000001",),
) -> MockConfigEntry:
    """Build a v3 MockConfigEntry with one or more customer-account subentries."""
    subentries: list[ConfigSubentryData] = [
        ConfigSubentryData(
            subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
            title=f"Account {cn}",
            unique_id=cn,
            data={
                CONF_CUSTOMER_NUMBER: cn,
                CONF_BUSINESS_AGREEMENT_NUMBER: f"B-{cn}",
                CONF_PREMISES_NUMBER: f"P-{cn}",
                CONF_CONSUMPTION_ADDRESS: f"Addr {cn}",
            },
        )
        for cn in customer_numbers
    ]
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
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)
    return entry


def _create_subentry_device(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    subentry_id: str,
) -> dr.DeviceEntry:
    """Create the customer-account device that owns energy entities."""
    device_reg = dr.async_get(hass)
    return device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry_id,
        identifiers={(DOMAIN, subentry_id)},
        manufacturer="ENGIE Belgium",
        name=f"Premises {subentry_id}",
    )


def _create_login_device(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> dr.DeviceEntry:
    """Create the login-scoped device used by the auth binary sensor."""
    device_reg = dr.async_get(hass)
    return device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"login_{entry.entry_id}")},
        manufacturer="ENGIE Belgium",
        name="Account",
    )


def _seed_b1_energy_entity(  # noqa: PLR0913 - test seeder, all kwargs are intent-revealing
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    device_id: str,
    ean: str,
    direction: str,
    suggested_object_id: str | None = None,
) -> str:
    """
    Seed a single b1-shape energy entity in the registry.

    Returns the resulting ``entity_id`` so callers can assert it survives
    the rewrite (the contract is that user-facing entity_ids must not
    change when only the unique_id is rewritten).
    """
    entity_reg = er.async_get(hass)
    old_uid = f"{entry.entry_id}_{ean}_{direction}"
    ent = entity_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        old_uid,
        suggested_object_id=suggested_object_id or f"engie_be_{ean}_{direction}",
        config_entry=entry,
        device_id=device_id,
        # Deliberately leave config_subentry_id unset to mirror the
        # b1 bug shape exactly.
    )
    return ent.entity_id


async def test_heal_rewrites_b1_energy_uids_to_canonical_shape(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A b1-shape energy uid must be rewritten and stamped with subentry_id."""
    entry = _build_entry_with_subentries(hass)
    subentry = next(iter(entry.subentries.values()))
    device = _create_subentry_device(hass, entry, subentry.subentry_id)

    ean = "541448820000000001"
    seeded_entity_ids: dict[tuple[str, str], str] = {}
    for direction in ("offtake", "offtake_excl_vat", "injection", "injection_excl_vat"):
        seeded_entity_ids[(ean, direction)] = _seed_b1_energy_entity(
            hass,
            entry,
            device_id=device.id,
            ean=ean,
            direction=direction,
        )

    await _async_heal_b1_energy_unique_ids(hass, entry)

    entity_reg = er.async_get(hass)
    for (e, direction), entity_id in seeded_entity_ids.items():
        ent = entity_reg.async_get(entity_id)
        assert ent is not None, f"entity {entity_id} disappeared"
        expected_uid = f"{entry.entry_id}_{subentry.subentry_id}_{e}_{direction}"
        assert ent.unique_id == expected_uid, (
            f"unique_id not rewritten: got {ent.unique_id}, want {expected_uid}"
        )
        assert ent.config_subentry_id == subentry.subentry_id


async def test_heal_drops_b1_orphan_when_canonical_sibling_exists(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Remove the b1 orphan when a canonical-shape sibling already exists.

    This mirrors the boot-after-failed-heal scenario: a previous b3 boot
    registered fresh canonical entities under new entity_ids while the
    b1-shape rows lingered as unavailable orphans.
    """
    entry = _build_entry_with_subentries(hass)
    subentry = next(iter(entry.subentries.values()))
    device = _create_subentry_device(hass, entry, subentry.subentry_id)

    ean = "541448820000000001"
    direction = "offtake"
    canonical_uid = f"{entry.entry_id}_{subentry.subentry_id}_{ean}_{direction}"

    # b1 orphan: no config_subentry_id, b1-shape uid, original entity_id.
    b1_entity_id = _seed_b1_energy_entity(
        hass,
        entry,
        device_id=device.id,
        ean=ean,
        direction=direction,
        suggested_object_id="engie_be_offtake",
    )
    # Canonical sibling registered later by the b3 platform.
    entity_reg = er.async_get(hass)
    canonical = entity_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        canonical_uid,
        suggested_object_id="lindestraat_offtake_price",
        config_entry=entry,
        config_subentry_id=subentry.subentry_id,
        device_id=device.id,
    )

    await _async_heal_b1_energy_unique_ids(hass, entry)

    assert entity_reg.async_get(b1_entity_id) is None, (
        "b1 orphan should be removed when canonical sibling exists"
    )
    survivor = entity_reg.async_get(canonical.entity_id)
    assert survivor is not None
    assert survivor.unique_id == canonical_uid


async def test_heal_is_noop_on_canonical_install(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """A clean v3 install must be untouched by the heal pass."""
    entry = _build_entry_with_subentries(hass)
    subentry = next(iter(entry.subentries.values()))
    device = _create_subentry_device(hass, entry, subentry.subentry_id)

    entity_reg = er.async_get(hass)
    canonical_uid = (
        f"{entry.entry_id}_{subentry.subentry_id}_541448820000000001_offtake"
    )
    ent = entity_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        canonical_uid,
        suggested_object_id="engie_be_offtake",
        config_entry=entry,
        config_subentry_id=subentry.subentry_id,
        device_id=device.id,
    )
    snapshot: dict[str, Any] = {
        "unique_id": ent.unique_id,
        "config_subentry_id": ent.config_subentry_id,
        "entity_id": ent.entity_id,
    }

    await _async_heal_b1_energy_unique_ids(hass, entry)

    after = entity_reg.async_get(ent.entity_id)
    assert after is not None
    assert after.unique_id == snapshot["unique_id"]
    assert after.config_subentry_id == snapshot["config_subentry_id"]


async def test_heal_is_idempotent_on_repeat_invocation(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Two heal passes back-to-back must not change state after the first."""
    entry = _build_entry_with_subentries(hass)
    subentry = next(iter(entry.subentries.values()))
    device = _create_subentry_device(hass, entry, subentry.subentry_id)

    entity_id = _seed_b1_energy_entity(
        hass,
        entry,
        device_id=device.id,
        ean="541448820000000001",
        direction="offtake_excl_vat",
    )

    await _async_heal_b1_energy_unique_ids(hass, entry)
    entity_reg = er.async_get(hass)
    first = entity_reg.async_get(entity_id)
    assert first is not None
    first_state = (first.unique_id, first.config_subentry_id)

    await _async_heal_b1_energy_unique_ids(hass, entry)
    second = entity_reg.async_get(entity_id)
    assert second is not None
    assert (second.unique_id, second.config_subentry_id) == first_state


async def test_heal_skips_entity_without_subentry_keyed_device(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Skip entities whose device is not a customer-account device.

    An energy entity attached to the login device (or no device) cannot be
    healed because there is no subentry to attribute it to. The helper
    must log a warning and leave the row untouched.
    """
    entry = _build_entry_with_subentries(hass)
    login_device = _create_login_device(hass, entry)

    entity_id = _seed_b1_energy_entity(
        hass,
        entry,
        device_id=login_device.id,
        ean="541448820000000001",
        direction="offtake",
    )

    await _async_heal_b1_energy_unique_ids(hass, entry)

    entity_reg = er.async_get(hass)
    ent = entity_reg.async_get(entity_id)
    assert ent is not None, "untouched entity must survive"
    # Unique id must still be the b1 shape because nothing was rewritten.
    assert ent.unique_id == f"{entry.entry_id}_541448820000000001_offtake"
    assert ent.config_subentry_id is None


async def test_heal_attributes_uids_to_correct_subentry_in_multi_account(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """
    Route each b1 uid to the correct subentry in a multi-account install.

    With two customer-account subentries each owning their own EAN, the
    heal must route each b1-shape uid to the subentry whose device the
    entity is attached to (not the first or last subentry by iteration).
    """
    entry = _build_entry_with_subentries(
        hass,
        customer_numbers=("1500000001", "1500000002"),
    )
    sub_a, sub_b = entry.subentries.values()
    device_a = _create_subentry_device(hass, entry, sub_a.subentry_id)
    device_b = _create_subentry_device(hass, entry, sub_b.subentry_id)

    ean_a = "541448820000000001"
    ean_b = "541448820000000002"
    entity_a = _seed_b1_energy_entity(
        hass,
        entry,
        device_id=device_a.id,
        ean=ean_a,
        direction="offtake",
        suggested_object_id="engie_be_a_offtake",
    )
    entity_b = _seed_b1_energy_entity(
        hass,
        entry,
        device_id=device_b.id,
        ean=ean_b,
        direction="offtake",
        suggested_object_id="engie_be_b_offtake",
    )

    await _async_heal_b1_energy_unique_ids(hass, entry)

    entity_reg = er.async_get(hass)
    a = entity_reg.async_get(entity_a)
    b = entity_reg.async_get(entity_b)
    assert a is not None
    assert b is not None
    assert a.unique_id == f"{entry.entry_id}_{sub_a.subentry_id}_{ean_a}_offtake"
    assert a.config_subentry_id == sub_a.subentry_id
    assert b.unique_id == f"{entry.entry_id}_{sub_b.subentry_id}_{ean_b}_offtake"
    assert b.config_subentry_id == sub_b.subentry_id


async def test_heal_returns_early_when_no_customer_subentries(
    hass: HomeAssistant,
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """An entry with zero customer subentries is a no-op."""
    entry = _build_entry_with_subentries(hass, customer_numbers=())
    # Even if we seed something b1-shaped, the absence of subentries
    # short-circuits the heal so nothing is rewritten.
    fake_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"login_{entry.entry_id}")},
        name="Login",
    )
    entity_id = _seed_b1_energy_entity(
        hass,
        entry,
        device_id=fake_device.id,
        ean="541448820000000001",
        direction="offtake",
    )

    await _async_heal_b1_energy_unique_ids(hass, entry)

    entity_reg = er.async_get(hass)
    ent = entity_reg.async_get(entity_id)
    assert ent is not None
    assert ent.unique_id == f"{entry.entry_id}_541448820000000001_offtake"
