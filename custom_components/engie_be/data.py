"""Custom types for the ENGIE Belgium integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry

    from .api import EngieBeApiClient
    from .coordinator import (
        EngieBeDataUpdateCoordinator,
        EngieBeEpexCoordinator,
    )
    from .store import EngieBeHappyHoursStore, EngieBePeaksStore


type EngieBeConfigEntry = ConfigEntry[EngieBeData]


@dataclass(slots=True, frozen=True)
class EpexSlot:
    """
    Single EPEX day-ahead market price slot.

    ``start``/``end`` are timezone-aware datetimes (Europe/Brussels).
    ``value_eur_per_kwh`` is the wholesale market price in EUR/kWh
    (raw API value is EUR/MWh and divided by 1000 on ingest).
    ``duration_minutes`` is carried explicitly so a future move from
    hourly to 15-minute publication does not require touching the
    payload shape.
    """

    start: datetime
    end: datetime
    value_eur_per_kwh: float
    duration_minutes: int


@dataclass(slots=True, frozen=True)
class EpexPayload:
    """Latest known EPEX day-ahead slate plus its publication metadata."""

    slots: tuple[EpexSlot, ...]
    publication_time: datetime | None
    market_date: str | None


@dataclass
class EngieBeSubentryData:
    """
    Per-subentry runtime state.

    One instance lives in :class:`EngieBeData.subentry_data` per active
    business agreement (one ``ConfigSubentry`` of type
    ``business_agreement``). Each subentry owns its own customer-data
    coordinator, the per-account service-points lookup, and a peaks
    store keyed off the subentry id so historical peaks survive across
    restarts independently per business agreement.

    ``is_dynamic_override`` is set from the energy-contracts endpoint
    during setup and takes precedence over the legacy payload-shape
    heuristic when not None. It survives across coordinator refreshes
    so a transient outage on the prices endpoint never silently flips
    the account back to fixed. ``energy_contracts_payload`` retains the
    raw contracts response for diagnostics so support bundles can
    correlate per-EAN product codes with the detection result.

    ``is_happy_hour_enrolled`` mirrors the latest reading of the ENGIE
    feature-flags endpoint (``happy-hours-service-enabled.value``). It
    is ``None`` until the first successful refresh, ``True`` once the
    customer signs the agreement in the Smart App, and ``False`` when
    the flag is absent or false. The coordinator schedules a config
    entry reload whenever this flips so Happy Hours entities appear or
    disappear without manual intervention.

    ``has_solar`` mirrors whether the customer has a solar installation
    that ENGIE can forecast. It is ``None`` until the first successful
    solar-surplus fetch, ``True`` when any hourly forecast slot across the
    3-day horizon carries a non-``NO_DATA`` level, and ``False`` when
    every slot is ``NO_DATA`` (the observed shape for customers without
    solar). The coordinator schedules a config-entry reload on a flip so
    the surplus sensor appears or disappears without manual intervention.

    ``is_tou_active`` mirrors the latest reading of ``dgo-tou-is-active``. It
    is ``None`` before the first successful refresh, ``True`` when the
    customer's supplier contract is TOU-priced, and ``False`` otherwise. Slot
    sensors are always created when the endpoint returns data (the network
    schedule applies universally); the flag is exposed as a binary sensor so
    users can distinguish supplier-side TOU from network-only TOU.

    ``happy_hours_store`` persists every Happy Hours window the
    coordinator observes (the API only ever returns the next upcoming
    window under ``tomorrow``, so historical windows would otherwise
    disappear the moment they expire). Stays ``None`` for un-enrolled
    accounts at first observation; the store is created up front so
    enrolment that flips on later can start recording immediately
    without a second wiring pass.
    """

    coordinator: EngieBeDataUpdateCoordinator
    service_points: dict[str, str] = field(default_factory=dict)
    peaks_store: EngieBePeaksStore | None = field(default=None)
    happy_hours_store: EngieBeHappyHoursStore | None = field(default=None)
    is_dynamic_override: bool | None = field(default=None)
    energy_contracts_payload: dict[str, Any] | None = field(default=None)
    is_happy_hour_enrolled: bool | None = field(default=None)
    has_solar: bool | None = field(default=None)
    is_tou_active: bool | None = field(default=None)


@dataclass
class EngieBeData:
    """
    Runtime data for the ENGIE Belgium integration.

    The parent :class:`ConfigEntry` owns a single :class:`EngieBeApiClient`
    and a single :class:`EngieBeEpexCoordinator` (EPEX wholesale prices
    are account-agnostic, so polling them once per login is correct).
    Per-account state lives under ``subentry_data`` keyed by
    ``ConfigSubentry.subentry_id``.

    ``reload_pending`` is a one-shot debounce flag set by the coordinator
    when a Happy Hours enrolment flip is detected. It guarantees that a
    refresh tick which observes simultaneous flips on multiple subentries
    schedules at most one ``async_reload`` call per parent entry.

    ``pending_subentry_target`` collapses a multi-pick subentry add into a
    single reload. When a user selects N business agreements in one picker
    run, the flow writes them with N separate ``async_add_subentry`` calls,
    each of which schedules this integration's update listener. Without a
    gate, the listener would observe N intermediate subentry sets and fire N
    reloads. The picker sets this to the *final* expected set of business-
    agreement numbers (BANs) before adding; ``async_reload_entry`` then
    suppresses every reload until that full BAN set is present, reloading
    exactly once when it is reached (and clearing the gate). BANs are used
    rather than subentry ids because the first pick's ``subentry_id`` is
    generated by the framework's finish path and is not known up front,
    whereas the BAN (``unique_id``) is set by the picker. ``None`` means no
    multi-add is in progress.
    """

    client: EngieBeApiClient
    epex_coordinator: EngieBeEpexCoordinator
    subentry_data: dict[str, EngieBeSubentryData] = field(default_factory=dict)
    authenticated: bool = field(default=False)
    last_options: dict[str, Any] = field(default_factory=dict)
    last_subentry_ids: set[str] = field(default_factory=set)
    reload_pending: bool = field(default=False)
    pending_subentry_target: set[str] | None = field(default=None)
    cancel_token_refresh: Callable[[], None] | None = field(default=None)
