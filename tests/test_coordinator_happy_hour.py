"""
Tests for the debug-only happy-hour-event probe in the coordinator.

This module pins the redaction + log-level contract of the probe so we
catch regressions before they ship in a prerelease. The probe itself
is debug-only and not meant to merge to ``main``; the tests are kept
focused so they're cheap to drop when the probe is removed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.engie_be.api import (
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
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
    SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
)
from custom_components.engie_be.coordinator import EngieBeDataUpdateCoordinator
from custom_components.engie_be.data import EngieBeData

if TYPE_CHECKING:
    import pytest
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

_BAN = "002209795515"
_EAN = "541448820070414088"
_CAN = "1504462994"

_NON_EMPTY_PAYLOAD = {
    "businessAgreementNumber": _BAN,
    "customerAccountNumber": _CAN,
    "ean": _EAN,
    "event": {
        "startDateTime": "2026-05-19T18:00:00+02:00",
        "endDateTime": "2026-05-19T20:00:00+02:00",
        "status": "ACTIVE",
    },
}


def _build_entry(hass: HomeAssistant) -> MockConfigEntry:
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
                unique_id=_CAN,
                data={
                    CONF_CUSTOMER_NUMBER: _CAN,
                    CONF_BUSINESS_AGREEMENT_NUMBER: _BAN,
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


def _build_coordinator(
    hass: HomeAssistant,
    client: MagicMock,
) -> EngieBeDataUpdateCoordinator:
    """Build a coordinator with the runtime stub wired in."""
    entry = _build_entry(hass)
    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=MagicMock(),
        subentry_data={},
        authenticated=True,
        last_options=dict(entry.options),
    )
    return EngieBeDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        subentry=_only_subentry(entry),
    )


async def test_happy_hour_probe_logs_non_empty_payload_redacted_at_debug(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A non-empty response is logged at DEBUG with BAN/EAN/CAN masked.

    Regression guard for the audit blocker: prior to redaction the
    success path dumped the raw payload at WARNING, leaking the full
    BAN/EAN/CAN. Pin both the level (DEBUG, not WARNING) and the
    masking (``_redact_body`` -> last-4 only for partial-mask keys).
    """
    client = MagicMock()
    client.async_get_happy_hour_event = AsyncMock(return_value=_NON_EMPTY_PAYLOAD)
    coordinator = _build_coordinator(hass, client)

    with caplog.at_level(logging.DEBUG, logger="custom_components.engie_be"):
        await coordinator._async_log_happy_hour_event(client)

    # Exactly one record from the probe, at DEBUG.
    probe_records = [
        r for r in caplog.records if "happy-hour-event response" in r.getMessage()
    ]
    assert len(probe_records) == 1
    record = probe_records[0]
    assert record.levelno == logging.DEBUG

    message = record.getMessage()
    # Full identifiers must NOT appear in cleartext.
    assert _BAN not in message
    assert _EAN not in message
    assert _CAN not in message
    # Partial-mask keys keep the last 4 chars, so the tails do appear.
    assert "5515" in message  # BAN tail
    assert "4088" in message  # EAN tail
    assert "2994" in message  # CAN tail
    # Non-PII event fields pass through untouched.
    assert "ACTIVE" in message
    assert "2026-05-19T18:00:00" in message

    client.async_get_happy_hour_event.assert_awaited_once_with(_BAN)


async def test_happy_hour_probe_empty_payload_does_not_warn(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty ``{}`` response is logged at DEBUG, never WARNING."""
    client = MagicMock()
    client.async_get_happy_hour_event = AsyncMock(return_value={})
    coordinator = _build_coordinator(hass, client)

    with caplog.at_level(logging.DEBUG, logger="custom_components.engie_be"):
        await coordinator._async_log_happy_hour_event(client)

    probe_records = [r for r in caplog.records if "happy-hour-event" in r.getMessage()]
    assert len(probe_records) == 1
    assert probe_records[0].levelno == logging.DEBUG
    # No WARNING records from the probe on the happy path.
    assert not any(
        r.levelno == logging.WARNING and "happy-hour-event" in r.getMessage()
        for r in caplog.records
    )


async def test_happy_hour_probe_auth_error_warns_and_swallows(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Auth failure on the probe surfaces a WARNING but does not raise."""
    client = MagicMock()
    client.async_get_happy_hour_event = AsyncMock(
        side_effect=EngieBeApiClientAuthenticationError("token rejected")
    )
    coordinator = _build_coordinator(hass, client)

    with caplog.at_level(logging.WARNING, logger="custom_components.engie_be"):
        # Must not raise -- the probe is best-effort.
        await coordinator._async_log_happy_hour_event(client)

    warn_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and "happy-hour-event probe failed (auth)" in r.getMessage()
    ]
    assert len(warn_records) == 1


async def test_happy_hour_probe_generic_error_warns_and_swallows(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Generic API failure on the probe surfaces a WARNING but does not raise."""
    client = MagicMock()
    client.async_get_happy_hour_event = AsyncMock(
        side_effect=EngieBeApiClientError("upstream 500")
    )
    coordinator = _build_coordinator(hass, client)

    with caplog.at_level(logging.WARNING, logger="custom_components.engie_be"):
        await coordinator._async_log_happy_hour_event(client)

    warn_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.getMessage().startswith("happy-hour-event probe failed:")
    ]
    assert len(warn_records) == 1
