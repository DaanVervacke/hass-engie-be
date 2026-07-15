"""Tests for the ENGIE Belgium event platform."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from homeassistant.helpers import entity_registry as er

from custom_components.engie_be.const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_EXPOSE_ALL_ENTITIES,
    DOMAIN,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
    TRANSLATION_KEY_AUTHENTICATION,
    TRANSLATION_KEY_EPEX_NEGATIVE,
    TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
)
from custom_components.engie_be.event import (
    AUTHENTICATION_EVENTS_DESCRIPTION,
    EPEX_EVENTS_DESCRIPTION,
    EPEX_EVENTS_QUARTER_HOURLY_DESCRIPTION,
    HAPPY_HOURS_EVENTS_DESCRIPTION,
    TOU_EVENTS_DESCRIPTION,
    EngieBeAuthenticationEvent,
    EngieBeTransitionEvent,
    WatchedSibling,
    async_setup_entry,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pytest
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    AddEventEntity = Callable[[HomeAssistant, object], Awaitable[None]]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _add_event_entity(hass: HomeAssistant, entity: object) -> None:
    """Bind an event entity to hass and drive async_added_to_hass."""
    entity.hass = hass  # type: ignore[attr-defined]
    platform = MagicMock()
    platform.platform_name = DOMAIN
    platform.domain = "event"
    entity.platform = platform  # type: ignore[attr-defined]
    if entity.entity_id is None:  # type: ignore[attr-defined]
        entity.entity_id = f"event.test_{type(entity).__name__.lower()}"  # type: ignore[attr-defined]
    await entity.async_added_to_hass()  # type: ignore[attr-defined]


def _make_subentry(
    subentry_id: str = "sub_test", ban: str = "000000000000"
) -> MagicMock:
    """Build a MagicMock ConfigSubentry stub."""
    subentry = MagicMock()
    subentry.subentry_id = subentry_id
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {CONF_BUSINESS_AGREEMENT_NUMBER: ban}
    return subentry


def _make_entry(  # noqa: PLR0913
    subentries: dict[str, MagicMock],
    *,
    is_dynamic: bool = False,
    happy_hour_enrolled: bool = False,
    tou_active: bool = False,
    epex_qh_coordinator: object | None = None,
    expose_all: bool = False,
) -> MagicMock:
    """Build a MagicMock config entry wired for ``async_setup_entry``."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.options = {CONF_EXPOSE_ALL_ENTITIES: expose_all}
    entry.subentries = subentries
    entry.data = {}

    subentry_data: dict[str, MagicMock] = {}
    for subentry_id in subentries:
        coordinator = MagicMock()
        coordinator.is_dynamic = is_dynamic
        feature_flags = MagicMock()
        feature_flags.happy_hour_enrolled = happy_hour_enrolled
        feature_flags.tou_active = tou_active
        sub_data = MagicMock()
        sub_data.coordinator = coordinator
        sub_data.feature_flags = feature_flags
        subentry_data[subentry_id] = sub_data

    entry.runtime_data = MagicMock()
    entry.runtime_data.subentry_data = subentry_data
    entry.runtime_data.epex_qh_coordinator = epex_qh_coordinator
    return entry


def _description_keys(entities: list[object]) -> set[str]:
    """Return the entity_description.key of every entity that has one."""
    return {
        entity.entity_description.key  # type: ignore[attr-defined]
        for entity in entities
        if hasattr(entity, "entity_description")
    }


# ---------------------------------------------------------------------------
# WatchedSibling.resolve
# ---------------------------------------------------------------------------


def test_watched_sibling_resolves_exact_transition() -> None:
    """A registered ``(old, new)`` pair resolves to its event_type."""
    watched = WatchedSibling(
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
        transitions={("off", "on"): "price_negative", ("on", "off"): "price_positive"},
    )
    assert watched.resolve("off", "on") == ("price_negative", {})
    assert watched.resolve("on", "off") == ("price_positive", {})


def test_watched_sibling_ignores_unmapped_transition() -> None:
    """A transition not in ``transitions`` and no ``changed_event_type`` is None."""
    watched = WatchedSibling(
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
        transitions={("off", "on"): "price_negative"},
    )
    assert watched.resolve("unknown", "off") is None


def test_watched_sibling_changed_event_type_fires_on_any_difference() -> None:
    """``changed_event_type`` fires for any distinct old/new pair, with attributes."""
    watched = WatchedSibling(
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        changed_event_type="offtake_slot_changed",
    )
    assert watched.resolve("peak", "offpeak") == (
        "offtake_slot_changed",
        {"previous": "peak", "current": "offpeak"},
    )


def test_watched_sibling_changed_event_type_does_not_fire_on_same_value() -> None:
    """No event fires when old and new are identical, even with a wildcard rule."""
    watched = WatchedSibling(
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
        changed_event_type="offtake_slot_changed",
    )
    assert watched.resolve("peak", "peak") is None


# ---------------------------------------------------------------------------
# Entity metadata
# ---------------------------------------------------------------------------


def test_transition_event_unique_id_is_entry_and_subentry_scoped() -> None:
    """Unique IDs follow ``{entry_id}_{subentry_id}_{key}``."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    subentry = _make_subentry(subentry_id="sub_xyz")
    entity = EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
    assert entity.unique_id == "test_entry_id_sub_xyz_epex_events"


def test_transition_event_entity_id_uses_ban_when_present() -> None:
    """A subentry with a BAN gets the stable ``engie_belgium_<ban>_...`` slug."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    subentry = _make_subentry(ban="002208796420")
    entity = EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
    assert entity.entity_id == "event.engie_belgium_002208796420_epex_events"


def test_transition_event_no_entity_id_override_without_ban() -> None:
    """No BAN on the subentry: entity_id is left for HA to assign."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    subentry = _make_subentry(ban="")
    entity = EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
    assert entity.entity_id is None


def test_transition_event_device_info_matches_subentry_device() -> None:
    """The event entity attaches to the same device as its sibling entities."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    subentry = _make_subentry(subentry_id="sub_xyz")
    entity = EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
    assert entity.device_info is not None
    assert entity.device_info["identifiers"] == {(DOMAIN, "sub_xyz")}


def test_authentication_event_unique_id_is_entry_scoped() -> None:
    """The auth event entity's unique_id has no subentry component."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {}
    entity = EngieBeAuthenticationEvent(entry)
    assert entity.unique_id == "test_entry_id_authentication_events"


def test_authentication_event_device_info_matches_login_device() -> None:
    """The auth event entity attaches to the same login device as the auth sensor."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {}
    entity = EngieBeAuthenticationEvent(entry)
    assert entity.device_info is not None
    assert entity.device_info["identifiers"] == {(DOMAIN, "login_test_entry_id")}


# ---------------------------------------------------------------------------
# async_setup_entry gating
# ---------------------------------------------------------------------------


async def test_setup_creates_all_entities_when_flags_on() -> None:
    """Every feature flag on: all five event entities are created."""
    subentry = _make_subentry()
    entry = _make_entry(
        {subentry.subentry_id: subentry},
        is_dynamic=True,
        happy_hour_enrolled=True,
        tou_active=True,
        epex_qh_coordinator=MagicMock(),
    )
    entities: list = []

    def _add(ents: list, *_a: object, **_kw: object) -> None:
        entities.extend(ents)

    await async_setup_entry(MagicMock(), entry, _add)

    assert _description_keys(entities) == {
        "authentication_events",
        "epex_events",
        "epex_events_quarter_hourly",
        "happy_hours_events",
        "tou_events",
    }


async def test_setup_creates_only_auth_entity_when_flags_off() -> None:
    """Every feature flag off: only the entry-level auth event entity is created."""
    subentry = _make_subentry()
    entry = _make_entry({subentry.subentry_id: subentry})
    entities: list = []

    def _add(ents: list, *_a: object, **_kw: object) -> None:
        entities.extend(ents)

    await async_setup_entry(MagicMock(), entry, _add)

    assert len(entities) == 1
    assert isinstance(entities[0], EngieBeAuthenticationEvent)


async def test_setup_skips_qh_event_when_qh_coordinator_missing() -> None:
    """``is_dynamic`` alone does not create the QH event entity."""
    subentry = _make_subentry()
    entry = _make_entry(
        {subentry.subentry_id: subentry}, is_dynamic=True, epex_qh_coordinator=None
    )
    entities: list = []

    def _add(ents: list, *_a: object, **_kw: object) -> None:
        entities.extend(ents)

    await async_setup_entry(MagicMock(), entry, _add)

    keys = _description_keys(entities)
    assert "epex_events" in keys
    assert "epex_events_quarter_hourly" not in keys


async def test_setup_expose_all_creates_gated_entities_despite_flags_off() -> None:
    """``expose_all_entities`` overrides feature-flag gating."""
    subentry = _make_subentry()
    entry = _make_entry(
        {subentry.subentry_id: subentry},
        expose_all=True,
        epex_qh_coordinator=MagicMock(),
    )
    entities: list = []

    def _add(ents: list, *_a: object, **_kw: object) -> None:
        entities.extend(ents)

    await async_setup_entry(MagicMock(), entry, _add)

    assert _description_keys(entities) == {
        "authentication_events",
        "epex_events",
        "epex_events_quarter_hourly",
        "happy_hours_events",
        "tou_events",
    }


async def test_setup_skips_non_business_agreement_subentry() -> None:
    """Subentries that are not business agreements are ignored."""
    subentry = MagicMock()
    subentry.subentry_id = "sub_other"
    subentry.subentry_type = "some_other_type"
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.options = {}
    entry.data = {}
    entry.subentries = {"sub_other": subentry}
    entities: list = []

    def _add(ents: list, *_a: object, **_kw: object) -> None:
        entities.extend(ents)

    await async_setup_entry(MagicMock(), entry, _add)

    assert len(entities) == 1
    assert isinstance(entities[0], EngieBeAuthenticationEvent)


async def test_auth_event_is_entry_scoped_not_subentry_scoped() -> None:
    """Exactly one auth event entity exists regardless of subentry count."""
    subentry_a = _make_subentry(subentry_id="sub_a", ban="000000000001")
    subentry_b = _make_subentry(subentry_id="sub_b", ban="000000000002")
    subentries = {
        subentry_a.subentry_id: subentry_a,
        subentry_b.subentry_id: subentry_b,
    }
    entry = _make_entry(subentries)
    entities: list = []

    def _add(ents: list, *_a: object, **_kw: object) -> None:
        entities.extend(ents)

    await async_setup_entry(MagicMock(), entry, _add)

    auth_entities = [e for e in entities if isinstance(e, EngieBeAuthenticationEvent)]
    assert len(auth_entities) == 1


# ---------------------------------------------------------------------------
# State-change transitions (binary siblings)
# ---------------------------------------------------------------------------


async def test_binary_off_to_on_fires_price_negative(
    hass: HomeAssistant,
    build_engie_entry: Callable[[HomeAssistant, str], MockConfigEntry],
) -> None:
    """EPEX-negative binary sensor off->on fires ``price_negative``."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))

    ent_reg = er.async_get(hass)
    sibling = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "unique_epex_negative",
        config_entry=entry,
        config_subentry_id=subentry.subentry_id,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
    )

    entity = EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
    await _add_event_entity(hass, entity)

    hass.states.async_set(sibling.entity_id, "off")
    await hass.async_block_till_done()
    hass.states.async_set(sibling.entity_id, "on")
    await hass.async_block_till_done()

    assert entity.state is not None
    assert entity.state_attributes["event_type"] == "price_negative"


async def test_binary_on_to_off_fires_price_positive(
    hass: HomeAssistant,
    build_engie_entry: Callable[[HomeAssistant, str], MockConfigEntry],
) -> None:
    """EPEX-negative binary sensor on->off fires ``price_positive``."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))

    ent_reg = er.async_get(hass)
    sibling = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "unique_epex_negative",
        config_entry=entry,
        config_subentry_id=subentry.subentry_id,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
    )

    entity = EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
    await _add_event_entity(hass, entity)

    hass.states.async_set(sibling.entity_id, "on")
    await hass.async_block_till_done()
    hass.states.async_set(sibling.entity_id, "off")
    await hass.async_block_till_done()

    assert entity.state is not None
    assert entity.state_attributes["event_type"] == "price_positive"


async def test_no_event_fires_on_first_ever_state(
    hass: HomeAssistant,
    build_engie_entry: Callable[[HomeAssistant, str], MockConfigEntry],
) -> None:
    """The very first state write (old_state is None) does not fire an event."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))

    ent_reg = er.async_get(hass)
    sibling = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "unique_epex_negative",
        config_entry=entry,
        config_subentry_id=subentry.subentry_id,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
    )

    entity = EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
    await _add_event_entity(hass, entity)

    hass.states.async_set(sibling.entity_id, "off")
    await hass.async_block_till_done()

    assert entity.state is None


async def test_no_event_fires_on_unavailable_transition(
    hass: HomeAssistant,
    build_engie_entry: Callable[[HomeAssistant, str], MockConfigEntry],
) -> None:
    """A transition into/out of ``unavailable`` does not fire an event."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))

    ent_reg = er.async_get(hass)
    sibling = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "unique_epex_negative",
        config_entry=entry,
        config_subentry_id=subentry.subentry_id,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
    )

    entity = EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
    await _add_event_entity(hass, entity)

    hass.states.async_set(sibling.entity_id, "off")
    await hass.async_block_till_done()
    hass.states.async_set(sibling.entity_id, "unavailable")
    await hass.async_block_till_done()

    assert entity.state is None


async def test_no_event_fires_on_unknown_transition(
    hass: HomeAssistant,
    build_engie_entry: Callable[[HomeAssistant, str], MockConfigEntry],
) -> None:
    """A transition into ``unknown`` does not fire an event."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))

    ent_reg = er.async_get(hass)
    sibling = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "unique_epex_negative",
        config_entry=entry,
        config_subentry_id=subentry.subentry_id,
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
    )

    entity = EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
    await _add_event_entity(hass, entity)

    hass.states.async_set(sibling.entity_id, "off")
    await hass.async_block_till_done()
    hass.states.async_set(sibling.entity_id, "unknown")
    await hass.async_block_till_done()

    assert entity.state is None


# ---------------------------------------------------------------------------
# State-change transitions (enum siblings)
# ---------------------------------------------------------------------------


async def test_enum_change_fires_event_with_previous_and_current(
    hass: HomeAssistant,
    build_engie_entry: Callable[[HomeAssistant, str], MockConfigEntry],
) -> None:
    """A TOU offtake slot change fires with ``previous``/``current`` attributes."""
    entry = build_engie_entry(hass)
    subentry = next(iter(entry.subentries.values()))

    ent_reg = er.async_get(hass)
    sibling = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        "unique_tou_offtake_slot",
        config_entry=entry,
        config_subentry_id=subentry.subentry_id,
        translation_key=TRANSLATION_KEY_TOU_OFFTAKE_SLOT,
    )

    entity = EngieBeTransitionEvent(TOU_EVENTS_DESCRIPTION, entry, subentry)
    await _add_event_entity(hass, entity)

    hass.states.async_set(sibling.entity_id, "peak")
    await hass.async_block_till_done()
    hass.states.async_set(sibling.entity_id, "offpeak")
    await hass.async_block_till_done()

    assert entity.state is not None
    assert entity.state_attributes["event_type"] == "offtake_slot_changed"
    assert entity.state_attributes["previous"] == "peak"
    assert entity.state_attributes["current"] == "offpeak"


# ---------------------------------------------------------------------------
# QH description watches the correct translation key (regression guard)
# ---------------------------------------------------------------------------


def test_qh_description_watches_qh_translation_key() -> None:
    """The QH event description watches the QH-specific translation_key."""
    watched = EPEX_EVENTS_QUARTER_HOURLY_DESCRIPTION.watched_translation_keys
    assert len(watched) == 1
    assert watched[0].translation_key == "epex_negative_quarter_hour"


def test_happy_hours_description_transitions() -> None:
    """Happy Hours event description maps activated/deactivated correctly."""
    watched = HAPPY_HOURS_EVENTS_DESCRIPTION.watched_translation_keys[0]
    assert watched.resolve("off", "on") == ("activated", {})
    assert watched.resolve("on", "off") == ("deactivated", {})


def test_authentication_description_transitions() -> None:
    """Authentication event description maps lost/restored correctly."""
    watched = AUTHENTICATION_EVENTS_DESCRIPTION.watched_translation_keys[0]
    assert watched.resolve("on", "off") == ("lost", {})
    assert watched.resolve("off", "on") == ("restored", {})


# ---------------------------------------------------------------------------
# async_setup_entry: missing runtime data
# ---------------------------------------------------------------------------


async def test_setup_warns_and_skips_when_subentry_runtime_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A BAN subentry without runtime data logs a warning and is skipped."""
    subentry = _make_subentry(subentry_id="sub_ban")
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.options = {}
    entry.data = {}
    entry.subentries = {"sub_ban": subentry}
    entry.runtime_data.subentry_data = {}
    entities: list = []

    def _add(ents: list, *_a: object, **_kw: object) -> None:
        entities.extend(ents)

    with caplog.at_level(logging.WARNING, logger="custom_components.engie_be"):
        await async_setup_entry(MagicMock(), entry, _add)

    assert "No runtime data for subentry sub_ban" in caplog.text
    assert len(entities) == 1
    assert isinstance(entities[0], EngieBeAuthenticationEvent)


# ---------------------------------------------------------------------------
# _handle_state_change defensive branches
# ---------------------------------------------------------------------------


async def test_handle_state_change_ignores_untracked_entity(
    hass: HomeAssistant,
) -> None:
    """A state-change event for an entity_id not in the watch map is a no-op."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    subentry = _make_subentry()
    entity = EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
    await _add_event_entity(hass, entity)

    fake_event = MagicMock()
    fake_event.data = {
        "entity_id": "binary_sensor.not_tracked",
        "old_state": MagicMock(state="off"),
        "new_state": MagicMock(state="on"),
    }
    entity._handle_state_change(fake_event)

    assert entity.state is None


async def test_handle_state_change_ignores_unresolved_transition(
    hass: HomeAssistant,
) -> None:
    """A tracked entity whose transition has no matching rule is a no-op."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    subentry = _make_subentry()
    entity = EngieBeTransitionEvent(EPEX_EVENTS_DESCRIPTION, entry, subentry)
    await _add_event_entity(hass, entity)
    entity._entity_watch_map["binary_sensor.tracked"] = WatchedSibling(
        translation_key=TRANSLATION_KEY_EPEX_NEGATIVE,
        transitions={("off", "on"): "price_negative"},
    )

    fake_event = MagicMock()
    fake_event.data = {
        "entity_id": "binary_sensor.tracked",
        "old_state": MagicMock(state="unrelated_old"),
        "new_state": MagicMock(state="unrelated_new"),
    }
    entity._handle_state_change(fake_event)

    assert entity.state is None


# ---------------------------------------------------------------------------
# Authentication event: end-to-end lost/restored transitions
# ---------------------------------------------------------------------------


async def test_auth_event_fires_lost_on_on_to_off(
    hass: HomeAssistant,
    build_engie_entry: Callable[[HomeAssistant, str], MockConfigEntry],
) -> None:
    """The auth sensor flipping on->off fires ``lost``."""
    entry = build_engie_entry(hass)

    ent_reg = er.async_get(hass)
    sibling = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "unique_authentication",
        config_entry=entry,
        translation_key=TRANSLATION_KEY_AUTHENTICATION,
    )

    entity = EngieBeAuthenticationEvent(entry)
    await _add_event_entity(hass, entity)

    hass.states.async_set(sibling.entity_id, "on")
    await hass.async_block_till_done()
    hass.states.async_set(sibling.entity_id, "off")
    await hass.async_block_till_done()

    assert entity.state is not None
    assert entity.state_attributes["event_type"] == "lost"


async def test_auth_event_fires_restored_on_off_to_on(
    hass: HomeAssistant,
    build_engie_entry: Callable[[HomeAssistant, str], MockConfigEntry],
) -> None:
    """The auth sensor flipping off->on fires ``restored``."""
    entry = build_engie_entry(hass)

    ent_reg = er.async_get(hass)
    sibling = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "unique_authentication",
        config_entry=entry,
        translation_key=TRANSLATION_KEY_AUTHENTICATION,
    )

    entity = EngieBeAuthenticationEvent(entry)
    await _add_event_entity(hass, entity)

    hass.states.async_set(sibling.entity_id, "off")
    await hass.async_block_till_done()
    hass.states.async_set(sibling.entity_id, "on")
    await hass.async_block_till_done()

    assert entity.state is not None
    assert entity.state_attributes["event_type"] == "restored"
