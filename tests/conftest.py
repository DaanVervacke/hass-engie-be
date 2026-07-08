"""Common fixtures for ENGIE Belgium tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CONSUMPTION_ADDRESS,
    CONF_PREMISES_NUMBER,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from custom_components.engie_be.coordinator import EngieBeDataUpdateCoordinator
from custom_components.engie_be.data import EngieBeData, EngieBeSubentryData

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def _disable_relations_backfill(
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """
    Stub the relations-backfill side effect for all coordinator tests by default.

    The coordinator now calls ``client.async_get_customer_account_relations``
    once per refresh as a best-effort fill of subentry display fields.
    Pre-existing tests build their client with a bare ``MagicMock``, so the
    new ``await`` raises ``TypeError: 'MagicMock' object can't be awaited``
    even though the behaviour under test has nothing to do with backfill.

    This autouse fixture replaces the backfill method with a no-op for the
    duration of every test. Tests that explicitly exercise backfill
    behaviour can opt out with the ``backfill`` marker, e.g.::

        @pytest.mark.backfill
        async def test_backfill_does_something(...):
            ...

    Centralising the stub here avoids re-stubbing the relations method in
    every test client builder across four test files.
    """
    if "backfill" in request.keywords:
        yield
        return

    target = (
        "custom_components.engie_be.coordinator."
        "EngieBeDataUpdateCoordinator._async_try_backfill_subentry"
    )
    with patch(target, return_value=None):
        yield


@pytest.fixture(autouse=True)
def _disable_solar_surplus_flag_probe(
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """
    Stub the solar-surplus feature-flag probe by default.

    The coordinator now probes ``solar-surplus-shown-dashboard`` on every
    refresh, but pre-existing tests build clients that do not mock the
    new endpoint. Stubbing the coordinator method itself keeps every
    non-solar test unaware of the new call. Solar-surplus-specific tests
    opt out with the ``solar_surplus`` marker.
    """
    if "solar_surplus" in request.keywords:
        yield
        return

    target = (
        "custom_components.engie_be.coordinator."
        "EngieBeDataUpdateCoordinator._async_fetch_solar_flag"
    )
    with patch(target, return_value=False):
        yield


@pytest.fixture(autouse=True)
def _disable_tou_flag_probe(
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """
    Stub the TOU feature-flag probe and schedule fetch by default.

    The coordinator now probes ``dgo-tou-is-active`` and fetches
    ``/tou-schedules`` on every refresh. Pre-existing tests build clients
    that do not mock these new endpoints. Stubbing the coordinator methods
    keeps every non-TOU test unaware of the new calls. TOU-specific tests
    opt out with the ``tou`` marker.
    """
    if "tou" in request.keywords:
        yield
        return

    flag_target = (
        "custom_components.engie_be.coordinator."
        "EngieBeDataUpdateCoordinator._async_fetch_tou_flag"
    )
    sched_target = (
        "custom_components.engie_be.coordinator."
        "EngieBeDataUpdateCoordinator._async_fetch_tou_schedules"
    )
    with patch(flag_target, return_value=False), patch(sched_target, return_value=None):
        yield


@pytest.fixture(autouse=True)
def _disable_billing_fetch(
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """
    Stub the billing fetch by default.

    The coordinator calls ``_async_fetch_billing`` on every refresh. Pre-existing
    tests build clients that do not mock ``async_get_account_balance``. Stubbing
    the coordinator method keeps every non-billing test unaware of the new call.
    Billing-specific tests opt out with the ``billing`` marker.
    """
    if "billing" in request.keywords:
        yield
        return

    target = (
        "custom_components.engie_be.coordinator."
        "EngieBeDataUpdateCoordinator._async_fetch_billing"
    )
    with patch(target, return_value=None):
        yield


def pytest_configure(config: pytest.Config) -> None:
    """Register test markers so opting out is documented."""
    config.addinivalue_line(
        "markers",
        "backfill: test exercises relations-backfill behaviour; do not stub it",
    )
    config.addinivalue_line(
        "markers",
        "solar_surplus: test exercises the solar-surplus flag probe; do not stub it",
    )
    config.addinivalue_line(
        "markers",
        "tou: test exercises the TOU flag probe; do not stub it",
    )
    config.addinivalue_line(
        "markers",
        "billing: test exercises the billing (account-balance) endpoint; "
        "no autouse stub needed (no feature flag)",
    )


# --- Shared coordinator-test builders ---
#
# Extracted from tests/test_coordinator_solar_surplus.py and
# tests/test_coordinator_tou.py where they were duplicated verbatim.
# Feature-specific ``_make_client`` builders stay in each test file.


@pytest.fixture
def build_engie_entry() -> Callable[[HomeAssistant, str], MockConfigEntry]:
    """
    Return a factory that builds a v5 MockConfigEntry with one subentry.

    The BAN defaults to ``B-0001`` and can be overridden per-call for
    multi-account scenarios.
    """

    def _factory(hass: HomeAssistant, ban: str = "B-0001") -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=5,
            title="user@example.com",
            unique_id="user_example_com",
            data={
                CONF_USERNAME: "user@example.com",
                CONF_PASSWORD: "hunter2",
                CONF_ACCESS_TOKEN: "stored-access",
                CONF_REFRESH_TOKEN: "stored-refresh",
            },
            options={"update_interval": 60},
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_BUSINESS_AGREEMENT,
                    title="placeholder",
                    unique_id=ban,
                    data={
                        CONF_BUSINESS_AGREEMENT_NUMBER: ban,
                        CONF_PREMISES_NUMBER: f"P-{ban}",
                        CONF_CONSUMPTION_ADDRESS: "Test 1, 1000 Brussels",
                    },
                ),
            ],
        )
        entry.add_to_hass(hass)
        return entry

    return _factory


@pytest.fixture
def build_engie_coordinator() -> Callable[
    [HomeAssistant, MockConfigEntry, ConfigSubentry], EngieBeDataUpdateCoordinator
]:
    """Return a factory that instantiates ``EngieBeDataUpdateCoordinator``."""

    def _factory(
        hass: HomeAssistant,
        entry: MockConfigEntry,
        subentry: ConfigSubentry,
    ) -> EngieBeDataUpdateCoordinator:
        return EngieBeDataUpdateCoordinator(
            hass=hass,
            config_entry=entry,
            subentry=subentry,
        )

    return _factory


@pytest.fixture
def wire_engie_runtime() -> Callable[
    [MockConfigEntry, MagicMock, ConfigSubentry, EngieBeDataUpdateCoordinator],
    None,
]:
    """
    Return a factory that attaches ``runtime_data`` to an entry.

    Usage::

        wire_engie_runtime(entry, client, subentry, coord,
                           service_points={"EAN123": "ELECTRICITY"})

    The default ``service_points`` provides a single ELECTRICITY EAN
    matching the current-tree convention.
    """

    def _factory(
        entry: MockConfigEntry,
        client: MagicMock,
        subentry: ConfigSubentry,
        coord: EngieBeDataUpdateCoordinator,
        *,
        service_points: dict[str, str] | None = None,
    ) -> None:
        default_ean = "541448820070414088"
        sub_data = EngieBeSubentryData(
            coordinator=coord,
            service_points=(
                service_points
                if service_points is not None
                else {default_ean: "ELECTRICITY"}
            ),
        )
        entry.runtime_data = EngieBeData(
            client=client,
            epex_coordinator=MagicMock(),
            subentry_data={subentry.subentry_id: sub_data},
            authenticated=True,
            last_options=dict(entry.options),
        )

    return _factory


@pytest.fixture
def add_sensor() -> Callable[[HomeAssistant, Entity], Awaitable[None]]:
    """
    Bind an entity to ``hass`` and drive ``async_added_to_hass``.

    Used by the boundary-scheduler tests across both binary_sensor and
    sensor platforms. Avoids spinning up a full config entry / platform
    pipeline: the scheduler behaviour under test depends only on
    ``self.hass`` being a real HomeAssistant instance, not on the entity
    being registered with a platform. The platform stub carries just
    enough attributes for ``async_write_ha_state`` to render a
    translation key.

    The fallback ``entity_id`` slug is derived from the entity's class
    name so binary sensors land under ``binary_sensor.`` and sensors
    under ``sensor.`` without per-call wiring.
    """

    async def _add(hass: HomeAssistant, entity: Entity) -> None:
        entity.hass = hass
        domain = "binary_sensor" if isinstance(entity, BinarySensorEntity) else "sensor"
        platform = MagicMock()
        platform.platform_name = "engie_be"
        platform.domain = domain
        entity.platform = platform
        if entity.entity_id is None:
            entity.entity_id = f"{domain}.test_{type(entity).__name__.lower()}"
        await entity.async_added_to_hass()

    return _add
