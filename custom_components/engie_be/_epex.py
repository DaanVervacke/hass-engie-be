"""
Pure helpers for EPEX slot-boundary scheduling.

Kept dependency-free so the slot-boundary computation can be unit
tested in isolation and reused across the binary-sensor and sensor
platforms without crossing entity-class boundaries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from .data import EpexPayload


def next_epex_slot_boundary(
    payload: EpexPayload | None,
    now: datetime,
) -> datetime | None:
    """
    Return the next UTC instant at which the current EPEX slot changes.

    The returned datetime is the earliest of:

    * ``slot.end`` for the slot covering ``now`` (when one exists), and
    * ``slot.start`` for the earliest slot whose start is strictly
      greater than ``now``.

    Returns ``None`` when the payload is ``None``, when the payload
    has no slots, or when every slot is entirely in the past.

    The function does not assume contiguity: gaps between slots are
    handled correctly by considering both the current-slot end and
    the next-slot start as independent candidates.
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
