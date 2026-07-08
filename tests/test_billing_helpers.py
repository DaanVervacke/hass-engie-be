"""Tests for the pure ``_billing`` helper module."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.engie_be._billing import (
    _transactions,
    billing_status,
    next_due_date,
    overview_due_amount,
    overview_open_amount,
)
from custom_components.engie_be.coordinator import EngieBeDataUpdateCoordinator

pytestmark = pytest.mark.billing

_FIXTURES = Path(__file__).parent / "fixtures"
_BRUSSELS = ZoneInfo("Europe/Brussels")

# Reference instant: 2026-07-20 noon Brussels (CEST = UTC+2)
_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=_BRUSSELS)


def _coord(fixture_name: str) -> MagicMock:
    """Load a billing fixture and wrap it in a mock coordinator."""
    payload = json.loads((_FIXTURES / f"billing_{fixture_name}.json").read_text())
    wrapper = {"data": payload, "fetched_at": _NOW.isoformat()}
    coord = MagicMock(spec=EngieBeDataUpdateCoordinator)
    coord.data = {"billing": wrapper}
    return coord


def _coord_from_wrapper(wrapper: object) -> MagicMock:
    """Build a mock coordinator whose billing wrapper is the given object."""
    coord = MagicMock(spec=EngieBeDataUpdateCoordinator)
    coord.data = {"billing": wrapper}
    return coord


def _coord_no_billing() -> MagicMock:
    """Build a mock coordinator with no billing key."""
    coord = MagicMock(spec=EngieBeDataUpdateCoordinator)
    coord.data = {}
    return coord


def _coord_no_data() -> MagicMock:
    """Build a mock coordinator with None as coordinator data."""
    coord = MagicMock(spec=EngieBeDataUpdateCoordinator)
    coord.data = None
    return coord


# ---------------------------------------------------------------------------
# _transactions
# ---------------------------------------------------------------------------


def test_transactions_returns_empty_for_none_coordinator_data() -> None:
    """_transactions returns [] when coordinator.data is None."""
    assert _transactions(_coord_no_data()) == []


def test_transactions_returns_empty_when_details_missing() -> None:
    """_transactions returns [] when 'details' is absent from payload."""
    coord = _coord_from_wrapper({"data": {"status": "CLEAR"}})
    assert _transactions(coord) == []


def test_transactions_returns_empty_when_details_not_dict() -> None:
    """_transactions returns [] when 'details' is not a dict."""
    coord = _coord_from_wrapper({"data": {"details": "not a dict"}})
    assert _transactions(coord) == []


def test_transactions_returns_list() -> None:
    """_transactions returns the financialTransactions list."""
    tx = [{"openAmount": 10.0, "dueDate": "2026-07-22"}]
    coord = _coord_from_wrapper({"data": {"details": {"financialTransactions": tx}}})
    assert _transactions(coord) == tx


# ---------------------------------------------------------------------------
# overview_open_amount
# ---------------------------------------------------------------------------


def test_overview_open_amount_none_when_no_billing_data() -> None:
    """overview_open_amount returns None when coordinator has no billing key."""
    assert overview_open_amount(_coord_no_billing()) is None


def test_overview_open_amount_none_when_overview_missing() -> None:
    """overview_open_amount returns None when overview is absent."""
    coord = _coord_from_wrapper({"data": {"status": "CLEAR"}})
    assert overview_open_amount(coord) is None


def test_overview_open_amount_none_when_open_amount_missing() -> None:
    """overview_open_amount returns None when openAmount is absent."""
    coord = _coord_from_wrapper({"data": {"overview": {"dueAmount": 0.0}}})
    assert overview_open_amount(coord) is None


def test_overview_open_amount_open_debit_fixture() -> None:
    """overview_open_amount returns 80.6 for the open_debit fixture."""
    assert overview_open_amount(_coord("open_debit")) == pytest.approx(80.6)


def test_overview_open_amount_cleared_fixture() -> None:
    """overview_open_amount returns 0.0 for the cleared fixture."""
    assert overview_open_amount(_coord("cleared")) == pytest.approx(0.0)


def test_overview_open_amount_invalid_value_returns_none() -> None:
    """overview_open_amount returns None when the amount is not numeric."""
    coord = _coord_from_wrapper({"data": {"overview": {"openAmount": "not-a-number"}}})
    assert overview_open_amount(coord) is None


# ---------------------------------------------------------------------------
# overview_due_amount
# ---------------------------------------------------------------------------


def test_overview_due_amount_none_when_no_billing_data() -> None:
    """overview_due_amount returns None when coordinator has no billing key."""
    assert overview_due_amount(_coord_no_billing()) is None


def test_overview_due_amount_zero_for_open_debit_fixture() -> None:
    """overview_due_amount returns 0.0 for the open_debit fixture (not yet due)."""
    assert overview_due_amount(_coord("open_debit")) == pytest.approx(0.0)


def test_overview_due_amount_positive_when_due_amount_set() -> None:
    """overview_due_amount returns the dueAmount value when positive."""
    coord = _coord_from_wrapper({"data": {"overview": {"dueAmount": 50.0}}})
    assert overview_due_amount(coord) == pytest.approx(50.0)


def test_overview_due_amount_none_when_overview_not_dict() -> None:
    """overview_due_amount returns None when overview is not a dict."""
    coord = _coord_from_wrapper({"data": {"overview": "not a dict"}})
    assert overview_due_amount(coord) is None


def test_overview_due_amount_none_when_due_amount_absent() -> None:
    """overview_due_amount returns None when dueAmount key is missing."""
    coord = _coord_from_wrapper({"data": {"overview": {"openAmount": 10.0}}})
    assert overview_due_amount(coord) is None


def test_overview_due_amount_invalid_value_returns_none() -> None:
    """overview_due_amount returns None when the amount is not numeric."""
    coord = _coord_from_wrapper({"data": {"overview": {"dueAmount": []}}})
    assert overview_due_amount(coord) is None


# ---------------------------------------------------------------------------
# billing_status
# ---------------------------------------------------------------------------


def test_billing_status_none_when_no_billing_data() -> None:
    """billing_status returns None when coordinator has no billing key."""
    assert billing_status(_coord_no_billing()) is None


def test_billing_status_open_debit() -> None:
    """billing_status returns 'OPEN_DEBIT' for the open_debit fixture."""
    assert billing_status(_coord("open_debit")) == "OPEN_DEBIT"


def test_billing_status_cleared() -> None:
    """billing_status returns 'CLEAR' for the cleared fixture."""
    assert billing_status(_coord("cleared")) == "CLEAR"


def test_billing_status_open_overdue() -> None:
    """billing_status returns 'OPEN_OVERDUE' for a coordinator with that status."""
    coord = _coord_from_wrapper({"data": {"status": "OPEN_OVERDUE"}})
    assert billing_status(coord) == "OPEN_OVERDUE"


def test_billing_status_non_string_returns_none() -> None:
    """billing_status returns None when status field is not a string."""
    coord = _coord_from_wrapper({"data": {"status": 42}})
    assert billing_status(coord) is None


# ---------------------------------------------------------------------------
# next_due_date
# ---------------------------------------------------------------------------


def test_next_due_date_none_when_no_billing_data() -> None:
    """next_due_date returns None when coordinator has no billing key."""
    assert next_due_date(_coord_no_billing()) is None


def test_next_due_date_none_for_cleared_fixture() -> None:
    """next_due_date returns None when there are no open transactions."""
    assert next_due_date(_coord("cleared")) is None


def test_next_due_date_returns_aware_datetime_for_open_debit() -> None:
    """next_due_date returns midnight Brussels time for the open_debit fixture."""
    result = next_due_date(_coord("open_debit"))
    assert result is not None
    assert result.tzinfo is not None
    assert result == datetime(2026, 7, 22, 0, 0, tzinfo=_BRUSSELS)


def test_next_due_date_skips_zero_open_amount() -> None:
    """next_due_date ignores transactions where openAmount <= 0."""
    coord = _coord_from_wrapper(
        {
            "data": {
                "details": {
                    "financialTransactions": [
                        {"openAmount": 0.0, "dueDate": "2026-06-01"},
                        {"openAmount": -5.0, "dueDate": "2026-06-02"},
                    ]
                }
            }
        }
    )
    assert next_due_date(coord) is None


def test_next_due_date_skips_invalid_date_string() -> None:
    """next_due_date ignores transactions with non-parseable dueDate."""
    coord = _coord_from_wrapper(
        {
            "data": {
                "details": {
                    "financialTransactions": [
                        {"openAmount": 10.0, "dueDate": "not-a-date"},
                    ]
                }
            }
        }
    )
    assert next_due_date(coord) is None


def test_next_due_date_skips_non_string_due_date() -> None:
    """next_due_date ignores transactions where dueDate is not a string."""
    coord = _coord_from_wrapper(
        {
            "data": {
                "details": {
                    "financialTransactions": [
                        {"openAmount": 10.0, "dueDate": None},
                    ]
                }
            }
        }
    )
    assert next_due_date(coord) is None


def test_next_due_date_handles_non_numeric_open_amount() -> None:
    """next_due_date treats non-numeric openAmount as 0 and skips the transaction."""
    coord = _coord_from_wrapper(
        {
            "data": {
                "details": {
                    "financialTransactions": [
                        {"openAmount": "bad", "dueDate": "2026-07-22"},
                    ]
                }
            }
        }
    )
    assert next_due_date(coord) is None


def test_next_due_date_picks_earliest_when_multiple_open() -> None:
    """next_due_date returns the earliest due date among multiple open transactions."""
    coord = _coord_from_wrapper(
        {
            "data": {
                "details": {
                    "financialTransactions": [
                        {"openAmount": 20.0, "dueDate": "2026-08-15"},
                        {"openAmount": 30.0, "dueDate": "2026-07-25"},
                        {"openAmount": 10.0, "dueDate": "2026-09-01"},
                    ]
                }
            }
        }
    )
    result = next_due_date(coord)
    assert result is not None
    assert result == datetime(2026, 7, 25, 0, 0, tzinfo=_BRUSSELS)
