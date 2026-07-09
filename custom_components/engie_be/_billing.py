"""Pure helpers for the ENGIE Belgium account-balance payload."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from .data import unwrap_dict_payload

# Brussels timezone used to anchor ``dueDate`` strings (which are dates,
# not datetimes) to a timezone-aware datetime at midnight local time.
_BRUSSELS_TZ = ZoneInfo("Europe/Brussels")

if TYPE_CHECKING:
    from .coordinator import EngieBeDataUpdateCoordinator


def overview_open_amount(
    coordinator: EngieBeDataUpdateCoordinator,
) -> float | None:
    """
    Return the total open (unpaid) amount from the billing overview.

    Returns ``None`` when the coordinator has no billing data or the
    payload is malformed.
    """
    payload = unwrap_dict_payload(coordinator, "billing")
    if payload is None:
        return None
    overview = payload.get("overview")
    if not isinstance(overview, dict):
        return None
    raw = overview.get("openAmount")
    if raw is None:
        return None
    try:
        return float(raw)
    except TypeError, ValueError:
        return None


def overview_due_amount(
    coordinator: EngieBeDataUpdateCoordinator,
) -> float | None:
    """
    Return the amount that is past its due date from the billing payload.

    Returns ``None`` when the coordinator has no billing data or the
    payload is malformed.
    """
    payload = unwrap_dict_payload(coordinator, "billing")
    if payload is None:
        return None
    overview = payload.get("overview")
    if not isinstance(overview, dict):
        return None
    raw = overview.get("dueAmount")
    if raw is None:
        return None
    try:
        return float(raw)
    except TypeError, ValueError:
        return None


def next_due_date(
    coordinator: EngieBeDataUpdateCoordinator,
) -> datetime | None:
    """
    Return the earliest due date among open transactions, as a timezone-aware datetime.

    The returned datetime is midnight Brussels-local on the due date,
    timezone-aware.

    Returns ``None`` when no open transactions exist or the coordinator
    has no billing data.
    """
    transactions = _transactions(coordinator)
    if not transactions:
        return None

    earliest: datetime | None = None
    for tx in transactions:
        open_amt = tx.get("openAmount")
        try:
            open_float = float(open_amt) if open_amt is not None else 0.0
        except TypeError, ValueError:
            open_float = 0.0
        if open_float <= 0:
            continue
        due_raw = tx.get("dueDate")
        if not isinstance(due_raw, str):
            continue
        try:
            due_date_obj = datetime.strptime(due_raw, "%Y-%m-%d").replace(
                tzinfo=_BRUSSELS_TZ,
            )
        except ValueError:
            continue
        due_dt = due_date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
        if earliest is None or due_dt < earliest:
            earliest = due_dt
    return earliest


def billing_status(
    coordinator: EngieBeDataUpdateCoordinator,
) -> str | None:
    """Return the top-level ``status`` string from the billing payload, or None."""
    payload = unwrap_dict_payload(coordinator, "billing")
    if payload is None:
        return None
    status = payload.get("status")
    return status if isinstance(status, str) else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _transactions(
    coordinator: EngieBeDataUpdateCoordinator,
) -> list[dict[str, Any]]:
    """Return the financialTransactions list, or an empty list."""
    payload = unwrap_dict_payload(coordinator, "billing")
    if payload is None:
        return []
    details = payload.get("details")
    if not isinstance(details, dict):
        return []
    transactions = details.get("financialTransactions")
    return transactions if isinstance(transactions, list) else []
