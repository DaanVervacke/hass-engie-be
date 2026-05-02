"""Tests for the ENGIE Belgium aggregated calendar entity."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from homeassistant.components.calendar import CalendarEvent

from custom_components.engie_be.calendar import EngieBeCalendar

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


def _make_coordinator(data: dict | None) -> MagicMock:
    """Build a MagicMock coordinator stub with the given ``.data``."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_successful_fetch = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry_id"
    return coordinator


def test_calendar_unique_id_namespaced_to_entry() -> None:
    """The calendar carries a stable per-entry unique_id."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    calendar = EngieBeCalendar(coordinator)
    assert calendar.unique_id == "test_entry_id_calendar"


def test_event_property_returns_captar_peak() -> None:
    """``event`` exposes the captar peak window as a ``CalendarEvent``."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    calendar = EngieBeCalendar(coordinator)
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
    calendar = EngieBeCalendar(coordinator)
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
    calendar = EngieBeCalendar(coordinator)
    event = calendar.event
    assert event is not None
    assert event.description is not None
    assert "Fallback" not in event.description


async def test_async_get_events_returns_event_when_overlapping() -> None:
    """``async_get_events`` returns the event when its window overlaps."""
    coordinator = _make_coordinator({"peaks": _wrap(_peaks())})
    calendar = EngieBeCalendar(coordinator)
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
    calendar = EngieBeCalendar(coordinator)
    events = await calendar.async_get_events(
        hass=MagicMock(),
        start_date=datetime.fromisoformat("2026-05-01T00:00:00+02:00"),
        end_date=datetime.fromisoformat("2026-05-31T23:59:59+02:00"),
    )
    assert events == []


async def test_async_get_events_returns_empty_when_no_providers_yield() -> None:
    """``async_get_events`` returns ``[]`` when no provider yields anything."""
    coordinator = _make_coordinator({"items": []})
    calendar = EngieBeCalendar(coordinator)
    events = await calendar.async_get_events(
        hass=MagicMock(),
        start_date=datetime.fromisoformat("2026-04-01T00:00:00+02:00"),
        end_date=datetime.fromisoformat("2026-04-30T23:59:59+02:00"),
    )
    assert events == []
