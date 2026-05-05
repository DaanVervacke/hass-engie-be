"""Common fixtures for ENGIE Belgium tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

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


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``backfill`` marker so opting out is documented."""
    config.addinivalue_line(
        "markers",
        "backfill: test exercises relations-backfill behaviour; do not stub it",
    )
