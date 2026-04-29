"""Tests for the ENGIE Belgium diagnostics platform."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CUSTOMER_NUMBER,
    CONF_REFRESH_TOKEN,
    DEFAULT_CLIENT_ID,
    DOMAIN,
)
from custom_components.engie_be.data import EngieBeData
from custom_components.engie_be.diagnostics import (
    EAN_HASH_LENGTH,
    TO_REDACT,
    _hash_ean,
    async_get_config_entry_diagnostics,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

REDACTED_MARKER = "**REDACTED**"

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "prices_sample.json"


def _build_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a MockConfigEntry with realistic data + runtime_data attached."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_CUSTOMER_NUMBER: "000000000000",
            CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
            CONF_ACCESS_TOKEN: "eyJfake.access.token",
            CONF_REFRESH_TOKEN: "v1.fake_refresh_token",
        },
        options={"update_interval": 60},
        unique_id="user_example_com",
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.data = json.loads(_FIXTURE_PATH.read_text())
    coordinator.last_update_success = True
    coordinator.update_interval = timedelta(minutes=60)

    entry.runtime_data = EngieBeData(
        client=MagicMock(),
        coordinator=coordinator,
        authenticated=True,
        last_options={"update_interval": 60},
        service_points={
            "541448820000000001_ID1": "ELECTRICITY",
            "541448820000000002_ID1": "GAS",
        },
    )
    return entry


async def test_redacts_credentials(hass: HomeAssistant) -> None:
    """All credential fields in TO_REDACT are replaced with the redaction marker."""
    entry = _build_entry(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    entry_data = diag["entry"]["data"]
    for key in TO_REDACT:
        assert key in entry_data, f"Expected key {key!r} to be present in entry.data"
        assert entry_data[key] == REDACTED_MARKER, (
            f"Expected {key!r} to be redacted, got {entry_data[key]!r}"
        )


async def test_payload_structure_and_ean_hashing(hass: HomeAssistant) -> None:
    """Diagnostics payload exposes only privacy-preserving structure and hashed EANs."""
    entry = _build_entry(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    # Top-level structure
    assert set(diag.keys()) == {"entry", "runtime", "coordinator"}
    assert diag["entry"]["version"] == 2
    assert diag["entry"]["title"] == "user@example.com"
    assert diag["entry"]["options"] == {"update_interval": 60}

    # Runtime section: service_points keyed by hash, no raw EANs
    runtime = diag["runtime"]
    assert runtime["authenticated"] is True
    assert set(runtime["service_points"].values()) == {"ELECTRICITY", "GAS"}
    for hashed_ean in runtime["service_points"]:
        assert len(hashed_ean) == EAN_HASH_LENGTH
        assert all(c in "0123456789abcdef" for c in hashed_ean)
    assert "541448820000000001_ID1" not in runtime["service_points"]
    assert "541448820000000002_ID1" not in runtime["service_points"]

    # Coordinator section
    coord = diag["coordinator"]
    assert coord["last_update_success"] is True
    assert coord["update_interval_seconds"] == 3600.0
    assert coord["data_summary"]["item_count"] >= 1
    assert all(len(h) == EAN_HASH_LENGTH for h in coord["data_summary"]["ean_hashes"])
    # Raw EAN must not appear anywhere in the serialised payload
    serialised = json.dumps(diag)
    assert "541448820000000001_ID1" not in serialised
    assert "541448820000000002_ID1" not in serialised
    assert "hunter2" not in serialised
    assert "000000000000" not in serialised
    assert "v1.fake_refresh_token" not in serialised


def test_hash_ean_is_deterministic_and_short() -> None:
    """_hash_ean returns a stable short hex digest for the same input."""
    ean = "541448820000000001_ID1"
    digest_a = _hash_ean(ean)
    digest_b = _hash_ean(ean)
    assert digest_a == digest_b
    assert len(digest_a) == EAN_HASH_LENGTH
    assert _hash_ean("different_ean") != digest_a
