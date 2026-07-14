"""
Defensive-branch coverage for ``custom_components.engie_be.sensor``.

Targets pre-v0.10.0b1 missing lines:

- L211, L215-219 -- ``async_setup_entry`` skips non-business-agreement subentries
  and logs a warning when a subentry has no runtime data.
- L441, L450-451 -- ``EngieBeMonthlyPeakTimestampSensor.native_value`` guards
  against a non-dict ``peakOfTheMonth`` and an unparseable ISO string.
- L567 -- ``EngieBeEnergySensor.__init__`` skips the ``entity_id`` override
  when the subentry has no BAN.
- L572, L577-586 -- ``native_value`` / ``extra_state_attributes`` happy paths.
- L590-595 -- ``_get_current_price_entry`` early-out + EAN-miss + match.
- L599-617 -- ``_get_price_value`` direction-missing / slot-missing / None /
  matched-value branches.

Tests exercise the private helpers directly with crafted payloads; this
mirrors the pattern used by ``tests/test_coordinator_defensive_branches.py``
and avoids spinning up full setup flows for unit-scope assertions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.config_entries import ConfigSubentry

from custom_components.engie_be.const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.sensor import (
    EngieBeEnergySensor,
    EngieBeMonthlyPeakTimestampSensor,
    async_setup_entry,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_FIXTURES = Path(__file__).parent / "fixtures"
_PRICES_FIXTURE = _FIXTURES / "prices_sample.json"

_EAN = "541448820000000001_ID1"
_MISSING_EAN = "999999999999999999_ID1"


def _coordinator_with_prices() -> MagicMock:
    """Return a coordinator stub whose ``data["items"]`` mirrors the fixture."""
    coordinator = MagicMock()
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.entry_id = "entry_abc"
    coordinator.data = json.loads(_PRICES_FIXTURE.read_text())
    coordinator.last_successful_fetch = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    return coordinator


def _subentry(*, ban: str | None = "B-0001") -> MagicMock:
    """Return a ConfigSubentry stub with optional BAN."""
    subentry = MagicMock(spec=ConfigSubentry)
    subentry.subentry_id = "sub_1"
    subentry.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    subentry.title = "Test Account"
    subentry.data = {} if ban is None else {CONF_BUSINESS_AGREEMENT_NUMBER: ban}
    return subentry


def _build_energy_sensor(
    *,
    value_key: str,
    slot_code: str,
    ban: str | None = "B-0001",
    coordinator: MagicMock | None = None,
) -> EngieBeEnergySensor:
    """Wire an ``EngieBeEnergySensor`` for a target slot."""
    coord = coordinator or _coordinator_with_prices()
    sub = _subentry(ban=ban)
    desc = SensorEntityDescription(
        key=f"{_EAN}_offtake",
        translation_key="electricity_offtake_price_eur_per_kwh",
    )
    return EngieBeEnergySensor(
        coordinator=coord,
        subentry=sub,
        entity_description=desc,
        ean=_EAN,
        value_key=value_key,
        slot_code=slot_code,
    )


# ---------------------------------------------------------------------------
# async_setup_entry: non-business-agreement skip + runtime-data-missing warning
# (sensor.py L211, L215-219)
# ---------------------------------------------------------------------------


async def test_setup_entry_skips_non_business_agreement_subentry(
    hass: HomeAssistant,
) -> None:
    """A subentry whose ``subentry_type`` is not the BAN literal is skipped."""
    entry = MagicMock()
    entry.runtime_data = MagicMock()
    entry.runtime_data.epex_coordinator = MagicMock()
    foreign = MagicMock()
    foreign.subentry_type = "some_other_type"
    foreign.subentry_id = "sub_foreign"
    entry.subentries = {"sub_foreign": foreign}
    entry.runtime_data.subentry_data = {}

    added: list[Any] = []

    def _capture(items: Any, *, update_before_add: bool = False) -> None:  # noqa: ARG001
        added.extend(list(items))

    await async_setup_entry(hass, entry, _capture)

    assert added == []


async def test_setup_entry_warns_and_skips_when_runtime_data_missing(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Subentries without runtime data must be skipped with a WARNING log."""
    entry = MagicMock()
    entry.runtime_data = MagicMock()
    entry.runtime_data.epex_coordinator = MagicMock()
    sub = MagicMock()
    sub.subentry_type = SUBENTRY_TYPE_BUSINESS_AGREEMENT
    sub.subentry_id = "sub_missing_runtime"
    entry.subentries = {"sub_missing_runtime": sub}
    entry.runtime_data.subentry_data = {}  # explicitly empty

    added: list[Any] = []

    def _capture(items: Any, *, update_before_add: bool = False) -> None:  # noqa: ARG001
        added.extend(list(items))

    with caplog.at_level("WARNING", logger="custom_components.engie_be.sensor"):
        await async_setup_entry(hass, entry, _capture)

    assert added == []
    assert any(
        "No runtime data for subentry" in rec.message
        and "sub_missing_runtime" in rec.message
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# EngieBeMonthlyPeakTimestampSensor.native_value defensive branches
# (sensor.py L441, L450-451)
# ---------------------------------------------------------------------------


def _peak_timestamp_sensor(peaks_payload: Any) -> EngieBeMonthlyPeakTimestampSensor:
    """
    Wire a peak timestamp sensor whose coordinator returns *peaks_payload*.

    The coordinator stores peaks under ``{"peaks": {"data": <inner>}}`` so the
    helper must double-wrap.
    """
    coord = MagicMock()
    coord.config_entry = MagicMock()
    coord.config_entry.entry_id = "entry_abc"
    if peaks_payload is None:
        coord.data = None
    else:
        coord.data = {"peaks": {"data": peaks_payload}}
    sub = _subentry()
    desc = SensorEntityDescription(
        key="captar_monthly_peak_start",
        translation_key="captar_monthly_peak_start",
    )
    return EngieBeMonthlyPeakTimestampSensor(
        coordinator=coord,
        subentry=sub,
        entity_description=desc,
        field="start",
    )


def test_peak_timestamp_native_value_returns_none_when_peaks_payload_none() -> None:
    """``peaks_payload`` is None -> early ``return None`` (L441)."""
    sensor = _peak_timestamp_sensor(peaks_payload=None)
    assert sensor.native_value is None


def test_peak_timestamp_native_value_returns_none_when_monthly_not_dict() -> None:
    """``peakOfTheMonth`` is not a dict -> ``return None``."""
    sensor = _peak_timestamp_sensor(peaks_payload={"peakOfTheMonth": "oops"})
    assert sensor.native_value is None


def test_peak_timestamp_native_value_returns_none_when_iso_unparseable() -> None:
    """A non-ISO string in the field triggers ``ValueError`` -> None (L450-451)."""
    sensor = _peak_timestamp_sensor(
        peaks_payload={"peakOfTheMonth": {"start": "not-a-date"}},
    )
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# EngieBeEnergySensor.__init__: BAN-missing skip of entity_id override (L567)
# ---------------------------------------------------------------------------


def test_energy_sensor_skips_entity_id_override_when_ban_missing() -> None:
    """No BAN on the subentry -> ``self.entity_id`` is not forced."""
    sensor = _build_energy_sensor(
        value_key="offtake.priceValue",
        slot_code="TOTAL_HOURS",
        ban=None,
    )
    # entity_id should not have been forced to the engie_belgium_ prefix.
    forced = "sensor.engie_belgium_"
    assert not str(getattr(sensor, "entity_id", "")).startswith(forced)


def test_energy_sensor_sets_ban_prefixed_entity_id_when_ban_present() -> None:
    """A BAN-bearing subentry forces ``entity_id`` to the canonical slug."""
    sensor = _build_energy_sensor(
        value_key="offtake.priceValue",
        slot_code="TOTAL_HOURS",
        ban="B-0001",
    )
    assert sensor.entity_id == (f"sensor.engie_belgium_B-0001_{_EAN}_offtake")


# ---------------------------------------------------------------------------
# native_value + extra_state_attributes happy paths (L572, L577-586)
# ---------------------------------------------------------------------------


def test_energy_sensor_native_value_returns_matched_slot_price() -> None:
    """``native_value`` returns the float for the matched slot code."""
    sensor = _build_energy_sensor(
        value_key="offtake.priceValue",
        slot_code="TOTAL_HOURS",
    )
    assert sensor.native_value == pytest.approx(0.123456)


def test_energy_sensor_extra_state_attributes_full_shape() -> None:
    """
    Verify the full attributes shape.

    Full attributes shape includes ean, last_fetched, from/to, vat_tariff,
    and time_of_use_slot_code. ``ean`` is stripped of its delivery-point
    suffix - the raw ``_ID1``-suffixed EAN is an internal lookup detail,
    not something to expose to the user.
    """
    sensor = _build_energy_sensor(
        value_key="offtake.priceValue",
        slot_code="TOTAL_HOURS",
    )
    attrs = sensor.extra_state_attributes
    assert attrs["ean"] == _EAN.split("_", maxsplit=1)[0]
    assert attrs["last_fetched"] == "2026-05-22T12:00:00+00:00"
    assert attrs["from"] == "2000-01-01"
    assert attrs["to"] == "2099-12-31"
    assert attrs["vat_tariff"] == 6.0
    assert attrs["time_of_use_slot_code"] == "TOTAL_HOURS"


def test_energy_sensor_extra_state_attributes_omits_last_fetched_when_unset() -> None:
    """``last_fetched`` is omitted when the coordinator has never succeeded."""
    coord = _coordinator_with_prices()
    coord.last_successful_fetch = None
    sensor = _build_energy_sensor(
        value_key="offtake.priceValue",
        slot_code="TOTAL_HOURS",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert "last_fetched" not in attrs


def test_energy_sensor_extra_state_attributes_when_no_price_entry() -> None:
    """
    Verify attributes when no matching price entry exists.

    No price entry for this EAN -> attrs contain only ``ean`` (and
    ``last_fetched`` since the coordinator has a successful-fetch
    timestamp); the ``from`` / ``to`` / ``vat_tariff`` / ``slot_code``
    keys must NOT be present.
    """
    coord = _coordinator_with_prices()
    sub = _subentry()
    desc = SensorEntityDescription(
        key=f"{_MISSING_EAN}_offtake",
        translation_key="electricity_offtake_price_eur_per_kwh",
    )
    sensor = EngieBeEnergySensor(
        coordinator=coord,
        subentry=sub,
        entity_description=desc,
        ean=_MISSING_EAN,
        value_key="offtake.priceValue",
        slot_code="TOTAL_HOURS",
    )
    attrs = sensor.extra_state_attributes
    assert attrs == {
        "ean": _MISSING_EAN.split("_", maxsplit=1)[0],
        "last_fetched": "2026-05-22T12:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# _get_current_price_entry: empty coordinator + EAN-miss (L590-595)
# ---------------------------------------------------------------------------


def test_get_current_price_entry_returns_none_when_no_coordinator_data() -> None:
    """``coordinator.data`` is falsy -> early return None."""
    coord = MagicMock()
    coord.config_entry = MagicMock()
    coord.config_entry.entry_id = "entry_abc"
    coord.data = None
    coord.last_successful_fetch = None
    sensor = _build_energy_sensor(
        value_key="offtake.priceValue",
        slot_code="TOTAL_HOURS",
        coordinator=coord,
    )
    assert sensor._get_current_price_entry() is None


def test_get_current_price_entry_returns_none_when_ean_not_found() -> None:
    """No item matches the sensor's EAN -> return None."""
    coord = _coordinator_with_prices()
    sub = _subentry()
    desc = SensorEntityDescription(
        key=f"{_MISSING_EAN}_offtake",
        translation_key="electricity_offtake_price_eur_per_kwh",
    )
    sensor = EngieBeEnergySensor(
        coordinator=coord,
        subentry=sub,
        entity_description=desc,
        ean=_MISSING_EAN,
        value_key="offtake.priceValue",
        slot_code="TOTAL_HOURS",
    )
    assert sensor._get_current_price_entry() is None


# ---------------------------------------------------------------------------
# _get_price_value branches (L599-617)
# ---------------------------------------------------------------------------


def test_get_price_value_returns_none_when_no_price_entry() -> None:
    """No price entry -> None (early-out at L600-601)."""
    coord = _coordinator_with_prices()
    sub = _subentry()
    desc = SensorEntityDescription(
        key=f"{_MISSING_EAN}_offtake",
        translation_key="electricity_offtake_price_eur_per_kwh",
    )
    sensor = EngieBeEnergySensor(
        coordinator=coord,
        subentry=sub,
        entity_description=desc,
        ean=_MISSING_EAN,
        value_key="offtake.priceValue",
        slot_code="TOTAL_HOURS",
    )
    assert sensor._get_price_value() is None


def test_get_price_value_returns_none_when_direction_missing() -> None:
    """A direction not present in the configurations -> None (L606-607)."""
    sensor = _build_energy_sensor(
        value_key="nonexistent_direction.priceValue",
        slot_code="TOTAL_HOURS",
    )
    assert sensor._get_price_value() is None


def test_get_price_value_returns_none_when_slot_code_missing() -> None:
    """No slot entry matches the sensor's slot_code -> fall through to None (L617)."""
    sensor = _build_energy_sensor(
        value_key="offtake.priceValue",
        slot_code="UNKNOWN_SLOT",
    )
    assert sensor._get_price_value() is None


def test_get_price_value_returns_none_when_field_value_is_none() -> None:
    """
    Return None when the matched slot's field value is None.

    A matched slot whose ``field_name`` is missing/None hits the
    ``if value is None: return None`` branch (L613-614).
    """
    coord = _coordinator_with_prices()
    # Mutate the fixture in place: blank out priceValueExclVAT for TOTAL_HOURS.
    items = coord.data["items"]
    for item in items:
        if item["ean"] == _EAN:
            for slot in item["prices"][0]["proportionalPriceConfigurations"]["offtake"]:
                if slot["timeOfUseSlotCode"] == "TOTAL_HOURS":
                    slot["priceValueExclVAT"] = None
    sensor = _build_energy_sensor(
        value_key="offtake.priceValueExclVAT",
        slot_code="TOTAL_HOURS",
        coordinator=coord,
    )
    assert sensor._get_price_value() is None


def test_get_price_value_returns_float_for_matched_slot() -> None:
    """A matched slot returns the float-cast value (L615)."""
    sensor = _build_energy_sensor(
        value_key="offtake.priceValueExclVAT",
        slot_code="TOTAL_HOURS",
    )
    assert sensor._get_price_value() == pytest.approx(0.116468)


def test_get_price_value_returns_none_for_non_numeric_string() -> None:
    """Non-numeric string in matched slot returns None, not ValueError."""
    coord = _coordinator_with_prices()
    items = coord.data["items"]
    for item in items:
        if item["ean"] == _EAN:
            for slot in item["prices"][0]["proportionalPriceConfigurations"]["offtake"]:
                if slot["timeOfUseSlotCode"] == "TOTAL_HOURS":
                    slot["priceValue"] = "N/A"
    sensor = _build_energy_sensor(
        value_key="offtake.priceValue",
        slot_code="TOTAL_HOURS",
        coordinator=coord,
    )
    assert sensor._get_price_value() is None
