"""Pure helpers for the ENGIE Belgium account-balance payload."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

# Brussels timezone used to anchor ``dueDate`` strings (which are dates,
# not datetimes) to a timezone-aware datetime at midnight local time.
_BRUSSELS_TZ = ZoneInfo("Europe/Brussels")


def overview_open_amount(wrapper: Any) -> float | None:
    """
    Return the total open (unpaid) amount from the billing wrapper.

    Wrapper shape: ``{"data": <account-balance-payload>, "fetched_at": ISO}``.
    Returns ``None`` when the wrapper is absent or malformed.
    """
    payload = _unwrap(wrapper)
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


def overview_due_amount(wrapper: Any) -> float | None:
    """
    Return the amount that is past its due date from the billing wrapper.

    Returns ``None`` when the wrapper is absent or malformed.
    """
    payload = _unwrap(wrapper)
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


def next_due_date(wrapper: Any) -> datetime | None:
    """
    Return the earliest due date among open transactions, as a timezone-aware datetime.

    The returned datetime is midnight Brussels-local on the due date,
    timezone-aware.

    Returns ``None`` when no open transactions exist or the wrapper is absent.
    """
    transactions = _transactions(wrapper)
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


def billing_status(wrapper: Any) -> str | None:
    """Return the top-level ``status`` string from the billing wrapper, or None."""
    payload = _unwrap(wrapper)
    if payload is None:
        return None
    status = payload.get("status")
    return status if isinstance(status, str) else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _unwrap(wrapper: Any) -> dict[str, Any] | None:
    """Unwrap the coordinator billing wrapper, returning the inner payload."""
    if not isinstance(wrapper, dict):
        return None
    payload = wrapper.get("data")
    return payload if isinstance(payload, dict) else None


def _transactions(wrapper: Any) -> list[dict[str, Any]]:
    """Return the financialTransactions list, or an empty list."""
    payload = _unwrap(wrapper)
    if payload is None:
        return []
    details = payload.get("details")
    if not isinstance(details, dict):
        return []
    transactions = details.get("financialTransactions")
    return transactions if isinstance(transactions, list) else []
