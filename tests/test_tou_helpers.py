"""
Pure-helper unit tests for _tou.py defensive branches.

The helper is tested via its public functions; this file specifically
covers the malformed-input guards that the coordinator/sensor tests do
not exercise.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.engie_be._tou import (
    _parse_hhmm,
    current_slot,
    has_multiple_slot_codes,
    schedule_for_ean,
)

pytestmark = pytest.mark.tou

_BRUSSELS = ZoneInfo("Europe/Brussels")
_UTC = ZoneInfo("UTC")


# --- _parse_hhmm defensive branches (lines 28, 31, 34-35, 38) ---


def test_parse_hhmm_returns_none_for_non_string() -> None:
    """Non-string input (int, None, dict) returns None."""
    assert _parse_hhmm(None) is None
    assert _parse_hhmm(6) is None
    assert _parse_hhmm({"hour": 6}) is None


def test_parse_hhmm_returns_none_for_missing_colon() -> None:
    """'0600' with no colon returns None."""
    assert _parse_hhmm("0600") is None
    assert _parse_hhmm("6") is None


def test_parse_hhmm_returns_none_for_non_integer_parts() -> None:
    """'HH:MM' with non-integer digits returns None."""
    assert _parse_hhmm("ab:cd") is None
    assert _parse_hhmm("06:xx") is None


def test_parse_hhmm_returns_none_for_out_of_range() -> None:
    """Hour >= 24 or minute >= 60 returns None."""
    assert _parse_hhmm("25:00") is None
    assert _parse_hhmm("06:60") is None
    assert _parse_hhmm("-1:00") is None


def test_parse_hhmm_accepts_zero_zero_sentinel() -> None:
    """'00:00' parses cleanly (end-of-day sentinel)."""
    assert _parse_hhmm("00:00") == time(hour=0, minute=0)


def test_parse_hhmm_accepts_normal_values() -> None:
    """Regular HH:MM values parse to the expected time object."""
    assert _parse_hhmm("06:30") == time(hour=6, minute=30)
    assert _parse_hhmm("23:59") == time(hour=23, minute=59)


# --- current_slot defensive branch (line 72) ---


def test_current_slot_skips_malformed_slot() -> None:
    """A slot with missing startTime is skipped; the next valid one wins."""
    schedule = {
        "monday": [
            {"startTime": "bogus", "endTime": "06:00", "slotCode": "PEAK"},
            {"startTime": "00:00", "endTime": "06:00", "slotCode": "OFFPEAK"},
        ],
        "tuesday": [],
        "wednesday": [],
        "thursday": [],
        "friday": [],
        "saturday": [],
        "sunday": [],
    }
    # 04:00 Brussels on Monday 2026-07-06.
    now = datetime(2026, 7, 6, 4, 0, tzinfo=_BRUSSELS)
    code, end_dt = current_slot(schedule, now=now)
    assert code == "offpeak"
    assert end_dt is not None


def test_current_slot_returns_none_when_no_slot_covers_now() -> None:
    """Empty schedule for today returns (None, None)."""
    schedule = {
        k: []
        for k in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }
    now = datetime(2026, 7, 6, 4, 0, tzinfo=_BRUSSELS)
    assert current_slot(schedule, now=now) == (None, None)


# --- current_slot DST fall-back fold handling ---

# 2026-10-25 is the last Sunday of October: Brussels clocks fall back from
# CEST (+02:00) to CET (+01:00) at 03:00 CEST local (01:00 UTC), so the
# 02:00-03:00 wall-clock hour occurs twice. fold=0 is the first (CEST)
# occurrence, fold=1 is the second (CET) occurrence.
_DST_SCHEDULE = {
    "monday": [],
    "tuesday": [],
    "wednesday": [],
    "thursday": [],
    "friday": [],
    "saturday": [],
    "sunday": [
        {"startTime": "02:00", "endTime": "02:45", "slotCode": "PEAK"},
        {"startTime": "02:45", "endTime": "03:00", "slotCode": "OFFPEAK"},
    ],
}


def test_current_slot_dst_fallback_fold_zero_reports_cest_transition() -> None:
    """fold=0 (first, CEST occurrence) resolves to the CEST end-of-slot instant."""
    now = datetime(2026, 10, 25, 2, 15, tzinfo=_BRUSSELS, fold=0)
    code, end_dt = current_slot(_DST_SCHEDULE, now=now)
    assert code == "peak"
    assert end_dt is not None
    assert end_dt.utcoffset().total_seconds() == 2 * 3600
    # Absolute instant: 02:45 CEST == 00:45 UTC.
    assert end_dt.astimezone(_UTC).hour == 0
    assert end_dt.astimezone(_UTC).minute == 45


def test_current_slot_dst_fallback_fold_one_reports_cet_transition() -> None:
    """
    fold=1 (second, CET occurrence) resolves to the CET end-of-slot instant.

    Regression test: without propagating ``fold`` from ``now_local`` into
    the constructed slot-boundary datetimes, the returned transition
    instant would incorrectly collapse to the CEST (fold=0) occurrence,
    reporting a UTC instant one hour earlier than the true CET transition.
    """
    now = datetime(2026, 10, 25, 2, 15, tzinfo=_BRUSSELS, fold=1)
    code, end_dt = current_slot(_DST_SCHEDULE, now=now)
    assert code == "peak"
    assert end_dt is not None
    assert end_dt.utcoffset().total_seconds() == 1 * 3600
    # Absolute instant: 02:45 CET == 01:45 UTC (one hour later than the
    # fold=0 case above, despite identical wall-clock time).
    assert end_dt.astimezone(_UTC).hour == 1
    assert end_dt.astimezone(_UTC).minute == 45


# --- schedule_for_ean defensive branches (lines 93, 97) ---


def test_schedule_for_ean_returns_none_when_items_not_list() -> None:
    """No ``items`` key or wrong type returns None."""
    assert schedule_for_ean({}, "any_ID1") is None
    assert schedule_for_ean({"items": "not a list"}, "any_ID1") is None
    assert schedule_for_ean({"items": None}, "any_ID1") is None


def test_schedule_for_ean_returns_none_when_ean_absent() -> None:
    """EAN not present in items returns None."""
    data = {
        "items": [
            {"eanWithSuffix": "other_ID1", "supplierSchedule": {}},
        ]
    }
    assert schedule_for_ean(data, "missing_ID1") is None


def test_schedule_for_ean_ignores_non_dict_items() -> None:
    """Non-dict items are skipped without raising."""
    data = {
        "items": [
            "not a dict",
            None,
            {"eanWithSuffix": "wanted_ID1", "supplierSchedule": {}},
        ]
    }
    result = schedule_for_ean(data, "wanted_ID1")
    assert result is not None
    assert result["eanWithSuffix"] == "wanted_ID1"


# --- has_multiple_slot_codes defensive branch (line 111) ---


def test_has_multiple_slot_codes_skips_non_list_weekday() -> None:
    """A weekday whose value isn't a list is silently skipped."""
    schedule = {
        "monday": "oops",  # not a list
        "tuesday": [{"slotCode": "PEAK"}, {"slotCode": "OFFPEAK"}],
        "wednesday": [],
        "thursday": [],
        "friday": [],
        "saturday": [],
        "sunday": [],
    }
    assert has_multiple_slot_codes(schedule) is True


def test_has_multiple_slot_codes_false_for_flat_schedule() -> None:
    """All-OFFPEAK schedule returns False (no meaningful transitions)."""
    schedule = {
        k: [{"slotCode": "OFFPEAK"}]
        for k in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }
    assert has_multiple_slot_codes(schedule) is False


def test_has_multiple_slot_codes_ignores_non_dict_slots() -> None:
    """Non-dict slots and slots without slotCode are skipped."""
    schedule = {
        "monday": ["oops", {"otherKey": "value"}, {"slotCode": "PEAK"}],
        "tuesday": [{"slotCode": "OFFPEAK"}],
        "wednesday": [],
        "thursday": [],
        "friday": [],
        "saturday": [],
        "sunday": [],
    }
    assert has_multiple_slot_codes(schedule) is True
