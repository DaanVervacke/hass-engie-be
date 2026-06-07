"""Tests for the ``_happy_hour`` module helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.engie_be._happy_hour import (
    happy_hour_events,
    happy_hour_window,
    happy_hour_windows,
    is_enrolled_from_flags,
    is_happy_hour_active,
)
from custom_components.engie_be.const import HAPPY_HOURS_SERVICE_ENABLED_KEY

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    """Return a fresh copy of the named feature-flags fixture."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_enrolled_fixture_reports_true() -> None:
    """The sanitized enrolled fixture flips the helper to True."""
    assert is_enrolled_from_flags(_load("feature_flags_enrolled.json")) is True


def test_not_enrolled_fixture_reports_false() -> None:
    """The sanitized not-enrolled fixture keeps the helper at False."""
    assert is_enrolled_from_flags(_load("feature_flags_not_enrolled.json")) is False


@pytest.mark.parametrize(
    "flags",
    [
        None,
        "not a dict",
        123,
        [],
        {},
        {"happy-hours-shown": {"value": True}},
        {HAPPY_HOURS_SERVICE_ENABLED_KEY: None},
        {HAPPY_HOURS_SERVICE_ENABLED_KEY: "true"},
        {HAPPY_HOURS_SERVICE_ENABLED_KEY: {}},
        {HAPPY_HOURS_SERVICE_ENABLED_KEY: {"reason": "TARGETING_MATCH"}},
        {HAPPY_HOURS_SERVICE_ENABLED_KEY: {"value": False}},
        {HAPPY_HOURS_SERVICE_ENABLED_KEY: {"value": None}},
        {HAPPY_HOURS_SERVICE_ENABLED_KEY: {"value": 0}},
        {HAPPY_HOURS_SERVICE_ENABLED_KEY: {"value": ""}},
    ],
)
def test_non_enrolled_shapes_return_false(flags: object) -> None:
    """Every observed and plausible non-enrolled shape must return False."""
    assert is_enrolled_from_flags(flags) is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "flags",
    [
        {HAPPY_HOURS_SERVICE_ENABLED_KEY: {"value": True}},
        {
            HAPPY_HOURS_SERVICE_ENABLED_KEY: {
                "value": True,
                "reason": "TARGETING_MATCH",
            },
            "happy-hours-shown": {"value": False},
        },
    ],
)
def test_enrolled_shapes_return_true(flags: dict[str, Any]) -> None:
    """Enrolled means service-enabled.value is truthy, regardless of siblings."""
    assert is_enrolled_from_flags(flags) is True


def test_ignores_sibling_happy_hours_shown_flag() -> None:
    """``happy-hours-shown`` must not influence enrolment."""
    flags = {"happy-hours-shown": {"value": True}}
    assert is_enrolled_from_flags(flags) is False


# ---------------------------------------------------------------------------
# happy_hour_events() merge logic (store + live, dedup by start)
# ---------------------------------------------------------------------------


def _coord(
    happy_hour_payload: dict | None,
    *,
    store_windows: list[dict] | None = None,
    subentry_id: str = "sub_a",
) -> MagicMock:
    """Build a coordinator stub for ``happy_hour_events``."""
    coordinator = MagicMock()
    coordinator.data = (
        {"happy_hour": {"data": happy_hour_payload}}
        if happy_hour_payload is not None
        else {}
    )
    coordinator.subentry = MagicMock()
    coordinator.subentry.subentry_id = subentry_id
    coordinator.config_entry = MagicMock()

    if store_windows is None:
        coordinator.config_entry.runtime_data = None
    else:
        store = MagicMock()
        store.windows = list(store_windows)
        sub_data = MagicMock()
        sub_data.happy_hours_store = store
        runtime = MagicMock()
        runtime.subentry_data = {subentry_id: sub_data}
        coordinator.config_entry.runtime_data = runtime
    return coordinator


def test_happy_hour_events_empty_when_no_data() -> None:
    """No payload and no store yields an empty list."""
    coord = _coord(None)
    assert happy_hour_events(coord) == []


def test_happy_hour_events_returns_live_window_when_store_missing() -> None:
    """A live window surfaces even when the store is unavailable."""
    coord = _coord(
        {
            "tomorrow": {
                "startTime": "2026-05-23T12:00:00+02:00",
                "endTime": "2026-05-23T15:00:00+02:00",
            },
        },
    )
    events = happy_hour_events(coord)
    assert len(events) == 1
    assert events[0].summary == "Happy Hour"
    assert events[0].description == "Free energy window"
    assert events[0].start == datetime.fromisoformat("2026-05-23T12:00:00+02:00")


def test_happy_hour_events_returns_past_windows_from_store() -> None:
    """Past windows in the store are surfaced (no forward-only filter)."""
    coord = _coord(
        None,
        store_windows=[
            {
                "start": "2025-01-15T10:00:00+01:00",
                "end": "2025-01-15T13:00:00+01:00",
            },
            {
                "start": "2026-03-20T11:00:00+01:00",
                "end": "2026-03-20T14:00:00+01:00",
            },
        ],
    )
    events = happy_hour_events(coord)
    starts = sorted(event.start.isoformat() for event in events)
    assert starts == [
        "2025-01-15T10:00:00+01:00",
        "2026-03-20T11:00:00+01:00",
    ]


def test_happy_hour_events_dedups_live_window_against_store() -> None:
    """A live window already in the store is not emitted twice."""
    coord = _coord(
        {
            "tomorrow": {
                "startTime": "2026-05-23T12:00:00+02:00",
                "endTime": "2026-05-23T15:00:00+02:00",
            },
        },
        store_windows=[
            {
                "start": "2026-05-23T12:00:00+02:00",
                "end": "2026-05-23T15:00:00+02:00",
            },
        ],
    )
    events = happy_hour_events(coord)
    assert len(events) == 1


def test_happy_hour_events_combines_store_history_with_live() -> None:
    """Disjoint store entries plus a live entry all appear."""
    coord = _coord(
        {
            "tomorrow": {
                "startTime": "2026-05-23T12:00:00+02:00",
                "endTime": "2026-05-23T15:00:00+02:00",
            },
        },
        store_windows=[
            {
                "start": "2026-05-21T10:00:00+02:00",
                "end": "2026-05-21T13:00:00+02:00",
            },
        ],
    )
    events = happy_hour_events(coord)
    starts = sorted(event.start.isoformat() for event in events)
    assert starts == [
        "2026-05-21T10:00:00+02:00",
        "2026-05-23T12:00:00+02:00",
    ]


def test_happy_hour_events_skips_invalid_store_entries() -> None:
    """Store entries with missing or tz-naive timestamps are dropped."""
    coord = _coord(
        None,
        store_windows=[
            {"start": "2026-05-23T12:00:00", "end": "2026-05-23T15:00:00"},  # tz-naive
            {"start": "not-a-date", "end": "2026-05-23T15:00:00+02:00"},
            {"start": "2026-05-23T12:00:00+02:00", "end": "2026-05-23T15:00:00+02:00"},
        ],
    )
    events = happy_hour_events(coord)
    assert len(events) == 1
    assert events[0].start == datetime.fromisoformat("2026-05-23T12:00:00+02:00")


# ---------------------------------------------------------------------------
# Window parsing across the ``today`` / ``tomorrow`` keys
# ---------------------------------------------------------------------------

# A window re-published under the ``today`` key once midnight passes.
_TODAY_KEY_PAYLOAD = {
    "today": {
        "startTime": "2026-06-07T11:00:00+02:00",
        "endTime": "2026-06-07T17:00:00+02:00",
    },
}
# A window announced the day before under the ``tomorrow`` key.
_TOMORROW_KEY_PAYLOAD = {
    "tomorrow": {
        "startTime": "2026-06-08T12:00:00+02:00",
        "endTime": "2026-06-08T15:00:00+02:00",
    },
}
# Both keys present carrying two distinct windows.
_BOTH_KEYS_PAYLOAD = {
    "today": {
        "startTime": "2026-06-07T11:00:00+02:00",
        "endTime": "2026-06-07T17:00:00+02:00",
    },
    "tomorrow": {
        "startTime": "2026-06-08T12:00:00+02:00",
        "endTime": "2026-06-08T15:00:00+02:00",
    },
}


def test_happy_hour_windows_parses_today_key() -> None:
    """A window under the ``today`` key is parsed (post-midnight regression)."""
    windows = happy_hour_windows(_coord(_TODAY_KEY_PAYLOAD))
    assert windows == [
        (
            datetime.fromisoformat("2026-06-07T11:00:00+02:00"),
            datetime.fromisoformat("2026-06-07T17:00:00+02:00"),
        ),
    ]


def test_happy_hour_windows_parses_tomorrow_key() -> None:
    """A window under the ``tomorrow`` key is parsed (evening announcement)."""
    windows = happy_hour_windows(_coord(_TOMORROW_KEY_PAYLOAD))
    assert windows == [
        (
            datetime.fromisoformat("2026-06-08T12:00:00+02:00"),
            datetime.fromisoformat("2026-06-08T15:00:00+02:00"),
        ),
    ]


def test_happy_hour_windows_parses_both_keys_sorted() -> None:
    """Both keys are parsed and returned earliest-start first."""
    windows = happy_hour_windows(_coord(_BOTH_KEYS_PAYLOAD))
    starts = [start.isoformat() for start, _end in windows]
    assert starts == [
        "2026-06-07T11:00:00+02:00",
        "2026-06-08T12:00:00+02:00",
    ]


def test_happy_hour_windows_empty_payload_returns_empty() -> None:
    """An explicit no-event payload (``{}``) yields no windows."""
    assert happy_hour_windows(_coord({})) == []


def test_happy_hour_windows_no_data_returns_empty() -> None:
    """Absent coordinator data yields no windows."""
    assert happy_hour_windows(_coord(None)) == []


def test_happy_hour_windows_skips_tz_naive_sub_payload() -> None:
    """A tz-naive sub-payload is skipped while a valid sibling is kept."""
    payload = {
        "today": {
            "startTime": "2026-06-07T11:00:00",
            "endTime": "2026-06-07T17:00:00",
        },
        "tomorrow": {
            "startTime": "2026-06-08T12:00:00+02:00",
            "endTime": "2026-06-08T15:00:00+02:00",
        },
    }
    windows = happy_hour_windows(_coord(payload))
    assert windows == [
        (
            datetime.fromisoformat("2026-06-08T12:00:00+02:00"),
            datetime.fromisoformat("2026-06-08T15:00:00+02:00"),
        ),
    ]


def test_happy_hour_window_returns_today_key_window() -> None:
    """The single-window helper surfaces a ``today`` key window."""
    window = happy_hour_window(_coord(_TODAY_KEY_PAYLOAD))
    assert window == (
        datetime.fromisoformat("2026-06-07T11:00:00+02:00"),
        datetime.fromisoformat("2026-06-07T17:00:00+02:00"),
    )


def test_happy_hour_window_returns_earliest_of_both_keys() -> None:
    """With both keys present the earliest-start window is returned."""
    window = happy_hour_window(_coord(_BOTH_KEYS_PAYLOAD))
    assert window is not None
    assert window[0] == datetime.fromisoformat("2026-06-07T11:00:00+02:00")


def test_happy_hour_window_none_when_no_event() -> None:
    """An explicit no-event payload yields ``None``."""
    assert happy_hour_window(_coord({})) is None


def test_is_happy_hour_active_inside_today_window() -> None:
    """``now`` inside a ``today`` key window reports active."""
    now = datetime.fromisoformat("2026-06-07T12:00:00+02:00")
    assert is_happy_hour_active(_coord(_TODAY_KEY_PAYLOAD), now) is True


def test_is_happy_hour_active_before_window() -> None:
    """``now`` before the window start reports inactive."""
    now = datetime.fromisoformat("2026-06-07T09:00:00+02:00")
    assert is_happy_hour_active(_coord(_TODAY_KEY_PAYLOAD), now) is False


def test_is_happy_hour_active_inside_second_window() -> None:
    """``now`` inside the later of two windows reports active."""
    now = datetime.fromisoformat("2026-06-08T13:00:00+02:00")
    assert is_happy_hour_active(_coord(_BOTH_KEYS_PAYLOAD), now) is True


def test_is_happy_hour_active_false_when_no_event() -> None:
    """No scheduled event reports inactive regardless of ``now``."""
    now = datetime.fromisoformat("2026-06-07T12:00:00+02:00")
    assert is_happy_hour_active(_coord({}), now) is False
