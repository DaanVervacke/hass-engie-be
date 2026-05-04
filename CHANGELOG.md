# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Multi-account support via Home Assistant's ConfigSubentries framework.
  A single ENGIE login can now own multiple customer accounts under one
  config entry: each account becomes its own subentry with its own
  device, service points, price sensors, captar peak sensors, calendar,
  and (for dynamic accounts) EPEX sensors. During initial setup the
  integration calls ENGIE's customer-account-relations endpoint and
  shows a multi-select picker of every customer account your login has
  access to. Additional accounts can be added later via the **Add
  subentry** button on the integration card, and removed by deleting
  the subentry without affecting the parent entry or its siblings. The
  authentication binary sensor lives on a separate "login" device tied
  to the parent entry, since it reflects the OAuth session rather than
  any single account.
- Support for ENGIE's dynamic (EPEX-indexed) electricity tariff. Dynamic
  contracts are auto-detected at the account level: when ENGIE returns
  an empty `items` list from the supplier-prices endpoint (the documented
  signal that no fixed monthly tariff applies), the integration switches
  to fetching day-ahead wholesale prices from the public EPEX endpoint
  and exposes three new sensors per config entry: `EPEX current price`,
  `EPEX lowest price today`, and `EPEX highest price today`. Prices are
  reported in EUR/kWh (raw EUR/MWh kept as an attribute) and the today /
  tomorrow hourly slot arrays are exposed as attributes for plotting in
  ApexCharts and similar dashboard cards. The current-price sensor
  follows the active hour automatically on the next coordinator refresh;
  tomorrow's slate appears once ENGIE publishes it (typically around
  13:15 Europe/Brussels). Slots carry an explicit `duration_minutes`
  field so a future switch to 15-minute granularity is non-breaking.
  When the EPEX endpoint returns 404 (tomorrow not yet published) or a
  transient error, the integration keeps the last-known payload rather
  than wiping the sensors.
- New binary sensor `EPEX price is negative` that turns on when the
  current EPEX wholesale slot has a negative price. Lets users build
  simple `state`-based automations ("run the dishwasher when ENGIE is
  paying me to consume") without a `numeric_state` template. Only
  created on dynamic (EPEX-indexed) accounts, so fixed-tariff users
  don't see a permanently unavailable entity. Reports `unavailable`
  before the first successful EPEX fetch and `unknown` when the cached
  payload has no slot covering the current instant, so automations
  don't fire on stale data. Zero is treated as non-negative.

### Changed
- The EPEX coordinator now lives at the parent-entry level instead of
  per-account, since the day-ahead wholesale price is identical for
  every customer account on the same login. EPEX sensors and the
  negative-price binary sensor are still created per dynamic subentry,
  but only one HTTP call to the public EPEX endpoint runs per refresh
  cycle regardless of how many dynamic accounts you have.
- Diagnostics output is now organised top-level by `entry`, `runtime`,
  `epex_coordinator`, and `subentries`, with subentry titles and
  customer account numbers redacted via stable 8-character SHA-256
  hashes so support bundles stay shareable while still letting you
  correlate which subentry a log line refers to.

### Fixed
- `translations/en.json` had drifted out of sync with `strings.json`
  over several feature batches: the entire
  `config_subentries.customer_account` block was missing (so the
  **Add customer account** dialog showed the raw `selected_accounts`
  key instead of a proper field label), the `config.step.user`
  description still mentioned a removed customer-number field with
  two stale field labels, and the four EPEX entities
  (`binary_sensor.epex_negative`, `sensor.epex_current`,
  `sensor.epex_high_today`, `sensor.epex_low_today`) had no friendly
  names. The translations file is now a literal mirror of
  `strings.json`. Tweaked the picker description to read "its own
  sensors" instead of "its own price sensors" since the new
  customer-account device exposes binary, peak, and EPEX sensors
  alongside the price sensors.
- The native-User-Agent assertion in
  `tests/test_api_relations.py::test_async_get_customer_account_relations_sends_native_user_agent`
  was checking for the literal string `"ENGIE"` in the User-Agent
  header, but the API client sends the Dalvik UA
  (`Dalvik/2.1.0 (Linux; U; Android 16; ...)`) that mimics the ENGIE
  Smart App. The assertion now compares against the
  `USER_AGENT_NATIVE` constant directly.
- The initial-setup flow now correctly chains into the customer-account
  picker. The previous implementation set `next_flow` on the parent
  flow result pointing at a `CONFIG_SUBENTRIES_FLOW`, which Home
  Assistant rejects: only `CONFIG_FLOW` targets are valid for that
  hand-off. The picker is now an integrated `select_accounts` step
  that runs after MFA succeeds, fetches relations with the just-issued
  tokens, and creates the parent entry plus chosen subentries
  atomically via `async_create_entry(subentries=...)`. If the
  relations endpoint fails or returns zero accounts at this point the
  flow still finishes cleanly: the parent entry is created without
  subentries and the user can add accounts later from the **Add
  subentry** button.
- The relations-backfill matcher in the coordinator now finds legacy
  entries whose stored `customer_number` is the
  `businessAgreementNumber` (BAN) rather than the
  `customerAccountNumber` (CAN). The previous matcher only walked the
  flat CAN list, so subentries created from older API payloads (or
  entries migrated from a single-account v1/v2 schema where the
  stored identifier happened to be the BAN) silently failed to be
  enriched with `account_holder_name`, `consumption_address`, etc.
- Backfilling relations now refreshes the subentry title and renames
  the customer-account device to match. Previously the title and
  device name were set once at subentry creation and never updated,
  so subentries created before relations were available kept showing
  the raw customer number even after the backfill populated the
  consumption address. `name_by_user` is preserved so user-customised
  device names are not overwritten.
- v2->v3 migration is now idempotent and survives partial-failure
  retries: an existing customer-account subentry with the same
  customer number is reused instead of triggering an
  `already_configured` abort, the device-registry update now passes
  both `add_config_entry_id` and `add_config_subentry_id` (Home
  Assistant 2026.3 requires both), and the entity-registry rename
  step is properly awaited (`er.async_migrate_entries` is a
  coroutine). Without these fixes a v2 entry whose first migration
  attempt failed (for example because the OAuth token had expired
  and the relations backfill returned 403) would refuse to load on
  subsequent restarts.
- The **Add customer account** subentry picker no longer offers
  duplicates of accounts that are already configured under a different
  identifier shape. ENGIE returns each account with both a
  `customerAccountNumber` (CAN) and one or more
  `businessAgreementNumber`s (BANs); legacy v2-migrated subentries
  store the BAN as their `unique_id`, while the picker derives its
  candidates from the CAN. The picker now dedupes against the union
  of every existing subentry's `unique_id`, `customer_number`, and
  `business_agreement_number`, and walks every candidate's CAN plus
  all of its BANs before deciding whether it is already configured.
  Without this, opening the picker for a v2-migrated login would list
  the same physical premises a second time, and confirming the
  selection would create a duplicate device with parallel sensors.

### Migration
- Existing single-account config entries (schema v1 and v2) are
  upgraded automatically on first load to the new schema (v3). The
  existing customer account becomes a single subentry under your
  existing entry, entity unique IDs are rewritten in place from the
  old `{entry_id}_{key}` pattern to the new
  `{entry_id}_{subentry_id}_{key}` pattern, and the authentication
  binary sensor's unique ID is normalised to `{entry_id}_authentication`.
  Sensor history is preserved across the migration. No manual
  reconfiguration is required.

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
