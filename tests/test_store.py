"""Tests for the persistent peaks history store."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.engie_be.store import EngieBePeaksStore

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def fake_store_load() -> Iterator[MagicMock]:
    """Patch ``Store.async_load`` to return a controlled payload per call."""
    with patch(
        "custom_components.engie_be.store.Store",
        autospec=True,
    ) as store_cls:
        instance = MagicMock()
        instance.async_load = AsyncMock(return_value=None)
        instance.async_delay_save = MagicMock()
        store_cls.return_value = instance
        yield instance


async def test_load_starts_empty_when_no_persisted_data(
    fake_store_load: MagicMock,
) -> None:
    """A fresh store with no persisted data exposes an empty peaks list."""
    fake_store_load.async_load.return_value = None
    store = EngieBePeaksStore(MagicMock(), subentry_id="abc")
    await store.async_load()
    assert store.peaks == []


async def test_load_skips_invalid_entries(fake_store_load: MagicMock) -> None:
    """Malformed entries in the persisted payload are dropped on load."""
    fake_store_load.async_load.return_value = {
        "peaks": [
            {
                "year": 2026,
                "month": 3,
                "start": "2026-03-12T19:00:00+01:00",
                "end": "2026-03-12T19:15:00+01:00",
                "peakKW": "2.80000000",
                "peakKWh": "0.70000000",
            },
            "not-a-dict",
            {"year": "bad", "month": 4, "start": "x", "end": "y"},
        ],
    }
    store = EngieBePeaksStore(MagicMock(), subentry_id="abc")
    await store.async_load()
    assert len(store.peaks) == 1
    assert store.peaks[0]["month"] == 3


async def test_upsert_inserts_new_entry_and_schedules_save(
    fake_store_load: MagicMock,
) -> None:
    """Inserting a previously-unseen month adds an entry and schedules a save."""
    store = EngieBePeaksStore(MagicMock(), subentry_id="abc")
    await store.async_load()

    changed = store.upsert(
        year=2026,
        month=4,
        start="2026-04-15T18:00:00+02:00",
        end="2026-04-15T18:15:00+02:00",
        peak_kw="3.50000000",
        peak_kwh="0.87500000",
    )

    assert changed is True
    assert len(store.peaks) == 1
    fake_store_load.async_delay_save.assert_called_once()


async def test_upsert_overwrites_existing_month_when_window_changes(
    fake_store_load: MagicMock,
) -> None:
    """A larger peak in the same month replaces the existing window."""
    store = EngieBePeaksStore(MagicMock(), subentry_id="abc")
    await store.async_load()

    store.upsert(
        year=2026,
        month=4,
        start="2026-04-15T18:00:00+02:00",
        end="2026-04-15T18:15:00+02:00",
        peak_kw="3.50000000",
        peak_kwh="0.87500000",
    )
    fake_store_load.async_delay_save.reset_mock()

    changed = store.upsert(
        year=2026,
        month=4,
        start="2026-04-22T19:30:00+02:00",
        end="2026-04-22T19:45:00+02:00",
        peak_kw="4.20000000",
        peak_kwh="1.05000000",
    )

    assert changed is True
    assert len(store.peaks) == 1
    assert store.peaks[0]["start"] == "2026-04-22T19:30:00+02:00"
    assert store.peaks[0]["peakKW"] == "4.20000000"
    fake_store_load.async_delay_save.assert_called_once()


async def test_upsert_returns_false_and_skips_save_when_unchanged(
    fake_store_load: MagicMock,
) -> None:
    """Re-upserting an identical entry is a no-op."""
    store = EngieBePeaksStore(MagicMock(), subentry_id="abc")
    await store.async_load()

    store.upsert(
        year=2026,
        month=4,
        start="2026-04-15T18:00:00+02:00",
        end="2026-04-15T18:15:00+02:00",
        peak_kw="3.50000000",
        peak_kwh="0.87500000",
    )
    fake_store_load.async_delay_save.reset_mock()

    changed = store.upsert(
        year=2026,
        month=4,
        start="2026-04-15T18:00:00+02:00",
        end="2026-04-15T18:15:00+02:00",
        peak_kw="3.50000000",
        peak_kwh="0.87500000",
    )

    assert changed is False
    fake_store_load.async_delay_save.assert_not_called()


async def test_peaks_are_returned_in_chronological_order(
    fake_store_load: MagicMock,  # noqa: ARG001 - patches Store for the test
) -> None:
    """``peaks`` always sorts entries by (year, month) ascending."""
    store = EngieBePeaksStore(MagicMock(), subentry_id="abc")
    await store.async_load()

    store.upsert(
        year=2026,
        month=4,
        start="2026-04-15T18:00:00+02:00",
        end="2026-04-15T18:15:00+02:00",
        peak_kw="3.50000000",
        peak_kwh="0.87500000",
    )
    store.upsert(
        year=2026,
        month=2,
        start="2026-02-10T18:00:00+01:00",
        end="2026-02-10T18:15:00+01:00",
        peak_kw="2.10000000",
        peak_kwh="0.52500000",
    )
    store.upsert(
        year=2025,
        month=12,
        start="2025-12-05T20:00:00+01:00",
        end="2025-12-05T20:15:00+01:00",
        peak_kw="2.90000000",
        peak_kwh="0.72500000",
    )

    months = [(p["year"], p["month"]) for p in store.peaks]
    assert months == [(2025, 12), (2026, 2), (2026, 4)]


async def test_summary_reports_oldest_newest_and_count(
    fake_store_load: MagicMock,  # noqa: ARG001 - patches Store for the test
) -> None:
    """``summary`` reflects the persisted history at a glance."""
    store = EngieBePeaksStore(MagicMock(), subentry_id="abc")
    await store.async_load()

    store.upsert(
        year=2026,
        month=2,
        start="2026-02-10T18:00:00+01:00",
        end="2026-02-10T18:15:00+01:00",
        peak_kw="2.10000000",
        peak_kwh="0.52500000",
    )
    store.upsert(
        year=2026,
        month=4,
        start="2026-04-15T18:00:00+02:00",
        end="2026-04-15T18:15:00+02:00",
        peak_kw="3.50000000",
        peak_kwh="0.87500000",
    )

    summary = store.summary()
    assert summary == {
        "count": 2,
        "oldest": "2026-02",
        "newest": "2026-04",
        "latest_peakKW": "3.50000000",
    }


async def test_summary_when_empty(
    fake_store_load: MagicMock,  # noqa: ARG001 - patches Store for the test
) -> None:
    """``summary`` returns zeroed fields when nothing is persisted."""
    store = EngieBePeaksStore(MagicMock(), subentry_id="abc")
    await store.async_load()
    assert store.summary() == {
        "count": 0,
        "oldest": None,
        "newest": None,
        "latest_peakKW": None,
    }
