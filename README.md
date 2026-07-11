<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-engie-dark.svg">
    <img src=".github/assets/logo-engie-light.svg" alt="ENGIE" width="220">
  </picture>
</p>

<h1 align="center">ENGIE (BE) - Home Assistant integration</h1>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square" alt="HACS Custom"></a>
  <a href="https://github.com/DaanVervacke/hass-engie-be/releases"><img src="https://img.shields.io/github/v/release/DaanVervacke/hass-engie-be?style=flat-square&label=version&sort=semver" alt="Latest release"></a>
  <a href="https://github.com/DaanVervacke/hass-engie-be/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/DaanVervacke/hass-engie-be/validate.yml?style=flat-square&label=hacs%20%2F%20hassfest" alt="Validate"></a>
  <a href="https://github.com/DaanVervacke/hass-engie-be/actions/workflows/test.yml"><img src="https://img.shields.io/github/actions/workflow/status/DaanVervacke/hass-engie-be/test.yml?style=flat-square&label=tests" alt="Tests"></a>
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/dynamic/json?url=https://raw.githubusercontent.com/DaanVervacke/hass-engie-be/main/hacs.json&query=$.homeassistant&label=Home%20Assistant&prefix=%3E%3D&color=41BDF5&logo=home-assistant&logoColor=white&style=flat-square" alt="Home Assistant"></a>
  <a href="https://www.home-assistant.io/docs/quality_scale/"><img src="https://img.shields.io/badge/quality_scale-gold-gold.svg?style=flat-square" alt="Quality Scale"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/github/license/DaanVervacke/hass-engie-be?style=flat-square" alt="License"></a>
</p>

---

Custom [Home Assistant](https://www.home-assistant.io/) integration for
[ENGIE Belgium](https://www.engie.be/). Retrieves your personal energy price
data, monthly capacity-tariff peaks, Happy Hours free-energy windows, and
EPEX (European Power Exchange) day-ahead wholesale prices from the ENGIE API and exposes them
as sensors, binary sensors, and calendar events.

## Table of contents

- [Features](#features)
- [Sensors](#sensors)
  - [Gas](#gas)
  - [Electricity: single-rate](#electricity-single-rate)
  - [Electricity: dual-rate (peak / off-peak)](#electricity-dual-rate-peak--off-peak)
  - [Electricity: tri-rate (peak / off-peak / super off-peak)](#electricity-tri-rate-peak--off-peak--super-off-peak)
  - [Electricity: dynamic tariff (EPEX-indexed)](#electricity-dynamic-tariff-epex-indexed)
  - [Capacity tariff (captar)](#capacity-tariff-captar)
  - [Happy Hours](#happy-hours)
  - [Solar Surplus](#solar-surplus)
  - [Time-of-Use tariff schedules](#time-of-use-tariff-schedules)
  - [Billing](#billing)
- [Automations](#automations)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Multiple households](#multiple-households)
- [Historical usage import (Energy dashboard)](#historical-usage-import-energy-dashboard)
- [Re-authentication](#re-authentication)
- [Known limitations](#known-limitations)
- [Removing the integration](#removing-the-integration)
- [Troubleshooting](#troubleshooting)
- [Credential storage](#credential-storage)
- [Changelog](#changelog)
- [License](#license)

## Features

- Authenticates with your ENGIE account using two-factor authentication (MFA)
- Auto-detects gas and electricity contracts, including dynamic (EPEX-indexed) tariffs with hourly or quarter-hourly price data
- Import your hourly usage / historic data from ENGIE into the Energy dashboard: electricity consumption, injection, gas consumption, and per-hour costs
- Creates price sensors per energy type, direction (offtake / injection), and tariff rate
- Tracks the monthly capacity-tariff (captar) peak window for each electricity meter
- Time-of-use (TOU) slot sensors per electricity meter, with "is optimal" binary sensors and a full weekly calendar view
- Surfaces ENGIE's Happy Hours free-energy promotions on each account, both as sensors and as calendar events
- Exposes ENGIE's Solar Surplus forecasts (3-day hourly injection outlook) for households with solar panels
- Supports multiple households (business agreements) under a single ENGIE login, including several active addresses under one customer account
- Billing sensors per account: outstanding balance, overdue amount, and next invoice due date
- Native automation surface for the automation editor: 29 purpose-specific triggers, 10 conditions, and calendar-slot events. No template YAML required
- Configurable update interval

## Sensors

The integration auto-detects your contracts and creates the right sensors. All
price sensors report values in **EUR/kWh** with 6 decimals, and each one
exposes its EAN (18-digit meter identifier), the validity window, and the applicable VAT rate as
attributes. Every sensor below is also available as an `_excl_vat` variant
(same name, `_excl_vat` suffix on both the entity ID and the friendly name).

> Entity IDs shown in the tables and examples below use two
> placeholders: `{BAN}` for the 12-digit business-agreement number and
> `{EAN}` for the 18-digit meter identifier (used by per-meter sensors
> like solar surplus and TOU slots). Substitute your own values when
> referencing a sensor in automations or dashboards. Both values are
> visible in **Developer tools** > **States** and in the device name
> in **Settings** > **Devices & services**.

### Gas

Gas contracts are always single-rate.

Price-sensor entity IDs embed the meter's EAN and the tariff direction
(and, for multi-rate contracts, the slot code). The friendly name in
the Home Assistant UI is translated per contract type, but the entity ID always
follows this pattern.

| Sensor | Entity ID | Description |
|---|---|---|
| Gas offtake price | `sensor.engie_belgium_{BAN}_{EAN}_offtake` | Current gas offtake price for this meter (EUR/kWh, VAT included) |

### Electricity: single-rate

Created when your contract has a single electricity rate.

| Sensor | Entity ID | Description |
|---|---|---|
| Electricity offtake price | `sensor.engie_belgium_{BAN}_{EAN}_offtake` | Current electricity offtake price for this meter (EUR/kWh, VAT included) |
| Electricity injection price | `sensor.engie_belgium_{BAN}_{EAN}_injection` | Current injection compensation for this meter (EUR/kWh, VAT included) |

### Electricity: dual-rate (peak / off-peak)

Created when your contract has separate peak and off-peak rates. These replace
the single-rate sensors above for that meter.

| Sensor | Entity ID | Description |
|---|---|---|
| Electricity peak offtake price | `sensor.engie_belgium_{BAN}_{EAN}_offtake_peak` | Offtake price during peak hours (EUR/kWh, VAT included) |
| Electricity off-peak offtake price | `sensor.engie_belgium_{BAN}_{EAN}_offtake_offpeak` | Offtake price during off-peak hours (EUR/kWh, VAT included) |
| Electricity peak injection price | `sensor.engie_belgium_{BAN}_{EAN}_injection_peak` | Injection compensation during peak hours (EUR/kWh, VAT included) |
| Electricity off-peak injection price | `sensor.engie_belgium_{BAN}_{EAN}_injection_offpeak` | Injection compensation during off-peak hours (EUR/kWh, VAT included) |

### Electricity: tri-rate (peak / off-peak / super off-peak)

Created when your contract has three time-of-use rates.

| Sensor | Entity ID | Description |
|---|---|---|
| Electricity peak offtake price | `sensor.engie_belgium_{BAN}_{EAN}_offtake_peak` | Offtake price during peak hours (EUR/kWh, VAT included) |
| Electricity off-peak offtake price | `sensor.engie_belgium_{BAN}_{EAN}_offtake_offpeak` | Offtake price during off-peak hours (EUR/kWh, VAT included) |
| Electricity super off-peak offtake price | `sensor.engie_belgium_{BAN}_{EAN}_offtake_superoffpeak` | Offtake price during super off-peak hours (EUR/kWh, VAT included) |
| Electricity peak injection price | `sensor.engie_belgium_{BAN}_{EAN}_injection_peak` | Injection compensation during peak hours (EUR/kWh, VAT included) |
| Electricity off-peak injection price | `sensor.engie_belgium_{BAN}_{EAN}_injection_offpeak` | Injection compensation during off-peak hours (EUR/kWh, VAT included) |
| Electricity super off-peak injection price | `sensor.engie_belgium_{BAN}_{EAN}_injection_superoffpeak` | Injection compensation during super off-peak hours (EUR/kWh, VAT included) |

> Injection sensors are only created when injection data is present.

### Electricity: dynamic tariff (EPEX-indexed)

Customers on ENGIE's dynamic (EPEX-indexed) electricity contract get sensors that expose day-ahead wholesale prices from the EPEX day-ahead auction. All dynamic tariff contracts get eight sensors: four for hourly slots and four for 15-minute (quarter-hourly) slots.

| Sensor | Entity ID | Description |
|---|---|---|
| EPEX current hour price | `sensor.engie_belgium_{BAN}_epex_current` | Wholesale EPEX day-ahead price for the current hour (EUR/kWh) |
| EPEX next hour price | `sensor.engie_belgium_{BAN}_epex_next_hour` | Wholesale EPEX day-ahead price for the next hour (EUR/kWh) |
| EPEX lowest hour price today | `sensor.engie_belgium_{BAN}_epex_low_today` | Lowest EPEX hour price of today (EUR/kWh) |
| EPEX highest hour price today | `sensor.engie_belgium_{BAN}_epex_high_today` | Highest EPEX hour price of today (EUR/kWh) |
| EPEX current quarter-hourly price | `sensor.engie_belgium_{BAN}_epex_current_quarter_hour` | Wholesale EPEX day-ahead price for the current 15-minute slot (EUR/kWh) |
| EPEX next quarter-hourly price | `sensor.engie_belgium_{BAN}_epex_next_quarter_hour` | Wholesale EPEX day-ahead price for the next 15-minute slot (EUR/kWh) |
| EPEX lowest quarter-hourly price today | `sensor.engie_belgium_{BAN}_epex_low_today_quarter_hour` | Lowest EPEX quarter-hourly price of today (EUR/kWh) |
| EPEX highest quarter-hourly price today | `sensor.engie_belgium_{BAN}_epex_high_today_quarter_hour` | Highest EPEX quarter-hourly price of today (EUR/kWh) |

All four sensors are in **EUR/kWh** (4 decimals). Tomorrow's prices appear
once ENGIE publishes them, typically around 14:00 Europe/Brussels.

The current-price sensor exposes the full today / tomorrow slate as
attributes, which is convenient for plotting:

```yaml
type: custom:apexcharts-card
graph_span: 24h
span:
  start: day
header:
  show: true
  title: EPEX day-ahead (today)
series:
  - entity: sensor.engie_belgium_{BAN}_epex_current
    type: column
    data_generator: |
      return entity.attributes.today.map(s => [new Date(s.start).getTime(), s.value]);
```

> Wholesale prices can be **negative** during periods of oversupply. This is
> reported faithfully.

A binary sensor `binary_sensor.engie_belgium_{BAN}_epex_negative` turns on
when the current wholesale slot has a negative price, so you can build simple
state-based automations without a template.

### Capacity tariff (captar)

Four sensors expose the monthly peak window used for the Belgian
capacity-tariff calculation. ENGIE returns one aggregated peak per
business agreement.

| Sensor | Entity ID | Description |
|---|---|---|
| Captar monthly peak power | `sensor.engie_belgium_{BAN}_captar_monthly_peak_power` | Highest 15-minute average power for the month, in kW |
| Captar monthly peak energy | `sensor.engie_belgium_{BAN}_captar_monthly_peak_energy` | Energy consumed during that 15-minute window, in kWh |
| Captar monthly peak start | `sensor.engie_belgium_{BAN}_captar_monthly_peak_start` | Start of the 15-minute peak window |
| Captar monthly peak end | `sensor.engie_belgium_{BAN}_captar_monthly_peak_end` | End of the 15-minute peak window |

ENGIE only publishes a peak after the first 15-minute peak of the month is
recorded, so the current month is empty for the first day or so. Until then,
the integration shows the previous month's peak and marks it via the
`peak_is_fallback` attribute (and `peak_month` shows which month the value
covers).

A calendar entity (`calendar.engie_belgium_{BAN}`) is also created and shows the
current monthly peak as a single event, with the peak power and energy in the
event description.

### Happy Hours

Happy Hours are free-energy windows ENGIE schedules for enrolled
accounts. Windows are announced the day before, and enrolment is per
address through the ENGIE Smart App under "Je producten". See
[engie.be/nl/happyhours](https://www.engie.be/nl/happyhours/) for
eligibility.

| Entity | Entity ID | Description |
|---|---|---|
| Happy Hours is active | `binary_sensor.engie_belgium_{BAN}_happy_hours_active` | On while a scheduled Happy Hours window is currently active |
| Happy Hours next start | `sensor.engie_belgium_{BAN}_happy_hours_next_start` | Start timestamp of the next scheduled Happy Hours window |
| Happy Hours next end | `sensor.engie_belgium_{BAN}_happy_hours_next_end` | End timestamp of the next scheduled Happy Hours window |
| Happy Hours monthly consumption | `sensor.engie_belgium_{BAN}_happy_hours_month_consumption` | Electricity consumed during Happy Hours this month (kWh) |
| Happy Hours eligible hours this month | `sensor.engie_belgium_{BAN}_happy_hours_month_eligible_hours` | Number of eligible Happy Hours windows this month |
| Happy Hours monthly reward | `sensor.engie_belgium_{BAN}_happy_hours_month_reward` | Value of the free energy earned this month (EUR) |
| Happy Hours monthly consumption vs last month | `sensor.engie_belgium_{BAN}_happy_hours_month_consumption_change` | Percent change in Happy Hours consumption vs last month |
| Happy Hours eligible hours vs last month | `sensor.engie_belgium_{BAN}_happy_hours_month_eligible_hours_change` | Percent change in eligible-hour count vs last month |
| Happy Hours monthly reward vs last month | `sensor.engie_belgium_{BAN}_happy_hours_month_reward_change` | Percent change in reward earned vs last month |

The reward is the euro value of the energy you used during Happy Hours
at your regular rate. The reward sensor exposes an
`is_calculation_ongoing` attribute that is `true` while ENGIE is still
finalising the number for the month.

The next-start and next-end timestamps report `unknown` when nothing
is scheduled. The active binary sensor stays `off` in that case too,
so if you need to distinguish "no window today" from "window later
but not right now", check the timestamps.

The integration also emits Happy Hours events on the per-account
calendar and keeps a rolling archive of every window it observes so
the calendar shows the full history across restarts. Windows from
before you installed the integration are not retrievable via the
Smart App or the API used by this integration, though ENGIE
publishes a public overview at
[engie.be/nl/happyhours/overzicht](https://www.engie.be/nl/happyhours/overzicht).

Enrolment is picked up automatically on every refresh. Entities
appear shortly after you enrol an address and disappear after you
opt out.

### Solar Surplus

Solar Surplus is ENGIE's hourly forecast of how much of your solar
production will exceed your consumption over the next three days.
ENGIE derives it from weather, your panel setup, and your recent
consumption pattern, and refreshes it each morning. The forecast is
indicative.

Five sensors are created per electricity meter on accounts where
ENGIE returns forecast data:

| Sensor | Entity ID | Description |
|---|---|---|
| Solar surplus forecast (today's level) | `sensor.engie_belgium_{BAN}_{EAN}_solar_surplus_forecast` | Aggregate surplus level for today (no_data / no_surplus / minimal_surplus / low_surplus / high_surplus) |
| Solar surplus current hour (kWh) | `sensor.engie_belgium_{BAN}_{EAN}_solar_surplus_current` | Expected surplus for the current hour (kWh) |
| Solar surplus next hour (kWh) | `sensor.engie_belgium_{BAN}_{EAN}_solar_surplus_next_hour` | Expected surplus for the next hour (kWh) |
| Solar surplus total today (kWh) | `sensor.engie_belgium_{BAN}_{EAN}_solar_surplus_today_total` | Expected total surplus for today (kWh) |
| Solar surplus peak today (kWh) | `sensor.engie_belgium_{BAN}_{EAN}_solar_surplus_today_peak` | Highest hourly surplus expected today (kWh) |

The level sensor's `forecast` attribute carries the full 3-day
hourly outlook so you can plot it or feed it into your own
automations. Two provenance attributes describe today's forecast:
`forecast_creation_date` (when ENGIE computed it) and `inference_key`
(the string `actuals` for a real forecast, `no_data` for a placeholder
served to accounts without a solar setup). The current-hour and
next-hour sensors flip exactly on the hour boundary.

The integration also feeds the Energy dashboard's solar production
forecast. Once your ENGIE forecast is available, HA lists it as an
option under **Settings** > **Dashboards** > **Energy** on the
solar-production source.

**Unit note.** The ENGIE Smart App labels these values in kW, but our
sensors declare them as kWh. For hourly slots the numeric value is
the same either way (kW-average during an hour equals kWh delivered
in that hour). HA's kWh choice is what lets the Energy dashboard and
the total-today sum work correctly.

Prerequisites (per ENGIE, see
[engie.be/nl/solar-surplus](https://www.engie.be/nl/solar-surplus)):

- Solar panels with injection
- A digital meter that shares data with ENGIE
- An active ENGIE Smart App
- An Easy, Direct, Empower, or Flow electricity contract (Basic and
  vacancy contracts do not qualify)

The sensors appear automatically once ENGIE returns a forecast and
disappear when they stop. Typical use: shift heavy loads (washing
machine, dryer, dishwasher, boiler, EV charging) to hours the
forecast tags as `high_surplus`.

### Time-of-Use tariff schedules

Time-of-use (TOU) is a supplier-product pricing model: the price
per kWh depends on the time of day, split into peak, off-peak, and
sometimes super-off-peak or exclusive-night slots. When your ENGIE
product is TOU-billed, ENGIE publishes the full weekly slot layout
per meter and per direction (offtake and injection). Two enum
sensors and two binary sensors surface it:

| Entity | Entity ID | Description |
|---|---|---|
| Current offtake slot | `sensor.engie_belgium_{BAN}_{EAN}_offtake_slot` | Current tariff slot for offtake on this meter (peak / offpeak / superoffpeak / exclusive_night / day) |
| Current injection slot | `sensor.engie_belgium_{BAN}_{EAN}_injection_slot` | Current tariff slot for injection on this meter |
| Offtake at optimal slot | `binary_sensor.engie_belgium_{BAN}_{EAN}_tou_offtake_is_optimal` | On when the current offtake slot matches the schedule's optimal code |
| Injection at optimal slot | `binary_sensor.engie_belgium_{BAN}_{EAN}_tou_injection_is_optimal` | On when the current injection slot matches the schedule's optimal code |

The slot sensors flip exactly on the slot boundary. Their state is
one of `peak`, `offpeak`, `superoffpeak`, `exclusive_night`, or
`day`.

Each slot sensor exposes these attributes:

| Attribute | Description |
|---|---|
| `optimal_slot` | The schedule's declared optimal slot code (e.g. `offpeak`) |
| `next_transition` | ISO-8601 timestamp of the next slot boundary in Brussels local time |
| `weekday_slots` | Full weekly schedule as a dict of day-name to slot list |
| `dgo_tgo_slot` | Current slot code from the Fluvius DGO / TGO (Transmission Grid Operator) schedule |

The "is optimal" binary sensors turn `on` when the current slot
matches the optimal slot for the schedule direction. For offtake
that usually means `on` during OFFPEAK hours (cheapest network
cost). For injection it usually means `on` during PEAK hours (best
sell price). Flat schedules with only one slot code across the week
do not get an "is optimal" sensor, since the answer would be
constantly on.

Accounts whose supplier contract is TOU-billed also see one calendar
event per slot per direction for the next seven days on the
per-account calendar, so the whole week is visible in any Home
Assistant calendar card without opening the ENGIE Smart App.

Example automation: run the dishwasher when it is optimal to consume:

```yaml
automation:
  triggers:
    - trigger: state
      entity_id: binary_sensor.engie_belgium_{BAN}_{EAN}_tou_offtake_is_optimal
      to: "on"
  actions:
    - action: switch.turn_on
      target:
        entity_id: switch.dishwasher
```

### Billing

Three entities per business agreement reflect the current billing
state. They appear automatically once ENGIE returns billing data
for the account.

| Entity | Entity ID | Description |
|---|---|---|
| Outstanding balance | `sensor.engie_belgium_{BAN}_outstanding_balance` | Total open (unpaid) amount owed to ENGIE in EUR |
| Overdue amount | `sensor.engie_belgium_{BAN}_overdue_amount` | Portion of the outstanding balance past its due date in EUR |
| Next invoice due | `sensor.engie_belgium_{BAN}_next_invoice_due` | Timestamp of the earliest open invoice due date |

The outstanding-balance sensor reports the full amount owed to ENGIE (all
invoices not yet paid, regardless of due date). The overdue-amount sensor
reports only the portion that ENGIE marks as past its due date. Both are
in EUR with two decimal places.

The next-invoice-due sensor is a timestamp at midnight Brussels-local time
on the earliest open invoice due date. It returns `unknown` when the
balance is zero (no open transactions).

## Automations

The integration ships **purpose-based triggers and conditions** that
appear directly in the Home Assistant automation editor. Instead of
writing generic entity-state triggers by hand, you pick from a
labelled list of events specific to ENGIE (EPEX became negative,
Happy Hours became active, captar peak crossed threshold, and so on).

Open **Settings** > **Automations & scenes**, add a new automation,
and pick your ENGIE device under "When" (triggers) or "And if"
(conditions). See the Home Assistant docs on
[triggers](https://www.home-assistant.io/docs/automation/trigger/) and
[conditions](https://www.home-assistant.io/docs/automation/condition/)
for the general automation model.

### Triggers

Each trigger targets a specific ENGIE entity (binary sensor, sensor,
or calendar). Pick that entity in the "When" step of the automation
editor.

**State-transition triggers**:

| Trigger | Fires when |
|---|---|
| EPEX price became negative | Current EPEX price crosses below zero |
| EPEX price no longer negative | Current EPEX price returns to zero or above |
| Offtake became optimal | Current offtake slot enters the schedule's optimal code |
| Offtake no longer optimal | Current offtake slot leaves the optimal code |
| Injection became optimal | Current injection slot enters the schedule's optimal code |
| Injection no longer optimal | Current injection slot leaves the optimal code |
| Happy Hours became active | Current moment enters a Happy Hours window |
| Happy Hours became inactive | Current moment leaves a Happy Hours window |
| Authentication lost | Integration reports a lost session with ENGIE |
| Authentication restored | Integration reports a re-established session with ENGIE |

**Enum-change triggers**:

| Trigger | Fires when |
|---|---|
| Solar surplus level changed | Aggregate surplus level transitions to any different code |
| Offtake slot changed | Current offtake slot transitions to any different code |
| Injection slot changed | Current injection slot transitions to any different code |
| Solar surplus became `<level>` | Aggregate surplus level enters a chosen code |
| Offtake slot became `<code>` | Current offtake slot enters a chosen code |
| Injection slot became `<code>` | Current injection slot enters a chosen code |

**Threshold triggers**:

| Trigger | Fires when |
|---|---|
| EPEX current price crossed threshold | Current EPEX price crosses a chosen EUR/kWh threshold |
| EPEX next hour price crossed threshold | Next-hour EPEX price crosses a chosen EUR/kWh threshold |
| Solar surplus current hour crossed threshold | Current-hour surplus crosses a chosen kWh threshold |
| Solar surplus next hour crossed threshold | Next-hour surplus crosses a chosen kWh threshold |
| Captar peak crossed threshold | Monthly captar peak crosses a chosen kW threshold |

**Value-update triggers**:

| Trigger | Fires when |
|---|---|
| Captar peak updated | Monthly captar peak value or window changes |
| EPEX high today updated | Highest EPEX price of today changes |
| EPEX low today updated | Lowest EPEX price of today changes |

**Calendar-slot triggers**:

| Trigger | Fires when |
|---|---|
| Captar peak window started | At the start of the current monthly captar peak window |
| Captar peak window ended | At the end of the current monthly captar peak window |
| Happy Hours window started | At the start of a scheduled Happy Hours window |
| Happy Hours window ended | At the end of a scheduled Happy Hours window |
| TOU slot started | At the start of a chosen TOU slot for a chosen direction |

### Conditions

- **Binary state**: EPEX price is negative, offtake is optimal,
  injection is optimal, Happy Hours is active.
- **Enum state**: solar surplus is at level, offtake slot is,
  injection slot is.
- **Thresholds**: EPEX price is below / above threshold, captar
  peak is above threshold.

No template YAML is required for any of the above. Dropdown
options track `SOLAR_SURPLUS_LEVELS` and `TOU_SLOT_CODES` in
`const.py` automatically, so new codes added by ENGIE appear in
the editor without a code change.

### YAML-based examples

#### Run the dishwasher during Happy Hours

```yaml
automation:
  alias: "Start dishwasher on Happy Hours"
  triggers:
    - trigger: state
      entity_id: binary_sensor.engie_belgium_{BAN}_happy_hours_active
      to: "on"
  actions:
    - action: switch.turn_on
      target:
        entity_id: switch.dishwasher
```

#### Charge an EV when EPEX price is negative

```yaml
automation:
  alias: "Charge car when electricity price is negative"
  triggers:
    - trigger: state
      entity_id: binary_sensor.engie_belgium_{BAN}_epex_negative
      to: "on"
  actions:
    - action: switch.turn_on
      target:
        entity_id: switch.ev_charger
  mode: single
```

#### Notify when tomorrow's EPEX prices are available

```yaml
automation:
  alias: "EPEX tomorrow prices published"
  triggers:
    - trigger: template
      value_template: >
        {{ state_attr('sensor.engie_belgium_{BAN}_epex_current', 'tomorrow') | length > 0 }}
  actions:
    - action: notify.mobile_app
      data:
        title: "Tomorrow's electricity prices available"
        message: >
          Cheapest slot tomorrow:
          {{ state_attr('sensor.engie_belgium_{BAN}_epex_current', 'tomorrow')
             | sort(attribute='value') | first | to_json }}
```

### Authentication

A binary connectivity sensor named "Authentication" is attached to the
login-scoped "Account" device (one per configured ENGIE login, not per
BAN) and shows whether the integration is currently authenticated with
the ENGIE API. Its entity ID is generated by Home Assistant from the
device name, so the slug varies with your account. Find it under
**Developer tools** > **States** or on the account device page.

## Prerequisites

> **Required:** This integration requires a dedicated ENGIE account.
> Do not use the same ENGIE account you use to sign into
> [engie.be](https://www.engie.be/) or the ENGIE Smart App. Signing
> into the same ENGIE account from another place appears to revoke
> the integration's refresh token, which forces Home Assistant to
> prompt you for re-authentication. Create a separate user via the
> [ENGIE user management page](https://www.engie.be/nl/energiedesk/usermanagement/manage-access/)
> and grant it access to your customer number.

- Home Assistant **2026.7.0** or newer
- A dedicated [ENGIE Belgium](https://www.engie.be/) account for this
  integration (see required callout above)
- Access to SMS or email for two-factor authentication during setup

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DaanVervacke&repository=hass-engie-be&category=integration)

Click the badge above to open this repository in HACS, then select **Download**.
After HACS finishes, restart Home Assistant.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=engie_be)

Click the badge above to open the **Add integration** dialog for ENGIE
Belgium. The credential form opens directly. See [Configuration](#configuration)
for what each field expects.

#### Manual steps

If the badges above do not work in your browser:

1. Open HACS in your Home Assistant instance.
2. Search for **ENGIE Belgium** in the HACS search bar and install it.
3. Restart Home Assistant.
4. Go to **Settings** > **Devices & services** > **Add integration** and search for **ENGIE Belgium**.

If the search returns no results, add this repository as a custom repository
first:

1. Open HACS, click the three dots in the top right corner, and select **Custom repositories**.
2. Add `https://github.com/DaanVervacke/hass-engie-be` with category **Integration**.
3. Search for **ENGIE Belgium** in HACS and install it.

## Configuration

Configuration is done entirely through the Home Assistant UI. New ENGIE
accounts created via the [user management page](https://www.engie.be/nl/energiedesk/usermanagement/manage-access/)
have 2FA enabled by default. The integration only supports accounts
where 2FA via SMS or email is enabled.

If you haven't reached the credential form yet, use the **Add integration**
badge in [Installation](#hacs-recommended) or open
**Settings** > **Devices & services** > **Add integration** and search for
**ENGIE Belgium**.

1. Enter your credentials:
   - **Email address**: your ENGIE email.
   - **Password**: your ENGIE password.
   - **Two-factor authentication method**: choose SMS or Email.
2. Click **Submit**. You will receive a verification code via your chosen method.
3. Enter the 6-digit code and click **Submit**.
4. Pick one or more households from the list. Each address (business
   agreement) your login can access becomes its own device with its own
   sensors, even if several of them are billed under the same customer
   account.

The integration then fetches your energy prices and capacity-tariff peaks
and creates the sensors for each household.

### Options

After setup, you can change the price update interval:

[![Open your Home Assistant instance and show your ENGIE Belgium integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=engie_be)

1. Click the badge above (or go to **Settings** > **Devices & services** and find **ENGIE Belgium**).
2. Click **Configure** (the cog wheel icon).
3. Set the **Update interval** (5-1440 minutes, default: 60 minutes).

## Multiple households

A single ENGIE login can be linked to several households. This includes the
case where two or more addresses are billed under the same ENGIE customer
account (for example a primary residence and a holiday home grouped under
one residential account). After you finish 2FA during setup, the
integration shows a picker of every active address (business agreement)
your login can access. Pick one or more, and each becomes its own device
with its own sensors and its own captar peaks history.

To add another household later, open the ENGIE Belgium card in
**Settings** > **Devices & services** and click **Add business agreement**.
To remove one, delete its subentry.

> Each subentry is keyed on its business-agreement number (BAN), so the
> picker hides every BAN you have already added. If you replace the EAN
> on an existing BAN (for example after a meter swap or a move), the
> integration picks up the new metering point automatically on the next
> refresh.

## Historical usage import (Energy dashboard)

The integration can backfill your energy data straight from ENGIE. It pulls
every hour of electricity consumption, electricity injection, and gas
consumption ENGIE has on record for your business agreement, and adds it to
Home Assistant's long-term statistics. The first import goes back to the
start of your initial business agreement. Later runs only fetch what's new.
Optionally, per-hour EUR costs come in too. Once the data's in, you still
need to add it to the Energy dashboard yourself, see the steps below.

### Import during setup

During the initial setup flow and when adding a new business agreement, a
"Historical import options" step lets you turn on a one-time background import
per business agreement. Enable "Import history", choose which energy types to
include, and optionally turn on "Include costs" to also import per-hour EUR
amounts. The import runs in the background after setup finishes and does not
block the integration from loading. Reloading or restarting Home Assistant does
not re-trigger the import once statistics are already present. To re-run an
import later (or trigger one you skipped at setup), use the **Import historical
usage** action from **Settings** > **Developer tools** > **Actions**.

### Set up a daily sync

The easiest way is the included blueprint. It runs the import once a day
so your dashboard stays up-to-date.

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FDaanVervacke%2Fhass-engie-be%2Fblob%2Fmain%2Fblueprints%2Fautomation%2FDaanVervacke%2Fengie_be_daily_history_sync.yaml)

Or manually:

1. **Settings** > **Automations & scenes** > **Blueprints** > **Import blueprint**.
2. Paste: `https://github.com/DaanVervacke/hass-engie-be/blob/main/blueprints/automation/DaanVervacke/engie_be_daily_history_sync.yaml`
3. **Preview** > **Import blueprint**.
4. Under **Automations**, click **Create automation** on the imported blueprint.
5. Pick your business agreement, a time to run, and save.

If you have multiple households, repeat once per business agreement.

### Run a one-off import

Use this for a first-time backfill of your Energy dashboard, a specific
month, or a re-import after ENGIE corrects some data:

1. Open **Settings** > **Developer tools** > **Actions**.
2. Pick **Import historical usage** under **ENGIE Belgium**.
3. Select your business-agreement device as the target.
4. Leave both dates empty to import everything from your first business
   agreement onwards, or fill in a specific window.
5. Click **Perform action**.

Running the same window again is safe, existing hours are overwritten.
Turn on **Include costs** in the same dialog to also import what each
hour cost you in EUR.

### Add to the Energy dashboard

After the first import finishes, open **Settings** > **Dashboards** >
**Energy** and add the ENGIE statistics.

**Electricity.** Under **Grid connections**, click **Add grid connection**. In the **Configure grid connection** dialog:

- **Energy imported from grid**: `Historical electricity consumption - {your address}`
- **Energy exported to grid**: `Historical electricity injection - {your address}`

If you enabled **Include costs**, tick **Use an entity tracking the total costs** and pick `Historical electricity consumption cost - {your address}` in **Entity with the total costs**. For **Export compensation**, tick **Use an entity tracking the total compensation** and pick `Historical electricity injection compensation - {your address}`.

**Gas.** Under **Gas consumption**, click **Add gas source**. In the **Configure gas consumption** dialog, pick `Historical gas consumption - {your address}`. Add the matching cost statistic the same way if you enabled **Include costs**.

Save and give the dashboard a moment to refresh.

### Clear an import

To wipe the imported data (e.g. before a full re-import), use
**Clear historical usage statistics** (under **ENGIE Belgium**) from the same
**Developer tools** > **Actions** screen.

### Combining with a P1 meter

If you have a P1 meter for real-time data but still want accurate
historical data from ENGIE, you can use both. The Energy dashboard lets
you add multiple grid connections under **Grid consumption** (and the
same for return-to-grid and gas consumption).

## Re-authentication

ENGIE rotates refresh tokens regularly, and the upstream auth server can revoke
a session at any time. The most common trigger is the same ENGIE account being
used elsewhere (engie.be web, ENGIE Smart App). If you see the **Repairs**
notification frequently, use a dedicated account (see [Prerequisites](#prerequisites)).

To complete re-authentication:

1. Open **Settings** > **Devices & services** and click the **Reconfigure**
   prompt on the ENGIE Belgium card (or click the notification).
2. Choose how you want to receive a verification code (SMS or email).
3. Enter the 6-digit code that ENGIE sends you.

Your stored email and password are reused. No sensors are
removed and no history is lost.

## Known limitations

- **Historical usage data lags a few days.** ENGIE only publishes hourly usage once a day is finalised, so today and yesterday are not available yet. For real-time consumption you need a separate P1-port digital-meter reader.
- **No historical price retrieval.** ENGIE does not expose historical energy prices through the API. The integration can only report the currently active price period. Historical sensor data is what Home Assistant's own recorder stores.
- **Happy Hours history starts when the integration is installed.** ENGIE's Smart App and API surface only the next upcoming Happy Hours window. The integration records each window it observes locally, so past windows are recoverable inside HA only from the point of install onwards. ENGIE also publishes a public archive of all past Happy Hours at [engie.be/nl/happyhours/overzicht](https://www.engie.be/nl/happyhours/overzicht) if you need older data.
- **Happy Hours enrolment paused until 2027.** ENGIE closed new enrolments on 1 June 2026 via the Smart App and states enrolments will reopen in 2027. Existing enrolments continue automatically. See [engie.be/nl/happyhours](https://www.engie.be/nl/happyhours/).
- **EPEX prices published around 14:00 Brussels time.** ENGIE publishes the next day's dynamic prices each afternoon after the EPEX day-ahead auction settles. Before that time, only today's prices are available and tomorrow's sensors show `unknown`. See [engie.be/nl/dynamic-tarief/dagelijks-gebruik](https://www.engie.be/nl/dynamic-tarief/dagelijks-gebruik/).
- **Dedicated account required.** The same ENGIE credentials cannot be shared with engie.be or the ENGIE Smart App without triggering frequent re-authentication prompts. This is an ENGIE platform constraint, not a Home Assistant limitation. See [Prerequisites](#prerequisites).
- **Two-factor authentication required.** The integration requires MFA to be enabled on the ENGIE account. Accounts without MFA (e.g. older sub-accounts) are not supported.
- **Read-only.** The integration only reads data from the ENGIE API and never modifies your account, contracts, or settings.

## Removing the integration

1. Go to **Settings** > **Devices & services**.
2. Find the **ENGIE Belgium** card and click the three-dot menu.
3. Select **Delete**.

No cleanup is required on your ENGIE account. The integration only reads data
and never modifies anything upstream.

## Troubleshooting

If the integration is misbehaving, work through these steps before filing an
issue:

1. **Enable debug logging.** Open **Settings** > **Devices & services**, click
   the three-dot menu on the ENGIE Belgium entry, and select **Enable debug
   logging**. Reproduce the issue, then choose **Disable debug logging** from
   the same menu, and Home Assistant will offer to download the captured log.

   If the issue happens *before* you can add the integration (for example
   sign-in or MFA problems in the setup wizard), the three-dot menu is not
   available yet. Enable logging up front by adding the block below to
   `configuration.yaml` and restarting Home Assistant:

   ```yaml
   logger:
     default: info
     logs:
       custom_components.engie_be: debug
   ```

   Reproduce the failing setup step, then check **Settings** > **System** >
   **Logs** for the `custom_components.engie_be` entries.

2. **Download diagnostics.** From the same three-dot menu, choose **Download
   diagnostics**. The resulting JSON redacts your password, tokens, and EAN
   identifiers, and is safe to attach to a GitHub issue.

3. **Common errors.**
   - *Authentication with ENGIE Belgium failed*: your stored tokens were
     rejected. Follow the [Re-authentication](#re-authentication) steps above.
     If this happens repeatedly, the most common cause is sharing your ENGIE
     account between this integration and engie.be or the ENGIE Smart App. A
     dedicated account is required. See [Prerequisites](#prerequisites).
   - *Cannot connect to the ENGIE Belgium API*: the upstream API is unreachable
     or returned an HTTP error. The integration will retry on its next
     interval.
   - *Invalid verification code*: re-trigger the MFA step and enter the latest
     code.

4. **File an issue.** If the problem persists, open one at
   [github.com/DaanVervacke/hass-engie-be/issues](https://github.com/DaanVervacke/hass-engie-be/issues)
   and include the diagnostics JSON plus the relevant log lines.

## Credential storage

The integration stores your ENGIE credentials and OAuth tokens in Home
Assistant's standard config-entry storage. The **Download diagnostics** payload
redacts all of these fields and is safe to share.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the version history. Notable changes
since the last release live under the **[Unreleased]** section.

## License

[MIT](LICENSE) - Daan Vervacke ([@DaanVervacke](https://github.com/DaanVervacke))

---

*Data provided by ENGIE Belgium*
