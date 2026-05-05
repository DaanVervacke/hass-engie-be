"""Tests for is_dynamic detection on the per-subentry coordinator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CLIENT_ID,
    CONF_CONSUMPTION_ADDRESS,
    CONF_CUSTOMER_NUMBER,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    KEY_IS_DYNAMIC,
    SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
)
from custom_components.engie_be.coordinator import EngieBeDataUpdateCoordinator
from custom_components.engie_be.data import EngieBeData, EngieBeSubentryData

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

_FIXTURES = Path(__file__).parent / "fixtures"
_PRICES_FIXTURE = _FIXTURES / "prices_sample.json"
_PEAKS_FIXTURE = _FIXTURES / "peaks_2026_04.json"


def _build_entry(
    hass: HomeAssistant,
    *,
    customer_number: str = "000000000000",
) -> MockConfigEntry:
    """Build a v3 MockConfigEntry with one customer-account subentry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        title="user@example.com",
        unique_id="user_example_com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "stored-access",
            CONF_REFRESH_TOKEN: "stored-refresh",
        },
        options={"update_interval": 60},
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
                title="placeholder",
                unique_id=customer_number,
                data={
                    CONF_CUSTOMER_NUMBER: customer_number,
                    CONF_BUSINESS_AGREEMENT_NUMBER: "B-0001",
                    CONF_PREMISES_NUMBER: "P-0001",
                    CONF_CONSUMPTION_ADDRESS: "Test 1, 1000 Brussels",
                },
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


def _only_subentry(entry: MockConfigEntry) -> ConfigSubentry:
    """Return the single customer-account subentry on the test entry."""
    return next(iter(entry.subentries.values()))


def _attach_runtime(entry: MockConfigEntry, client: MagicMock) -> None:
    """Attach an EngieBeData runtime stub with the given mocked client."""
    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=MagicMock(),
        subentry_data={},
        authenticated=True,
        last_options=dict(entry.options),
    )


def _build_dynamic_client() -> MagicMock:
    """
    Build a mocked client whose prices endpoint returns ``items=[]`` (dynamic).

    ``async_get_monthly_peaks`` returns the standard peaks fixture so
    the peaks branch of ``_async_update_data`` succeeds and doesn't
    interfere with what we're actually testing.
    """
    peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))
    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value={"items": []})
    client.async_get_monthly_peaks = AsyncMock(return_value=peaks)
    return client


def _make_coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> EngieBeDataUpdateCoordinator:
    """Construct a per-subentry coordinator for the test entry."""
    return EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=_only_subentry(entry),
    )


# ---------------------------------------------------------------------------
# Detection: is_dynamic flag
#
# In the multi-account architecture, EPEX prices are polled by a
# separate :class:`EngieBeEpexCoordinator` at the parent-entry level
# (one fetch per login regardless of how many subentries exist). The
# per-subentry coordinator therefore no longer fetches or parses EPEX
# data; it only records whether each subentry's tariff is dynamic so
# the platform layer can decide whether to instantiate EPEX entities
# for that subentry. EPEX coordinator behaviour is covered separately
# in ``tests/test_epex_coordinator.py``.
# ---------------------------------------------------------------------------


async def test_non_dynamic_account_records_is_dynamic_false(
    hass: HomeAssistant,
) -> None:
    """A populated ``items`` list must produce ``is_dynamic=False``."""
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(return_value=peaks)
    _attach_runtime(entry, client)

    coordinator = _make_coordinator(hass, entry)
    result = await coordinator._async_update_data()

    assert result[KEY_IS_DYNAMIC] is False
    assert coordinator.is_dynamic is False


async def test_dynamic_account_records_is_dynamic_true(
    hass: HomeAssistant,
) -> None:
    """An empty ``items`` list is the documented signal for a dynamic tariff."""
    entry = _build_entry(hass)
    client = _build_dynamic_client()
    _attach_runtime(entry, client)

    coordinator = _make_coordinator(hass, entry)
    result = await coordinator._async_update_data()
    # The coordinator's ``async_refresh`` is what sets ``self.data`` for the
    # public ``is_dynamic`` property; calling ``_async_update_data`` directly
    # bypasses that, so mirror what the framework would have done.
    coordinator.data = result

    assert result[KEY_IS_DYNAMIC] is True
    assert coordinator.is_dynamic is True


async def test_per_subentry_coordinator_does_not_fetch_epex(
    hass: HomeAssistant,
) -> None:
    """
    The per-subentry coordinator must never call the EPEX endpoint.

    EPEX is the responsibility of :class:`EngieBeEpexCoordinator`. A
    regression that re-introduced EPEX fetches here would multiply the
    EPEX load by the number of subentries.
    """
    entry = _build_entry(hass)
    client = _build_dynamic_client()
    # Attach an EPEX mock so we can assert it's never touched.
    client.async_get_epex_prices = AsyncMock()
    _attach_runtime(entry, client)

    coordinator = _make_coordinator(hass, entry)
    await coordinator._async_update_data()

    client.async_get_epex_prices.assert_not_called()


async def test_is_dynamic_property_false_before_first_refresh(
    hass: HomeAssistant,
) -> None:
    """``is_dynamic`` must default to False when no data has been fetched yet."""
    entry = _build_entry(hass)
    _attach_runtime(entry, MagicMock())

    coordinator = _make_coordinator(hass, entry)

    # No refresh has happened; ``data`` is None.
    assert coordinator.data is None
    assert coordinator.is_dynamic is False


async def test_is_dynamic_property_reflects_latest_refresh(
    hass: HomeAssistant,
) -> None:
    """
    ``is_dynamic`` must reflect the most recent refresh.

    A subentry whose tariff is later switched from dynamic to fixed
    (or vice versa) must immediately show the new state on the
    property after the next successful poll.
    """
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_monthly_peaks = AsyncMock(return_value=peaks)
    # First poll: dynamic. Second poll: fixed.
    client.async_get_prices = AsyncMock(side_effect=[{"items": []}, prices])
    _attach_runtime(entry, client)

    coordinator = _make_coordinator(hass, entry)

    first = await coordinator._async_update_data()
    coordinator.data = first
    assert coordinator.is_dynamic is True

    second = await coordinator._async_update_data()
    coordinator.data = second
    assert coordinator.is_dynamic is False


# ---------------------------------------------------------------------------
# Override precedence (b6 contracts-driven detection)
#
# When ``EngieBeSubentryData.is_dynamic_override`` is set by
# ``_async_populate_dynamic_flags``, it MUST take precedence over the
# legacy ``len(items) == 0`` heuristic on the prices payload. The
# legacy heuristic remains as a fallback only when the override is
# ``None`` (e.g. the contracts call failed at setup).
# ---------------------------------------------------------------------------


def _attach_runtime_with_override(
    entry: MockConfigEntry,
    client: MagicMock,
    *,
    override: bool | None,
) -> str:
    """Attach runtime with an EngieBeSubentryData carrying ``is_dynamic_override``."""
    sub = next(iter(entry.subentries.values()))
    sub_data = EngieBeSubentryData(
        coordinator=MagicMock(),  # replaced by the real coordinator below
        is_dynamic_override=override,
    )
    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=MagicMock(),
        subentry_data={sub.subentry_id: sub_data},
        authenticated=True,
        last_options=dict(entry.options),
    )
    return sub.subentry_id


async def test_override_true_wins_over_populated_prices(
    hass: HomeAssistant,
) -> None:
    """An override of ``True`` must flip ``is_dynamic`` even with priced items."""
    entry = _build_entry(hass)
    prices = json.loads(_PRICES_FIXTURE.read_text(encoding="utf-8"))
    peaks = json.loads(_PEAKS_FIXTURE.read_text(encoding="utf-8"))

    client = MagicMock()
    client.async_get_prices = AsyncMock(return_value=prices)
    client.async_get_monthly_peaks = AsyncMock(return_value=peaks)
    sid = _attach_runtime_with_override(entry, client, override=True)

    coordinator = _make_coordinator(hass, entry)
    # Wire the real coordinator into the runtime so the property can find it.
    entry.runtime_data.subentry_data[sid].coordinator = coordinator

    result = await coordinator._async_update_data()
    coordinator.data = result

    # Legacy heuristic on populated ``items`` would say False; override wins.
    assert result[KEY_IS_DYNAMIC] is False
    assert coordinator.is_dynamic is True


async def test_override_false_wins_over_empty_prices(
    hass: HomeAssistant,
) -> None:
    """An override of ``False`` must beat the empty-items dynamic heuristic."""
    entry = _build_entry(hass)
    client = _build_dynamic_client()
    sid = _attach_runtime_with_override(entry, client, override=False)

    coordinator = _make_coordinator(hass, entry)
    entry.runtime_data.subentry_data[sid].coordinator = coordinator

    result = await coordinator._async_update_data()
    coordinator.data = result

    # Legacy heuristic on empty ``items`` would say True; override wins.
    assert result[KEY_IS_DYNAMIC] is True
    assert coordinator.is_dynamic is False


async def test_override_none_falls_back_to_legacy_heuristic(
    hass: HomeAssistant,
) -> None:
    """An override of ``None`` must defer to the legacy heuristic."""
    entry = _build_entry(hass)
    client = _build_dynamic_client()
    sid = _attach_runtime_with_override(entry, client, override=None)

    coordinator = _make_coordinator(hass, entry)
    entry.runtime_data.subentry_data[sid].coordinator = coordinator

    result = await coordinator._async_update_data()
    coordinator.data = result

    # Empty ``items`` + override=None => legacy heuristic flags dynamic.
    assert coordinator.is_dynamic is True
