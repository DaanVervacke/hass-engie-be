"""Energy dashboard Solar production forecast hook."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._solar import flat_slots, parse_slot_start, solar_surplus_payload

if TYPE_CHECKING:
    from homeassistant.components.energy.types import SolarForecastType
    from homeassistant.core import HomeAssistant

# ENGIE returns kWh per hour. The Energy dashboard expects Wh.
_KWH_TO_WH = 1000.0


async def async_get_solar_forecast(
    hass: HomeAssistant,
    config_entry_id: str,
) -> SolarForecastType | None:
    """Return ``{"wh_hours": {iso: wh}}`` aggregated across subentries, or ``None``."""
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
    for slot in flat_slots(forecasts):
        raw_value = slot.get("value")
        # The key is re-serialised from the parsed datetime, which
        # normalises the offset (a trailing Z becomes +00:00).
        parsed = parse_slot_start(slot.get("startTime"))
        if parsed is None:
            continue
        try:
            value_kwh = float(raw_value) if raw_value is not None else 0.0
        except TypeError, ValueError:
            continue
        if value_kwh <= 0.0:
            # ``NO_DATA`` / ``NO_SURPLUS`` slots carry ``value: 0``, skip them.
            continue
        iso = parsed.isoformat()
        into[iso] = into.get(iso, 0.0) + value_kwh * _KWH_TO_WH
