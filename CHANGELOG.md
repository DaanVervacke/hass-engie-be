# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Type `_BoundaryScheduleMixin` against `CoordinatorEntity` to drop seven type-ignore markers.

## [0.12.0b12] - 2026-07-07

### Added

- The historical-import step in setup and **Add business agreement** now explains that the import runs once and does not fetch new days after that, and includes a one-click link to install the daily-sync blueprint for automated refreshes.

### Fixed

- Historical import no longer writes all-zero statistic streams for energy types the business agreement has no contract for. The importer now fetches the BAN's contract history (active and inactive) once per run and imports only streams whose division has at least one contract. If the contracts endpoint is unavailable the filter is skipped and the original stream selection is used. Users who already have zero-value streams from a previous beta can remove them via the **Clear historical usage statistics** service action.

### Changed

- Multi-BAN historical imports now run concurrently via `asyncio.gather` instead of sequentially, so a service call spanning N business agreements completes in roughly the time of the slowest one instead of the sum.
- Setup fetches the energy-contracts payload once with `include_inactive=True` and reuses it for the division filter and start-date lookup, dropping one redundant API call per historical import.

### Chore

- Bump ruff pin from 0.14.14 to 0.15.20.

## [0.12.0b11] - 2026-07-07

### Docs

- README header redesigned: centered ENGIE logo (theme-aware SVG under `.github/assets/`), centered title reading **ENGIE (BE) - Home Assistant integration**, centered flat-square badge row (HACS · release · hacs/hassfest · tests · Home Assistant · quality-scale · license).

## [0.12.0b10] - 2026-07-07

### Fixed

- **CI** hassfest rejected inline URLs in the four setup-flow step descriptions. Moved the URL to a module-level constant and injected it via `description_placeholders` as `{readme_url}` so `strings.json` no longer hardcodes the link.

## [0.12.0b9] - 2026-07-07

### Fixed

- **CI** hassfest rejected the Repairs description because `{title}` was wrapped in single quotes; dropped the quotes. Also reformatted `tests/test_config_flow.py` to satisfy `ruff format --check`.

## [0.12.0b8] - 2026-07-07

### Docs

- Setup-flow step descriptions cleaned up: drop the duplicate sub-description under **Import history** (the step description already covers it), drop the "Each section below is one BAN" filler, add a "Leave everything off to skip" hint on the first screen, and switch the misleading "auto-select from the contract start date" phrasing on **Start date** to "walk back to the earliest known contract on this business agreement".
- README paragraph on the setup-time historical import no longer points users at a removed **Edit** dialog. The **Import historical usage** action from **Developer tools** > **Actions** is now the documented way to re-run.
- Every README link in the setup-flow strings uses the same **[README](...)** anchor so users get a consistent path from every step.

## [0.12.0b7] - 2026-07-07

### Added

- **Historical import option in the setup flow.** After picking a business agreement in setup or in **Add business agreement**, a new step lets you pick per-household which BANs to backfill, then a second step exposes energy types, an optional date window, and cost tracking for the ones you ticked. When the toggle is on for a BAN, the integration runs the import in a background task after setup completes. The guard on existing statistics prevents a re-run on reload or restart. Failures land in **Settings** > **Repairs** as a per-BAN card. Use the **Import historical usage** service action from **Developer tools** > **Actions** to re-run an import later.
- Persistent notifications during the setup-time historical import: one when it starts, replaced by a completion notification when it finishes. Failures still surface via **Settings** > **Repairs**.
- Section headers on the setup-time historical-import step show the consumption address, and the section now includes optional **Start date** / **End date** pickers matching the import service action.

## [0.12.0b6] - 2026-07-07

### Added

- **Debug log for malformed rows in the import converter.** When ENGIE returns a row with a missing or unparseable timestamp, the converter still skips it silently but now emits a per-chunk `DEBUG` line with the count so future API schema drift is diagnosable.

## [0.12.0b5] - 2026-07-07

### Changed

- **Injection cost stream renamed to "compensation"** in the Energy Dashboard picker. Statistic ID stays `engie_be:{BAN}_injection_cost` for backwards compatibility with earlier beta imports; only the display name changes. Matches HA's own **Export compensation** terminology.
- **Blueprint URLs point at the feature branch** until this release lands on `main`. Import badge, manual paste URL, and blueprint `source_url` metadata all reference `feat/import-historical-energy-data`.

### Docs

- README "Add to the Energy Dashboard" section rewritten with the real HA UI labels: **Grid connections**, **Add grid connection**, **Configure grid connection**, **Energy imported from grid**, **Energy exported to grid**, **Use an entity tracking the total costs**, **Export compensation**, **Add gas source**, **Configure gas consumption**.
- Retitled "Import a specific date range" to "Run a one-off import" and led with the first-time-backfill use case.
- UI capitalization corrected across README, service strings, and blueprint: **Devices & services**, **Developer tools**, **Automations & scenes**, **Add integration**, **Repairs**.

## [0.12.0b4] - 2026-07-07

### Changed

- **Blueprint updated to the 2024.8+ automation syntax**: `triggers:` / `trigger: time` / `actions:` / `action:` in place of the pre-2024.8 `trigger:` / `platform:` / `action:` / `service:` keys. HA still accepts the old shape but is moving away from it.

### Docs

- Dropped "out of the box" and "appropriate sensors" from the README and the config-flow user step description.

## [0.12.0b3] - 2026-07-07

### Fixed

- **End date on `import_history` is now inclusive.** Picking `2026-04-15` as end date now imports through the 15th (previously the 15th was excluded). Field description updated to reflect this.

## [0.12.0b2] - 2026-07-07

### Fixed

- **Historical import now returns data from before your current contract.** The usage-details endpoint on `api.engie.be` only serves data for the currently-active contract on a BAN. Switched to the `www.engie.be` variant, which serves history across all prior contracts on the same BAN. Also flipped `includeSimulation=true` so ENGIE returns cross-contract history.
- **Row filter no longer drops real historical data.** ENGIE flags rows from expired contracts as `partialData: true` even though the values are final. The converter now skips only rows whose `end` timestamp is in the future (in-progress or simulated hours), which keeps past-dated final rows regardless of the partial flag.

### Changed

- **`energy_type` and `include_costs` fields on both services are now required.** The per-field checkbox in the Developer Tools UI is gone: `energy_type` must have at least one selection (validated) and `include_costs` is already a boolean toggle. Defaults preserved (all three energy types pre-selected; costs off).

### Added

- **Debug logging across the historical-import path.** Service handlers log the raw `device_ids` and resolved BAN/title per dispatch; the orchestrator logs the active streams, selected import window and code path (explicit / contract-start / fallback / resume), running-sums seed on resume, and the `statistic_id` list about to be cleared.

## [0.12.0b1] - 2026-07-06

### Added

- **Import historical usage into the Energy Dashboard** via the `engie_be.import_history` service action (Developer Tools > Actions > *Import historical usage* under **ENGIE Belgium**, or from an automation). Target one or more business-agreement devices; optional `energy_type` field (consumption / injection / gas consumption / any combination), `start_date`, and `end_date`. Omit all fields for auto mode: first call pulls hourly usage from ENGIE back to the business agreement's start date and writes it into Home Assistant's long-term statistics under per-BAN external statistic IDs (`engie_be:{BAN}_consumption`, `_injection`, `_gas`); subsequent calls only fetch new hours since the last run. Provide dates to re-import a specific window, overwriting existing hourly rows in place. The values appear in the Energy Dashboard's electricity and gas source pickers.
- **`engie_be.clear_import_history` service** deletes imported statistic streams for the targeted business-agreement device. Optional energy-type field to clear only the selected streams. The next `Import historical usage` call for the same device and energy type backfills again from the business agreement's start date. Useful when ENGIE republishes historical data after the fact.
- **Blueprint: daily historical data sync** (`blueprints/automation/DaanVervacke/engie_be_daily_history_sync.yaml`) - import from the README, pick a device, a time, and one or more energy types. Home Assistant then runs `engie_be.import_history` once per day for users without a P1 meter.
- **`include_costs` field on `import_history` and `clear_import_history` services** - set to `true` to also import or clear per-hour cost (EUR) statistics alongside kWh streams. Adds three new per-BAN statistic IDs: `engie_be:{BAN}_consumption_cost`, `engie_be:{BAN}_injection_cost`, and `engie_be:{BAN}_gas_cost`. Cost data is sourced from the same `usage-details` payload; no additional API calls. Off by default. The blueprint also exposes the new input.

## [0.11.0] - 2026-07-04

### Added

- **Reconfiguration flow**: you can now change your preferred two-factor authentication method (SMS or email) from **Settings > Devices & Services > ENGIE Belgium > Reconfigure** without going through a full re-authentication. Your session and stored tokens are preserved.
- **Icon translations**: entity icons are now served from `icons.json` rather than being hard-coded in Python entity descriptions, satisfying the Gold `icon-translations` quality-scale rule.
- **Automation and dashboard examples** added to `README.md` (Happy Hours, negative EPEX price, tomorrow's price notification).
- **Known limitations** section added to `README.md`.
- **`async_remove_config_entry_device`**: stale devices (devices whose corresponding subentry has been deleted) can now be cleaned up from the device registry UI without removing the entire integration entry.
- **Pre-setup debug logging instructions** in the README Troubleshooting section, so you can capture logs when the failure happens before a config entry exists (setup wizard or MFA errors).
- **Tests and Home-Assistant-version badges** in the README header. The Home-Assistant badge is dynamic and always reflects the current `hacs.json` floor.

### Fixed

- **Token-refresh timer log spam after a timeout**: when ENGIE processed a `POST /oauth/token` request but the client timed out before reading the response, the stored refresh token became stale. Subsequent 60-second timer ticks hit HTTP 403, each calling `entry.async_start_reauth`, producing repeated "Scheduled token refresh rejected" warnings until the user completed the reauth flow. The timer is now cancelled immediately on the first authentication error before starting the reauth flow, so only one reauth attempt is made.

### Changed

- **Raised the minimum supported Home Assistant version to `2026.7.0`**, dropping support for earlier versions. HACS will not offer this update on older Home Assistant installs.
- **Removed the OAuth Client ID field from setup and reconfiguration.** It served no practical purpose to users and is now hardcoded internally. Existing installs are unaffected.
- **Some sensors are now disabled by default** to reduce dashboard clutter. They are still available and can be enabled per-entity in Settings > Devices & Services:
  - All `_excl_vat` price sensors (the pre-VAT variants of every price sensor). Most users only need the VAT-inclusive value.
  - Captar monthly peak energy. The peak power (kW) sensor stays enabled as it is the value that drives your capacity tariff.
  - Captar monthly peak start and peak end (timestamps). These are also categorised as diagnostic entities to make clear they are contextual detail about the peak power value.
- **Reconfigure success message** now says explicitly which setting was saved and when it takes effect, instead of the generic "Settings updated successfully."
- **Removed the outdated v0.8.x upgrade notice** from the top of the README. Anyone installing today no longer needs to see it.

### Chore

- Bumped `quality_scale` in `manifest.json` from `silver` to `gold`.
- Updated `quality_scale.yaml` to mark all newly-satisfied Gold rules as `done`.
- Debug log for token rotation now also records `refresh_token_expires_in` from the OAuth response, to help diagnose "reauth needed every 24 hours" style reports.
- Switched the Happy Hours enrolment probe from the group-feature-flags endpoint to the targeted boolean-feature-flags endpoint, reducing per-refresh payload size.

## [0.10.1] - 2026-07-03

### Added

- **Three new Happy Hours monthly-summary sensors** for each business agreement enrolled in ENGIE's Happy Hours program. These give you a running total of how the program is going for you this month:
  - **Happy Hours monthly consumption** (`sensor.engie_belgium_*_happy_hours_month_consumption`): how much energy you used during Happy Hours windows this month, in kWh.
  - **Happy Hours eligible hours this month** (`sensor.engie_belgium_*_happy_hours_month_eligible_hours`): how many Happy Hours windows counted toward this month's total.
  - **Happy Hours monthly reward** (`sensor.engie_belgium_*_happy_hours_month_reward`): the value of the free energy you used during Happy Hours windows this month, in EUR. In other words, what that energy would have cost you at your regular rate. It exposes an `is_calculation_ongoing` attribute that is `true` while ENGIE is still finalising the number.
- Right at the start of a new billing month ENGIE sometimes has not yet published this month's totals. In that case the three sensors above now show the most recent completed month instead of `unknown`, with a `report_is_fallback` attribute set to `true` and a `report_month` attribute telling you which month is being displayed.

### Fixed

- **Reauthentication reloads the integration automatically.** If Home Assistant restarted and your stored ENGIE tokens were no longer valid, you would see the "Reconfigure" prompt, sign in again, and then nothing happened. The integration stayed stuck in the "authentication required" state until you manually reloaded it or restarted Home Assistant a second time. Completing the reauthentication flow now wires the new tokens into the running integration on its own, so sensors come back live as soon as you finish signing in.

### Changed

- **Refreshed ENGIE brand assets** (icon and logo, including dark-mode variants) to match ENGIE's current visual style.

## [0.10.0b9] - 2026-07-03

### Added

- Pre-v5 config entries now surface a translated Repairs issue in Settings →
  Repairs when they can no longer be migrated, replacing the previous generic
  setup-error banner with an actionable card.
- `quality_scale.yaml` now declares Gold and Platinum rule status alongside the
  existing Bronze and Silver rows, reflecting `diagnostics` and `repair-issues`
  as done and tracking the remaining gaps as `todo`/`exempt`.

### Changed

- Duplicate-login detection moved earlier in the config flow: configuring an
  ENGIE login that is already set up now aborts at the credentials step
  before any MFA code is requested, instead of after the user has typed it.

### Fixed

- Diagnostics now redacts `id_token` alongside access and refresh tokens,
  defensively guarding against any future code path that persists the OAuth
  id token on the config entry.

## [0.10.0] - 2026-07-03

This release adds support for **Happy Hours**. ENGIE's free-energy windows now
show up right inside Home Assistant. On top of that, your time-based sensors now
flip the moment a window or price slot changes instead of lagging behind,
signing in is more reliable, and the integration has earned Home Assistant's
**Silver** quality badge.

> [!CAUTION]
> **Coming from v0.9.0?** Just update. There's nothing to remove or re-add, and
> your accounts and settings carry over. You do need **Home Assistant 2026.6.0
> or newer**. HACS won't offer the update on older versions.
>
> **Still on v0.8.x or older?** You can't jump straight here. Install **v0.9.0
> first** (that one needs a clean remove-and-re-add, see its release notes),
> then update to this release.

### What's new

- **Happy Hours support.** ENGIE occasionally schedules "Happy Hours" windows
  where the electricity you use at home is free. Happy Hours is an opt-in ENGIE
  program, so these entities only show up for addresses you've enrolled (see
  [engie.be/nl/happyhours](https://www.engie.be/nl/happyhours/)). The
  integration detects enrolment on its own, so they appear and disappear
  without you touching anything. For each enrolled address you get:
  - **Happy Hours is active**, a binary sensor that's `on` for the whole
    window. Perfect for automations like charging the car or running the
    dishwasher while energy is free.
  - **Happy Hours next start** and **Happy Hours next end**, sensors that tell
    you when the next window begins and ends.
  - A **"Happy Hours" event** on each account's calendar, next to the monthly
    capacity-tariff peak. Past windows are kept so the calendar shows a full
    history, though windows from before you installed the integration can't be
    recovered.

- **Sensors now react on the second.** The Happy Hours sensor, the
  **EPEX price is negative** sensor, and the **EPEX current price** and
  **EPEX next hour price** sensors used to only refresh on the next background
  poll, so they could be up to an hour behind the real change. They now flip the
  instant a window opens or closes, or the instant the hourly market price rolls
  over, so price-driven automations fire right on time without needing an
  aggressive refresh interval.

- **Home Assistant Silver quality scale.** The integration now meets all of
  Home Assistant's Silver-tier requirements. As part of this,
  the minimum supported Home Assistant version is now
  **2026.6.0**.

### Improvements & fixes

- **Signing in is more reliable.** Some accounts could not finish setup or
  re-authentication and got an "Invalid username or password." error even when
  everything was correct. ENGIE's login can return one of two different shapes
  after your verification code is accepted, and only one was handled before.
  Both work now. You'll also see a clearer message if sign-in does fail after
  the code step, instead of it being wrongly blamed on your password.
- **The Authentication sensor updates right away.** It now reflects the result
  of the background token refresh immediately, so a sign-in hiccup (or recovery)
  shows up at once instead of waiting for an unrelated update.
- **Adding several accounts at once is tidier.** Picking multiple business
  agreements in one go now reloads the integration just once instead of once
  per account. Each agreement still becomes its own device.
- **Steadier setup and re-authentication.** A brief ENGIE outage during setup no
  longer cascades into an unexpected re-login prompt, and re-authenticating no
  longer reloads the integration twice in a row.
- **Clearer message when an account is already set up.** If you try to
  configure the same ENGIE login twice, the setup wizard now stops at the
  sign-in step with an "already configured" message, instead of asking for
  your 2FA code first and only then telling you it is a duplicate.
- **Actionable Repairs card for very old installs.** If you skipped v0.9.0
  and are upgrading from v0.8.x or earlier, Home Assistant now surfaces an
  actionable card under **Settings** > **Repairs** telling you exactly what
  to do, instead of showing a generic setup-error banner.
- **Diagnostics downloads are safer to share.** The OAuth `id_token` is now
  redacted from diagnostics alongside the access and refresh tokens, so you
  can attach diagnostics to bug reports without worrying about leaking a
  session identifier.

### What you need to do after updating

Coming from v0.9.0, nothing. Your accounts, settings, and history carry over,
and Happy Hours entities appear on their own for enrolled addresses. (Make sure
Home Assistant is on 2026.6.0 or newer first, as noted above.)

## [0.10.0b8] - 2026-06-13

### Added

- **Debug logging for the time-boundary scheduler.** The shared
  `_BoundaryScheduleMixin` behind the Happy Hours, EPEX-negative, and
  EPEX price entities now emits DEBUG lines when it arms a boundary
  timer, when that timer fires, and when no future boundary exists.
  Previously the on-the-second state flip at a window boundary wrote
  nothing to the integration log, so a shared debug bundle could not
  prove a flip happened without cross-referencing Home Assistant's
  state history. Business-agreement numbers are masked in the log
  output (e.g. `happy_hours_active[***6420]`).

## [0.10.0b7] - 2026-06-12

> [!CAUTION]
> **Upgrade from v0.9.0, v0.10.0b1, v0.10.0b2, v0.10.0b3, v0.10.0b4,
> v0.10.0b5, or v0.10.0b6 only.** If you are still on v0.8.x or any
> earlier version, install v0.9.0 first (which requires a clean
> reinstall, see its release notes) and only then move to this release.
> Skipping v0.9.0 leaves your config entry on a schema this release no
> longer migrates, and the integration will refuse to load.
>
> **This release requires Home Assistant 2026.6.0 or newer.** Older
> Home Assistant versions are no longer supported, and HACS will not
> offer this update on them.

### Changed

- **Renamed the Happy Hours entities to ENGIE's official plural program name.**
  Both the entity IDs and the friendly names now use "Happy Hours":
  - `binary_sensor.engie_belgium_<BAN>_happy_hour_active` →
    `..._happy_hours_active`
  - `sensor.engie_belgium_<BAN>_happy_hour_next_start` →
    `..._happy_hours_next_start`
  - `sensor.engie_belgium_<BAN>_happy_hour_next_end` →
    `..._happy_hours_next_end`

  The calendar event title is now "Happy Hours" as well. Because the unique
  IDs changed, Home Assistant registers these as new entities: update any
  dashboards, automations, or scripts that reference the old IDs, and note
  that long-term statistics tied to the old entity IDs do not carry over.

## [0.10.0b6] - 2026-06-10

> [!CAUTION]
> **Upgrade from v0.9.0, v0.10.0b1, v0.10.0b2, v0.10.0b3, v0.10.0b4,
> or v0.10.0b5 only.** If you are still on v0.8.x or any earlier
> version, install v0.9.0 first (which requires a clean reinstall, see
> its release notes) and only then move to this release. Skipping
> v0.9.0 leaves your config entry on a schema this release no longer
> migrates, and the integration will refuse to load.
>
> **This release requires Home Assistant 2026.6.0 or newer.** Older
> Home Assistant versions are no longer supported, and HACS will not
> offer this update on them.

### Fixed

- The authentication binary sensor now updates immediately when the scheduled
  token refresh marks the ENGIE session authenticated or unauthenticated. It no
  longer waits for an unrelated coordinator update before showing a refresh
  failure or recovery.
- Adding multiple business agreements in one picker run now triggers exactly
  one config-entry reload instead of one per selected agreement. Each
  agreement is still written as its own subentry, but the intermediate
  reloads are suppressed until the full selection is in place.

## [0.10.0b5] - 2026-06-08

> [!CAUTION]
> **Upgrade from v0.9.0, v0.10.0b1, v0.10.0b2, v0.10.0b3, or v0.10.0b4
> only.** If you are still on v0.8.x or any earlier version, install
> v0.9.0 first (which requires a clean reinstall, see its release
> notes) and only then move to this release. Skipping v0.9.0 leaves
> your config entry on a schema this release no longer migrates, and
> the integration will refuse to load.
>
> **This release requires Home Assistant 2026.6.0 or newer.** Older
> Home Assistant versions are no longer supported, and HACS will not
> offer this update on them.

### Changed

- Promoted the integration to the Home Assistant **Silver** quality
  scale. All Silver-tier rules are met: config-entry unloading,
  documented installation and configuration parameters, entity
  unavailability handling, an integration owner, log-when-unavailable
  behaviour, `PARALLEL_UPDATES` on every platform, a re-authentication
  flow, and above-95% test coverage. The integration page in Home
  Assistant now shows the Silver badge.
- Raised the minimum supported Home Assistant version to `2026.6.0`,
  dropping support for earlier versions.

### Chore

- Raised the CI coverage gate from 85% to 95% (`--cov-fail-under=95`),
  matching the Silver-tier `test-coverage` requirement.
- Audited the integration against Home Assistant 2026.6 and bumped the
  development and test pins to match (Home Assistant `2026.6.1`,
  `pytest-homeassistant-custom-component` `0.13.337`, ruff `0.14.14`).
  No runtime behaviour changes: the integration already avoids every
  API deprecated up to this release, so no code changes were required.

### Tests

- Raised test coverage of the API client (`api.py`) from 78% to 99%,
  adding unit tests for request/response logging redaction (mappings,
  bodies, lists, and non-coercible values) and the low-level
  `_api_wrapper` paths (authentication errors, non-JSON text bodies,
  header-returning calls, and timeouts). Every module is now at or
  above 95% coverage, with a project total of 99%.
- The three Happy Hour scheduler tests that deliberately leave a timer
  armed for a far-future window now cancel that timer before they
  finish. The newer test harness
  (`pytest-homeassistant-custom-component` `0.13.337`) fails any test
  that leaves a timer running, so these are cleaned up explicitly; this
  is a test-only change with no effect on the shipped integration.

## [0.10.0b4] - 2026-06-07

> [!CAUTION]
> **Upgrade from v0.9.0, v0.10.0b1, v0.10.0b2, or v0.10.0b3 only.** If
> you are still on v0.8.x or any earlier version, install v0.9.0 first
> (which requires a clean reinstall, see its release notes) and only
> then move to this release. Skipping v0.9.0 leaves your config entry
> on a schema this release no longer migrates, and the integration
> will refuse to load.

### Fixed

- **Happy Hour sensors could go blank or show the wrong state after a
  restart later in the day.** When a Happy Hour window falls on the
  current day, ENGIE reports it under a different field than the one
  it uses for the next day. The integration previously read only the
  next-day field, so after a restart (or the first scheduled refresh)
  past midnight the "Happy Hour next start" and "Happy Hour next end"
  sensors could show *unknown* and the "Happy Hour active" sensor
  could stay *off* during a live Happy Hour window. Both day fields
  are now read, so the sensors stay correct throughout the day.

### Tests

- Extended the Happy Hour unit and platform tests to cover the
  current-day payload field: window parsing, active-state detection,
  the next start/end timestamp sensors, the active binary sensor
  (including its instant-flip scheduler), and per-subentry history
  persistence.

## [0.10.0b3] - 2026-05-26

> [!CAUTION]
> **Upgrade from v0.9.0, v0.10.0b1, or v0.10.0b2 only.** If you are
> still on v0.8.x or any earlier version, install v0.9.0 first
> (which requires a clean reinstall, see its release notes) and only
> then move to this release. Skipping v0.9.0 leaves your config entry
> on a schema this release no longer migrates, and the integration
> will refuse to load.

### Fixed

- **Setup and re-authentication could fail with "Invalid username
  or password." even when the password and verification code were
  both correct.** The integration now handles the second of two
  sign-in shapes that the ENGIE login system can return after the
  verification code is accepted. Previously only the first shape
  worked, and accounts that received the second one could not
  complete sign-in.

### Changed

- **Different error message when sign-in fails after the
  verification code is accepted.** The verification-code screen
  used to show "Invalid username or password." for any failure
  that happened after the code was submitted. It now shows a
  separate message indicating the failure occurred after the code
  was accepted and suggesting you cancel and start setup again.
  The "Invalid username or password." message is unchanged on the
  email/password screen.

### Tests

- New `tests/test_api_auth_step9.py` locks both Auth0 outcomes
  (callback short-circuit and passkey-enrollment interstitial) plus
  defensive negative cases (callback URI without a `code` parameter,
  passkey body without an extractable state). Coverage of `api.py`
  rises from ~68% to ~73%, overall coverage from ~85% to ~92%.
- New `test_user_step_credential_error_keeps_auth_key` guards
  against accidentally rerouting the pre-MFA `auth` branch when
  future changes touch the post-MFA error mapping.
- `test_mfa_step_auth_error_recovers` and
  `test_reauth_mfa_auth_error` updated to assert the new
  `post_mfa_auth_failed` key.

## [0.10.0b2] - 2026-05-23

> [!CAUTION]
> **Upgrade from v0.9.0 or v0.10.0b1 only.** If you are still on
> v0.8.x or any earlier version, install v0.9.0 first (which
> requires a clean reinstall, see its release notes) and only then
> move to this release. Skipping v0.9.0 leaves your config entry on
> a schema this release no longer migrates, and the integration
> will refuse to load.

### Fixed

- **Happy Hour active binary sensor now flips at the second.** The
  `binary_sensor.*_happy_hour_active` entity previously only updated
  when the coordinator next refreshed, which meant the on/off
  transition could lag by up to a full refresh interval. The sensor
  now schedules a precise point-in-time callback at the start and
  end of each window, mirroring the pattern used by Home Assistant's
  built-in Time of Day helper. Automations that key off this sensor
  (for example, to start an EV charger or run the dishwasher) now
  see the transition within a second of the window boundary.
  ([#25][])
- **EPEX negative-price binary sensor now flips at the slot
  boundary.** The `binary_sensor.*_epex_negative_now` entity used the
  same coordinator-refresh cadence as the Happy Hour sensor and could
  lag by up to an hour at the top of each market slot. It now uses the
  same point-in-time scheduler, so the on/off transition lines up with
  the exact second the EPEX market moves to the next hourly slot.
- **EPEX current-price and next-hour sensors now roll at the slot
  boundary.** `sensor.*_epex_current` and `sensor.*_epex_next_hour`
  share the same scheduler and now publish the new slot's price the
  instant the market rolls over, instead of waiting for the next
  coordinator refresh. Dashboards and price-driven automations no
  longer need a tight refresh interval to track hourly transitions.

[#25]: https://github.com/DaanVervacke/hass-engie-be/issues/25

## [0.10.0b1] - 2026-05-22

> [!CAUTION]
> **Upgrade from v0.9.0 only.** If you are still on v0.8.x or any
> earlier version, install v0.9.0 first (which requires a clean
> reinstall, see its release notes) and only then move to this
> release. Skipping v0.9.0 leaves your config entry on a schema this
> release no longer migrates, and the integration will refuse to load.

### Added

- **Happy Hour support.** ENGIE Belgium occasionally schedules Happy
  Hour windows during which the energy you use at home is free. These
  windows are announced the day before via the ENGIE app, and the
  integration now surfaces them for every account enrolled in the
  Happy Hours program:
  - A binary sensor that turns on while a Happy Hour window is active.
  - Two timestamp sensors showing when the next window starts and ends.
  - A "Happy Hour" event on the per-account calendar, alongside the
    monthly captar peak. Past Happy Hour windows you have seen are
    kept in a local history file so the calendar can show the full
    archive across restarts. Windows that ran before you installed
    the integration cannot be retrieved.

  The integration auto-detects enrolment by checking ENGIE's feature
  flags on every refresh. Entities appear shortly after you enrol an
  address and disappear shortly after you opt out. You do not need to
  remove and re-add the integration when your enrolment changes.

  Happy Hours is an opt-in program. You need to enrol each address
  separately through the ENGIE Smart App under "Je diensten". See
  [engie.be/nl/happyhours](https://www.engie.be/nl/happyhours/) for
  eligibility and the latest details.

### Changed

- Renamed the Happy Hour binary sensor from "Happy Hour active" to
  "Happy Hour is active" so the label reads naturally in dashboards
  and voice assistants.
- More descriptive debug logging across the Happy Hour code paths
  (enrolment detection, payload interpretation, history persistence,
  platform setup gating). Enable
  `custom_components.engie_be: debug` to see why the integration did
  or did not surface a Happy Hour window. The pre-existing peaks
  history log now also includes the subentry identifier so users with
  multiple addresses can tell the entries apart.

### Fixed

- Scheduled token refresh no longer rotates ENGIE refresh tokens
  against a half-set-up integration during retry. The recurring
  refresh timer now starts only after every setup step has
  completed, so a transient ENGIE outage during setup no longer
  cascades into a reauth prompt on the next retry.
- Reauthentication no longer triggers two reloads of the integration
  in quick succession. This also removes a Home Assistant deprecation
  warning that would otherwise have become an error in Home Assistant
  2026.12.

### Known limitations

- Auth-flow unit test coverage in `api.py` is at 65% (project-wide
  coverage is comfortably above the 85% floor). Steps in the multi-step
  ENGIE login flow, the MFA detours, and the timeout / connection-error
  arms are exercised only against the live API today. This is tracked
  for a follow-up release. Report any login failures you hit during
  the beta so the missing paths get fixtures.

## [0.9.0] - 2026-05-19

> [!CAUTION]
> **This update requires you to remove the integration, log in to
> ENGIE again (including 2FA), and pick your business agreements
> from scratch.** There is no automatic upgrade path from v0.8.x or
> any earlier version.
>
> Until you do this, the ENGIE Belgium integration will show
> "Failed to set up" in **Settings** > **Devices & services** and
> raise a notice under **Settings** > **Repairs**. Your existing
> sensors, calendars, and history will stay visible but will stop
> updating until you complete the steps below.

### What you need to do after updating

1. Open **Settings** > **Devices & services**, find the
   **ENGIE Belgium** card, and click **Delete**. Confirm. This
   removes the old config. Home Assistant keeps your existing
   sensor history so you can still look at past graphs.
2. Click **+ Add integration** (bottom-right), search for
   **ENGIE Belgium**, and log in again with the same ENGIE
   account. You will need to complete the 2FA code that ENGIE
   sends to your phone or email.
3. At the end of the setup wizard, tick the business agreements
   you want Home Assistant to track. Each one becomes its own
   device.
4. (Optional) If you have automations, dashboards, or scripts
   that use the old entity names, update them. See
   **What changes** below.

### What you get

- **One device per business agreement.** If your ENGIE account
  covers more than one address or contract (ENGIE calls this a
  **business agreement**, or BAN), each one now shows up as its
  own device in Home Assistant, with its own sensors and its own
  capacity-tariff calendar. In earlier versions, every contract
  on the same ENGIE customer account (CAN) was bundled under one
  device, which made multi-address setups hard to read.
- **Add more business agreements later without logging in again.**
  Open the ENGIE Belgium card and click
  **Add business agreement** to bring in a contract you skipped
  during setup.
- **Cleaner setup wizard wording.** The flow now talks about
  **business agreements** throughout, matching what ENGIE shows
  in their own app and customer portal.

### What changes (and what does not carry over)

- **Entity names now end in the BAN, not the CAN.** For example,
  what was `sensor.engie_belgium_1234567890_gas_offtake_price`
  may become
  `sensor.engie_belgium_002201234567_gas_offtake_price`. Anywhere
  you use the old entity names (dashboards, automations, scripts)
  needs to be updated to the new names once you have re-added the
  integration.
- **Long-term statistics and history from v0.8.x will not flow
  into the new sensors.** Home Assistant keeps the old data
  attached to the old entity names, so nothing is deleted, but
  graphs that span the upgrade date will show a gap. You can
  delete the orphaned entities later under **Settings** >
  **Devices & services** > **Entities** if you want to clean
  them up.
- **Captar (capacity-tariff) peak history starts fresh.** The
  monthly peak that the integration tracks per electricity meter
  is now tracked against the new device, so the rolling history
  resets on first run. The integration will catch up the current
  month's peak automatically on the next refresh.

## [0.8.3] - 2026-05-18

### Fixed

- **DEBUG logging redaction:** the Auth0 login form body printed the
  user's email (`username` field) and the opaque flow `state` token
  verbatim because neither key was in the body redaction sets. Both
  are now masked: `username` is partial-masked (last-4 preserved) and
  `state` is fully masked, on both the JSON and form-encoded body
  paths ([#80]).

### Changed

- **Structured DEBUG-level request/response logging** in the ENGIE
  Belgium API client. Each HTTP call is bracketed with paired `→` /
  `←` (or `✗` on failure) log lines tagged with an 8-character
  correlation ID and elapsed milliseconds. URL query parameters,
  request headers, request bodies, and response bodies are recursively
  redacted: tokens are fully masked, while emails, EAN identifiers,
  and customer IDs are partially masked (last 4 chars preserved).
  HTML bodies are truncated to 120 characters to avoid dumping live
  auth pages full of CSRF tokens. No behaviour changes; logging is
  only emitted when the integration logger is at DEBUG ([#80]).

### Internal

- Extracted `_log_request` / `_log_response` / `_log_error` helpers
  in `api.py` so the `_api_wrapper` and EPEX inline paths share a
  single source of truth for the `→ / ← / ✗` log format. Documented
  the conscious divergence from `homeassistant.components.diagnostics`
  `async_redact_data` (we need case-insensitive header matching and
  tail-preserving partial masks for greppable PII identifiers) ([#80]).
- Form-encoded body redaction now applies the partial-mask key set
  (previously full-mask only), so PII fields posted through OAuth /
  Auth0 endpoints are masked the same way as JSON bodies ([#80]).
- Hoisted the deferred `EpexPayload` import in `sensor.py:_epex_payload`
  to the module-level imports and dropped the unjustified
  `# noqa: PLC0415`. `data.py` has no runtime imports of any sibling
  module (everything is `TYPE_CHECKING`-gated), so the local import
  was not load-bearing. Audit hygiene only; no runtime behaviour
  change ([#82]).

## [0.8.2] - 2026-05-07

### Added

- **New `EPEX next hour price` sensor** for dynamic-tariff
  electricity accounts. Shows the wholesale electricity price one
  hour from now, so you can run appliances when the upcoming hour
  is cheap.

## [0.8.1] - 2026-05-06

### Changed

- **Authentication sensor moved to diagnostics.** The
  **Authentication** binary sensor is now categorised as a diagnostic
  entity, so it no longer appears on default dashboards (Overview,
  Energy). It remains visible on the integration's device page and
  continues to work in automations and on any custom dashboard that
  references it directly.

## [0.8.0] - 2026-05-05

> [!IMPORTANT]
> You will need to re-authenticate after upgrading. Open the ENGIE Belgium
> card under **Settings** then **Devices & Services** and use
> **Reconfigure** to sign in again.

### Added

- **Multiple ENGIE customer accounts under one login.** If your ENGIE
  login owns more than one customer account (for example a home and a
  rental property), you can now add all of them with a single setup.
  At the end of the setup wizard you pick which accounts to add. Each
  account becomes its own device with its own sensors and calendar. To
  add another account later, open the ENGIE Belgium card under
  **Settings** then **Devices & Services** and click
  **Add customer account**.
- **Dynamic (EPEX-indexed) electricity tariff support.** If your
  contract uses ENGIE's dynamic tariff, the integration now exposes
  three new sensors (**EPEX current price**, **EPEX lowest price today**,
  **EPEX highest price today**) plus a new **EPEX price is negative**
  binary sensor that turns on when the wholesale price drops below
  zero. Hourly slots for today and tomorrow are exposed as attributes
  for plotting in ApexCharts and similar dashboard cards. Tomorrow's
  prices appear once ENGIE publishes them.

### Changed

- **Calendar now leads with the brand name.** Your calendar shows up
  in the calendar panel as **ENGIE Belgium &lt;address&gt;** instead of
  just the address.
- **Entity IDs now include your customer account number** (for example
  `sensor.engie_belgium_1500000123_gas_offtake_price`). Long-term
  statistics and history are preserved automatically, but any
  hard-coded entity ID in an automation, script, scene, or dashboard
  will need updating.

### Migration

- Existing installs upgrade automatically on first load. You will be
  asked to re-authenticate once after upgrading.

## [0.7.1] - 2026-05-03

### Added

- New aggregated calendar entity `calendar.engie_belgium` that surfaces
  ENGIE-related events in one place. The first event type is the monthly
  capacity-tariff peak window ("Captar monthly peak"), with peak power
  and peak energy in the event description. Past monthly peaks are
  persisted across restarts in a small per-config-entry store, so the
  calendar keeps surfacing previous months even after the ENGIE API has
  rolled over to a new month and dropped the old value. The entity is
  built around a provider-list pattern so future event types (outage
  windows, billing dates, contract renewals) can plug in without
  spawning extra calendar entities. Fallback-month provenance is
  intentionally not duplicated in the description because the existing
  `peak_is_fallback` sensor attribute already covers that. The entity
  reads from the existing coordinator payload, so no extra API calls are
  made. Diagnostics gained a `peaks_history` summary
  (`count`, `oldest`, `newest`, `latest_peakKW`) for visibility into the
  persisted store ([#61]).

### Changed

- Internal refactor: payload-shape helpers for the captar peaks payload
  moved from `sensor.py` into a new shared `_peaks` module that also
  hosts the captar event provider used by the new calendar platform
  ([#61]).

### Chore

- All entity platforms (`binary_sensor`, `calendar`, `sensor`) now
  declare `PARALLEL_UPDATES = 0` to make the coordinator-centralised
  update model explicit, per Home Assistant integration quality scale
  guidance ([#61]).

## [0.7.0] - 2026-05-02

### Added

- Four new capacity-tariff (captar) sensors that expose the current
  month's peak power and energy plus the start and end timestamps of
  that monthly peak window. Data comes from the ENGIE
  `b2c-energy-insights` peaks endpoint and is fetched on every
  coordinator poll. If the peaks endpoint is temporarily unavailable,
  the integration keeps the last-known values so the sensors stay
  populated until the next successful poll. Because the endpoint
  omits the monthly peak until the first 15-minute peak of the new
  month is recorded, the coordinator falls back to the previous
  month while the current month is still empty. Each sensor exposes
  two attributes (`peak_month` and `peak_is_fallback`) so the source
  of the displayed value is explicit ([#58]).

### Docs

- README updated to describe the captar feature outside the per-PR
  section: intro, features list, sensors intro, configuration
  walkthrough, and how-it-works now mention the second endpoint and
  the captar sensors. The captar section gained a one-paragraph caveat
  covering always-created behaviour, per-EAN emission, the deliberate
  non-Energy-dashboard choice, and the intentional omission of daily
  peak entries ([#59]).

## [0.6.1] - 2026-05-01

### Docs

- Require a dedicated ENGIE account for this integration. The README,
  the setup form, the re-authentication dialog, and the bug-report
  template now state this as a hard requirement rather than a
  recommendation. Signing into the same ENGIE account from engie.be
  or the ENGIE Smart App appears to revoke the integration's refresh
  token. A dedicated account avoids the repeated re-auth prompts
  ([#55]).

## [0.6.0] - 2026-04-30

### Changed

- Bumped minimum Home Assistant version to 2026.3.0 in hacs.json. The
  integration's brand icon now ships with the integration itself via the
  Brands Proxy API (HA 2026.3+), so HACS shows the logo without needing
  an entry in the upstream brands repo. Users on older Home Assistant
  versions should stay on 0.5.0 or upgrade Home Assistant ([#53]).
- Bumped dev/test pins to homeassistant 2026.3.4 and
  pytest-homeassistant-custom-component 0.13.320 so CI runs at or above
  the new minimum Home Assistant version ([#53]).

### Docs

- README now leads with one-click "Open in HACS" and "Add Integration"
  badges, with the manual steps kept as a fallback ([#52]).

## [0.5.0] - 2026-04-29

### Added

- Diagnostics platform with credential redaction so users can share
  sanitized data when reporting bugs ([#37]).
- Silent re-authentication flow that triggers a UI reauth instead of
  removing the entry when refresh tokens are revoked ([#36]).
- Pull request template to standardize PR descriptions ([#47]).

### Changed

- Declared Bronze quality scale and met all 18 Bronze rules ([#42], [#43]).
- Hardened logging: removed redundant debug toggle, scrubbed sensitive
  values from log output ([#40]).
- Translatable exceptions, HTTP status constants, and clearer README
  sections from the audit pass ([#39]).
- Regenerated brand assets to match the Home Assistant brand spec ([#41]).
- Service-point lookups now run in parallel during setup, so multi-EAN
  customers no longer pay sum-of-latencies on every reload ([#49]).

### Fixed

- Em-dashes removed from README in favor of natural punctuation ([#46]).

### Docs

- Disclosed AI assistance used during development ([#44]).
- Added a CHANGELOG and linked it from the README ([#48]).
- Bug-report template now points at the README's troubleshooting steps
  for enabling debug logs ([#50]).

### Tests

- Initial test scaffolding and CI wiring ([#35]).
- Coordinator and `__init__` unit coverage with `pytest-cov`
  reporting ([#38]).
- Regression coverage proving `update_interval` from the options flow
  reaches the live coordinator ([#45]).

## [0.4.2] - 2026-03-23

### Fixed

- Properly refresh energy prices and sensors; allow refresh interval
  to be set in minutes ([#30]).

## [0.4.1] - 2026-03-23

### Changed

- Improved authorization code extraction during login ([#27]).
- Clearer login instructions ([#28]).

## [0.4.0] - 2026-03-13

### Added

- Option to enable debug logging during initial setup ([#22]).

### Changed

- Clarified 2FA requirements and authentication issues in the
  README ([#20]).
- Improved customer number field string ([#23]).

### Fixed

- Reverted gas prices back to EUR/kWh ([#21]).

## [0.3.1] - 2026-03-10

### Docs

- README updated to cover tri-rate (super off-peak) support ([#18]).

## [0.3.0] - 2026-03-10

### Added

- Tri-rate (super off-peak) tariff support ([#17]).

### Fixed

- Customer numbers with whitespace no longer cause API 400 errors ([#16]).

## [0.2.3] - 2026-03-03

### Fixed

- Use EUR per m³ for gas pricing ([#14]).

## [0.2.2] - 2026-03-02

### Added

- Energy type now derived automatically from the service-points
  endpoint ([#12]).

## [0.2.1] - 2026-03-02

### Docs

- README updated to reflect recent changes ([#11]).

## [0.2.0] - 2026-03-02

### Added

- Day and night tariff support ([#8]).

## [0.1.3] - 2026-03-02

### Changed

- Version bump only ([#7]).

## [0.1.2] - 2026-03-02

### Added

- Improved customer number input field ([#5]).

### Fixed

- Stopped reloading the config entry on every token rotation ([#4]).

## [0.1.1] - 2026-03-02

No user-visible changes.

## [0.1.0] - 2026-02-28

### Added

- Initial release: ENGIE Belgium custom integration with electricity
  and gas sensors, OAuth login, and email-based 2FA ([#1]).
- HACS publication metadata ([#2]).

[#1]: https://github.com/DaanVervacke/hass-engie-be/pull/1
[#2]: https://github.com/DaanVervacke/hass-engie-be/pull/2
[#4]: https://github.com/DaanVervacke/hass-engie-be/pull/4
[#5]: https://github.com/DaanVervacke/hass-engie-be/pull/5
[#7]: https://github.com/DaanVervacke/hass-engie-be/pull/7
[#8]: https://github.com/DaanVervacke/hass-engie-be/pull/8
[#11]: https://github.com/DaanVervacke/hass-engie-be/pull/11
[#12]: https://github.com/DaanVervacke/hass-engie-be/pull/12
[#14]: https://github.com/DaanVervacke/hass-engie-be/pull/14
[#16]: https://github.com/DaanVervacke/hass-engie-be/pull/16
[#17]: https://github.com/DaanVervacke/hass-engie-be/pull/17
[#18]: https://github.com/DaanVervacke/hass-engie-be/pull/18
[#20]: https://github.com/DaanVervacke/hass-engie-be/pull/20
[#21]: https://github.com/DaanVervacke/hass-engie-be/pull/21
[#22]: https://github.com/DaanVervacke/hass-engie-be/pull/22
[#23]: https://github.com/DaanVervacke/hass-engie-be/pull/23
[#27]: https://github.com/DaanVervacke/hass-engie-be/pull/27
[#28]: https://github.com/DaanVervacke/hass-engie-be/pull/28
[#30]: https://github.com/DaanVervacke/hass-engie-be/pull/30
[#35]: https://github.com/DaanVervacke/hass-engie-be/pull/35
[#36]: https://github.com/DaanVervacke/hass-engie-be/pull/36
[#37]: https://github.com/DaanVervacke/hass-engie-be/pull/37
[#38]: https://github.com/DaanVervacke/hass-engie-be/pull/38
[#39]: https://github.com/DaanVervacke/hass-engie-be/pull/39
[#40]: https://github.com/DaanVervacke/hass-engie-be/pull/40
[#41]: https://github.com/DaanVervacke/hass-engie-be/pull/41
[#42]: https://github.com/DaanVervacke/hass-engie-be/pull/42
[#43]: https://github.com/DaanVervacke/hass-engie-be/pull/43
[#44]: https://github.com/DaanVervacke/hass-engie-be/pull/44
[#45]: https://github.com/DaanVervacke/hass-engie-be/pull/45
[#46]: https://github.com/DaanVervacke/hass-engie-be/pull/46
[#47]: https://github.com/DaanVervacke/hass-engie-be/pull/47
[#48]: https://github.com/DaanVervacke/hass-engie-be/pull/48
[#49]: https://github.com/DaanVervacke/hass-engie-be/pull/49
[#50]: https://github.com/DaanVervacke/hass-engie-be/pull/50
[#52]: https://github.com/DaanVervacke/hass-engie-be/pull/52
[#53]: https://github.com/DaanVervacke/hass-engie-be/pull/53
[#55]: https://github.com/DaanVervacke/hass-engie-be/pull/55
[#58]: https://github.com/DaanVervacke/hass-engie-be/pull/58
[#59]: https://github.com/DaanVervacke/hass-engie-be/pull/59
[#61]: https://github.com/DaanVervacke/hass-engie-be/pull/61
[#80]: https://github.com/DaanVervacke/hass-engie-be/pull/80
[#82]: https://github.com/DaanVervacke/hass-engie-be/pull/82

[0.12.0b11]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.12.0b10...v0.12.0b11
[0.12.0b10]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.12.0b9...v0.12.0b10
[0.12.0b9]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.12.0b8...v0.12.0b9
[0.12.0b8]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.12.0b7...v0.12.0b8
[0.12.0b7]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.12.0b6...v0.12.0b7
[0.12.0b6]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.12.0b5...v0.12.0b6
[0.12.0b5]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.12.0b4...v0.12.0b5
[0.12.0b4]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.12.0b3...v0.12.0b4
[0.12.0b3]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.12.0b2...v0.12.0b3
[0.12.0b2]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.12.0b1...v0.12.0b2
[0.12.0b1]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.11.0...v0.12.0b1
[0.11.0]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.9.0...v0.10.0
[0.10.0b9]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.10.0b8...v0.10.0b9
[0.10.0b8]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.10.0b7...v0.10.0b8
[0.10.0b7]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.10.0b6...v0.10.0b7
[0.10.0b6]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.10.0b5...v0.10.0b6
[0.10.0b5]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.10.0b4...v0.10.0b5
[0.10.0b4]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.10.0b3...v0.10.0b4
[0.10.0b3]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.10.0b2...v0.10.0b3
[0.10.0b2]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.10.0b1...v0.10.0b2
[0.10.0b1]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.9.0...v0.10.0b1
[0.9.0]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.8.3...v0.9.0
[0.8.3]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.8.2...v0.8.3
[0.8.2]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/DaanVervacke/hass-engie-be/releases/tag/v0.1.0
