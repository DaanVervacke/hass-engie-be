# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0b3] - 2026-05-05

> Beta release. Please report issues on
> [GitHub](https://github.com/DaanVervacke/hass-engie-be/issues).

### Fixed
- **No more duplicate energy price sensors after upgrading from 0.7.x.**
  After upgrading from 0.7.x to 0.8.0b2, the gas and electricity
  offtake/injection price sensors could end up listed twice in your
  customer account: once with the original entity IDs (the migrated
  sensors that keep your history) and once again with new entity IDs
  derived from your address. The integration now reuses the migrated
  sensors directly, so only the original six energy price sensors
  appear and your history stays intact.

## [0.8.0b2] - 2026-05-05

> Beta release. Please report issues on
> [GitHub](https://github.com/DaanVervacke/hass-engie-be/issues).

### Fixed
- **Customer account no longer appears twice in the integration card.**
  After upgrading from 0.7.x to 0.8.0b1, some installs showed the same
  customer account listed under two groups in **Settings** > **Devices
  & Services** (once under the parent entry and once under its own
  subentry). The integration now cleans this up automatically the next
  time it starts. No action is required and your sensors, history, and
  customisations are preserved.
- **Energy price sensors now stay attached to the customer account
  device after upgrading from 0.7.x.** Previously the gas and
  electricity offtake/injection price sensors were moved onto a
  separate "Account" device during the upgrade, which broke
  area assignments and grouped them away from the captar peak,
  calendar, and EPEX sensors. They are now correctly grouped under
  the customer account device alongside the rest of the sensors.
  History and customisations are preserved.

## [0.8.0b1] - 2026-05-05

> Beta release. Please report issues on
> [GitHub](https://github.com/DaanVervacke/hass-engie-be/issues).

### Added
- **Multiple ENGIE customer accounts under one login.** If your ENGIE
  login owns more than one customer account (for example a home plus a
  rental property), you can now add all of them with a single setup. At
  the end of the setup wizard you pick which accounts to add from a
  list. Each account becomes its own device with its own sensors,
  calendar, and (for dynamic contracts) wholesale-price sensors. To add
  another account later, open the ENGIE Belgium card in **Settings**
  > **Devices & Services** and click **Add customer account**. To
  remove one, delete its subentry.
- **Dynamic (EPEX-indexed) electricity tariff support.** If your
  contract uses ENGIE's dynamic tariff, the integration now exposes
  three new sensors: **EPEX current price**, **EPEX lowest price
  today**, and **EPEX highest price today**. Prices are reported in
  EUR/kWh, and the hourly slots for today and tomorrow are exposed as
  attributes so you can plot them in ApexCharts or similar dashboard
  cards. Tomorrow's prices appear once ENGIE publishes them (typically
  around 13:15 Brussels time). These sensors are only created on
  dynamic accounts.
- **Negative-price binary sensor.** A new **EPEX price is negative**
  binary sensor turns on when the current wholesale price is below
  zero, so you can build simple automations like "run the dishwasher
  when ENGIE is paying me to consume" without writing templates. Only
  created on dynamic accounts.

### Changed
- **The calendar now leads with the ENGIE Belgium brand name.**
  Previously the calendar showed up in the calendar panel as just your
  consumption address, making it indistinguishable from other calendar
  sources. It now shows as **ENGIE Belgium &lt;address&gt;**.
- **Entity IDs now include your customer account number.** Entity IDs
  for sensors and calendars now look like
  `sensor.engie_belgium_1500000123_gas_offtake_price` and
  `calendar.engie_belgium_1500000123`. This applies to every install,
  not just multi-account ones, and avoids accidental `_2` suffixes
  when adding a second account later. Long-term statistics and history
  are preserved automatically. **You will need to update any automation,
  script, scene, or dashboard that references the old entity IDs.**
- **Diagnostics output is easier to share.** Customer account numbers
  and addresses are now redacted from diagnostics downloads, so you can
  share support bundles without leaking personal information.

### Fixed
- Adding or removing a customer account after initial setup now works
  reliably. Previously the new account could appear without any sensors
  until you reloaded the integration manually.
- Setup no longer fails partway through with a confusing error about
  the customer-account picker.
- The **Add customer account** dialog now shows a proper field label
  instead of a raw `selected_accounts` placeholder, and EPEX sensors
  now have proper friendly names.
- The **Add customer account** picker no longer offers accounts that
  are already configured (the deduplication check missed certain legacy
  installs).
- Token-refresh failures now log a useful error message so transient
  upstream issues are easier to diagnose.

### Migration
- **Existing installs are upgraded automatically on first load.** Your
  current customer account becomes a single account under the new
  multi-account structure, and entity history is preserved. No manual
  reconfiguration is required, but see the **Changed** section above
  about updating automations and dashboards that reference the old
  entity IDs.

### Docs
- Simplified the README: removed internal implementation details,
  trimmed the multi-account and credential sections, collapsed the
  incl-VAT / excl-VAT sensor tables into single rows, and clarified the
  install steps to lead with the HACS search bar.

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

[Unreleased]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.7.1...HEAD
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
