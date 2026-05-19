# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.0b1] - 2026-05-19

> **Pre-release (beta).** This is the first beta of v0.9.0. To
> install, enable "Show beta versions" on the integration in
> HACS. Production users on v0.8.x should not install this
> unless they are willing to re-add the integration; there is
> no in-place upgrade path (see Breaking section).
>
> **Breaking change.** This release requires every existing user to
> remove the integration from Home Assistant and add it again. There
> is no in-place upgrade path. On first boot after the update, every
> pre-v0.9.0 config entry is flagged with `setup_error` and a
> Repairs notice; nothing is lost, but no devices or entities are
> exposed until the user re-adds the integration through the UI.
> See "Upgrade instructions" in the README before installing.

### Breaking
- **Config-entry schema bumped to v5** (`VERSION = 5`). The
  cascading v1 to v2 to v3 to v4 migration chain has been removed
  entirely. `async_migrate_entry` now returns `False` for any
  entry below v5, which causes Home Assistant to mark the entry
  as `setup_error` and surface a Repairs notice. The user must
  manually delete the entry from Settings > Devices & Services
  and re-add it; the re-add walks the current config flow and
  produces a fresh v5 entry keyed on business-agreement numbers
  (BANs).
- **Subentry data shape changed.** `customer_number` is no
  longer stored on subentries; `business_agreement_number` is
  the sole identifier. The subentry type literal also changed
  from `customer_account` to `business_agreement`.
- **Storage rename**: every entity ID and unique ID is now
  derived from the BAN. Users who relied on the pre-v0.9.0
  entity IDs in dashboards, automations, scripts, or templates
  must update those references after re-adding the integration.
  The peaks history store filename remains
  `engie_be.peaks_history.{subentry_id}` but the `subentry_id`
  changes on re-add, so historical capacity-tariff peak data
  from earlier installs is not carried over.

### Why
- The v1 to v4 migration chain and the slug-rename / unique-id
  heal helpers together accounted for roughly 3000 lines of
  one-shot upgrade code that had to be regression-tested on
  every release and survive every long-tail upgrade path. Pre-1.0
  is the right window to take this hit; from v0.9.0 onward the
  codebase keeps only the data shape it actually wants to live
  with.

### Changed
- `_relations.py` slimmed: the legacy helpers
  `extract_accounts`, `iter_account_identifiers`,
  `find_account_for_customer_number`, and
  `flatten_customer_account*` are removed. New helper
  `find_agreement_for_ban(relations, ban)` is used by the
  coordinator backfill in place of the old CAN-based lookup.
- User-facing strings updated to use "business agreement"
  throughout the config flow (picker title, subentry type
  label, error messages) rather than the ambiguous
  "customer account".

### Removed
- All cascading migration helpers in `__init__.py`
  (`_async_migrate_v1_*`, `_async_migrate_v2_*`,
  `_async_migrate_v3_to_v4`).
- `_async_migrate_entity_id_slugs` and
  `_async_try_heal_legacy_unique_ids` (slug-rename and
  BAN-to-CAN heal one-shots).
- Constant `CONF_CUSTOMER_NUMBER` and constant
  `SUBENTRY_TYPE_CUSTOMER_ACCOUNT`.

### Tests
- Deleted `tests/test_migrate_entry.py` (866 LOC) and
  `tests/test_heal_b1_energy_unique_ids.py` (388 LOC).
- Rewrote `tests/test_relations.py` against the v5 helper
  surface; 15 tests, all passing.
- New `tests/test_async_migrate_entry.py` asserts every legacy
  version 1 to 4 is rejected with an error-level log line.
- Remaining test files updated to `version=5`, the new subentry
  type literal, and BAN-only subentry data; dead migration /
  slug / heal tests removed.

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

[Unreleased]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.9.0b1...HEAD
[0.9.0b1]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.8.3...v0.9.0b1
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
