"""Unit tests for the pure EPEX slot-boundary helper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.engie_be._epex import next_epex_slot_boundary
from custom_components.engie_be.data import EpexPayload, EpexSlot


def _slot(
    start: datetime, *, duration_minutes: int = 60, value: float = 0.05
) -> EpexSlot:
    """Build a single contiguous EpexSlot starting at ``start``."""
    return EpexSlot(
        start=start,
        end=start + timedelta(minutes=duration_minutes),
        value_eur_per_kwh=value,
        duration_minutes=duration_minutes,
    )


def _payload(slots: tuple[EpexSlot, ...]) -> EpexPayload:
    """Wrap slots into an EpexPayload with empty publication metadata."""
    return EpexPayload(slots=slots, publication_time=None, market_date=None)


def test_returns_none_when_payload_is_none() -> None:
    """A missing payload yields no boundary."""
    now = datetime(2026, 5, 4, 12, 30, tzinfo=UTC)
    assert next_epex_slot_boundary(None, now) is None


def test_returns_none_for_empty_slot_tuple() -> None:
    """An EpexPayload with no slots yields no boundary."""
    now = datetime(2026, 5, 4, 12, 30, tzinfo=UTC)
    assert next_epex_slot_boundary(_payload(()), now) is None


def test_returns_slot_end_when_now_inside_slot() -> None:
    """When ``now`` sits inside a slot, the boundary is that slot's end."""
    start = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    payload = _payload((_slot(start),))
    now = datetime(2026, 5, 4, 12, 30, tzinfo=UTC)
    assert next_epex_slot_boundary(payload, now) == start + timedelta(hours=1)


def test_returns_next_slot_start_when_now_before_first_slot() -> None:
    """When ``now`` precedes every slot, the boundary is the earliest start."""
    earliest = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    later = datetime(2026, 5, 4, 13, 0, tzinfo=UTC)
    payload = _payload((_slot(earliest), _slot(later)))
    now = datetime(2026, 5, 4, 11, 30, tzinfo=UTC)
    assert next_epex_slot_boundary(payload, now) == earliest


def test_handles_gap_between_slots() -> None:
    """A gap between slots is fine: the next-start candidate wins."""
    covering = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    after_gap = datetime(2026, 5, 4, 14, 0, tzinfo=UTC)
    payload = _payload((_slot(covering), _slot(after_gap)))
    now = datetime(2026, 5, 4, 13, 30, tzinfo=UTC)
    # No slot covers 13:30 (gap 13:00..14:00); next boundary is 14:00 start.
    assert next_epex_slot_boundary(payload, now) == after_gap


def test_returns_none_when_every_slot_is_in_the_past() -> None:
    """If every slot ends at or before ``now``, no future boundary exists."""
    start = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    payload = _payload((_slot(start),))
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    assert next_epex_slot_boundary(payload, now) is None


def test_exact_boundary_treated_as_start_of_next_slot() -> None:
    """
    At a slot start instant, that slot covers ``now`` (closed-open).

    The boundary should then be the end of that slot, not the start.
    """
    start = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    payload = _payload((_slot(start),))
    now = start  # slot.start <= now < slot.end is True at the boundary
    assert next_epex_slot_boundary(payload, now) == start + timedelta(hours=1)


def test_picks_earliest_candidate_with_contiguous_slots() -> None:
    """With contiguous slots, slot.end == next slot.start; min() is stable."""
    s1 = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    s2 = datetime(2026, 5, 4, 13, 0, tzinfo=UTC)
    s3 = datetime(2026, 5, 4, 14, 0, tzinfo=UTC)
    payload = _payload((_slot(s1), _slot(s2), _slot(s3)))
    now = datetime(2026, 5, 4, 12, 30, tzinfo=UTC)
    # slot1.end == s2; slot2.start == s2; slot3.start == s3.
    # Earliest candidate is s2.
    assert next_epex_slot_boundary(payload, now) == s2
