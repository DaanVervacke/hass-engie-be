"""
Defensive-branch coverage for ``custom_components.engie_be._peaks``.

Targets the remaining guard branches exercised by crafted inputs rather
than a full setup flow:

- L52 -- ``peaks_meta`` returns ``None`` when the wrapper's ``year`` /
  ``month`` are not integers (payload drift / partial wrapper).
- L121 -- ``_build_event`` rejects non-string ``start`` / ``end`` bounds.
- L125-126 -- ``_build_event`` swallows an unparseable ISO string.
- L129 -- ``_build_event`` rejects naive (tz-unaware) datetimes, which a
  timed ``CalendarEvent`` cannot accept.

Tests exercise the private helpers directly with crafted payloads; this
mirrors the pattern used by ``tests/test_sensor_defensive_branches.py``
and ``tests/test_coordinator_defensive_branches.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.engie_be._peaks import _build_event, peaks_meta


def _coordinator(data: object) -> MagicMock:
    """Return a coordinator stub whose ``data`` attribute is ``data``."""
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


# ---------------------------------------------------------------------------
# peaks_meta
# ---------------------------------------------------------------------------


def test_peaks_meta_returns_none_for_non_int_year_month() -> None:
    """A peaks wrapper with non-int ``year`` / ``month`` yields ``None``."""
    coordinator = _coordinator({"peaks": {"year": "2026", "month": 4}})
    assert peaks_meta(coordinator) is None


def test_peaks_meta_returns_metadata_for_valid_wrapper() -> None:
    """A well-formed wrapper round-trips year/month/is_fallback."""
    coordinator = _coordinator(
        {"peaks": {"year": 2026, "month": 4, "is_fallback": True}},
    )
    assert peaks_meta(coordinator) == {
        "year": 2026,
        "month": 4,
        "is_fallback": True,
    }


# ---------------------------------------------------------------------------
# _build_event
# ---------------------------------------------------------------------------


def test_build_event_returns_none_for_non_string_bounds() -> None:
    """Non-string ``start`` / ``end`` short-circuit to ``None``."""
    assert _build_event(1, 2, "3.50000000", "0.87500000") is None


def test_build_event_returns_none_for_unparseable_iso() -> None:
    """An unparseable ISO string is swallowed and yields ``None``."""
    assert _build_event("not-a-date", "also-not-a-date", None, None) is None


def test_build_event_returns_none_for_naive_datetimes() -> None:
    """Naive (tz-unaware) datetimes are rejected for timed events."""
    assert (
        _build_event(
            "2026-04-15T18:00:00",
            "2026-04-15T18:15:00",
            None,
            None,
        )
        is None
    )
