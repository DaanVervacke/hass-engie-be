"""Pure helpers for EPEX slot-boundary scheduling and slot metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from .coordinator import EngieBeEpexCoordinator, EngieBeEpexQuarterHourCoordinator
    from .data import EpexPayload, EpexSlot


def next_epex_slot_boundary(
    payload: EpexPayload | None,
    now: datetime,
) -> datetime | None:
    """
    Return the next UTC instant at which the current EPEX slot changes, or None.

    Handles gaps between slots by considering both the current-slot end
    and the next-slot start as candidates.
    """
    if payload is None or not payload.slots:
        return None

    candidates: list[datetime] = []
    for slot in payload.slots:
        if slot.start <= now < slot.end:
            candidates.append(slot.end)
        elif slot.start > now:
            candidates.append(slot.start)

    if not candidates:
        return None
    return min(candidates)


def epex_payload(
    coordinator: EngieBeEpexCoordinator | EngieBeEpexQuarterHourCoordinator,
) -> EpexPayload | None:
    """Return the cached EPEX payload, or ``None`` if not yet fetched."""
    from .data import EpexPayload  # noqa: PLC0415 - runtime isinstance check

    payload = coordinator.data
    return payload if isinstance(payload, EpexPayload) else None


def _slot_duration_minutes(slot: EpexSlot) -> float:
    """Return the duration of an EPEX slot in minutes."""
    return (slot.end - slot.start).total_seconds() / 60
