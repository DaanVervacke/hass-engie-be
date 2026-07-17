"""Direct tests for ``_tou_calendar._slots_to_events`` date-math edges."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.engie_be._tou_calendar import _slots_to_events, tou_slot_events

_BRUSSELS = ZoneInfo("Europe/Brussels")

# 2026-07-13 is a Monday, giving a full Mon..Sun spread within the
# start..horizon window below (horizon is exactly 7 days later, so the
# start weekday recurs once at the far edge of the window too).
_START = datetime(2026, 7, 13, 10, 0, tzinfo=_BRUSSELS)
_HORIZON = _START + timedelta(days=7)


def test_midnight_rollover_end_lands_on_next_day() -> None:
    """A slot with ``endTime`` "00:00" ends at next-day midnight, not same-day."""
    schedule = {
        "tuesday": [{"startTime": "22:00", "endTime": "00:00", "slotCode": "OFFPEAK"}]
    }
    events = _slots_to_events(
        direction="offtake",
        schedule=schedule,
        start=_START,
        horizon=_HORIZON,
    )
    assert len(events) == 1
    event = events[0]
    assert event.start == datetime(2026, 7, 14, 20, 0, tzinfo=UTC)
    assert event.end == datetime(2026, 7, 14, 22, 0, tzinfo=UTC)
    assert event.end.date() == event.start.date()


def test_active_event_straddling_start_is_included() -> None:
    """A slot that started before ``start`` and ends after it is emitted as-is."""
    schedule = {
        "monday": [{"startTime": "08:00", "endTime": "12:00", "slotCode": "PEAK"}]
    }
    events = _slots_to_events(
        direction="offtake",
        schedule=schedule,
        start=_START,
        horizon=_HORIZON,
    )
    active = [
        event
        for event in events
        if event.start == datetime(2026, 7, 13, 6, 0, tzinfo=UTC)
    ]
    assert len(active) == 1
    assert active[0].end == datetime(2026, 7, 13, 10, 0, tzinfo=UTC)


def test_slot_fully_before_start_is_clipped() -> None:
    """
    A slot whose end is at/before ``start`` is omitted for that occurrence.

    The following week's occurrence of the same weekday/time is not "past"
    relative to ``start`` and must still be emitted, proving the clip is
    based on the absolute instant rather than the weekday alone.
    """
    schedule = {
        "monday": [{"startTime": "06:00", "endTime": "08:00", "slotCode": "OFFPEAK"}]
    }
    events = _slots_to_events(
        direction="offtake",
        schedule=schedule,
        start=_START,
        horizon=_HORIZON,
    )
    starts = {event.start.date() for event in events}
    assert date(2026, 7, 13) not in starts
    assert date(2026, 7, 20) in starts


def test_slot_at_or_after_horizon_is_clipped() -> None:
    """
    A slot starting at/after ``horizon`` is omitted for that occurrence.

    The earlier occurrence of the same weekday/time, well inside the
    window, must still be emitted, proving the clip is based on the
    absolute instant rather than the weekday alone.
    """
    schedule = {
        "monday": [{"startTime": "12:00", "endTime": "14:00", "slotCode": "PEAK"}]
    }
    events = _slots_to_events(
        direction="offtake",
        schedule=schedule,
        start=_START,
        horizon=_HORIZON,
    )
    starts = {event.start.date() for event in events}
    assert date(2026, 7, 13) in starts
    assert date(2026, 7, 20) not in starts


def test_dst_spring_forward_slot_duration_matches_real_elapsed_time() -> None:
    """
    A slot straddling the spring-forward gap must report its real duration.

    2026-03-29 is the Brussels DST transition day (clocks jump from
    01:59:59 CET straight to 03:00:00 CEST). A schedule slot from 01:00
    to 04:00 nominally spans 3 wall-clock hours but only 2 real hours
    elapse, since the 02:00-02:59 hour does not exist that day.
    """
    start = datetime(2026, 3, 29, 0, 0, tzinfo=_BRUSSELS)
    horizon = start + timedelta(days=1)
    schedule = {
        "sunday": [{"startTime": "01:00", "endTime": "04:00", "slotCode": "OFFPEAK"}]
    }
    events = _slots_to_events(
        direction="offtake",
        schedule=schedule,
        start=start,
        horizon=horizon,
    )
    assert len(events) == 1
    event = events[0]
    real_seconds = event.end.timestamp() - event.start.timestamp()
    assert real_seconds == timedelta(hours=2).total_seconds()
    assert (event.end - event.start) == timedelta(hours=2)


def _make_coordinator(tou_data: object = None, *, empty: bool = False) -> MagicMock:
    """Return a mock coordinator with a ``tou_schedules`` wrapper."""
    coordinator = MagicMock()
    if empty:
        coordinator.data = {}
    else:
        coordinator.data = {"tou_schedules": {"data": tou_data}}
    return coordinator


def test_tou_slot_events_no_tou_schedules_key_returns_empty() -> None:
    """A coordinator without a ``tou_schedules`` key at all yields no events."""
    coordinator = _make_coordinator(empty=True)
    assert tou_slot_events(coordinator) == []


def test_tou_slot_events_valid_schedule_produces_events() -> None:
    """A well-formed schedule yields at least one CalendarEvent."""
    coordinator = _make_coordinator(
        {
            "items": [
                {
                    "eanWithSuffix": "541448820070000000_ID1",
                    "supplierSchedule": {
                        "offtake": {
                            "monday": [
                                {
                                    "startTime": "06:00",
                                    "endTime": "22:00",
                                    "slotCode": "PEAK",
                                }
                            ]
                        },
                        "injection": {},
                    },
                }
            ]
        }
    )
    events = tou_slot_events(coordinator)
    assert len(events) > 0
    assert events[0].summary == "Peak (offtake)"


def test_tou_slot_events_items_not_a_list_returns_empty() -> None:
    """A ``tou_schedules`` payload with a non-list ``items`` key yields no events."""
    coordinator = _make_coordinator({"items": "not-a-list"})
    assert tou_slot_events(coordinator) == []


def test_tou_slot_events_non_dict_item_is_skipped() -> None:
    """Non-dict entries in ``items`` are skipped rather than raising."""
    coordinator = _make_coordinator({"items": ["not-a-dict", 42]})
    assert tou_slot_events(coordinator) == []


def test_tou_slot_events_missing_ean_is_skipped() -> None:
    """An item with a non-string (or missing) ``eanWithSuffix`` is skipped."""
    coordinator = _make_coordinator({"items": [{"eanWithSuffix": 12345}]})
    assert tou_slot_events(coordinator) == []


def test_tou_slot_events_missing_supplier_schedule_is_skipped() -> None:
    """An item with a missing or non-dict ``supplierSchedule`` is skipped."""
    coordinator = _make_coordinator(
        {
            "items": [
                {
                    "eanWithSuffix": "541_ID1",
                    "supplierSchedule": None,
                }
            ]
        }
    )
    assert tou_slot_events(coordinator) == []


def test_tou_slot_events_non_dict_direction_schedule_is_skipped() -> None:
    """A non-dict ``offtake``/``injection`` block is skipped for that direction."""
    coordinator = _make_coordinator(
        {
            "items": [
                {
                    "eanWithSuffix": "541_ID1",
                    "supplierSchedule": {
                        "offtake": "not-a-dict",
                        "injection": 42,
                    },
                }
            ]
        }
    )
    assert tou_slot_events(coordinator) == []


def test_slots_to_events_non_dict_slot_is_skipped() -> None:
    """A non-dict element in a weekday slot list is skipped rather than raising."""
    schedule = {"monday": ["not-a-dict", 42]}
    events = _slots_to_events(
        direction="offtake",
        schedule=schedule,
        start=_START,
        horizon=_HORIZON,
    )
    assert events == []


@pytest.mark.parametrize(
    "slot",
    [
        pytest.param(
            {"startTime": "08:00", "endTime": "12:00"}, id="missing-slot-code"
        ),
        pytest.param(
            {"startTime": "08:00", "endTime": "12:00", "slotCode": 42},
            id="non-string-slot-code",
        ),
        pytest.param(
            {"startTime": "bad", "endTime": "12:00", "slotCode": "PEAK"},
            id="unparseable-start-time",
        ),
        pytest.param({"endTime": "12:00", "slotCode": "PEAK"}, id="missing-start-time"),
    ],
)
def test_slots_to_events_incomplete_slot_is_skipped(slot: dict[str, object]) -> None:
    """A slot missing/malformed start, end, or code is skipped rather than raising."""
    schedule = {"monday": [slot]}
    events = _slots_to_events(
        direction="offtake",
        schedule=schedule,
        start=_START,
        horizon=_HORIZON,
    )
    assert events == []
