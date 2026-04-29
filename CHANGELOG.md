# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Fixed
- Em-dashes removed from README in favor of natural punctuation ([#46]).

### Docs
- Disclosed AI assistance used during development ([#44]).

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

[Unreleased]: https://github.com/DaanVervacke/hass-engie-be/compare/v0.4.2...HEAD
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
