"""Tests for the billing (outstanding balance + overdue amount) sensor entities."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.engie_be.const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.sensor import (
    EngieBeNextInvoiceDueSensor,
    EngieBeOutstandingBalanceSensor,
    EngieBeOverdueAmountSensor,
    _build_billing_sensors,
)

pytestmark = pytest.mark.billing

_FIXTURES = Path(__file__).parent / "fixtures"
_BILLING_OPEN = _FIXTURES / "billing_open_debit.json"
_BILLING_CLEAR = _FIXTURES / "billing_cleared.json"
_BRUSSELS = ZoneInfo("Europe/Brussels")


def _load(path: Path) -> dict:
    """Return a fresh copy of a JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def _wrap(payload: dict[str, Any]) -> dict:
    """Wrap a raw billing payload in the coordinator storage shape."""
    return {"billing": {"data": payload, "fetched_at": "2026-07-20T10:00:00+00:00"}}


def _make_subentry() -> MagicMock:
    """Build a MagicMock ConfigSubentry."""
    subentry = MagicMock()
    subentry.subentry_id = "sub_abc"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {CONF_BUSINESS_AGREEMENT_NUMBER: "000000000000"}
    return subentry


def _make_coordinator(data: object) -> MagicMock:
    """Build a MagicMock coordinator with the given ``.data``."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry"
    return coordinator


# ---------------------------------------------------------------------------
# _build_billing_sensors
# ---------------------------------------------------------------------------


def test_build_creates_three_sensors() -> None:
    """_build_billing_sensors returns exactly three sensor entities."""
    coord = _make_coordinator(_wrap(_load(_BILLING_OPEN)))
    sensors = _build_billing_sensors(coord, _make_subentry())
    assert len(sensors) == 3
    types = {type(s).__name__ for s in sensors}
    assert types == {
        "EngieBeOutstandingBalanceSensor",
        "EngieBeOverdueAmountSensor",
        "EngieBeNextInvoiceDueSensor",
    }


def test_unique_ids_follow_schema() -> None:
    """Each sensor's unique_id follows the {entry_id}_{subentry_id}_{key} schema."""
    coord = _make_coordinator(_wrap(_load(_BILLING_OPEN)))
    sensors = _build_billing_sensors(coord, _make_subentry())
    unique_ids = {s.unique_id for s in sensors}
    assert "test_entry_sub_abc_outstanding_balance" in unique_ids
    assert "test_entry_sub_abc_overdue_amount" in unique_ids
    assert "test_entry_sub_abc_next_invoice_due" in unique_ids


# ---------------------------------------------------------------------------
# EngieBeOutstandingBalanceSensor
# ---------------------------------------------------------------------------


def test_outstanding_balance_open_debit_returns_amount() -> None:
    """Outstanding balance sensor returns 80.6 for the open_debit fixture."""
    sensor = EngieBeOutstandingBalanceSensor(
        _make_coordinator(_wrap(_load(_BILLING_OPEN))),
        _make_subentry(),
    )
    assert sensor.native_value == pytest.approx(80.6)


def test_outstanding_balance_cleared_returns_zero() -> None:
    """Outstanding balance sensor returns 0.0 for the cleared fixture."""
    sensor = EngieBeOutstandingBalanceSensor(
        _make_coordinator(_wrap(_load(_BILLING_CLEAR))),
        _make_subentry(),
    )
    assert sensor.native_value == pytest.approx(0.0)


def test_outstanding_balance_missing_wrapper_returns_none() -> None:
    """Outstanding balance sensor returns None when the billing wrapper is absent."""
    sensor = EngieBeOutstandingBalanceSensor(
        _make_coordinator({}),
        _make_subentry(),
    )
    assert sensor.native_value is None


def test_outstanding_balance_none_coordinator_data_returns_none() -> None:
    """Outstanding balance sensor returns None when coordinator.data is None."""
    sensor = EngieBeOutstandingBalanceSensor(
        _make_coordinator(None),
        _make_subentry(),
    )
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# EngieBeOverdueAmountSensor
# ---------------------------------------------------------------------------


def test_overdue_amount_zero_for_open_debit() -> None:
    """Overdue amount sensor returns 0.0 for open_debit (dueAmount=0)."""
    sensor = EngieBeOverdueAmountSensor(
        _make_coordinator(_wrap(_load(_BILLING_OPEN))),
        _make_subentry(),
    )
    assert sensor.native_value == pytest.approx(0.0)


def test_overdue_amount_positive_when_due_amount_set() -> None:
    """Overdue amount sensor returns 50.0 when dueAmount is positive."""
    payload = {
        "status": "OPEN_OVERDUE",
        "overview": {"totalAmount": 50.0, "openAmount": 50.0, "dueAmount": 50.0},
        "details": {"financialTransactions": []},
    }
    sensor = EngieBeOverdueAmountSensor(
        _make_coordinator(_wrap(payload)),
        _make_subentry(),
    )
    assert sensor.native_value == pytest.approx(50.0)


def test_overdue_amount_missing_wrapper_returns_none() -> None:
    """Overdue amount sensor returns None when the billing wrapper is absent."""
    sensor = EngieBeOverdueAmountSensor(
        _make_coordinator({}),
        _make_subentry(),
    )
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# EngieBeNextInvoiceDueSensor
# ---------------------------------------------------------------------------


def test_next_invoice_due_returns_aware_datetime_for_open_debit() -> None:
    """Next-invoice-due sensor returns midnight Brussels time for open_debit."""
    sensor = EngieBeNextInvoiceDueSensor(
        _make_coordinator(_wrap(_load(_BILLING_OPEN))),
        _make_subentry(),
    )
    result = sensor.native_value
    assert result is not None
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert result == datetime(2026, 7, 22, 0, 0, tzinfo=_BRUSSELS)


def test_next_invoice_due_none_for_cleared() -> None:
    """Next-invoice-due sensor returns None when there are no open transactions."""
    sensor = EngieBeNextInvoiceDueSensor(
        _make_coordinator(_wrap(_load(_BILLING_CLEAR))),
        _make_subentry(),
    )
    assert sensor.native_value is None


def test_next_invoice_due_missing_wrapper_returns_none() -> None:
    """Next-invoice-due sensor returns None when the billing wrapper is absent."""
    sensor = EngieBeNextInvoiceDueSensor(
        _make_coordinator({}),
        _make_subentry(),
    )
    assert sensor.native_value is None
