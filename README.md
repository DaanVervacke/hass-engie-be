# ENGIE Belgium HACS integration

[![HACS Custom][hacsbadge]][hacs]
[![GitHub Release][releasebadge]][release]
[![Tests][testsbadge]][tests]
[![Home Assistant][habadge]][ha]
[![Quality Scale][qualitybadge]][quality]
[![License][licensebadge]](LICENSE)

Custom [Home Assistant](https://www.home-assistant.io/) integration for
[ENGIE Belgium](https://www.engie.be/). Retrieves your personal energy price
data, monthly capacity-tariff peaks, Happy Hours free-energy windows, and
EPEX day-ahead wholesale prices from the ENGIE Belgium API and exposes them
as sensors, binary sensors, and calendar events.

## Features

- Authenticates with your ENGIE Belgium account using two-factor authentication
- Auto-detects gas and electricity contracts, including dynamic (EPEX-indexed) tariffs
- Creates price sensors per energy type, direction (offtake / injection), and tariff rate
- Tracks the monthly capacity-tariff (captar) peak window for each electricity meter
- Surfaces ENGIE's Happy Hours free-energy promotions on each account, both as sensors and as calendar events
- Supports multiple households (business agreements) under a single ENGIE login, including several active addresses under one customer account
- Configurable update interval
- Imports hourly consumption / injection / gas history into the Home Assistant Energy Dashboard on demand, walking back to the business agreement's start date on first run

## Sensors

The integration auto-detects your contracts and creates the right sensors. All
price sensors report values in **EUR/kWh** with 6 decimals, and each one
exposes its EAN, the validity window, and the applicable VAT rate as
attributes. Every sensor below is also available as an `_excl_vat` variant
(same name, `_excl_vat` suffix on both the entity ID and the friendly name).

> Every entity ID created by the integration is prefixed with the
> business-agreement number (BAN) of the business agreement it belongs
> to. For example: `sensor.engie_belgium_002200001234_gas_offtake_price`.
> The bare `sensor.engie_belgium_*` IDs shown in the tables below are
> illustrative. Replace `engie_belgium_` with `engie_belgium_{your_business_agreement_number}_`
> when referencing a sensor in automations or dashboards. The BAN is
> visible in **Developer tools** > **States** and in the device name
> in **Settings** > **Devices & services**.

### Gas

Gas contracts are always single-rate.

| Sensor | Entity ID |
|---|---|
| Gas offtake price | `sensor.engie_belgium_gas_offtake_price` |

### Electricity: single-rate

Created when your contract has a single electricity rate.

| Sensor | Entity ID |
|---|---|
| Electricity offtake price | `sensor.engie_belgium_electricity_offtake_price` |
| Electricity injection price | `sensor.engie_belgium_electricity_injection_price` |

### Electricity: dual-rate (peak / off-peak)

Created when your contract has separate peak and off-peak rates. These replace
the single-rate sensors above for that meter.

| Sensor | Entity ID |
|---|---|
| Electricity peak offtake price | `sensor.engie_belgium_electricity_peak_offtake_price` |
| Electricity off-peak offtake price | `sensor.engie_belgium_electricity_off_peak_offtake_price` |
| Electricity peak injection price | `sensor.engie_belgium_electricity_peak_injection_price` |
| Electricity off-peak injection price | `sensor.engie_belgium_electricity_off_peak_injection_price` |

### Electricity: tri-rate (peak / off-peak / super off-peak)

Created when your contract has three time-of-use rates.

| Sensor | Entity ID |
|---|---|
| Electricity peak offtake price | `sensor.engie_belgium_electricity_peak_offtake_price` |
| Electricity off-peak offtake price | `sensor.engie_belgium_electricity_off_peak_offtake_price` |
| Electricity super off-peak offtake price | `sensor.engie_belgium_electricity_super_off_peak_offtake_price` |
| Electricity peak injection price | `sensor.engie_belgium_electricity_peak_injection_price` |
| Electricity off-peak injection price | `sensor.engie_belgium_electricity_off_peak_injection_price` |
| Electricity super off-peak injection price | `sensor.engie_belgium_electricity_super_off_peak_injection_price` |

> Injection sensors are only created when injection data is present.

### Capacity tariff (captar)

Four sensors expose the monthly peak window used for the Belgian
capacity-tariff calculation. They are created per electricity meter when peaks
data is available.

| Sensor | Entity ID | Description |
|---|---|---|
| Captar monthly peak power | `sensor.engie_belgium_captar_monthly_peak_power` | Highest 15-minute average power for the month, in kW |
| Captar monthly peak energy | `sensor.engie_belgium_captar_monthly_peak_energy` | Energy consumed during that 15-minute window, in kWh |
| Captar monthly peak start | `sensor.engie_belgium_captar_monthly_peak_start` | Start of the 15-minute peak window |
| Captar monthly peak end | `sensor.engie_belgium_captar_monthly_peak_end` | End of the 15-minute peak window |

ENGIE only publishes a peak after the first 15-minute peak of the month is
recorded, so the current month is empty for the first day or so. Until then,
the integration shows the previous month's peak and marks it via the
`peak_is_fallback` attribute (and `peak_month` shows which month the value
covers).

A calendar entity (`calendar.engie_belgium`) is also created and shows the
current monthly peak as a single event, with the peak power and energy in the
event description.

### Dynamic tariff (EPEX-indexed)

Customers on ENGIE's dynamic (EPEX-indexed) electricity contract get four
extra sensors that surface day-ahead wholesale prices from the public EPEX
endpoint.

| Sensor | Entity ID |
|---|---|
| EPEX current price | `sensor.engie_belgium_epex_current_price` |
| EPEX next hour price | `sensor.engie_belgium_epex_next_hour_price` |
| EPEX lowest price today | `sensor.engie_belgium_epex_lowest_price_today` |
| EPEX highest price today | `sensor.engie_belgium_epex_highest_price_today` |

All four sensors are in **EUR/kWh** (4 decimals). Tomorrow's prices appear
once ENGIE publishes them, typically shortly after 13:15 Europe/Brussels.

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
  - entity: sensor.engie_belgium_epex_current_price
    type: column
    data_generator: |
      return entity.attributes.today.map(s => [new Date(s.start).getTime(), s.value]);
```

> Wholesale prices can be **negative** during periods of oversupply. This is
> reported faithfully.

A binary sensor `binary_sensor.engie_belgium_epex_price_is_negative` turns on
when the current wholesale slot has a negative price, so you can build simple
state-based automations without a template.

### Happy Hours

ENGIE Belgium occasionally schedules "Happy Hours" windows during which the
energy consumed at your address is free. These are announced the day
before and are exposed here for every account enrolled
in the Happy Hours program.

| Entity | Entity ID |
|---|---|
| Happy Hours is active | `binary_sensor.engie_belgium_happy_hours_active` |
| Happy Hours next start | `sensor.engie_belgium_happy_hours_next_start` |
| Happy Hours next end | `sensor.engie_belgium_happy_hours_next_end` |
| Happy Hours monthly consumption | `sensor.engie_belgium_happy_hours_month_consumption` |
| Happy Hours eligible hours this month | `sensor.engie_belgium_happy_hours_month_eligible_hours` |
| Happy Hours monthly reward | `sensor.engie_belgium_happy_hours_month_reward` |

The binary sensor is `on` while the current moment falls inside a scheduled
window, and `off` otherwise. The three monthly-summary sensors show your
consumption (kWh), the number of eligible Happy Hours windows, and the reward
(EUR) for the current calendar month. The reward is the value of the free
energy you used during Happy Hours: what those kWh would have cost you at your
regular rate. The reward sensor also exposes an `is_calculation_ongoing`
attribute that ENGIE sets to `true` while it is still computing the final
value. It is always available, and the `off` state
covers both "no window scheduled" and "scheduled but not active right now".
If you need to tell those two cases apart, look at the timestamp sensors
instead. They report `unknown` when nothing is scheduled.

The scheduled window also appears as a "Happy Hours" event on the per-account
calendar entity, alongside the captar peak event. Past Happy Hours windows
the integration has observed are kept in a local history file so the
calendar can show the full archive across restarts. Windows that ran before
you installed the integration cannot be retrieved because ENGIE does not
expose Happy Hours history.

Happy Hours is an opt-in program from ENGIE. You need to enrol each address
separately through the ENGIE Smart App under "Je producten". See
[engie.be/nl/happyhours](https://www.engie.be/nl/happyhours/) for
eligibility and the latest details.

The integration checks your enrolment status on every refresh (by default every
60 minutes, and configurable). The three Happy Hours entities and the calendar
events appear shortly after you enrol an address and disappear shortly
after you opt out. You do not need to remove and re-add the integration
when your enrolment changes.

### Authentication

A binary connectivity sensor (`binary_sensor.engie_belgium_authentication`)
shows whether the integration is currently authenticated with the ENGIE API.

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

Click the badge above to open the **Add Integration** dialog for ENGIE
Belgium. The credential form opens directly. See [Configuration](#configuration)
for what each field expects.

#### Manual steps

If the badges above do not work in your browser:

1. Open HACS in your Home Assistant instance.
2. Search for **ENGIE Belgium** in the HACS search bar and install it.
3. Restart Home Assistant.
4. Go to **Settings** > **Devices & Services** > **Add Integration** and search for **ENGIE Belgium**.

If the search returns no results, add this repository as a custom repository
first:

1. Open HACS, click the three dots in the top right corner, and select **Custom repositories**.
2. Add `https://github.com/DaanVervacke/hass-engie-be` with category **Integration**.
3. Search for **ENGIE Belgium** in HACS and install it.

## Configuration

Configuration is done entirely through the Home Assistant UI. New ENGIE
accounts created via the [user management page](https://www.engie.be/nl/energiedesk/usermanagement/manage-access/)
have 2FA enabled by default and work out of the box. The integration only
supports accounts where 2FA via SMS or email is already enabled.

If you haven't reached the credential form yet, use the **Add Integration**
badge in [Installation](#hacs-recommended) or open
**Settings** > **Devices & Services** > **Add Integration** and search for
**ENGIE Belgium**.

1. Enter your credentials:
   - **Email address**: your ENGIE Belgium email.
   - **Password**: your ENGIE Belgium password.
   - **Two-factor authentication method**: choose SMS or Email.
2. Click **Submit**. You will receive a verification code via your chosen method.
3. Enter the 6-digit code and click **Submit**.
4. Pick one or more households from the list. Each address (business
   agreement) your login can access becomes its own device with its own
   sensors, even if several of them are billed under the same customer
   account.

The integration will then fetch your energy prices and capacity-tariff peaks
and create the appropriate sensors.

### Options

After setup, you can change the price update interval:

[![Open your Home Assistant instance and show your ENGIE Belgium integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=engie_be)

1. Click the badge above (or go to **Settings** > **Devices & Services** and find **ENGIE Belgium**).
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
**Settings** > **Devices & Services** and click **Add business agreement**.
To remove one, delete its subentry.

> Each subentry is keyed on its business-agreement number (BAN), so the
> picker hides every BAN you have already added. If you replace the EAN
> on an existing BAN (for example after a meter swap or a move), the
> integration picks up the new metering point automatically on the next
> refresh.

## Historical usage import (Energy Dashboard)

The integration can import hourly consumption, injection and gas from ENGIE
into Home Assistant's long-term statistics, per business agreement. First
run walks back to the business agreement's start date (or the last three
years if ENGIE's contracts endpoint is unavailable), subsequent runs only
fetch the delta since the last recorded hour. Once imported, the values are
selectable directly in the **Settings** > **Dashboards** > **Energy** source
pickers under the electricity grid, return-to-grid, and gas sources.

Two ways to trigger an import:

### One-click (auto backfill)

Each business-agreement device exposes two buttons:

- **Import historical electricity data** (consumption + injection)
- **Import historical gas data**

Open **Settings** > **Devices & Services** > **ENGIE Belgium** > the account
you want, then press the button. The first press walks back to the business
agreement's start date. Every subsequent press only fetches the delta since
the last recorded hour, so running it again is cheap. Fetching is chunked
(7-day windows) and persists after every chunk, so if a chunk fails partway
through you can just press again and the import resumes.

### With a specific date range

If you want to import a fixed window (for example a single month or a
back-fill after ENGIE republishes historical data), you have two native
routes.

**Device page (fastest):**

1. Open **Settings** > **Devices & Services** > **ENGIE Belgium**.
2. Click the business-agreement device you want to import for.
3. Click **Perform action** in the device page.
4. Pick action **ENGIE Belgium: Import historical usage data**.
5. Set **Energy type** (leave empty for both electricity and gas), **Start
   date**, and **End date**. All fields are optional and the same auto mode
   applies to any field you leave blank.
6. Click **Perform action** to run.

**Developer Tools (equivalent):**

Same action, callable from **Developer Tools** > **Actions** > *ENGIE
Belgium: Import historical usage data*. You pick the device via the target
selector instead of it being pre-filled.

The date pickers are native Home Assistant widgets. Explicit windows overwrite
any existing hourly rows in that range in place, so re-running the same
window is safe.

### Daily automatic sync (no P1 meter needed)

If your setup doesn't have a P1 / DSMR meter, the Energy Dashboard has no
real-time source to draw from. The ENGIE cloud data lags behind by up to a
day, but it lands in the dashboard just fine if you schedule a nightly
import.

The integration ships a Home Assistant [Blueprint](https://www.home-assistant.io/docs/automation/using_blueprints/)
that wires this up in three clicks:

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FDaanVervacke%2Fhass-engie-be%2Fblob%2Fmain%2Fblueprints%2Fautomation%2FDaanVervacke%2Fengie_be_daily_history_sync.yaml)

Or manually:

1. **Settings** > **Automations & scenes** > **Blueprints** > **Import blueprint**.
2. Paste the URL: `https://github.com/DaanVervacke/hass-engie-be/blob/main/blueprints/automation/DaanVervacke/engie_be_daily_history_sync.yaml`
3. Click **Preview** > **Import blueprint**.
4. Under **Automations**, click **Create automation** on the imported blueprint.
5. Pick the business-agreement device, a sync time (default 04:00), and
   which fuels to sync. Save.

The automation calls the same `engie_be.import_history` action once per day,
in auto mode (delta only). First run backfills back to the business
agreement's start date, subsequent runs are tiny. Repeat the import (steps
4-5) once per business agreement if you have several households.

To wipe imported statistics for a device (for example before a full re-import
after an ENGIE data correction), use the action **ENGIE Belgium: Clear
historical usage statistics** in the same **Developer Tools** > **Actions**
screen. Optional **Energy type** field clears only electricity or only gas.

The three per-BAN statistic IDs are:

- `engie_be:{BAN}_consumption` (kWh, grid offtake)
- `engie_be:{BAN}_injection` (kWh, return to grid)
- `engie_be:{BAN}_gas` (kWh, gas offtake reported as energy-equivalent by ENGIE)

### Combining with a live P1 / DSMR meter

If you install a P1 meter later on and want the Energy Dashboard timeline to
stay continuous, you can add both sources side by side. Under **Settings** >
**Dashboards** > **Energy** > **Electricity grid**, click **Add consumption**
twice: once for `Historical electricity consumption - {address}` (this
integration) and once for the DSMR sensor your P1 integration exposes.
Repeat for **Return to grid** with the injection statistic and DSMR
production sensor.

Home Assistant queries both sources per hour bucket and sums their
contributions. That works cleanly as long as the two streams don't overlap
in time - which is the normal case if you install the P1 meter today and
ENGIE data only covers up to yesterday. If both sources happen to have data
for the same hour (for example the day you first plug in the P1 meter),
that hour is counted twice in the graph. To avoid it, either run the ENGIE
import through the day before the P1 install and never after, or use the
`ENGIE Belgium: Clear historical usage statistics` action to drop the
overlapping ENGIE hours once the P1 meter is stable.

## Re-authentication

ENGIE rotates refresh tokens regularly, and the upstream auth server can revoke
a session at any time. The most common trigger is the same ENGIE account being
used elsewhere (engie.be web, ENGIE Smart App). If you see the **"Repair"**
notification frequently, use a dedicated account (see [Prerequisites](#prerequisites)).

To complete re-authentication:

1. Open **Settings** > **Devices & Services** and click the **Reconfigure**
   prompt on the ENGIE Belgium card (or click the notification).
2. Choose how you want to receive a verification code (SMS or email).
3. Enter the 6-digit code that ENGIE sends you.

Your stored email and password are reused. No sensors are
removed and no history is lost.

## Automation examples

### Run the dishwasher during Happy Hours

```yaml
automation:
  alias: "Start dishwasher on Happy Hours"
  trigger:
    - platform: state
      entity_id: binary_sensor.engie_belgium_002200001234_happy_hours_active
      to: "on"
  action:
    - service: switch.turn_on
      target:
        entity_id: switch.dishwasher
```

### Charge an EV when EPEX price is negative

```yaml
automation:
  alias: "Charge car when electricity price is negative"
  trigger:
    - platform: state
      entity_id: binary_sensor.engie_belgium_002200001234_epex_price_is_negative
      to: "on"
  action:
    - service: switch.turn_on
      target:
        entity_id: switch.ev_charger
  mode: single
```

### Notify when tomorrow's EPEX prices are available

```yaml
automation:
  alias: "EPEX tomorrow prices published"
  trigger:
    - platform: template
      value_template: >
        {{ state_attr('sensor.engie_belgium_002200001234_epex_current_price', 'tomorrow') | length > 0 }}
  action:
    - service: notify.mobile_app
      data:
        title: "Tomorrow's electricity prices available"
        message: >
          Cheapest slot tomorrow:
          {{ state_attr('sensor.engie_belgium_002200001234_epex_current_price', 'tomorrow')
             | sort(attribute='value') | first | to_json }}
```

> Replace `002200001234` with your actual business-agreement number (visible in
> **Developer tools** > **States** after the integration is set up).

## Known limitations

- **Historical usage data lags a few days.** ENGIE's `usage-details` endpoint only returns finalised hourly data, so today's and yesterday's hours are not yet available when you fetch. In-progress hours are marked `partialData: true` upstream and are deliberately skipped by the integration so nothing tentative lands in permanent statistics. If you need real-time consumption you need a live meter integration (P1 / DSMR).
- **No historical price retrieval.** ENGIE does not expose historical energy prices through the API. The integration can only report the currently active price period. Historical sensor data is what Home Assistant's own recorder stores.
- **Happy Hours history starts when the integration is installed.** ENGIE does not provide a historical list of past Happy Hours windows. The integration records each window it observes locally, so windows that ran before the integration was set up cannot be recovered.
- **EPEX prices available from ~13:15 Brussels time.** ENGIE publishes the next day's EPEX day-ahead prices after the daily auction closes. Before that time, only today's prices are available and tomorrow's sensors will show `unknown`.
- **Dedicated account required.** The same ENGIE credentials cannot be shared with engie.be or the ENGIE Smart App without triggering frequent re-authentication prompts. This is an ENGIE platform constraint, not a Home Assistant limitation. See [Prerequisites](#prerequisites).
- **Two-factor authentication required.** The integration requires MFA to be enabled on the ENGIE account. Accounts without MFA (e.g. older sub-accounts) are not supported.
- **Read-only.** The integration only reads data from the ENGIE API and never modifies your account, contracts, or settings.

## Removing the integration

1. Go to **Settings** > **Devices & Services**.
2. Find the **ENGIE Belgium** card and click the three-dot menu.
3. Select **Delete**.

No cleanup is required on your ENGIE account. The integration only reads data
and never modifies anything upstream.

## Troubleshooting

If the integration is misbehaving, work through these steps before filing an
issue:

1. **Enable debug logging.** Open **Settings** > **Devices & Services**, click
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

[hacs]: https://github.com/hacs/integration
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[release]: https://github.com/DaanVervacke/hass-engie-be/releases
[releasebadge]: https://img.shields.io/github/v/release/DaanVervacke/hass-engie-be
[tests]: https://github.com/DaanVervacke/hass-engie-be/actions/workflows/test.yml
[testsbadge]: https://img.shields.io/github/actions/workflow/status/DaanVervacke/hass-engie-be/test.yml?label=tests
[ha]: https://www.home-assistant.io/
[habadge]: https://img.shields.io/badge/dynamic/json?url=https://raw.githubusercontent.com/DaanVervacke/hass-engie-be/main/hacs.json&query=$.homeassistant&label=Home%20Assistant&prefix=%3E%3D&color=41BDF5&logo=home-assistant&logoColor=white
[quality]: https://www.home-assistant.io/docs/quality_scale/
[qualitybadge]: https://img.shields.io/badge/quality_scale-gold-gold.svg
[licensebadge]: https://img.shields.io/github/license/DaanVervacke/hass-engie-be
