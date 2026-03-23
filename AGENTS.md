# AGENTS.md

Coding agent instructions for the `hass-engie-be` repository — a Home Assistant
custom integration (HACS) for ENGIE Belgium energy prices.

## Build / Lint / Test Commands

```bash
# Install dependencies
scripts/setup                        # or: pip install -r requirements.txt

# Lint and format (auto-fix)
scripts/lint                         # runs: ruff format . && ruff check . --fix

# Lint (check only, as CI does)
ruff check .
ruff format . --check

# Run local Home Assistant with the integration loaded
scripts/develop                      # starts HA on port 8123 with --debug

# Run tests (no tests currently exist; pytest was used historically)
# pytest tests/                      # if tests are added back
# pytest tests/test_sensor.py        # single file
# pytest tests/test_sensor.py::test_name -k "test_name"  # single test
```

CI runs two workflows on push/PR to `main`:
- **lint.yml**: `ruff check .` + `ruff format . --check`
- **validate.yml**: `hassfest` (HA manifest/translation validation) + HACS validation

## Ruff Configuration

Defined in `.ruff.toml`. Target: Python 3.13. Selects **all** rules (`select = ["ALL"]`).

Ignored rules: `ANN401` (allows `typing.Any`), `D203`/`D212`/`COM812`/`ISC001`
(formatter conflicts). McCabe max complexity: 25. Default line length (88).

## Code Style

### Imports

Every file starts with:

```python
"""Module docstring."""

from __future__ import annotations
```

Import order: stdlib, third-party (`aiohttp`, `voluptuous`, `homeassistant.*`),
local (relative `.module` imports). Use `TYPE_CHECKING` guards for imports only
needed for type hints:

```python
if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .data import EngieBeConfigEntry
```

### Formatting

- 4-space indentation, LF line endings (enforced by `.gitattributes`)
- f-strings for string construction; `%`-style for logger calls only:
  ```python
  url = f"{AUTH_BASE_URL}/oauth/token"
  LOGGER.debug("Service-point %s: division=%s", ean, division)
  ```
- No `.format()` usage

### Type Annotations

- Annotate all function parameters and return types
- Use modern union syntax: `str | None`, not `Optional[str]`
- Use subscript generics: `list[str]`, `dict[str, Any]`, not `List`, `Dict`
- Use `type` alias syntax (Python 3.12+): `type EngieBeConfigEntry = ConfigEntry[EngieBeData]`
- Use keyword-only args with `*` for methods with many parameters

### Naming Conventions

| Element                 | Convention              | Example                                    |
|-------------------------|-------------------------|--------------------------------------------|
| Classes                 | PascalCase, `EngieBe` prefix | `EngieBeApiClient`, `EngieBeEntity`    |
| Exceptions              | `EngieBeApiClient*Error`| `EngieBeApiClientAuthenticationError`       |
| Constants               | `UPPER_SNAKE_CASE`      | `DOMAIN`, `CONF_CUSTOMER_NUMBER`           |
| Module-level privates   | `_UPPER_SNAKE_CASE`     | `_DIVISION_MAP`, `_BROWSER_HEADERS`        |
| Functions / methods     | `snake_case`            | `_find_current_price`, `_persist_tokens`   |
| Private methods         | Leading `_`             | `_api_wrapper`, `_get_price_value`         |
| HA async methods        | `async_` prefix         | `async_setup_entry`, `async_refresh_token` |
| HA entity attributes    | `_attr_` prefix         | `_attr_unique_id`, `_attr_has_entity_name` |
| Variables               | `snake_case`            | `access_token`, `mfa_challenge_state`      |

### Docstrings

Plain imperative style. Single-line for simple methods, multi-line with summary +
blank line + prose for complex ones. Use `*emphasis*` and ` ``code`` ` for
inline formatting:

```python
def _normalize_slot_code(raw_code: str) -> str:
    """
    Normalise a raw ``timeOfUseSlotCode`` to its rate portion.

    Bare codes (``TOTAL_HOURS``, ``PEAK``) are returned as-is.
    Prefixed codes are stripped down to the part after the last
    direction keyword (``OFFTAKE_`` / ``INJECTION_``).
    """
```

### Error Handling

Custom exception hierarchy rooted at `EngieBeApiClientError`:

```
EngieBeApiClientError (base)
├── EngieBeApiClientCommunicationError  (network/timeout)
├── EngieBeApiClientAuthenticationError (bad credentials, 401/403)
└── EngieBeApiClientMfaError            (invalid MFA code)
```

Patterns:
- Assign message to `msg` before raising: `msg = "..."; raise SomeError(msg)`
- Always chain with `from`: `raise UpdateFailed(exc) from exc`
- Catch most-specific exception first, broadest last
- In config flow: map exceptions to error string keys (`"auth"`, `"connection"`, etc.)
- Logging levels: `debug` for progress, `warning` for auth failures,
  `error` for communication issues, `exception` for unknowns

### Suppressed Lint Rules (noqa)

| Rule     | Why                                                              |
|----------|------------------------------------------------------------------|
| `S105`   | Config key names like `CONF_ACCESS_TOKEN` aren't hardcoded secrets |
| `ARG001` | HA platform setup requires `hass` param even when unused         |
| `ARG004` | HA `async_get_options_flow` requires `config_entry` param        |
| `PLR0913`| `_api_wrapper` intentionally has many keyword-only params        |
| `TRY301` | Intentional raise inside try for HTTP status checks              |
| `PLR2004`| HTTP status codes (400, 401, 403) are self-documenting           |

## Home Assistant Patterns

### Runtime Data

Uses the modern `runtime_data` pattern (not `hass.data[DOMAIN]`):

```python
@dataclass
class EngieBeData:
    client: EngieBeApiClient
    coordinator: EngieBeDataUpdateCoordinator
    ...

type EngieBeConfigEntry = ConfigEntry[EngieBeData]
```

### Entity Structure

- Base class: `EngieBeEntity(CoordinatorEntity)` — sets device info, attribution
- Sensors: `EngieBeEnergySensor(EngieBeEntity, SensorEntity)` — multiple inheritance
- Entity names via `translation_key`, not hardcoded `_attr_name`
- Unique IDs: `{entry_id}_{description.key}`
- Sensors built dynamically from API response in `_build_sensor_descriptions()`

### Config Flow

Multi-step: credentials → MFA (SMS or email) → entry created.
Options flow for update interval. Uses `selector` API for form fields.
`strings.json` and `translations/en.json` must stay identical.

### Coordinator

`EngieBeDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]])` polls
prices on a configurable interval. Maps API exceptions to HA's
`ConfigEntryAuthFailed` / `UpdateFailed`.

### Token Management

Access tokens refresh every 60s via `async_track_time_interval`. Refresh tokens
rotate on each use. Tokens persisted via `async_update_entry`. Reload listener
only fires when options change (compares `last_options`), not on token rotation.

## Project Structure

```
custom_components/engie_be/
├── __init__.py       # Setup: token refresh, service-point resolution, platform forwarding
├── api.py            # API client: OAuth2/PKCE + MFA (13-step auth), data fetching
├── binary_sensor.py  # Auth connectivity sensor
├── config_flow.py    # Multi-step config flow + options flow
├── const.py          # Constants: URLs, config keys, defaults
├── coordinator.py    # DataUpdateCoordinator for price polling
├── data.py           # Runtime data types (dataclass + type alias)
├── entity.py         # Base entity with device info
├── manifest.json     # Integration manifest (v0.4.0)
├── sensor.py         # Energy price sensors (gas/electricity, offtake/injection)
├── strings.json      # Translation source strings
└── translations/
    └── en.json       # English translations (must mirror strings.json)
```

## Commit Style

Imperative mood, concise. Examples: "Add tri-rate electricity support",
"Fix customer number whitespace causing API 400 errors", "Bump to 0.4.0".
Branch prefixes: `feat/`, `fix/`, `chore/`, `debug/`.
