# Plan 017: Expose ENGIE Time-of-Use (TOU) schedules as HA sensors + binary sensors

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs,
> stop and report — do not improvise. When done, update the status row
> for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 85011b7..HEAD -- custom_components/engie_be tests plans`
> Some of the working-tree state at "Planned at" may still be uncommitted
> (v0.13.0b0 Solar Surplus feature + plans 010-016). Compare the "Current
> state" excerpts against the live file before proceeding.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (new endpoint + new sensor lifecycle around boundary scheduling; well within the patterns established by Solar Surplus and EPEX)
- **Depends on**: none. Composes cleanly with the Solar Surplus feature-flag helper and the EPEX/Solar-Surplus boundary scheduler patterns.
- **Category**: feature
- **Planned at**: commit `85011b7`, 2026-07-08

## Why this matters

Fluvius is rolling out true time-of-use (TOU) tariff for Belgian digital-meter
customers; the network operator's transport-and-distribution charges are
already TOU-based for most accounts, even on a flat supplier contract. The
ENGIE Smart App exposes a per-EAN schedule endpoint that returns the full
weekly PEAK/OFFPEAK layout for both the supplier's product and the DGO's
network charges, in both directions (offtake + injection). The endpoint
responds even when the customer isn't on a TOU-priced supplier contract,
because the DGO schedule always applies.

For HA users this unlocks a headline automation shape: **"defer this load
until the current offtake slot is OFFPEAK"** and **"push my solar surplus
to charge the EV when current injection slot is PEAK (best sell price)"** —
directly complementing the existing EPEX and Solar Surplus sensors.

## Current state

### The endpoint

`GET https://api.engie.be/engie/ms/energy-insights/customer/v1/business-agreements/{BAN}/tou-schedules`

- Bearer-authenticated with the same access token the integration already
  uses for other energy-insights endpoints.
- Responds regardless of the `dgo-tou-is-active` feature-flag state — the
  flag only gates the Smart App's dashboard tile.
- Verified against BAN `002209795515` on 2026-07-08; response captured in
  `tests/fixtures/tou_schedules_sample.json` (to be added in this plan).

### Verified wire schema

Root:

```json
{
  "items": [
    {
      "eanWithSuffix": "541448820070414088_ID1",
      "supplierSchedule": {...},
      "dgoTgoSchedule": {...}
    }
  ]
}
```

Each schedule (both `supplierSchedule` and `dgoTgoSchedule`) has the shape:

```json
{
  "activeConfigurationId": "RTPCMP_004",
  "offtake": <DirectionSchedule>,
  "injection": <DirectionSchedule>
}
```

Each `DirectionSchedule`:

```json
{
  "optimalTimeslotCode": "OFFPEAK" | "PEAK",
  "monday":    [<Slot>, ...],
  "tuesday":   [<Slot>, ...],
  ...
  "sunday":    [<Slot>, ...]
}
```

Each `Slot`:

```json
{
  "startTime": "HH:MM",   // Brussels-local
  "endTime":   "HH:MM",   // "00:00" means end-of-day (midnight)
  "slotCode":  "PEAK" | "OFFPEAK"
}
```

Weekend days often collapse to a single all-day slot
`{ startTime: "00:00", endTime: "00:00", slotCode: "OFFPEAK" }`.

**Slot-code enum (union of everything the app can display):** `PEAK`,
`OFFPEAK`, `SUPEROFFPEAK`, `EXCLUSIVE_NIGHT`, `DAY`.

- `PEAK` / `OFFPEAK` / `EXCLUSIVE_NIGHT` — present in the Dart
  `TimeSlotCategory` enum (`peak`, `offPeak`, `exclusiveNight`); `PEAK`
  and `OFFPEAK` observed on the wire, `EXCLUSIVE_NIGHT` documented in
  the enum for the Fluvius rollout.
- `DAY` — Dart enum value (`day`), also documented but not yet observed.
- `SUPEROFFPEAK` — the app carries a "Super Offpeak" display label
  (found in `libapp.so`) and the integration already handles
  `SUPEROFFPEAK` for tri-rate price sensors at `sensor.py:81`. Tri-rate
  Belgian contracts split off-peak into off-peak and super-off-peak; the
  same code is expected to appear on the TOU schedule endpoint for
  those accounts.

Whitelisting all five keeps the sensor from going `unknown` when a new
slot code surfaces and stays consistent with the existing tri-rate price
convention.

### Feature flag (already-known pattern)

The Smart App gates the UI tile on the boolean feature flag
`dgo-tou-is-active`, queried via the same
`POST https://api.engie.be/engie/ms/feature-flags/customer/v1/boolean-feature-flags/_query`
endpoint the integration already uses for `happy-hours-service-enabled`
and `solar-surplus-shown-dashboard`. The shared helper
`_async_query_boolean_feature_flag(flag_name, ban)` in `api.py:1101`
(added in plan 016 series) is the point of extension.

The endpoint returning data does not imply the flag is `True` — the flag
signals whether the *supplier's* billing is TOU-priced. Expose the flag
state, but do NOT skip the fetch when it's `False`; the DGO/network
schedule is still real and useful.

### Repo conventions to follow

- Constants in `const.py`; use frozen sets / tuples for enums.
- New API method in `api.py` follows the pattern of
  `async_get_solar_surplus_forecasts`: single-line docstring summary +
  multi-paragraph body, delegates via `_api_wrapper`, uses
  `_authenticated_headers()`.
- Coordinator soft-fails transient errors (keep last-known wrapper). Auth
  errors escalate via `ConfigEntryAuthFailed`. See the Solar Surplus block
  in `coordinator.py::_async_update_data` for the canonical shape.
- Coordinator wrapper: `{"data": <api_payload>, "fetched_at": <ISO-UTC>}`.
  Matches Solar Surplus.
- Sensors extend `EngieBeEntity` for per-subentry devices. Boundary-scheduled
  sensors use `_BoundaryScheduleMixin(_EngieBeEntity, SensorEntity)` with the
  mixin listed FIRST in MRO (documented in `entity.py::_BoundaryScheduleMixin`).
- `unique_id` shape: `{entry_id}_{subentry_id}_{ean}_{key}`.
- `entity_id` slug: `sensor.engie_belgium_{ban}_{ean}_{key}`.
- Test files opt into custom pytest markers via
  `pytestmark = pytest.mark.<name>` at module top.
- Strings/translations synced: `strings.json` is authoritative,
  `translations/en.json` is a byte copy.

### Files that will be touched

- `custom_components/engie_be/const.py` — add slot-code enum + flag key.
- `custom_components/engie_be/api.py` — new `async_get_tou_schedules(ban)`
  method + new `async_get_dgo_tou_is_active_flag(ban)` wrapper on the shared
  helper.
- `custom_components/engie_be/data.py` — add `is_tou_active: bool | None`
  to `EngieBeSubentryData`.
- `custom_components/engie_be/coordinator.py` — new
  `_async_fetch_tou_schedules(...)`, `_async_fetch_tou_flag(...)`, and
  `_async_apply_tou_active(...)` methods; wired into `_async_update_data`
  after the solar-surplus block; add once-per-outage logging helpers
  matching the solar pattern.
- `custom_components/engie_be/sensor.py` — new sensor classes (see design
  section).
- `custom_components/engie_be/binary_sensor.py` — new "is optimal slot"
  binary sensors.
- `custom_components/engie_be/strings.json` + `translations/en.json` —
  entity names and slot-code enum translations.
- `tests/fixtures/tou_schedules_bihoraire.json` (new) — full response
  fixture captured from BAN 002209795515 with EAN redacted.
- `tests/fixtures/tou_schedules_flat_all_offpeak.json` (new) — synthetic
  fixture for the "all-day OFFPEAK" edge case.
- `tests/test_api_tou_schedules.py` (new).
- `tests/test_coordinator_tou.py` (new).
- `tests/test_sensor_tou.py` (new).
- `tests/test_binary_sensor_tou.py` (new).
- `tests/conftest.py` — add autouse stub for `_async_fetch_tou_flag` mirroring
  the `_disable_solar_surplus_flag_probe` pattern; register `tou` marker.
- `tests/test_init.py::_make_client` — add mocks for the two new methods.
- `README.md` — add "Time-of-Use tariff schedules" section.
- `CHANGELOG.md` — Unreleased entry.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Format | `.venv/bin/ruff format custom_components tests` | no diffs |
| Lint | `.venv/bin/ruff check custom_components tests` | `All checks passed!` |
| Target tests | `.venv/bin/pytest tests/test_api_tou_schedules.py tests/test_coordinator_tou.py tests/test_sensor_tou.py tests/test_binary_sensor_tou.py -v` | all pass |
| Full gate | `.venv/bin/python -m pytest tests/ -q --cov=custom_components.engie_be --cov-fail-under=95` | pass, ≥95% |
| Restart HA (implicit per user memory) | `podman restart ha-plugin-test` | container name |

## Scope

**In scope**:
- Endpoint fetch, per-EAN sensors, boundary scheduling, feature-flag probe,
  strings, tests, README, CHANGELOG.

**Out of scope**:
- Any statistics import based on TOU (that already lives in the
  usage-details / recorder pipeline).
- Any per-slot price calculation (integration doesn't know TOU tariff
  amounts; ENGIE bills them independently and users can already read them
  from invoices).
- Automatic switching of dependent entities based on slot changes — that's
  a Lovelace / blueprint concern.
- A calendar entity for the weekly schedule — deferred as follow-up if
  users request it.

## Git workflow

- Branch: `advisor/017-time-of-use-schedules`.
- Commit style: `feat(sensor): expose TOU tariff schedules per electricity meter`.

## Steps

### Step 1: Capture and sanitize the fixtures

Create `tests/fixtures/tou_schedules_bihoraire.json` from the real payload
already captured this session. **Redact the EAN** by replacing it with
`541448820070000000_ID1` (matches the redaction convention in existing
fixtures) and leave the schedule body unchanged.

Create `tests/fixtures/tou_schedules_flat_all_offpeak.json` as a synthetic
edge case:

```json
{
  "items": [
    {
      "eanWithSuffix": "541448820070000000_ID1",
      "supplierSchedule": {
        "activeConfigurationId": "RTPCMP_FLAT",
        "offtake": {
          "optimalTimeslotCode": "OFFPEAK",
          "monday":    [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "tuesday":   [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "wednesday": [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "thursday":  [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "friday":    [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "saturday":  [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "sunday":    [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}]
        },
        "injection": {
          "optimalTimeslotCode": "OFFPEAK",
          "monday":    [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "tuesday":   [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "wednesday": [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "thursday":  [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "friday":    [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "saturday":  [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}],
          "sunday":    [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}]
        }
      },
      "dgoTgoSchedule": {
        "activeConfigurationId": "RTPCMP_FLAT",
        "offtake":   {"optimalTimeslotCode": "OFFPEAK", "monday": [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}], "tuesday": [], "wednesday": [], "thursday": [], "friday": [], "saturday": [], "sunday": []},
        "injection": {"optimalTimeslotCode": "OFFPEAK", "monday": [{"startTime": "00:00", "endTime": "00:00", "slotCode": "OFFPEAK"}], "tuesday": [], "wednesday": [], "thursday": [], "friday": [], "saturday": [], "sunday": []}
      }
    }
  ]
}
```

**Verify**: `python3 -c "import json; [json.load(open(p)) for p in ('tests/fixtures/tou_schedules_bihoraire.json','tests/fixtures/tou_schedules_flat_all_offpeak.json')]; print('OK')"` → prints `OK`.

### Step 2: Add constants

Edit `custom_components/engie_be/const.py`. After the
`SOLAR_SURPLUS_SHOWN_DASHBOARD_KEY` line, add:

```python
# Feature-flag key that gates the Time-of-Use dashboard tile in the Smart
# App. Extracted from the Android app's ``libapp.so``
# (``dgo-tou-is-active`` + ``isTimeOfUseActive`` sync method). The flag
# only gates the UI tile; the /tou-schedules endpoint returns data even
# when the flag is false, because the DGO/network schedule always applies.
TOU_FLAG_KEY = "dgo-tou-is-active"

# Slot codes: union of every value the Smart App can display, so a new
# slot code from ENGIE never lands the sensor in ``unknown``.
#
# - ``peak`` / ``offpeak`` / ``exclusive_night`` — Dart ``TimeSlotCategory``
#   enum. ``PEAK`` and ``OFFPEAK`` observed on the wire (BAN 002209795515,
#   2026-07-08). ``EXCLUSIVE_NIGHT`` documented for the Fluvius rollout.
# - ``day`` — Dart enum, not yet observed.
# - ``superoffpeak`` — the app carries a ``"Super Offpeak"`` display label,
#   and the integration already maps ``SUPEROFFPEAK`` for tri-rate PRICE
#   sensors at ``sensor.py:81``. Tri-rate Belgian contracts extend the
#   binary peak/offpeak split; the same code is expected on the TOU
#   schedule endpoint for those accounts.
#
# Wire values are uppercase (e.g. ``PEAK``, ``OFFPEAK``, ``SUPEROFFPEAK``,
# ``EXCLUSIVE_NIGHT``, ``DAY``). Sensors expose the ``.lower()`` form so
# the ENUM device class matches the strings.json translation keys.
TOU_SLOT_CODES: tuple[str, ...] = (
    "peak",
    "offpeak",
    "superoffpeak",
    "exclusive_night",
    "day",
)

# Weekday keys returned by the API, in ISO order.
TOU_WEEKDAY_KEYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
```

**Verify**: `.venv/bin/python -c "from custom_components.engie_be.const import TOU_FLAG_KEY, TOU_SLOT_CODES, TOU_WEEKDAY_KEYS; print(TOU_SLOT_CODES)"` prints
`('peak', 'offpeak', 'superoffpeak', 'exclusive_night', 'day')`.

### Step 3: Add API methods

Edit `custom_components/engie_be/api.py`. Add two new methods immediately
after `async_get_solar_surplus_shown_dashboard_flag`. Model each after the
corresponding solar-surplus method:

```python
    async def async_get_tou_schedules(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """
        Fetch the time-of-use tariff schedules for a business agreement.

        Returns the parsed JSON response. Shape:
        ``{"items": [{"eanWithSuffix": "..._ID1", "supplierSchedule": {...},
        "dgoTgoSchedule": {...}}]}`` where each schedule has per-direction
        ``offtake`` / ``injection`` maps of weekday → list of
        ``{startTime, endTime, slotCode}`` slots. Endpoint responds even
        when the ``dgo-tou-is-active`` feature flag is off because the
        DGO/network schedule always applies to metered electricity.
        """
        ban = business_agreement_number.replace(" ", "")
        url = (
            f"{HAPPY_HOUR_BASE_URL}/business-agreements/{ban}/tou-schedules"
        )
        headers = self._authenticated_headers()
        return await self._api_wrapper(
            session=self._session,
            method="GET",
            url=url,
            headers=headers,
            json_response=True,
        )

    async def async_get_dgo_tou_is_active_flag(
        self,
        business_agreement_number: str,
    ) -> dict[str, Any]:
        """
        Fetch the ``dgo-tou-is-active`` boolean feature flag for a BAN.

        Mirrors the Smart App's UI gate for the TOU tile. ``value: true``
        means the customer's supplier contract is TOU-billed and slot
        sensors are directly relevant to their bill. ``value: false``
        still allows displaying the network/DGO schedule since that
        applies to all digital-meter customers.

        Returns the parsed JSON response as a flat dict (top-level
        ``value`` and ``reason`` keys).
        """
        return await self._async_query_boolean_feature_flag(
            TOU_FLAG_KEY,
            business_agreement_number,
        )
```

Add `TOU_FLAG_KEY` to the `.const` import block at the top of `api.py`
alongside the existing feature-flag key imports.

**Verify**: `.venv/bin/ruff check custom_components/engie_be/api.py` → exit 0.

### Step 4: Add subentry state field

Edit `custom_components/engie_be/data.py`. In `EngieBeSubentryData`, after
`has_solar`, add:

```python
    is_tou_active: bool | None = field(default=None)
```

Extend the class docstring with a short paragraph mirroring the `has_solar`
one:

```
``is_tou_active`` mirrors the latest reading of ``dgo-tou-is-active``. It
is ``None`` before the first successful refresh, ``True`` when the
customer's supplier contract is TOU-priced, and ``False`` otherwise. Slot
sensors are always created when the endpoint returns data (the network
schedule applies universally); the flag is exposed as a binary sensor so
users can distinguish supplier-side TOU from network-only TOU.
```

**Verify**: `.venv/bin/python -c "from custom_components.engie_be.data import EngieBeSubentryData; s = EngieBeSubentryData.__dataclass_fields__; print(list(s))"` includes `is_tou_active`.

### Step 5: Wire the coordinator

Edit `custom_components/engie_be/coordinator.py`. In `_async_update_data`,
after the solar-surplus block, add a mirroring TOU block:

```python
        # Fetch TOU schedules. The flag gates the supplier-side TOU
        # meaning but the endpoint returns data regardless (the network
        # schedule always applies to digital-meter customers). Fetch
        # unconditionally and surface the flag separately.
        previous_tou_wrapper: dict[str, Any] | None = None
        if isinstance(self.data, dict):
            existing_tou = self.data.get("tou_schedules")
            if isinstance(existing_tou, dict):
                previous_tou_wrapper = existing_tou

        previous_is_tou_active = self._read_cached_is_tou_active()
        tou_active = await self._async_fetch_tou_flag(
            client,
            business_agreement_number,
        )
        tou_wrapper = await self._async_fetch_tou_schedules(
            client,
            business_agreement_number,
            previous_tou_wrapper,
        )
        if tou_wrapper is not None:
            data["tou_schedules"] = tou_wrapper
        self._async_apply_is_tou_active(
            previous_is_tou_active=previous_is_tou_active,
            new_is_tou_active=tou_active,
        )
```

Add the four supporting methods, all mirroring the solar-surplus equivalents:

- `_read_cached_is_tou_active(self) -> bool | None` — reads from
  `runtime.subentry_data[subentry_id].is_tou_active`.
- `async def _async_fetch_tou_flag(self, client, ban) -> bool` — probes the
  flag via `client.async_get_dgo_tou_is_active_flag(ban)`. Auth errors
  escalate. Other errors soft-fail to previous cached value (default
  `False` on first observation, matching solar-surplus discipline for
  "assume not enrolled").
- `async def _async_fetch_tou_schedules(self, client, ban, previous_wrapper)
  -> dict[str, Any] | None` — fetches the endpoint. Returns
  `{"data": <payload>, "fetched_at": <ISO-UTC>}` on success, or
  `previous_wrapper` on transient failure. Auth errors escalate.
- `_async_apply_is_tou_active(self, *, previous, new)` — persists the flag
  onto `subentry_data.is_tou_active`; on flip, schedules a config-entry
  reload via `runtime.reload_pending` (same debounce as solar/happy-hour).

Add `_note_tou_unavailable` / `_note_tou_recovered` for once-per-outage
logging on the schedules endpoint, mirroring `_note_solar_unavailable` at
`coordinator.py:493`. Add `self._tou_unavailable: bool = False` to
`__init__`.

**Verify**:
- `.venv/bin/python -c "import ast; ast.parse(open('custom_components/engie_be/coordinator.py').read())"` succeeds.
- `.venv/bin/ruff check custom_components/engie_be/coordinator.py` → exit 0.

### Step 6: Design the sensors

Add these classes to `custom_components/engie_be/sensor.py`. Design
principles:

- **Per electricity EAN**, two enum-state sensors:
  - `sensor.engie_belgium_{ban}_{ean}_offtake_slot` — current supplier
    offtake slot (state ∈ TOU_SLOT_CODES). Attributes expose the full
    weekly schedule + `optimal_slot`.
  - `sensor.engie_belgium_{ban}_{ean}_injection_slot` — same shape for
    injection.
- **Boundary-scheduled**: state must flip at the exact slot boundary. Reuse
  `_BoundaryScheduleMixin` (established pattern from EPEX current-price
  and solar-surplus current-hour sensors).
- **Optional secondary source**: expose `dgo_tgo_slot` as an attribute
  alongside the primary supplier slot rather than as separate sensors —
  network-schedule usually equals supplier-schedule in Belgium (per the
  captured fixture) and duplicating sensors adds noise for no gain.
- **Next-transition timestamp** as an attribute (`next_transition`, ISO-8601
  Brussels-local). Automations can `state_attr(...)` on it.

Skeleton for the offtake sensor (mirror for injection):

```python
_TOU_OFFTAKE_SLOT = SensorEntityDescription(
    key="offtake_slot",
    translation_key="tou_offtake_slot",
    device_class=SensorDeviceClass.ENUM,
    options=list(TOU_SLOT_CODES),
    icon="mdi:transmission-tower-import",
)
_TOU_INJECTION_SLOT = SensorEntityDescription(
    key="injection_slot",
    translation_key="tou_injection_slot",
    device_class=SensorDeviceClass.ENUM,
    options=list(TOU_SLOT_CODES),
    icon="mdi:transmission-tower-export",
)


class _EngieBeTouSlotBase(_BoundaryScheduleMixin, EngieBeEntity, SensorEntity):
    """Per-EAN, per-direction current TOU slot with boundary scheduling."""

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entity_description: SensorEntityDescription,
        ean: str,
        direction: str,  # "offtake" or "injection"
    ) -> None:
        super().__init__(coordinator, subentry)
        self.entity_description = entity_description
        self._ean = ean
        self._direction = direction
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{subentry.subentry_id}_{ean}_{entity_description.key}"
        )
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            self.entity_id = (
                f"sensor.engie_belgium_{ban}_{ean}_{entity_description.key}"
            )
        self._attr_translation_placeholders = {"ean": ean}
```

Then a helper module `_tou.py` (small pure helper, HA-free — matches the
existing `_epex.py` / `_happy_hour.py` convention for pure helpers):

```python
"""Pure helpers for parsing ENGIE time-of-use schedules."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

_BRUSSELS = ZoneInfo("Europe/Brussels")
_WEEKDAY_KEYS = (
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)


def _parse_hhmm(raw: Any) -> time | None:
    if not isinstance(raw, str):
        return None
    parts = raw.split(":", 1)
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= h < 24 and 0 <= m < 60) and not (h == 0 and m == 0):
        return None
    return time(hour=h, minute=m)


def _weekday_slots(schedule: dict, weekday_index: int) -> list[dict]:
    """Return slot list for a given weekday index (0=Monday)."""
    key = _WEEKDAY_KEYS[weekday_index]
    slots = schedule.get(key)
    return slots if isinstance(slots, list) else []


def current_slot(
    schedule: dict[str, Any], now: datetime | None = None
) -> tuple[str | None, datetime | None]:
    """Return (current_slot_code_lowercase_or_None, next_transition_utc_or_None).

    ``schedule`` is one direction's block (has monday-sunday keys).
    ``now`` defaults to Brussels-local now. Handles the ``00:00`` end-time
    (== midnight/end-of-day) convention. Returns (None, None) if the
    schedule is empty or malformed.
    """
    now_local = (now.astimezone(_BRUSSELS) if now else datetime.now(_BRUSSELS))
    weekday = now_local.weekday()
    today_slots = _weekday_slots(schedule, weekday)
    for slot in today_slots:
        start = _parse_hhmm(slot.get("startTime"))
        end = _parse_hhmm(slot.get("endTime"))
        code = slot.get("slotCode")
        if start is None or end is None or not isinstance(code, str):
            continue
        start_dt = datetime.combine(now_local.date(), start, tzinfo=_BRUSSELS)
        # end="00:00" means end-of-day → treat as tomorrow 00:00
        if end == time(0, 0):
            end_dt = datetime.combine(
                now_local.date() + timedelta(days=1), time(0, 0), tzinfo=_BRUSSELS
            )
        else:
            end_dt = datetime.combine(now_local.date(), end, tzinfo=_BRUSSELS)
        if start_dt <= now_local < end_dt:
            return code.lower(), end_dt.astimezone(now_local.tzinfo)
    return None, None
```

**Sensor.native_value / next-boundary** must delegate to `current_slot()`.

### Step 7: Add binary sensors for "is optimal slot"

Edit `custom_components/engie_be/binary_sensor.py`. Add per-EAN
`is_offtake_optimal` and `is_injection_optimal` boolean sensors. State is
`true` when the current slot's lowercased code equals the schedule's
`optimalTimeslotCode` (also lowercased). Use `_BoundaryScheduleMixin`
too — the "optimal" state also flips at slot boundaries.

Gate these behind `sub_data.is_tou_active is True` OR any per-EAN schedule
having more than one distinct slot code across a week (i.e. don't spam
"is optimal" sensors on flat-rate accounts where every hour is OFFPEAK).

### Step 8: Strings + translations

Edit `custom_components/engie_be/strings.json` (in the `entity.sensor` block):

```json
"tou_offtake_slot": {
    "name": "Current offtake slot ({ean})",
    "state": {
        "peak": "Peak",
        "offpeak": "Off-peak",
        "superoffpeak": "Super off-peak",
        "exclusive_night": "Exclusive night",
        "day": "Day"
    }
},
"tou_injection_slot": {
    "name": "Current injection slot ({ean})",
    "state": {
        "peak": "Peak",
        "offpeak": "Off-peak",
        "superoffpeak": "Super off-peak",
        "exclusive_night": "Exclusive night",
        "day": "Day"
    }
}
```

And in the `entity.binary_sensor` block:

```json
"tou_offtake_is_optimal": { "name": "Offtake at optimal slot ({ean})" },
"tou_injection_is_optimal": { "name": "Injection at optimal slot ({ean})" }
```

Then: `cp custom_components/engie_be/strings.json custom_components/engie_be/translations/en.json`.

**Verify**: `python3 -c "import json; json.load(open('custom_components/engie_be/strings.json'))"` → no error.

### Step 9: Tests

Follow the exact structure of the Solar Surplus test files. Each new test
file starts with `pytestmark = pytest.mark.tou` so the autouse
`_disable_tou_flag_probe` fixture (added to conftest in this step) skips
this test file.

**Add to `tests/conftest.py`** — mirror the solar-surplus stub pattern:

```python
@pytest.fixture(autouse=True)
def _disable_tou_flag_probe(
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """Stub the TOU feature-flag probe by default."""
    if "tou" in request.keywords:
        yield
        return
    target = (
        "custom_components.engie_be.coordinator."
        "EngieBeDataUpdateCoordinator._async_fetch_tou_flag"
    )
    with patch(target, return_value=False):
        yield
```

Register the marker in `pytest_configure` alongside the existing markers.

**Add mock methods to `tests/test_init.py::_make_client`**:

```python
client.async_get_dgo_tou_is_active_flag = AsyncMock(return_value={})
client.async_get_tou_schedules = AsyncMock(return_value={"items": []})
```

**Test files to create**:

- `tests/test_api_tou_schedules.py` — mirror `tests/test_api_solar_surplus.py`:
  - URL builder test (asserts full URL matches)
  - BAN whitespace stripping test
  - Flag POST body test (mirrors solar-flag test)

- `tests/test_coordinator_tou.py` — mirror `tests/test_coordinator_solar_surplus.py`:
  - Flag-off with non-trivial payload → wrapper stored, `is_tou_active=False`
  - Flag-on → wrapper stored, `is_tou_active=True`
  - Transient endpoint error → previous wrapper preserved
  - Auth error on schedules → escalates
  - Auth error on flag → escalates
  - Flag flip (True→False) → reload scheduled once via
    `hass.config_entries.async_reload` (`monkeypatch.setattr` pattern from
    plan 015)
  - First observation seeds cache without reload

- `tests/test_sensor_tou.py` — unit tests on the sensor using the
  bihoraire fixture:
  - `native_value == "offpeak"` at 04:00 Monday
  - `native_value == "peak"` at 10:00 Wednesday
  - `native_value == "offpeak"` at 22:00 Friday
  - `native_value == "offpeak"` all day Saturday
  - `native_value is None` when wrapper absent
  - `native_value is None` when a slot has malformed times
  - Attributes: `optimal_slot`, `next_transition`, `weekday_slots`,
    `dgo_tgo_slot` all present
  - `unique_id` shape assertion

- `tests/test_binary_sensor_tou.py` — for the "is optimal" sensors:
  - Offtake optimal = OFFPEAK, current=OFFPEAK → `is_on=True`
  - Offtake optimal = OFFPEAK, current=PEAK → `is_on=False`
  - Injection optimal = PEAK, current=PEAK → `is_on=True`
  - No wrapper → `is_on is None`

Use `pytest-freezer` (already a project dev-dep) to freeze the clock in
`native_value` tests. Model the `freezer: FrozenDateTimeFactory` typing
pattern from `tests/test_sensor_solar_surplus.py`.

**Boundary scheduler tests** (optional follow-up plan, keep out of this
plan to bound scope — mirror plan-014-style deferred lifecycle tests).

**Verify**: `.venv/bin/pytest tests/test_api_tou_schedules.py tests/test_coordinator_tou.py tests/test_sensor_tou.py tests/test_binary_sensor_tou.py -v` → all pass.

### Step 10: README + CHANGELOG

Add a new subsection under the existing "Solar Surplus" section in
`README.md` titled "Time-of-Use tariff schedules" documenting:

- What TOU is in the Belgian context (bi-hourly, exclusive-night, Fluvius
  rollout)
- Which sensors are created (per electricity EAN, per direction)
- Which attributes are exposed (`optimal_slot`, `next_transition`,
  `weekday_slots`, `dgo_tgo_slot`)
- Which binary sensors are created and how to key automations off them
  ("run dishwasher when `binary_sensor.engie_belgium_{ean}_offtake_is_optimal`
  is `on`")
- The endpoint responds independently of the `dgo-tou-is-active` flag
  because the network schedule always applies

Add a `[Unreleased]` entry in `CHANGELOG.md` under `### Added`:

```
- Time-of-use (TOU) tariff schedules per electricity meter. Two enum
  sensors per EAN (current offtake slot, current injection slot) plus two
  binary sensors ("at optimal offtake slot", "at optimal injection slot").
  Uses hour-boundary scheduling so state flips exactly on the slot
  transition, not on the next coordinator refresh. Feature-flag
  ``dgo-tou-is-active`` state exposed as a diagnostic; sensors are
  created whenever the endpoint returns data (the DGO network schedule
  applies to all digital-meter customers). [#NN]
```

### Step 11: Final gate

- `.venv/bin/ruff format custom_components tests` → no diffs
- `.venv/bin/ruff check custom_components tests` → `All checks passed!`
- `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` → pass, ≥95%
- `podman restart ha-plugin-test` → smoke-test in devcontainer
- Confirm setup logs mention TOU fetch:
  `podman logs ha-plugin-test 2>&1 | grep -iE "tou|time.of.use" | tail -10`

## Test plan

- API layer: request-shape tests (URL, BAN sanitization, POST body).
- Coordinator: flag observation, flag flip, endpoint error paths, auth
  escalation on both endpoints, first-observation vs subsequent flip.
- Sensor: correct slot at every canonical time (early morning, midday,
  late evening, weekend); attribute presence; unique_id shape; graceful
  degradation on missing wrapper or malformed slot times.
- Binary sensor: matches / mismatches / no-wrapper.
- All new tests opt into `pytest.mark.tou`.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `.venv/bin/ruff check custom_components tests` exits 0.
- [ ] `grep "tou-schedules" custom_components/engie_be/api.py` returns one match.
- [ ] `grep "TOU_SLOT_CODES" custom_components/engie_be/const.py` returns two matches (definition + usage note).
- [ ] `grep "class _EngieBeTouSlotBase\|tou_offtake_slot\|tou_injection_slot" custom_components/engie_be/sensor.py` returns at least three matches.
- [ ] `grep "tou_offtake_is_optimal\|tou_injection_is_optimal" custom_components/engie_be/binary_sensor.py` returns at least two matches.
- [ ] `tests/fixtures/tou_schedules_bihoraire.json` parses as valid JSON and contains no unredacted EANs (grep for `54144882007041` returns no matches).
- [ ] `.venv/bin/pytest tests/test_api_tou_schedules.py tests/test_coordinator_tou.py tests/test_sensor_tou.py tests/test_binary_sensor_tou.py -v` all pass with ≥ 20 new tests total across the four files.
- [ ] `.venv/bin/python -m pytest tests/ -q --cov-fail-under=95` passes.
- [ ] `strings.json` and `translations/en.json` are byte-identical (`diff -q custom_components/engie_be/strings.json custom_components/engie_be/translations/en.json` returns no output).
- [ ] `README.md` contains a "Time-of-Use" section.
- [ ] `CHANGELOG.md` has an `[Unreleased]` entry mentioning TOU.
- [ ] Devcontainer boots without errors after restart; TOU-related debug logs are present.
- [ ] `plans/README.md` status row for 017 flipped to DONE.

## STOP conditions

Stop and report back (do not improvise) if:

- The `tests/fixtures/tou_schedules_bihoraire.json` file cannot be sanitized
  because unredacted EANs appear elsewhere in the payload — investigate
  where before storing.
- The endpoint returns a shape that doesn't match the documented schema
  (e.g. no `items` key at root, or `optimalTimeslotCode` missing) — a
  quick probe curl from the devcontainer will show. Do NOT force a match;
  report and re-plan.
- The `_BoundaryScheduleMixin` MRO breaks (any of the existing EPEX /
  solar-surplus boundary-scheduler tests fail) — the base class ordering
  is documented in `entity.py` and must not be reversed for the TOU sensors.
- `hass.config_entries.async_reload` cannot be monkeypatched from the
  coordinator test (different pattern in the pinned HA version) — abort
  and consult plan 015's reload-mock pattern for the actual signature.
- The `optimalTimeslotCode` field appears in the wire response but its
  value is anything other than one of `TOU_SLOT_CODES` — surface the new
  code as a finding, do not silently accept.

## Maintenance notes

For the human/agent who owns this after landing:

- **Fluvius rollout timeline**: Fluvius announced full TOU tariff rollout
  for all customers through 2026-2027. Expect the `dgo-tou-is-active`
  flag to flip `True` for many accounts. The sensors already surface data
  today; the flag flip only affects the "supplier-side TOU billing" story.
- **Slot-code enum growth**: `EXCLUSIVE_NIGHT`, `DAY`, and `SUPEROFFPEAK`
  are whitelisted in `TOU_SLOT_CODES` even though only `PEAK` / `OFFPEAK`
  have been observed on the `/tou-schedules` endpoint so far. When any
  of the other three surfaces in a real payload:
  1. No code change needed — the ENUM device class already accepts them.
  2. Add a targeted test in `test_sensor_tou.py` using the real payload
     as a new fixture (`tou_schedules_<pattern>.json`).
  3. Update the README's example if the user-visible label needs
     re-wording for a specific market/contract.
  `SUPEROFFPEAK` is expected first on tri-rate Belgian contracts;
  `EXCLUSIVE_NIGHT` next on Fluvius customers who opt into the
  night-tariff meter.
- **DGO schedule ≠ supplier schedule**: today they're identical on all
  captured accounts. When they diverge (e.g. supplier moves to a different
  peak window than the DGO), the `dgo_tgo_slot` attribute becomes
  informational. Consider promoting it to a separate sensor if there's
  demand.
- **Deferred**: a calendar entity showing the weekly schedule, and
  boundary-scheduler lifecycle tests for the TOU sensors (mirror
  `test_sensor_solar_surplus_schedulers.py`) — both should be follow-ups
  once the primary sensors are live.
- Reviewer should scrutinize the `current_slot` helper's `00:00`
  end-of-day handling and the DST transition (Brussels-local + Europe/Brussels
  timezone-aware datetimes), because that's the only non-obvious logic in
  the module.
