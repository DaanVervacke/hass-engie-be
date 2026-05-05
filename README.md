# ENGIE Belgium HACS integration

[![HACS Custom][hacsbadge]][hacs]
[![GitHub Release][releasebadge]][release]
[![License][licensebadge]](LICENSE)

Custom [Home Assistant](https://www.home-assistant.io/) integration for
[ENGIE Belgium](https://www.engie.be/). Retrieves your personal energy price
data and monthly capacity-tariff peaks from the ENGIE Belgium API and exposes
them as sensors.

## Features

- Authenticates with your ENGIE Belgium account using two-factor authentication
- Auto-detects gas and electricity contracts, including dynamic (EPEX-indexed) tariffs
- Creates price sensors per energy type, direction (offtake / injection), and tariff rate
- Tracks the monthly capacity-tariff (captar) peak window for each electricity meter
- Supports multiple customer accounts under a single ENGIE login
- Configurable update interval

## Sensors

The integration auto-detects your contracts and creates the right sensors. All
price sensors report values in **EUR/kWh** with 6 decimals, and each one
exposes its EAN, the validity window, and the applicable VAT rate as
attributes. Every sensor below is also available as an `_excl_vat` variant
(same name, `_excl_vat` suffix on both the entity ID and the friendly name).

> All entity IDs include your ENGIE customer number, e.g.
> `sensor.engie_belgium_1500000123_gas_offtake_price`. The bare
> `sensor.engie_belgium_*` IDs shown in the tables below are illustrative.
> Replace `engie_belgium_` with `engie_belgium_{your_customer_number}_` when
> referencing a sensor in automations or dashboards. The customer number
> appears in every entity ID created by the integration (visible in
> **Developer tools** > **States**).

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

Customers on ENGIE's dynamic (EPEX-indexed) electricity contract get three
extra sensors that surface day-ahead wholesale prices from the public EPEX
endpoint.

| Sensor | Entity ID |
|---|---|
| EPEX current price | `sensor.engie_belgium_epex_current_price` |
| EPEX lowest price today | `sensor.engie_belgium_epex_lowest_price_today` |
| EPEX highest price today | `sensor.engie_belgium_epex_highest_price_today` |

All three sensors are in **EUR/kWh** (4 decimals). Tomorrow's prices appear
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
   - **Client ID**: leave at the default unless you know what you are doing.
   - **Two-factor authentication method**: choose SMS or Email.
2. Click **Submit**. You will receive a verification code via your chosen method.
3. Enter the 6-digit code and click **Submit**.
4. Pick one or more customer accounts from the list of accounts your login can
   access. Each selection becomes its own device with its own sensors.

The integration will then fetch your energy prices and capacity-tariff peaks
and create the appropriate sensors.

### Options

After setup, you can change the price update interval:

[![Open your Home Assistant instance and show your ENGIE Belgium integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=engie_be)

1. Click the badge above (or go to **Settings** > **Devices & Services** and find **ENGIE Belgium**).
2. Click **Configure** (the cog wheel icon).
3. Set the **Update interval** (5-1440 minutes, default: 60 minutes).

## Multiple customer accounts

A single ENGIE login can be linked to several customer accounts (for example a
private home and a holiday house). After you finish 2FA during setup, the
integration shows a picker of every customer account your login can access. Pick
one or more, and each becomes its own device with its own sensors.

To add another customer account later, open the ENGIE Belgium card in
**Settings** > **Devices & Services** and click **Add subentry**. To remove
one, delete its subentry. The rest stay intact.

> Existing single-account installs are upgraded automatically and your sensor
> history is preserved. If any of your automations or dashboards reference an
> old entity ID, update them to include the customer number.

## Re-authentication

ENGIE rotates refresh tokens regularly, and the upstream auth server can revoke
a session at any time. The most common trigger is the same ENGIE account being
used elsewhere (engie.be web, ENGIE Smart App). If you see the **"Repair"**
notification frequently, see [Prerequisites](#prerequisites) for the
dedicated-account recommendation.

To complete re-authentication:

1. Open **Settings** > **Devices & Services** and click the **Reconfigure**
   prompt on the ENGIE Belgium card (or click the notification).
2. Choose how you want to receive a verification code (SMS or email).
3. Enter the 6-digit code that ENGIE sends you.

Your stored email and password are reused. No sensors are
removed and no history is lost.

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
[licensebadge]: https://img.shields.io/github/license/DaanVervacke/hass-engie-be
