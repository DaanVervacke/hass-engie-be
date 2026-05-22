"""
Tests for the Happy Hour gating in ``sensor.async_setup_entry``.

The platform setup must skip Happy Hour timestamp sensors when the
per-subentry ``is_happy_hour_enrolled`` flag is False, regardless of
what the coordinator may already have cached in ``data["happy_hour"]``.
The flag is the single source of truth: enrolment is detected via the
feature-flags endpoint during the coordinator's first refresh, and the
parent entry is reloaded when it flips so entities track service
status without per-tick re-checks.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.engie_be.const import SUBENTRY_TYPE_BUSINESS_AGREEMENT
from custom_components.engie_be.sensor import (
    EngieBeHappyHourTimestampSensor,
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


def _make_sub_data(*, is_happy_hour_enrolled: bool) -> MagicMock:
    """
    Build a per-subentry runtime-data stub.

    ``coordinator.data`` is a minimal ``{"items": []}`` so the energy
    sensor builder returns nothing and the test isolates the Happy
    Hour gate.  ``is_dynamic`` is False so the EPEX sensors are also
    skipped and the asserted entity counts reflect Happy Hour only.
    """
    sub_data = MagicMock()
    sub_data.coordinator = MagicMock()
    sub_data.coordinator.data = {"items": []}
    sub_data.coordinator.is_dynamic = False
    sub_data.coordinator.config_entry = MagicMock()
    sub_data.coordinator.config_entry.entry_id = "test_entry_id"
    sub_data.service_points = {}
    sub_data.is_happy_hour_enrolled = is_happy_hour_enrolled
    return sub_data


def _make_entry(
    *,
    subentries: dict[str, MagicMock],
    sub_runtime: dict[str, MagicMock],
) -> MagicMock:
    """Build a MagicMock parent ConfigEntry exposing the v5 runtime layout."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.subentries = subentries
    entry.runtime_data = MagicMock()
    entry.runtime_data.epex_coordinator = MagicMock()
    entry.runtime_data.subentry_data = sub_runtime
    return entry


async def test_setup_entry_omits_happy_hour_sensors_when_not_enrolled() -> None:
    """Un-enrolled subentry must not get any Happy Hour timestamp sensors."""
    subentry = _make_subentry(subentry_id="sub_no_hh")
    entry = _make_entry(
        subentries={"sub_no_hh": subentry},
        sub_runtime={"sub_no_hh": _make_sub_data(is_happy_hour_enrolled=False)},
    )

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    assert not any(isinstance(e, EngieBeHappyHourTimestampSensor) for e in added)


async def test_setup_entry_adds_happy_hour_sensors_when_enrolled() -> None:
    """Enrolled subentry gets both Happy Hour start and end sensors."""
    subentry = _make_subentry(subentry_id="sub_hh")
    entry = _make_entry(
        subentries={"sub_hh": subentry},
        sub_runtime={"sub_hh": _make_sub_data(is_happy_hour_enrolled=True)},
    )

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    hh = [e for e in added if isinstance(e, EngieBeHappyHourTimestampSensor)]
    assert len(hh) == 2
    fields = {e._field for e in hh}
    assert fields == {"start", "end"}


async def test_setup_entry_mixed_enrolment_gates_per_subentry() -> None:
    """Mixed install: only the enrolled subentry contributes HH sensors."""
    sub_yes = _make_subentry(subentry_id="sub_yes", title="Enrolled")
    sub_no = _make_subentry(subentry_id="sub_no", title="Not Enrolled")
    entry = _make_entry(
        subentries={"sub_yes": sub_yes, "sub_no": sub_no},
        sub_runtime={
            "sub_yes": _make_sub_data(is_happy_hour_enrolled=True),
            "sub_no": _make_sub_data(is_happy_hour_enrolled=False),
        },
    )

    added: list = []

    def _add(entities, *_args: object, **_kwargs: object) -> None:  # noqa: ANN001
        added.extend(entities)

    await async_setup_entry(MagicMock(), entry, _add)

    hh = [e for e in added if isinstance(e, EngieBeHappyHourTimestampSensor)]
    assert len(hh) == 2
    unique_ids = {e.unique_id for e in hh}
    assert unique_ids == {
        "test_entry_id_sub_yes_happy_hour_next_start",
        "test_entry_id_sub_yes_happy_hour_next_end",
    }
