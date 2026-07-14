"""
Tests for the ``expose_all`` debug-toggle gating in ``sensor.async_setup_entry``.

Five ``if <feature_flag> or expose_all:`` gates guard entity creation for
happy-hour, EPEX (``is_dynamic``), solar, TOU, and billing sensors. Each
gate must bypass its underlying flag when the debug toggle is on, and
none of them may fire when it is off. The lower-level ``expose_all``
tests in ``tests/test_sensor.py`` exercise ``_build_sensor_descriptions``
and ``_build_peak_sensors`` directly; this file is the ``async_setup_entry``
level counterpart, closing the same coverage gap that plan 137 already
closed for ``binary_sensor.py`` and ``calendar.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.engie_be.const import (
    CONF_EXPOSE_ALL_ENTITIES,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.sensor import (
    EngieBeEpexCurrentSensor,
    EngieBeHappyHourTimestampSensor,
    EngieBeOutstandingBalanceSensor,
    EngieBeSolarSurplusSensor,
    EngieBeTouSlotSensor,
    async_setup_entry,
)


def _make_subentry(
    subentry_id: str = "sub_test",
    title: str = "Test Account",
) -> MagicMock:
    """Build a MagicMock ConfigSubentry of the customer-account type."""
    subentry = MagicMock()
    subentry.subentry_id = subentry_id
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = title
    return subentry


def _make_sub_data(
    *,
    is_happy_hour_enrolled: bool = False,
    is_dynamic: bool = False,
    solar: bool = False,
    tou_active: bool = False,
    has_billing: bool = False,
) -> MagicMock:
    """
    Build a per-subentry runtime-data stub with every gate defaulting False.

    ``service_points`` defaults to ``{}`` here (no ELECTRICITY EAN, so the
    per-EAN solar/TOU builders yield nothing regardless of gate state);
    tests exercising the solar or TOU gate set it directly on the
    returned mock afterwards.
    """
    sub_data = MagicMock()
    sub_data.coordinator = MagicMock()
    sub_data.coordinator.data = {
        "items": [],
        "billing": {"data": {}} if has_billing else None,
    }
    sub_data.coordinator.is_dynamic = is_dynamic
    sub_data.coordinator.config_entry = MagicMock()
    sub_data.coordinator.config_entry.entry_id = "test_entry_id"
    sub_data.service_points = {}
    sub_data.feature_flags = MagicMock()
    sub_data.feature_flags.happy_hour_enrolled = is_happy_hour_enrolled
    sub_data.feature_flags.solar = solar
    sub_data.feature_flags.tou_active = tou_active
    return sub_data


def _make_entry(
    *,
    subentries: dict[str, MagicMock],
    sub_runtime: dict[str, MagicMock],
) -> MagicMock:
    """Build a MagicMock parent ConfigEntry exposing the v5 runtime layout."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.options = {}
    entry.subentries = subentries
    entry.runtime_data = MagicMock()
    entry.runtime_data.epex_coordinator = MagicMock()
    entry.runtime_data.subentry_data = sub_runtime
    return entry


async def test_expose_all_creates_happy_hour_sensors_when_not_enrolled() -> None:
    """expose_all bypasses the happy-hour-enrolled gate."""
    subentry = _make_subentry(subentry_id="sub_hh")
    entry = _make_entry(
        subentries={"sub_hh": subentry},
        sub_runtime={"sub_hh": _make_sub_data(is_happy_hour_enrolled=False)},
    )
    entry.options = {CONF_EXPOSE_ALL_ENTITIES: True}

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    assert any(isinstance(e, EngieBeHappyHourTimestampSensor) for e in added)


async def test_expose_all_creates_epex_sensors_when_not_dynamic() -> None:
    """expose_all bypasses the is_dynamic gate for EPEX sensors."""
    subentry = _make_subentry(subentry_id="sub_epex")
    entry = _make_entry(
        subentries={"sub_epex": subentry},
        sub_runtime={"sub_epex": _make_sub_data(is_dynamic=False)},
    )
    entry.options = {CONF_EXPOSE_ALL_ENTITIES: True}

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    assert any(isinstance(e, EngieBeEpexCurrentSensor) for e in added)


async def test_expose_all_creates_solar_sensors_when_flag_off() -> None:
    """expose_all bypasses the solar feature-flag gate."""
    subentry = _make_subentry(subentry_id="sub_solar")
    sub_data = _make_sub_data(solar=False)
    sub_data.service_points = {"541448820000000001": "ELECTRICITY"}
    entry = _make_entry(
        subentries={"sub_solar": subentry},
        sub_runtime={"sub_solar": sub_data},
    )
    entry.options = {CONF_EXPOSE_ALL_ENTITIES: True}

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    assert any(isinstance(e, EngieBeSolarSurplusSensor) for e in added)


async def test_expose_all_creates_tou_sensors_when_flag_off() -> None:
    """expose_all bypasses the tou_active feature-flag gate."""
    subentry = _make_subentry(subentry_id="sub_tou")
    sub_data = _make_sub_data(tou_active=False)
    sub_data.service_points = {"541448820000000001": "ELECTRICITY"}
    entry = _make_entry(
        subentries={"sub_tou": subentry},
        sub_runtime={"sub_tou": sub_data},
    )
    entry.options = {CONF_EXPOSE_ALL_ENTITIES: True}

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    assert any(isinstance(e, EngieBeTouSlotSensor) for e in added)


async def test_expose_all_creates_billing_sensors_when_absent() -> None:
    """expose_all bypasses the billing-wrapper-present gate."""
    subentry = _make_subentry(subentry_id="sub_billing")
    entry = _make_entry(
        subentries={"sub_billing": subentry},
        sub_runtime={"sub_billing": _make_sub_data(has_billing=False)},
    )
    entry.options = {CONF_EXPOSE_ALL_ENTITIES: True}

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    assert any(isinstance(e, EngieBeOutstandingBalanceSensor) for e in added)


async def test_without_expose_all_no_gated_sensors_appear() -> None:
    """Negative control: all flags off and no expose_all means no gated sensor fires."""
    subentry = _make_subentry(subentry_id="sub_none")
    sub_data = _make_sub_data()
    sub_data.service_points = {"541448820000000001": "ELECTRICITY"}
    entry = _make_entry(
        subentries={"sub_none": subentry},
        sub_runtime={"sub_none": sub_data},
    )
    entry.options = {}

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    assert not any(isinstance(e, EngieBeHappyHourTimestampSensor) for e in added)
    assert not any(isinstance(e, EngieBeEpexCurrentSensor) for e in added)
    assert not any(isinstance(e, EngieBeSolarSurplusSensor) for e in added)
    assert not any(isinstance(e, EngieBeTouSlotSensor) for e in added)
    assert not any(isinstance(e, EngieBeOutstandingBalanceSensor) for e in added)
