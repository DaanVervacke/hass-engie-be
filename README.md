# ENGIE Belgium HACS integration

[![HACS Custom][hacsbadge]][hacs]
[![GitHub Release][releasebadge]][release]
[![License][licensebadge]](LICENSE)

Custom [Home Assistant](https://www.home-assistant.io/) integration for
[ENGIE Belgium](https://www.engie.be/). Retrieves your personal energy price
data and monthly capacity-tariff peaks from the ENGIE Belgium API and exposes
them as sensors.

## A note on how this was built

The ENGIE login flow was reverse-engineered by hand, and the integration
around it was put together with AI assistance. Reviewed and tested
before release.

## Features

- Authenticates via ENGIE Belgium's OAuth2/PKCE flow with two-factor
  authentication (SMS or email)
- Automatically refreshes access tokens in the background
- Detects gas and electricity contracts via the ENGIE service-points endpoint
- Creates price sensors per energy type, direction (offtake / injection), and
  tariff rate (single-rate, dual-rate, or tri-rate contracts)
- Tracks the monthly capacity-tariff (captar) peak window per electricity service point
- Configurable update interval via the integration options

## Sensors

The integration auto-detects your energy contracts and creates price sensors
accordingly. Capacity-tariff peak sensors are created independently when peaks
data is available; see [Capacity tariff (captar)](#capacity-tariff-captar).

**Price sensors** are in **EUR/kWh** with 6 decimal precision. Each one exposes
the following attributes: `ean`, `from`, `to`, `vat_tariff`,
`time_of_use_slot_code`, and `last_fetched`.

Which price sensors are created depends on your contract type. The integration
reads the `timeOfUseSlotCode` from the API response to determine whether you
have a single-rate, dual-rate (peak / off-peak), or tri-rate
(peak / off-peak / super off-peak) contract.

### Gas

Gas contracts are always single-rate.

| Sensor | Entity ID | Description |
|---|---|---|
| Gas offtake price | `sensor.engie_belgium_gas_offtake_price` | Current gas offtake price incl. VAT |
| Gas offtake price (excl. VAT) | `sensor.engie_belgium_gas_offtake_price_excl_vat` | Current gas offtake price excl. VAT |

### Electricity: single-rate

Created when the API returns `TOTAL_HOURS` as the time-of-use slot code.

| Sensor | Entity ID | Description |
|---|---|---|
| Electricity offtake price | `sensor.engie_belgium_electricity_offtake_price` | Current electricity offtake price incl. VAT |
| Electricity offtake price (excl. VAT) | `sensor.engie_belgium_electricity_offtake_price_excl_vat` | Current electricity offtake price excl. VAT |
| Electricity injection price | `sensor.engie_belgium_electricity_injection_price` | Current electricity injection price incl. VAT |
| Electricity injection price (excl. VAT) | `sensor.engie_belgium_electricity_injection_price_excl_vat` | Current electricity injection price excl. VAT |

### Electricity: dual-rate (peak / off-peak)

Created when the API returns `PEAK` and `OFFPEAK` as time-of-use slot codes
(e.g. two-period meter contracts). These sensors replace the single-rate
offtake/injection sensors for that EAN.

| Sensor | Entity ID | Description |
|---|---|---|
| Electricity peak offtake price | `sensor.engie_belgium_electricity_peak_offtake_price` | Current electricity peak offtake price incl. VAT |
| Electricity peak offtake price (excl. VAT) | `sensor.engie_belgium_electricity_peak_offtake_price_excl_vat` | Current electricity peak offtake price excl. VAT |
| Electricity off-peak offtake price | `sensor.engie_belgium_electricity_off_peak_offtake_price` | Current electricity off-peak offtake price incl. VAT |
| Electricity off-peak offtake price (excl. VAT) | `sensor.engie_belgium_electricity_off_peak_offtake_price_excl_vat` | Current electricity off-peak offtake price excl. VAT |
| Electricity peak injection price | `sensor.engie_belgium_electricity_peak_injection_price` | Current electricity peak injection price incl. VAT |
| Electricity peak injection price (excl. VAT) | `sensor.engie_belgium_electricity_peak_injection_price_excl_vat` | Current electricity peak injection price excl. VAT |
| Electricity off-peak injection price | `sensor.engie_belgium_electricity_off_peak_injection_price` | Current electricity off-peak injection price incl. VAT |
| Electricity off-peak injection price (excl. VAT) | `sensor.engie_belgium_electricity_off_peak_injection_price_excl_vat` | Current electricity off-peak injection price excl. VAT |

### Electricity: tri-rate (peak / off-peak / super off-peak)

Created when the API returns `PEAK`, `OFFPEAK`, and `SUPEROFFPEAK` as
time-of-use slot codes.

| Sensor | Entity ID | Description |
|---|---|---|
| Electricity peak offtake price | `sensor.engie_belgium_electricity_peak_offtake_price` | Current electricity peak offtake price incl. VAT |
| Electricity peak offtake price (excl. VAT) | `sensor.engie_belgium_electricity_peak_offtake_price_excl_vat` | Current electricity peak offtake price excl. VAT |
| Electricity off-peak offtake price | `sensor.engie_belgium_electricity_off_peak_offtake_price` | Current electricity off-peak offtake price incl. VAT |
| Electricity off-peak offtake price (excl. VAT) | `sensor.engie_belgium_electricity_off_peak_offtake_price_excl_vat` | Current electricity off-peak offtake price excl. VAT |
| Electricity super off-peak offtake price | `sensor.engie_belgium_electricity_super_off_peak_offtake_price` | Current electricity super off-peak offtake price incl. VAT |
| Electricity super off-peak offtake price (excl. VAT) | `sensor.engie_belgium_electricity_super_off_peak_offtake_price_excl_vat` | Current electricity super off-peak offtake price excl. VAT |
| Electricity peak injection price | `sensor.engie_belgium_electricity_peak_injection_price` | Current electricity peak injection price incl. VAT |
| Electricity peak injection price (excl. VAT) | `sensor.engie_belgium_electricity_peak_injection_price_excl_vat` | Current electricity peak injection price excl. VAT |
| Electricity off-peak injection price | `sensor.engie_belgium_electricity_off_peak_injection_price` | Current electricity off-peak injection price incl. VAT |
| Electricity off-peak injection price (excl. VAT) | `sensor.engie_belgium_electricity_off_peak_injection_price_excl_vat` | Current electricity off-peak injection price excl. VAT |
| Electricity super off-peak injection price | `sensor.engie_belgium_electricity_super_off_peak_injection_price` | Current electricity super off-peak injection price incl. VAT |
| Electricity super off-peak injection price (excl. VAT) | `sensor.engie_belgium_electricity_super_off_peak_injection_price_excl_vat` | Current electricity super off-peak injection price excl. VAT |

> Injection sensors are only created when injection data is present in the API
> response.

### Capacity tariff (captar)

Four sensors expose the monthly peak window used for the Belgian
capacity-tariff calculation. Values come from the ENGIE
`b2c-energy-insights` peaks endpoint.

| Sensor name | Entity ID | Description |
|-------------|-----------|-------------|
| Captar monthly peak power | `sensor.engie_belgium_captar_monthly_peak_power` | Highest 15-minute average power for the month, in kW |
| Captar monthly peak energy | `sensor.engie_belgium_captar_monthly_peak_energy` | Energy consumed during that 15-minute window, in kWh |
| Captar monthly peak start | `sensor.engie_belgium_captar_monthly_peak_start` | Start timestamp of the 15-minute peak window |
| Captar monthly peak end | `sensor.engie_belgium_captar_monthly_peak_end` | End timestamp of the 15-minute peak window |

These sensors are always created when peaks data is available, are emitted per
electricity EAN, and are not intended as Energy-dashboard sources (the kW and
kWh values use `state_class=measurement` because they describe a 15-minute peak
window, not a cumulative counter). Daily peak entries returned by the same
endpoint are intentionally not exposed as sensors.

The ENGIE API only returns a monthly peak after the first 15-minute peak
of the month has been recorded, which means the current month is empty
during the first day or so. While that is the case, the integration
falls back to the previous month so the sensors stay populated. Two
attributes on each sensor make the source explicit:

- `peak_month`: the `YYYY-MM` the displayed value covers.
- `peak_is_fallback`: `true` when the value is carried over from the
  previous month, `false` once the current month has its own peak.

The integration adds a calendar entity (`calendar.engie_belgium`) that
shows your monthly capacity-tariff peak as a single event titled
"Captar monthly peak", with the peak power and energy in the event
description.

### Authentication

A binary connectivity sensor (`binary_sensor.engie_belgium_authentication`) is
always created, showing whether the integration is currently authenticated with
the ENGIE API.

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
- Your ENGIE customer number (business agreement number)
- Access to SMS or email for two-factor authentication during setup

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DaanVervacke&repository=hass-engie-be&category=integration)

Click the badge above to open this repository in HACS, then select **Download**.
After HACS finishes, restart Home Assistant.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=engie_be)

Click the badge above to open the **Add Integration** dialog for ENGIE
Belgium. The credential form opens directly. See [Configuration](#configuration)
for what each field expects and a note on 2FA.

#### Manual steps

If the badges above do not work in your browser:

1. Open HACS in your Home Assistant instance.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Add `https://github.com/DaanVervacke/hass-engie-be` with category **Integration**.
4. Search for **ENGIE Belgium** in HACS and install it.
5. Restart Home Assistant.
6. Go to **Settings** > **Devices & Services** > **Add Integration** and search for **ENGIE Belgium**.

## Configuration

Before starting, there are a few things to note:

**A dedicated ENGIE account is required for this integration** (see
[Prerequisites](#prerequisites) for the reasoning). New accounts
created via the [ENGIE user management page](https://www.engie.be/nl/energiedesk/usermanagement/manage-access/)
have 2FA enabled by default and work out of the box. The integration
only supports accounts where 2FA via SMS or e-mail is already enabled.

Configuration is done entirely through the Home Assistant UI. If you
haven't reached the credential form yet, use the **Add Integration**
badge in [Installation](#hacs-recommended) or open
**Settings** > **Devices & Services** > **Add Integration** and search
for **ENGIE Belgium**.

1. Enter your credentials:
   - **Email address** - your ENGIE Belgium login email
   - **Password** - your ENGIE Belgium password
   - **Customer number** - your ENGIE customer/business agreement number
   - **Client ID** - leave at the default unless you know what you're doing
   - **Two-factor authentication method** - choose SMS or Email
2. Click **Submit** - you will receive a verification code via your chosen method
3. Enter the 6-digit verification code and click **Submit**

The integration will authenticate, fetch your energy prices and capacity-tariff
peaks, and create the appropriate sensors.

### Options

After setup, you can configure the price update interval:

[![Open your Home Assistant instance and show your ENGIE Belgium integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=engie_be)

1. Click the badge above (or go to **Settings** > **Devices & Services** and find **ENGIE Belgium**).
2. Click **Configure**.
3. Set the **Update interval** (5-1440 minutes, default: 60 minutes).

## Re-authentication

ENGIE rotates refresh tokens regularly, and the upstream auth server can
revoke a session at any time. The most common trigger is the same ENGIE
account being used elsewhere (engie.be web, ENGIE Smart App). If you see
the **"Repair"** notification frequently, see
[Prerequisites](#prerequisites) for the dedicated-account recommendation.

To complete re-authentication:

1. Open **Settings** > **Devices & Services** and click the
   **Reconfigure** prompt on the ENGIE Belgium card (or click the
   notification).
2. Choose how you want to receive a verification code (SMS or email).
3. Enter the 6-digit code that ENGIE sends you.

Your stored email, password, and customer number are reused. Only fresh
access and refresh tokens are written back to the config entry. No
sensors are removed and no history is lost.

## Removing the integration

This integration follows the standard Home Assistant removal flow and does
not leave any state behind on Home Assistant or on the ENGIE side.

1. Go to **Settings** > **Devices & Services**.
2. Find the **ENGIE Belgium** card and click the three-dot menu.
3. Select **Delete**.

Removing the entry deletes its sensors and the stored credentials and
tokens (`.storage/core.config_entries` is updated by Home Assistant). No
cleanup is required on your ENGIE account: the integration only reads
data and never modifies anything upstream. The OAuth refresh token in
the deleted entry is left to expire naturally on ENGIE's side.

## Troubleshooting

If the integration is misbehaving, work through these steps before
filing an issue:

1. **Enable debug logging.** Open **Settings** > **Devices & Services**,
   click the three-dot menu on the ENGIE Belgium entry, and select
   **Enable debug logging**. Reproduce the issue, then choose **Disable
   debug logging** from the same menu, and Home Assistant will offer to
   download the captured log. Alternatively, add the following to
   `configuration.yaml` and restart Home Assistant:

   ```yaml
   logger:
     logs:
       custom_components.engie_be: debug
   ```

2. **Download diagnostics.** Open the integration in **Settings** >
   **Devices & Services**, click the three-dot menu on the ENGIE Belgium
   entry, and choose **Download diagnostics**. The resulting JSON
   redacts your password, tokens, and EAN identifiers, and is safe to
   attach to a GitHub issue.

3. **Common errors.**
   - *Authentication with ENGIE Belgium failed*: your stored tokens
     were rejected. Follow the [Re-authentication](#re-authentication)
     steps above. If this happens repeatedly, the most common cause is
     sharing your ENGIE account between this integration and engie.be
     or the ENGIE Smart App. A dedicated account is required. See
     [Prerequisites](#prerequisites).
   - *Cannot connect to the ENGIE Belgium API*: the upstream API is
     unreachable or returned an HTTP error. The coordinator will retry
     on its next interval.
   - *Invalid verification code*: re-trigger the MFA step and enter the
     latest code.

4. **File an issue.** If the problem persists, open one at
   [github.com/DaanVervacke/hass-engie-be/issues](https://github.com/DaanVervacke/hass-engie-be/issues)
   and include the diagnostics JSON plus the relevant log lines.

## Credential storage

This integration stores your ENGIE email, password, customer number,
client ID, and the latest OAuth access and refresh tokens in the
config entry (`.storage/core.config_entries`), which Home Assistant
keeps as plain JSON on disk. This is the standard Home Assistant
pattern for integrations that need persisted credentials.

The password is kept (rather than discarded after setup) because the
re-authentication flow re-runs the upstream OAuth `Resource Owner
Password Credentials` exchange to begin a new MFA challenge. Without
the password, every token revocation would require fully removing and
re-adding the integration.

If you do not want the password on disk, treat this integration the
same as any other credential-storing HA integration: protect the host,
restrict filesystem access, and consider full-disk encryption. The
**Download diagnostics** payload redacts all of these fields and is
safe to share.

## How it works

- **Authentication**: Uses OAuth2 with PKCE (public client, no secret) through
  ENGIE's Auth0 login. Two-factor authentication is required only during
  initial setup. The refresh token is persisted across restarts.
- **Token refresh**: Access tokens expire in ~2 minutes. The integration
  refreshes tokens every 60 seconds automatically. Refresh tokens are rotated
  and persisted to the config entry.
- **Data polling**: Energy prices and the current month's capacity-tariff peak
  are fetched at the configured interval (default: every 60 minutes). The
  coordinator polls two endpoints per update; if the peaks endpoint fails
  transiently, the previous value is preserved so the captar sensors stay
  populated.
- **Energy type detection**: At startup the integration calls the ENGIE
  service-points endpoint for each EAN to determine whether the contract is gas
  or electricity. If the lookup fails, a generic "Energy" label is used as
  fallback.

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
