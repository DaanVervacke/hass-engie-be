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
- Import your hourly usage / historic data from ENGIE into the Energy dashboard: electricity consumption, injection, gas consumption, and per-hour costs
- Creates price sensors per energy type, direction (offtake / injection), and tariff rate
- Tracks the monthly capacity-tariff (captar) peak window for each electricity meter
- Surfaces ENGIE's Happy Hours free-energy promotions on each account, both as sensors and as calendar events
- Supports multiple households (business agreements) under a single ENGIE login, including several active addresses under one customer account
- Configurable update interval

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
   - **Email address**: your ENGIE Belgium email.
   - **Password**: your ENGIE Belgium password.
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

Don't have a P1 meter? The integration can pull your hourly electricity
and gas consumption from ENGIE and feed it into the Energy dashboard. The first
import goes back to the start of your initial business agreement, later runs only
fetch what's new.

### Import during setup

During the initial setup flow and when adding a new business agreement, a
"Historical import options" step lets you turn on a one-time background import
per business agreement. Enable "Import history", choose which energy types to
include, and optionally turn on "Include costs" to also import per-hour EUR
amounts. The import runs in the background after setup finishes and does not
block the integration from loading. Reloading or restarting Home Assistant does
not re-trigger the import once statistics are already present. To edit these
settings later, open **Settings** > **Devices & services** > ENGIE Belgium,
click the business agreement, and choose **Edit**.

### Set up a daily sync

The easiest way is the included blueprint. It runs the import once a day
so your dashboard stays up-to-date.

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FDaanVervacke%2Fhass-engie-be%2Fblob%2Ffeat%2Fimport-historical-energy-data%2Fblueprints%2Fautomation%2FDaanVervacke%2Fengie_be_daily_history_sync.yaml)

Or manually:

1. **Settings** > **Automations & scenes** > **Blueprints** > **Import blueprint**.
2. Paste: `https://github.com/DaanVervacke/hass-engie-be/blob/feat/import-historical-energy-data/blueprints/automation/DaanVervacke/engie_be_daily_history_sync.yaml`
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

### Include costs

Turn on **Include costs** to also import what each hour cost you in EUR.

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

- **Historical usage data lags a few days.** ENGIE only publishes hourly usage once a day is finalised, so today and yesterday are not available yet. For real-time consumption you need a P1 / DSMR meter.
- **No historical price retrieval.** ENGIE does not expose historical energy prices through the API. The integration can only report the currently active price period. Historical sensor data is what Home Assistant's own recorder stores.
- **Happy Hours history starts when the integration is installed.** ENGIE does not provide a historical list of past Happy Hours windows. The integration records each window it observes locally, so windows that ran before the integration was set up cannot be recovered.
- **EPEX prices available from ~13:15 Brussels time.** ENGIE publishes the next day's EPEX day-ahead prices after the daily auction closes. Before that time, only today's prices are available and tomorrow's sensors will show `unknown`.
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
