"""Constants for the ENGIE Belgium integration."""

from __future__ import annotations

from enum import Enum
from logging import Logger, getLogger
from zoneinfo import ZoneInfo

LOGGER: Logger = getLogger(__package__)

DOMAIN = "engie_be"
ATTRIBUTION = "Data provided by ENGIE Belgium"

# Auth0 endpoint, followed by the ENGIE service base URLs
AUTH_BASE_URL = "https://account.engie.be"
API_BASE_URL = "https://www.engie.be/api/engie/be/ms/billing/customer/v1"
PREMISES_BASE_URL = "https://www.engie.be/api/engie/be/ms/premises/customer/v1"
PEAKS_BASE_URL = "https://api.engie.be/engie/ms/b2c-energy-insights/v1"
ACCOUNTS_BASE_URL = "https://api.engie.be/engie/ms/accounts/customer/v1"
HAPPY_HOUR_BASE_URL = "https://api.engie.be/engie/ms/energy-insights/customer/v1"
# v2 exposes ``usage-details`` for historical hourly backfill.
ENERGY_INSIGHTS_V2_BASE_URL = (
    "https://www.engie.be/api/engie/be/ms/energy-insights/customer/v2"
)
BOOLEAN_FEATURE_FLAG_BASE_URL = "https://api.engie.be/engie/ms/feature-flags/customer/v1/boolean-feature-flags/_query"
# Billing customer service (invoices, account balance).
BILLING_BASE_URL = "https://api.engie.be/engie/ms/billing/customer/v1"
BUSINESS_AGREEMENTS_BASE_URL = (
    "https://www.engie.be/api/engie/be/ms/business-agreements/customer/v1"
)

# Per-BAN Happy Hours enrolment. ``-service-enabled`` flips on contract sign,
# distinct from ``happy-hours-shown`` which only governs Smart App UI.
HAPPY_HOURS_SERVICE_ENABLED_KEY = "happy-hours-service-enabled"

# Solar Surplus dashboard gate (from Android ``libapp.so``). When false, skip
# the per-EAN forecast fetch to match the app's contract.
SOLAR_SURPLUS_SHOWN_DASHBOARD_KEY = "solar-surplus-shown-dashboard"

# Supplier-side TOU product flag. Distinct from ``dgo-tou-is-active`` (network)
# and ``tou-forecasts-shown`` (always true).
TOU_FLAG_KEY = "tou-is-active"

# TOU slot sensor ENUM states, lowercased. Wire codes normalise via
# ``_tou.normalize_slot_code``. ``day`` stays for backward compatibility.
TOU_SLOT_CODES: tuple[str, ...] = (
    "peak",
    "offpeak",
    "superoffpeak",
    "exclusive_night",
    "day",
    "total_hours",
)

# Weekday keys returned by the API, in ISO order.
TOU_WEEKDAY_KEYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

# Solar Surplus forecast levels (matches Smart App ``SolarSurplusForecastSunState``).
# ``no_data`` is the "no forecast" sentinel. The rest escalate with expected injection.
SOLAR_SURPLUS_LEVELS: tuple[str, ...] = (
    "no_data",
    "no_surplus",
    "minimal_surplus",
    "low_surplus",
    "high_surplus",
)

# OAuth configuration (public mobile-app client, no secret needed)
DEFAULT_CLIENT_ID = "R0PQyUdjO5B2tBaRnltgitVnnUmjGyld"
REDIRECT_URI = "be.engie.smart://login-callback/nl"
OAUTH_SCOPES = "openid profile roles offline_access"
OAUTH_AUDIENCE = "customer"

# Config entry keys (beyond homeassistant.const CONF_USERNAME / CONF_PASSWORD)
CONF_MFA_METHOD = "mfa_method"
CONF_ACCESS_TOKEN = "access_token"  # noqa: S105
CONF_REFRESH_TOKEN = "refresh_token"  # noqa: S105

# Subentry data keys (one ConfigSubentry per active ENGIE business agreement)
SUBENTRY_TYPE_BUSINESS_AGREEMENT = "business_agreement"
CONF_BUSINESS_AGREEMENT_NUMBER = "business_agreement_number"
CONF_PREMISES_NUMBER = "premises_number"
CONF_ACCOUNT_HOLDER_NAME = "account_holder_name"
CONF_CONSUMPTION_ADDRESS = "consumption_address"

# Subentry picker key
CONF_SELECTED_ACCOUNTS = "selected_accounts"

# Translation keys used by condition.py, trigger.py, and referenced on the
# matching entities. Keep in sync with the entity platform definitions.
TRANSLATION_KEY_EPEX_NEGATIVE = "epex_negative"
TRANSLATION_KEY_EPEX_NEGATIVE_QUARTER_HOUR = "epex_negative_quarter_hour"
TRANSLATION_KEY_SOLAR_SURPLUS_FORECAST = "solar_surplus_forecast"
TRANSLATION_KEY_TOU_OFFTAKE_SLOT = "tou_offtake_slot"
TRANSLATION_KEY_TOU_INJECTION_SLOT = "tou_injection_slot"

# Binary-sensor keys referenced by trigger.py
TRANSLATION_KEY_TOU_OFFTAKE_IS_OPTIMAL = "tou_offtake_is_optimal"
TRANSLATION_KEY_TOU_INJECTION_IS_OPTIMAL = "tou_injection_is_optimal"
TRANSLATION_KEY_HAPPY_HOURS_ACTIVE = "happy_hours_active"
TRANSLATION_KEY_AUTHENTICATION = "authentication"

# Sensor keys referenced by trigger.py (numerical / value-changed)
TRANSLATION_KEY_EPEX_CURRENT = "epex_current"
TRANSLATION_KEY_EPEX_NEXT_HOUR = "epex_next_hour"
TRANSLATION_KEY_EPEX_HIGH_TODAY = "epex_high_today"
TRANSLATION_KEY_EPEX_LOW_TODAY = "epex_low_today"
TRANSLATION_KEY_SOLAR_SURPLUS_CURRENT = "solar_surplus_current"
TRANSLATION_KEY_SOLAR_SURPLUS_NEXT_HOUR = "solar_surplus_next_hour"
TRANSLATION_KEY_CAPTAR_MONTHLY_PEAK_POWER = "captar_monthly_peak_power"
TRANSLATION_KEY_OUTSTANDING_BALANCE = "outstanding_balance"
TRANSLATION_KEY_OVERDUE_AMOUNT = "overdue_amount"

# Sensor keys for quarter-hourly EPEX (numerical / value-changed)
TRANSLATION_KEY_EPEX_CURRENT_QUARTER_HOUR = "epex_current_quarter_hour"
TRANSLATION_KEY_EPEX_NEXT_QUARTER_HOUR = "epex_next_quarter_hour"
TRANSLATION_KEY_EPEX_HIGH_TODAY_QUARTER_HOUR = "epex_high_today_quarter_hour"
TRANSLATION_KEY_EPEX_LOW_TODAY_QUARTER_HOUR = "epex_low_today_quarter_hour"

# Setup-time historical import options (stored per subentry)
CONF_IMPORT_HISTORY = "import_history"
CONF_IMPORT_ENERGY_TYPES = "import_energy_types"
CONF_IMPORT_INCLUDE_COSTS = "import_include_costs"
CONF_IMPORT_START_DATE = "import_start_date"
CONF_IMPORT_END_DATE = "import_end_date"

# MFA method options
MFA_METHOD_SMS = "sms"
MFA_METHOD_EMAIL = "email"

# User-Agent strings matching the ENGIE mobile app
USER_AGENT_BROWSER = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Mobile Safari/537.36"
)
USER_AGENT_NATIVE = "Dalvik/2.1.0 (Linux; U; Android 16; Pixel 6 Build/BP4A.251205.006)"

# Token refresh interval in seconds (access token valid ~2 min, refresh every 1 min)
TOKEN_REFRESH_INTERVAL_SECONDS = 60

# Dispatcher signal format for login-scoped authentication state changes.
SIGNAL_AUTHENTICATION_STATE_CHANGED = (
    f"{DOMAIN}_authentication_state_changed_{{entry_id}}"
)

# Price update interval (configurable via options flow)
CONF_UPDATE_INTERVAL = "update_interval"
CONF_EXPOSE_ALL_ENTITIES = "expose_all_entities"
DEFAULT_UPDATE_INTERVAL_MINUTES = 60
MIN_UPDATE_INTERVAL_MINUTES = 5
MAX_UPDATE_INTERVAL_MINUTES = 1440

# EPEX day-ahead prices (ENGIE Dynamic tariff). Requires auth.
EPEX_BASE_URL = "https://api.engie.be/engie/ms/pricing/v1/public/prices/epex"
# Bucket by Brussels civil day so slots line up with what customers see billed.
EPEX_TZ = "Europe/Brussels"
BRUSSELS_TZ = ZoneInfo(EPEX_TZ)
EPEX_DEFAULT_SLOT_DURATION_MINUTES = 60

# Raw EPEX is EUR/MWh. Normalise to EUR/kWh across the integration.
EPEX_MWH_TO_KWH = 1000.0

KEY_IS_DYNAMIC = "is_dynamic"

# ``productConfiguration.energyProduct`` codes that identify a dynamic tariff.
DYNAMIC_ENERGY_PRODUCTS: frozenset[str] = frozenset({"DYNAMIC"})

# Historical usage import.
# Fallback window when energy-contracts has no usable start date.
HISTORY_BACKFILL_YEARS = 3
# 7d chunk caps each response at 168 hourly items and bounds mid-import loss.
HISTORY_CHUNK_DAYS = 7
# A backfill whose newest stat is older than this is treated as interrupted
# and retried. See Guard 1 in _async_guarded_import (__init__.py).
HISTORY_BACKFILL_STALE_DAYS = 30
# Auto-mode re-imports this many days back and overwrites in place, so a
# late-published value is corrected instead of frozen behind the resume point.
# Must exceed ENGIE's 1-2 day publication lag.
HISTORY_HEAL_LOOKBACK_DAYS = 3
# Recorder-delete timeout. Also guards a raise on the recorder thread that
# would skip the completion callback and hang the caller.
CLEAR_STATISTICS_TIMEOUT_SECONDS = 60

# Optional ``start_date`` / ``end_date`` for explicit windows. Omit both for auto mode.
SERVICE_IMPORT_HISTORY = "import_history"
# Clears per-BAN external statistic streams so the next import walks to BAN start.
SERVICE_CLEAR_IMPORT_HISTORY = "clear_import_history"
ATTR_START_DATE = "start_date"
ATTR_END_DATE = "end_date"
ATTR_ENERGY_TYPE = "energy_type"
ATTR_INCLUDE_COSTS = "include_costs"
# User-facing identifiers for import/clear (separate from internal STREAM_* keys).
ENERGY_TYPE_CONSUMPTION = "consumption"
ENERGY_TYPE_INJECTION = "injection"
ENERGY_TYPE_GAS = "gas"
ENERGY_TYPE_OPTIONS: tuple[str, ...] = (
    ENERGY_TYPE_CONSUMPTION,
    ENERGY_TYPE_INJECTION,
    ENERGY_TYPE_GAS,
)


# EPEX granularity options
class EpexGranularity(Enum):
    """Granularity options for EPEX market data."""

    HOURLY = 60
    QUARTER_HOURLY = 15
