"""Common fixtures for ENGIE Belgium tests."""

from __future__ import annotations

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: object,  # noqa: ARG001
) -> None:
    """Enable loading of the custom integration in every test."""
    return
