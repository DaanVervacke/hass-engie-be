"""Tests for the TOU is-optimal binary sensor entity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.engie_be.binary_sensor import (
    TOU_INJECTION_IS_OPTIMAL_DESCRIPTION,
    TOU_OFFTAKE_IS_OPTIMAL_DESCRIPTION,
    EngieBeTouIsOptimalSensor,
    _build_tou_binary_sensors,
)
from custom_components.engie_be.const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.data import (
    EngieBeData,
    EngieBeSubentryData,
    FeatureFlagState,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_TOU_BIHORAIRE = _FIXTURES / "tou_schedules_bihoraire.json"

_EAN = "541448820070000000"

pytestmark = pytest.mark.tou


def _load(path: Path) -> dict:
    """Return a fresh copy of a JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def _wrap(payload: dict[str, Any]) -> dict:
    """Wrap a raw TOU payload in the coordinator storage shape."""
    return {
        "tou_schedules": {
            "data": payload,
            "fetched_at": "2026-07-08T10:00:00+00:00",
        }
    }


def _make_subentry() -> MagicMock:
    """Build a MagicMock ConfigSubentry."""
    subentry = MagicMock()
    subentry.subentry_id = "sub_abc"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {CONF_BUSINESS_AGREEMENT_NUMBER: "000000000000"}
    return subentry


def _make_coordinator(
    data: object,
    *,
    is_tou_active: bool | None = None,
    service_points: dict[str, str] | None = None,
) -> MagicMock:
    """Build a MagicMock coordinator with primed runtime_data."""
    subentry = _make_subentry()
    sub_data = EngieBeSubentryData(
        coordinator=MagicMock(),
        service_points=(
            service_points if service_points is not None else {_EAN: "ELECTRICITY"}
        ),
        feature_flags=FeatureFlagState(tou_active=is_tou_active),
    )
    runtime = EngieBeData(
        client=MagicMock(),
        epex_coordinator=MagicMock(),
        subentry_data={subentry.subentry_id: sub_data},
    )
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "test_entry"
    coordinator.config_entry.runtime_data = runtime
    return coordinator


def _offtake_sensor(
    data: object,
    *,
    is_tou_active: bool | None = None,
) -> EngieBeTouIsOptimalSensor:
    """Build the offtake is-optimal sensor under test."""
    return EngieBeTouIsOptimalSensor(
        _make_coordinator(data, is_tou_active=is_tou_active),
        _make_subentry(),
        TOU_OFFTAKE_IS_OPTIMAL_DESCRIPTION,
        ean=_EAN,
        direction="offtake",
    )


def _injection_sensor(
    data: object,
    *,
    is_tou_active: bool | None = None,
) -> EngieBeTouIsOptimalSensor:
    """Build the injection is-optimal sensor under test."""
    return EngieBeTouIsOptimalSensor(
        _make_coordinator(data, is_tou_active=is_tou_active),
        _make_subentry(),
        TOU_INJECTION_IS_OPTIMAL_DESCRIPTION,
        ean=_EAN,
        direction="injection",
    )


# ---------------------------------------------------------------------------
# is_on
# ---------------------------------------------------------------------------


def test_offtake_optimal_offpeak_current_offpeak_is_on(
    freezer,  # noqa: ANN001
) -> None:
    """Optimal=OFFPEAK, current=OFFPEAK -> is_on=True."""
    freezer.move_to("2026-07-06T02:00:00Z")  # 04:00 Brussels Monday -> OFFPEAK
    sensor = _offtake_sensor(_wrap(_load(_TOU_BIHORAIRE)))
    assert sensor.is_on is True


def test_offtake_optimal_offpeak_current_peak_is_off(
    freezer,  # noqa: ANN001
) -> None:
    """Optimal=OFFPEAK, current=PEAK -> is_on=False."""
    freezer.move_to("2026-07-06T08:00:00Z")  # 10:00 Brussels Monday -> PEAK
    sensor = _offtake_sensor(_wrap(_load(_TOU_BIHORAIRE)))
    assert sensor.is_on is False


def test_injection_optimal_peak_current_peak_is_on(
    freezer,  # noqa: ANN001
) -> None:
    """Optimal=PEAK (injection), current=PEAK -> is_on=True."""
    freezer.move_to("2026-07-06T08:00:00Z")  # 10:00 Brussels Monday -> PEAK
    sensor = _injection_sensor(_wrap(_load(_TOU_BIHORAIRE)))
    assert sensor.is_on is True


def test_no_wrapper_returns_none() -> None:
    """When coordinator data is None, is_on returns None."""
    sensor = _offtake_sensor(None)
    assert sensor.is_on is None


# ---------------------------------------------------------------------------
# _build_tou_binary_sensors
# ---------------------------------------------------------------------------


def test_build_creates_sensors_when_tou_active_and_nontrivial_schedule() -> None:
    """Flag active + bihoraire schedule (PEAK+OFFPEAK) triggers sensor creation."""
    subentry = _make_subentry()
    coord = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)), is_tou_active=True)
    sensors = _build_tou_binary_sensors(coord, subentry)
    keys = {s.entity_description.key for s in sensors}
    assert "tou_offtake_is_optimal" in keys
    assert "tou_injection_is_optimal" in keys


def test_build_no_sensors_when_flag_off_even_for_nontrivial_schedule() -> None:
    """Flag off with real bihoraire payload still yields no sensors."""
    subentry = _make_subentry()
    coord = _make_coordinator(_wrap(_load(_TOU_BIHORAIRE)), is_tou_active=False)
    sensors = _build_tou_binary_sensors(coord, subentry)
    assert sensors == []


def test_build_no_sensors_for_flat_schedule_when_active() -> None:
    """Flag active but flat all-OFFPEAK schedule → no sensors (would always be True)."""
    flat_payload = _load(_FIXTURES / "tou_schedules_flat_all_offpeak.json")
    subentry = _make_subentry()
    coord = _make_coordinator(_wrap(flat_payload), is_tou_active=True)
    sensors = _build_tou_binary_sensors(coord, subentry)
    assert sensors == []
