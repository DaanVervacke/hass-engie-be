"""Constants for the ENGIE Belgium integration."""

from __future__ import annotations

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "engie_be"
ATTRIBUTION = "Data provided by ENGIE Belgium"

# OAuth / Auth0 endpoints
AUTH_BASE_URL = "https://account.engie.be"
API_BASE_URL = "https://www.engie.be/api/engie/be/ms/billing/customer/v1"
PREMISES_BASE_URL = "https://www.engie.be/api/engie/be/ms/premises/customer/v1"
PEAKS_BASE_URL = "https://api.engie.be/engie/ms/b2c-energy-insights/v1"

# OAuth configuration (public mobile-app client, no secret needed)
DEFAULT_CLIENT_ID = "R0PQyUdjO5B2tBaRnltgitVnnUmjGyld"
REDIRECT_URI = "be.engie.smart://login-callback/nl"
OAUTH_SCOPES = "openid profile roles offline_access"
OAUTH_AUDIENCE = "customer"

# Config entry keys (beyond homeassistant.const CONF_USERNAME / CONF_PASSWORD)
CONF_CUSTOMER_NUMBER = "customer_number"
CONF_MFA_METHOD = "mfa_method"
CONF_CLIENT_ID = "client_id"
CONF_ACCESS_TOKEN = "access_token"  # noqa: S105
CONF_REFRESH_TOKEN = "refresh_token"  # noqa: S105

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

# Price update interval (configurable via options flow)
CONF_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL_MINUTES = 60
MIN_UPDATE_INTERVAL_MINUTES = 5
MAX_UPDATE_INTERVAL_MINUTES = 1440

# EPEX day-ahead prices (used for ENGIE Dynamic tariff customers).
# Public endpoint, no authentication required.
EPEX_BASE_URL = "https://api.engie.be/engie/ms/pricing/v1/public/prices/epex"
# All Belgian retail dynamic tariffs bill in local time; EPEX values
# carry explicit DST-aware offsets but slot bucketing must use the
# Brussels civil day to match what end-users see on their bill.
EPEX_TZ = "Europe/Brussels"
# Hourly slots today; carried as a constant so a future 15-min rollout
# only requires touching one place.
EPEX_SLOT_DURATION_MINUTES = 60
# Daily publication of next-day prices typically lands shortly after
# 13:00 Brussels time. Schedule a single retry tick so tomorrow's
# slate is picked up the same day instead of waiting for the next
# coordinator interval.
EPEX_PUBLICATION_HOUR = 13
EPEX_PUBLICATION_MINUTE = 15

# Raw EPEX values are EUR/MWh; the integration normalises everything
# to EUR/kWh for consistency with the existing supplier-energy-prices
# sensors.
EPEX_MWH_TO_KWH = 1000.0

# Coordinator payload keys for EPEX data (kept namespaced to avoid
# clashing with future ENGIE response fields).
KEY_EPEX = "epex"
KEY_IS_DYNAMIC = "is_dynamic"
