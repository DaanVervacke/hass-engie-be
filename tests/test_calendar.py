"""Tests for the ENGIE Belgium aggregated calendar entity."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt as dt_util

from custom_components.engie_be.calendar import (
    EngieBeCalendar,
    async_setup_entry,
    happy_hour_events,
)
from custom_components.engie_be.const import SUBENTRY_TYPE_BUSINESS_AGREEMENT

if TYPE_CHECKING:
    import pytest

_PEAKS_FIXTURE = Path(__file__).parent / "fixtures" / "peaks_2026_04.json"


def _peaks() -> dict:
    """Return a fresh copy of the peaks fixture payload."""
    return json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))


def _wrap(
    peaks: dict,
    *,
    year: int = 2026,
    month: int = 4,
    is_fallback: bool = False,
) -> dict:
    """Return the coordinator wrapper dict the calendar reads from."""
    return {
        "data": peaks,
        "year": year,
        "month": month,
        "is_fallback": is_fallback,
    }


def _make_subentry(
    subentry_id: str = "sub_test",
    title: str = "Test Account",
) -> MagicMock:
    """Build a MagicMock ConfigSubentry stub."""
    subentry = MagicMock()
    subentry.subentry_id = subentry_id
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = title
    return subentry


def _make_coordinator(
    data: dict | None,
    *,
    subentry_id: str = "sub_test",
    history: list[dict] | None = None,
) -> MagicMock:
    """
    Build a MagicMock per-subentry coordinator stub.

    ``captar_peak_events`` walks
    ``coordinator.config_entry.runtime_data.subentry_data[subentry_id].peaks_store.peaks``,
    so the runtime layout has to mirror that exactly.
    """
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    # The provider reads ``coordinator.subentry.subentry_id``.
    coordinator.subentry = MagicMock()
    coordinator.subentry.subentry_id = subentry_id

    if history is None:
        # Explicit None -> no runtime data; provider must still cope.
        coordinator.config_entry.runtime_data = None
    else:
        store = MagicMock()
        store.peaks = list(history)
        sub_data = MagicMock()
        sub_data.peaks_store = store
        runtime = MagicMock()
        runtime.subentry_data = {subentry_id: sub_data}
        coordinator.config_entry.runtime_data = runtime
    return coordinator


def test_calendar_unique_id_namespaced_to_subentry() -> None:
    """The calendar carries a stable per-subentry unique_id."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())}, subentry_id="sub_xyz")
    subentry = _make_subentry(subentry_id="sub_xyz")
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    assert calendar.unique_id == "test_entry_id_sub_xyz_calendar"


def test_calendar_naming_contract_delegates_to_ha_composition() -> None:
    """
    Pin has_entity_name=True + translation_key naming for HA 2026.4+.

    Regression guard for the v0.9.0b1 -> b2 fix: setting
    ``_attr_has_entity_name = False`` together with a brand-prefixed
    ``_attr_name`` produced a doubled friendly_name on HA 2026.4+, where
    the composition logic prepends the device name regardless of the
    has_entity_name flag when the registry stores no rename. The
    calendar must inherit has_entity_name=True and expose its name via
    translation key so HA composes ``<device-name> ENGIE Belgium``.
    """
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    assert calendar.has_entity_name is True
    assert calendar.translation_key == "engie_belgium"
    assert not hasattr(calendar, "_attr_name") or calendar._attr_name is None


def test_event_property_returns_captar_peak() -> None:
    """``event`` exposes the captar peak window as a ``CalendarEvent``."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    event = calendar.event
    assert isinstance(event, CalendarEvent)
    assert event.summary == "Captar monthly peak"
    assert event.start == datetime.fromisoformat("2026-04-15T18:00:00+02:00")
    assert event.end == datetime.fromisoformat("2026-04-15T18:15:00+02:00")
    assert event.description is not None
    assert "Peak power: 3.50000000 kW" in event.description
    assert "Peak energy: 0.87500000 kWh" in event.description


def test_event_returns_none_when_no_providers_yield_events() -> None:
    """``event`` is ``None`` when no provider yields anything."""
    coordinator = _make_coordinator({"items": []})
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    assert calendar.event is None


def test_event_fallback_does_not_annotate_description() -> None:
    """
    Fallback months do not add anything to the event description.

    The provenance is already exposed via the ``peak_is_fallback`` sensor
    attribute, so the calendar description stays focused on peak values.
    """
    coordinator = _make_coordinator(
        {"peaks": _wrap(_peaks(), year=2026, month=3, is_fallback=True)},
    )
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    event = calendar.event
    assert event is not None
    assert event.description is not None
    assert "Fallback" not in event.description


async def test_async_get_events_returns_event_when_overlapping() -> None:
    """``async_get_events`` returns the event when its window overlaps."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    events = await calendar.async_get_events(
        hass=MagicMock(),
        start_date=datetime.fromisoformat("2026-04-01T00:00:00+02:00"),
        end_date=datetime.fromisoformat("2026-04-30T23:59:59+02:00"),
    )
    assert len(events) == 1
    assert events[0].summary == "Captar monthly peak"


async def test_async_get_events_returns_empty_outside_window() -> None:
    """``async_get_events`` returns ``[]`` when the window does not overlap."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    events = await calendar.async_get_events(
        hass=MagicMock(),
        start_date=datetime.fromisoformat("2026-05-01T00:00:00+02:00"),
        end_date=datetime.fromisoformat("2026-05-31T23:59:59+02:00"),
    )
    assert events == []


async def test_async_get_events_returns_empty_when_no_providers_yield() -> None:
    """``async_get_events`` returns ``[]`` when no provider yields anything."""
    coordinator = _make_coordinator({"items": []})
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    events = await calendar.async_get_events(
        hass=MagicMock(),
        start_date=datetime.fromisoformat("2026-04-01T00:00:00+02:00"),
        end_date=datetime.fromisoformat("2026-04-30T23:59:59+02:00"),
    )
    assert events == []


async def test_async_get_events_returns_history_plus_current_month() -> None:
    """History entries from the per-subentry store appear with the live month."""
    history = [
        {
            "year": 2026,
            "month": 2,
            "start": "2026-02-10T18:00:00+01:00",
            "end": "2026-02-10T18:15:00+01:00",
            "peakKW": "2.10000000",
            "peakKWh": "0.52500000",
        },
        {
            "year": 2026,
            "month": 3,
            "start": "2026-03-12T19:00:00+01:00",
            "end": "2026-03-12T19:15:00+01:00",
            "peakKW": "2.80000000",
            "peakKWh": "0.70000000",
        },
    ]
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())}, history=history)
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    events = await calendar.async_get_events(
        hass=MagicMock(),
        start_date=datetime.fromisoformat("2026-01-01T00:00:00+01:00"),
        end_date=datetime.fromisoformat("2026-04-30T23:59:59+02:00"),
    )
    starts = sorted(event.start.isoformat() for event in events)
    assert starts == [
        "2026-02-10T18:00:00+01:00",
        "2026-03-12T19:00:00+01:00",
        "2026-04-15T18:00:00+02:00",
    ]


async def test_history_does_not_duplicate_current_month() -> None:
    """A history entry for the active month is not duplicated by the live payload."""
    history = [
        {
            "year": 2026,
            "month": 4,
            "start": "2026-04-15T18:00:00+02:00",
            "end": "2026-04-15T18:15:00+02:00",
            "peakKW": "3.50000000",
            "peakKWh": "0.87500000",
        },
    ]
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())}, history=history)
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    events = await calendar.async_get_events(
        hass=MagicMock(),
        start_date=datetime.fromisoformat("2026-04-01T00:00:00+02:00"),
        end_date=datetime.fromisoformat("2026-04-30T23:59:59+02:00"),
    )
    assert len(events) == 1
    assert events[0].start == datetime.fromisoformat("2026-04-15T18:00:00+02:00")


async def test_async_get_events_handles_missing_subentry_runtime() -> None:
    """A subentry without runtime data falls back to the live payload only."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    # Runtime exists but has no entry for this subentry id.
    runtime = MagicMock()
    runtime.subentry_data = {}
    coordinator.config_entry.runtime_data = runtime

    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    events = await calendar.async_get_events(
        hass=MagicMock(),
        start_date=datetime.fromisoformat("2026-04-01T00:00:00+02:00"),
        end_date=datetime.fromisoformat("2026-04-30T23:59:59+02:00"),
    )
    # Only the current-month live event survives.
    assert len(events) == 1
    assert events[0].start == datetime.fromisoformat("2026-04-15T18:00:00+02:00")


# ---------------------------------------------------------------------------
# Happy Hour event provider
# ---------------------------------------------------------------------------

_HAPPY_HOUR_SCHEDULED = {
    "tomorrow": {
        "startTime": "2026-05-23T12:00:00+02:00",
        "endTime": "2026-05-23T15:00:00+02:00",
    },
}


def test_event_property_surfaces_happy_hour_when_no_peak() -> None:
    """Without peaks, the ``event`` property surfaces the happy-hour window."""
    coordinator = _make_coordinator(
        {"happy_hour": {"data": _HAPPY_HOUR_SCHEDULED}},
    )
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    event = calendar.event
    assert isinstance(event, CalendarEvent)
    assert event.summary == "Happy Hour"
    assert event.start == datetime.fromisoformat("2026-05-23T12:00:00+02:00")
    assert event.end == datetime.fromisoformat("2026-05-23T15:00:00+02:00")
    assert event.description == "Free energy window"


async def test_async_get_events_returns_happy_hour_when_overlapping() -> None:
    """A scheduled happy-hour window is returned for an overlapping range."""
    coordinator = _make_coordinator(
        {"happy_hour": {"data": _HAPPY_HOUR_SCHEDULED}},
    )
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    events = await calendar.async_get_events(
        hass=MagicMock(),
        start_date=datetime.fromisoformat("2026-05-23T00:00:00+02:00"),
        end_date=datetime.fromisoformat("2026-05-23T23:59:59+02:00"),
    )
    assert len(events) == 1
    assert events[0].summary == "Happy Hour"


async def test_async_get_events_skips_happy_hour_outside_window() -> None:
    """A non-overlapping range yields no happy-hour event."""
    coordinator = _make_coordinator(
        {"happy_hour": {"data": _HAPPY_HOUR_SCHEDULED}},
    )
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    events = await calendar.async_get_events(
        hass=MagicMock(),
        start_date=datetime.fromisoformat("2026-05-24T00:00:00+02:00"),
        end_date=datetime.fromisoformat("2026-05-24T23:59:59+02:00"),
    )
    assert events == []


async def test_async_get_events_combines_peak_and_happy_hour() -> None:
    """Peak + happy-hour events are both yielded by ``async_get_events``."""
    coordinator = _make_coordinator(
        {
            "peaks": _wrap(_peaks()),
            "happy_hour": {"data": _HAPPY_HOUR_SCHEDULED},
        },
    )
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    events = await calendar.async_get_events(
        hass=MagicMock(),
        start_date=datetime.fromisoformat("2026-04-01T00:00:00+02:00"),
        end_date=datetime.fromisoformat("2026-05-31T23:59:59+02:00"),
    )
    summaries = sorted(event.summary for event in events)
    assert summaries == ["Captar monthly peak", "Happy Hour"]


def test_event_property_returns_none_when_happy_hour_empty() -> None:
    """An empty ``{}`` happy-hour payload yields no event."""
    coordinator = _make_coordinator({"happy_hour": {"data": {}}})
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    assert calendar.event is None


def test_event_providers_omits_happy_hour_when_not_enrolled() -> None:
    """
    Un-enrolled calendar must not register the Happy Hour event provider.

    When ``happy_hour_enrolled=False`` the per-instance event-provider
    list must NOT include :func:`happy_hour_events`. The baseline
    captar peak provider stays in place.
    """
    coordinator = _make_coordinator({})
    subentry = _make_subentry()

    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=False)

    assert happy_hour_events not in calendar._event_providers
    # Baseline providers still apply; the list is non-empty.
    assert len(calendar._event_providers) >= 1


def test_event_providers_includes_happy_hour_when_enrolled() -> None:
    """The enrolled path appends :func:`happy_hour_events`."""
    coordinator = _make_coordinator({})
    subentry = _make_subentry()

    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)

    assert happy_hour_events in calendar._event_providers


# ---------------------------------------------------------------------------
# event property time-window selection
# ---------------------------------------------------------------------------


def test_event_property_returns_active_window() -> None:
    """An in-progress window is selected as the active event (L167)."""
    now = dt_util.utcnow()
    start = (now - timedelta(hours=1)).isoformat()
    end = (now + timedelta(hours=1)).isoformat()
    coordinator = _make_coordinator(
        {"happy_hour": {"data": {"tomorrow": {"startTime": start, "endTime": end}}}},
    )
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    event = calendar.event
    assert event is not None
    assert event.summary == "Happy Hour"
    assert event.start == datetime.fromisoformat(start)


def test_event_property_returns_next_upcoming_window() -> None:
    """With no active event, the soonest upcoming window is selected (L170)."""
    now = dt_util.utcnow()
    start = (now + timedelta(hours=2)).isoformat()
    end = (now + timedelta(hours=3)).isoformat()
    coordinator = _make_coordinator(
        {"happy_hour": {"data": {"tomorrow": {"startTime": start, "endTime": end}}}},
    )
    subentry = _make_subentry()
    calendar = EngieBeCalendar(coordinator, subentry, happy_hour_enrolled=True)
    event = calendar.event
    assert event is not None
    assert event.summary == "Happy Hour"
    assert event.start == datetime.fromisoformat(start)


# ---------------------------------------------------------------------------
# async_setup_entry subentry routing
# ---------------------------------------------------------------------------


async def test_async_setup_entry_skips_non_business_agreement_subentry() -> None:
    """Subentries that are not business agreements are skipped (L66-67)."""
    subentry = MagicMock()
    subentry.subentry_id = "sub_other"
    subentry.subentry_type = "some_other_type"
    entry = MagicMock()
    entry.subentries = {"sub_other": subentry}
    async_add_entities = MagicMock()

    await async_setup_entry(MagicMock(), entry, async_add_entities)

    async_add_entities.assert_not_called()


async def test_async_setup_entry_warns_when_subentry_runtime_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A BAN subentry without runtime data logs a warning and is skipped (L70-75)."""
    subentry = _make_subentry(subentry_id="sub_ban")
    entry = MagicMock()
    entry.subentries = {"sub_ban": subentry}
    entry.runtime_data.subentry_data = {}
    async_add_entities = MagicMock()

    with caplog.at_level(logging.WARNING, logger="custom_components.engie_be"):
        await async_setup_entry(MagicMock(), entry, async_add_entities)

    async_add_entities.assert_not_called()
    assert "No runtime data for subentry sub_ban" in caplog.text
