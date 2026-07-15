"""Constants for the ENGIE Belgium integration."""

from __future__ import annotations

from enum import Enum
from logging import Logger, getLogger
from zoneinfo import ZoneInfo

LOGGER: Logger = getLogger(__package__)

DOMAIN = "engie_be"
ATTRIBUTION = "Data provided by ENGIE Belgium"

# OAuth / Auth0 endpoints
AUTH_BASE_URL = "https://account.engie.be"
API_BASE_URL = "https://www.engie.be/api/engie/be/ms/billing/customer/v1"
PREMISES_BASE_URL = "https://www.engie.be/api/engie/be/ms/premises/customer/v1"
PEAKS_BASE_URL = "https://api.engie.be/engie/ms/b2c-energy-insights/v1"
ACCOUNTS_BASE_URL = "https://api.engie.be/engie/ms/accounts/customer/v1"
HAPPY_HOUR_BASE_URL = "https://api.engie.be/engie/ms/energy-insights/customer/v1"
# v2 of the same energy-insights service exposes the ``usage-details``
# endpoint used to backfill historical hourly consumption / injection /
# gas into Home Assistant's long-term statistics.
ENERGY_INSIGHTS_V2_BASE_URL = (
    "https://www.engie.be/api/engie/be/ms/energy-insights/customer/v2"
)
BOOLEAN_FEATURE_FLAG_BASE_URL = "https://api.engie.be/engie/ms/feature-flags/customer/v1/boolean-feature-flags/_query"
# Billing customer service (invoices, account balance).
BILLING_BASE_URL = "https://api.engie.be/engie/ms/billing/customer/v1"
BUSINESS_AGREEMENTS_BASE_URL = (
    "https://www.engie.be/api/engie/be/ms/business-agreements/customer/v1"
)

# Feature-flags response key that authoritatively reports per-BAN Happy
# Hour enrolment. The endpoint also returns ``happy-hours-shown`` which
# governs Smart App UI visibility, but ``-service-enabled`` is the one
# that flips to ``true`` once a user signs the agreement; using it as
# the gate keeps the integration aligned with the actual service state
# rather than a UI quirk.
HAPPY_HOURS_SERVICE_ENABLED_KEY = "happy-hours-service-enabled"

# Feature-flag key that gates the Solar Surplus feature in the Smart App.
# Extracted from the Android app's ``libapp.so`` (``isSolarSurplusShownDashboard``).
# When ``false`` the app hides the surplus dashboard tile; we skip the
# per-EAN forecasts fetch entirely so we match the app's contract and
# save one GET per electricity EAN per refresh.
SOLAR_SURPLUS_SHOWN_DASHBOARD_KEY = "solar-surplus-shown-dashboard"

# Feature-flag key that gates the Time-of-Use dashboard tile in the Smart
# App. Extracted from the Android app's ``libapp.so``
# (``dgo-tou-is-active`` + ``isTimeOfUseActive`` sync method). The flag
# only gates the UI tile; the /tou-schedules endpoint returns data even
# when the flag is false, because the DGO/network schedule always applies.
TOU_FLAG_KEY = "dgo-tou-is-active"

# TOU_SLOT_CODES: union of every slot code the Smart App can display, so a
# new code from ENGIE never lands the sensor in ``unknown``.
#
# - ``peak`` / ``offpeak`` / ``exclusive_night`` - Dart ``TimeSlotCategory``
#   enum. ``PEAK`` and ``OFFPEAK`` observed on the wire (BAN 000000000000,
#   2026-07-08). ``EXCLUSIVE_NIGHT`` documented for the Fluvius rollout.
# - ``day`` - Dart enum, not yet observed.
# - ``superoffpeak`` - the app carries a ``"Super Offpeak"`` display label,
#   and the integration already maps ``SUPEROFFPEAK`` for tri-rate PRICE
#   sensors at ``sensor.py:81``. Tri-rate Belgian contracts extend the
#   binary peak/offpeak split; the same code is expected on the TOU
#   schedule endpoint for those accounts.
#
# Wire values are uppercase (e.g. ``PEAK``, ``OFFPEAK``, ``SUPEROFFPEAK``,
# ``EXCLUSIVE_NIGHT``, ``DAY``). Sensors expose the ``.lower()`` form so
# the ENUM device class matches the strings.json translation keys.
TOU_SLOT_CODES: tuple[str, ...] = (
    "peak",
    "offpeak",
    "superoffpeak",
    "exclusive_night",
    "day",
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

# Solar surplus forecast levels. Values match the ENGIE Smart App Flutter
# app's ``SolarSurplusForecastSunState`` enum (verified from the Android
# app's libapp.so) in lowercase. ``NO_DATA`` is the "no forecast yet" /
# "no solar" sentinel; the other four escalate from ``NO_SURPLUS`` to
# ``HIGH_SURPLUS`` for increasing expected injection.
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

# EPEX day-ahead prices (used for ENGIE Dynamic tariff customers).
# Requires authentication; see ``fetch_epex_prices`` in api.py.
EPEX_BASE_URL = "https://api.engie.be/engie/ms/pricing/v1/public/prices/epex"
# All Belgian retail dynamic tariffs bill in local time; EPEX values
# carry explicit DST-aware offsets but slot bucketing must use the
# Brussels civil day to match what end-users see on their bill.
EPEX_TZ = "Europe/Brussels"
# Shared Brussels ZoneInfo instance, reused across the integration so every
# module bucketing timestamps into local civil time agrees on the same
# object instead of constructing its own ZoneInfo("Europe/Brussels").
BRUSSELS_TZ = ZoneInfo(EPEX_TZ)
# Hourly slots today; carried as a constant so a future 15-min rollout
# only requires touching one place.
EPEX_DEFAULT_SLOT_DURATION_MINUTES = 60

# Raw EPEX values are EUR/MWh; the integration normalises everything
# to EUR/kWh for consistency with the existing supplier-energy-prices
# sensors.
EPEX_MWH_TO_KWH = 1000.0

# Coordinator payload key for the dynamic-tariff flag (kept namespaced
# to avoid clashing with future ENGIE response fields).
KEY_IS_DYNAMIC = "is_dynamic"

# Energy-contracts product codes that identify a dynamic (EPEX-indexed)
# tariff. The API returns ``productConfiguration.energyProduct`` per
# active contract; only contracts whose code appears in this set count
# as dynamic. Held as a frozenset so future codes (e.g. a renamed
# successor product) can be added in one place without touching the
# detection predicate.
DYNAMIC_ENERGY_PRODUCTS: frozenset[str] = frozenset({"DYNAMIC"})

# Historical usage import
# Fallback window applied only when the energy-contracts endpoint fails
# or returns no usable start date on a first-ever import. In the normal
# path the orchestrator walks back to the earliest active-contract
# ``legalContractStartDate`` returned by ENGIE.
HISTORY_BACKFILL_YEARS = 3
# Days per HTTP request when walking the backfill window. 7d bounds
# each response to 168 hourly items and caps the amount of unpersisted
# work lost to a mid-import failure at one week of rows.
HISTORY_CHUNK_DAYS = 7

# Service name for the ``import_history`` service. Exposes optional
# ``start_date`` / ``end_date`` for explicit windows; omit both for
# auto (incremental delta) mode.
SERVICE_IMPORT_HISTORY = "import_history"
# Companion service that clears the three per-BAN external statistic
# streams so the next import walks all the way back to the business
# agreement's start date again. Meant for post-hoc corrections when
# ENGIE republishes historical data.
SERVICE_CLEAR_IMPORT_HISTORY = "clear_import_history"
ATTR_START_DATE = "start_date"
ATTR_END_DATE = "end_date"
ATTR_ENERGY_TYPE = "energy_type"
ATTR_INCLUDE_COSTS = "include_costs"
# User-facing energy-type identifiers accepted by the import / clear
# services. Kept separate from the internal ``STREAM_*`` keys in
# ``_statistics.py`` so the service surface uses UI-friendly names while
# the orchestrator keeps its internal per-direction split.
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
