"""
Energy dashboard hook for the ENGIE Belgium integration.

Home Assistant's Energy dashboard auto-discovers integrations that implement
``async_get_solar_forecast(hass, config_entry_id) -> {"wh_hours": ...}`` and
uses the response to render the "Solar production forecast" card. The hook
lives at ``energy.py`` in the integration package; HA loads it lazily via
``async_get_integration_platforms``.

This implementation aggregates the ENGIE Smart App's solar-surplus forecasts
(injection expected to exceed household consumption) across every subentry
under the given config entry. Values are converted from kWh (ENGIE) to Wh
(HA Energy contract) and summed across delivery points sharing the same
hour, so a multi-EAN household sees a single household-level forecast.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ._solar_surplus import solar_surplus_payload

if TYPE_CHECKING:
    from homeassistant.components.energy.types import SolarForecastType
    from homeassistant.core import HomeAssistant

# ENGIE returns kWh per hour; the Energy dashboard expects Wh.
_KWH_TO_WH = 1000.0


async def async_get_solar_forecast(
    hass: HomeAssistant,
    config_entry_id: str,
) -> SolarForecastType | None:
    """
    Return the aggregated solar-surplus forecast for one ENGIE config entry.

    Returns ``None`` when the entry is not loaded, has no subentries with a
    solar-surplus payload, or every payload is empty. Otherwise returns
    ``{"wh_hours": {iso_timestamp: wh_value}}`` where timestamps are the
    slot start times ENGIE published (already timezone-aware) and values
    are non-negative floats.
    """
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if entry is None:
        return None
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        return None

    aggregated: dict[str, float] = {}
    for sub_data in runtime.subentry_data.values():
        if not sub_data.feature_flags.solar:
            continue
        coordinator = sub_data.coordinator
        per_ean = solar_surplus_payload(coordinator)
        if per_ean is None:
            continue
        for forecasts in per_ean.values():
            if not isinstance(forecasts, list):
                continue
            _accumulate_slots(forecasts, aggregated)

    if not aggregated:
        return None
    return {"wh_hours": dict(sorted(aggregated.items()))}


def _accumulate_slots(
    forecasts: list[Any],
    into: dict[str, float],
) -> None:
    """Fold every hourly slot in ``forecasts`` into the ``into`` accumulator."""
    for day in forecasts:
        if not isinstance(day, dict):
            continue
        details = day.get("details")
        if not isinstance(details, list):
            continue
        for slot in details:
            if not isinstance(slot, dict):
                continue
            raw_start = slot.get("startTime")
            raw_value = slot.get("value")
            if not isinstance(raw_start, str):
                continue
            try:
                # Validate the timestamp is parseable and timezone-aware
                # so downstream cards can chart it, but keep the string
                # form so the Energy dashboard renders the same instant
                # ENGIE published.
                parsed = datetime.fromisoformat(raw_start)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                continue
            try:
                value_kwh = float(raw_value) if raw_value is not None else 0.0
            except TypeError, ValueError:
                continue
            if value_kwh <= 0.0:
                # HA renders the forecast as a positive area; slots ENGIE
                # marks as ``NO_DATA`` / ``NO_SURPLUS`` carry ``value: 0``
                # and add no signal, so skip them to keep the payload lean.
                continue
            iso = parsed.isoformat()
            into[iso] = into.get(iso, 0.0) + value_kwh * _KWH_TO_WH
