"""
Historical usage import into Home Assistant long-term statistics.

Turns ENGIE usage-details payloads into hour-aligned StatisticData rows
and feeds them to ``async_add_external_statistics`` under six per-BAN
statistic IDs: three energy streams (``engie_be:{ban}_consumption``,
``_injection``, ``_gas`` in kWh) and three matching cost streams
(``_consumption_cost``, ``_injection_cost``, ``_gas_cost`` in EUR).
The energy dashboard picks these up automatically for electricity and
gas source pickers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.components.recorder.tasks import ClearStatisticsTask
from homeassistant.const import UnitOfEnergy
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .api import EngieBeApiClientError
from .const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    DOMAIN,
    ENERGY_TYPE_CONSUMPTION,
    ENERGY_TYPE_GAS,
    ENERGY_TYPE_INJECTION,
    HISTORY_BACKFILL_YEARS,
    HISTORY_CHUNK_DAYS,
    LOGGER,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from .api import EngieBeApiClient

_UNIT_EUR = "EUR"

# Statistic-stream keys, kept as module constants so the pure converter,
# the metadata factory and the orchestrator agree on spelling.
STREAM_CONSUMPTION = "consumption"
STREAM_INJECTION = "injection"
STREAM_GAS = "gas"
STREAM_CONSUMPTION_COST = "consumption_cost"
STREAM_INJECTION_COST = "injection_cost"
STREAM_GAS_COST = "gas_cost"
_STREAMS: tuple[str, ...] = (
    STREAM_CONSUMPTION,
    STREAM_INJECTION,
    STREAM_GAS,
    STREAM_CONSUMPTION_COST,
    STREAM_INJECTION_COST,
    STREAM_GAS_COST,
)

# User-facing energy-type selectors map 1:1 to internal energy streams.
_ENERGY_TYPE_TO_STREAMS: dict[str, frozenset[str]] = {
    ENERGY_TYPE_CONSUMPTION: frozenset({STREAM_CONSUMPTION}),
    ENERGY_TYPE_INJECTION: frozenset({STREAM_INJECTION}),
    ENERGY_TYPE_GAS: frozenset({STREAM_GAS}),
}

# Parallel cost streams for each energy stream.
_ENERGY_STREAM_TO_COST_STREAM: dict[str, str] = {
    STREAM_CONSUMPTION: STREAM_CONSUMPTION_COST,
    STREAM_INJECTION: STREAM_INJECTION_COST,
    STREAM_GAS: STREAM_GAS_COST,
}

_ENERGY_STREAMS: tuple[str, ...] = (STREAM_CONSUMPTION, STREAM_INJECTION, STREAM_GAS)


def streams_for_energy_types(
    energy_types: list[str] | tuple[str, ...] | None,
    *,
    include_costs: bool = False,
) -> frozenset[str]:
    """
    Return the set of internal streams matching a list of energy-type selectors.

    ``None`` or an empty list expands to all energy streams (auto mode).
    Unknown values are silently ignored so a future ENGIE-side addition
    (e.g. district heating) does not break older service calls.
    When ``include_costs`` is true, the matching cost stream is included
    alongside each resolved energy stream.
    """
    if not energy_types:
        base = frozenset(_ENERGY_STREAMS)
    else:
        result: set[str] = set()
        for value in energy_types:
            result |= _ENERGY_TYPE_TO_STREAMS.get(value, frozenset())
        base = frozenset(result) if result else frozenset(_ENERGY_STREAMS)

    if not include_costs:
        return base
    extra: set[str] = {
        _ENERGY_STREAM_TO_COST_STREAM[s]
        for s in base
        if s in _ENERGY_STREAM_TO_COST_STREAM
    }
    return base | frozenset(extra)


@dataclass(frozen=True, slots=True)
class _StreamSpec:
    """Where in the ENGIE payload each stream's hourly value lives."""

    # Full path from the item root (not from item["energy"]) to the leaf number.
    item_path: tuple[str, ...]
    display_name: str
    unit_of_measurement: str
    unit_class: str | None


_STREAM_SPECS: dict[str, _StreamSpec] = {
    STREAM_CONSUMPTION: _StreamSpec(
        item_path=("energy", "electricity", "offtake", "kWhSum"),
        display_name="electricity consumption",
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        unit_class=EnergyConverter.UNIT_CLASS,
    ),
    STREAM_INJECTION: _StreamSpec(
        item_path=("energy", "electricity", "injection", "kWhSum"),
        display_name="electricity injection",
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        unit_class=EnergyConverter.UNIT_CLASS,
    ),
    # ENGIE reports gas in kWh directly (energy-equivalent), so all three
    # energy streams share the same unit class and no m3-to-kWh conversion runs.
    STREAM_GAS: _StreamSpec(
        item_path=("energy", "gas", "kWh"),
        display_name="gas consumption",
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        unit_class=EnergyConverter.UNIT_CLASS,
    ),
    STREAM_CONSUMPTION_COST: _StreamSpec(
        item_path=("costs", "electricity", "offtake", "amountSum"),
        display_name="electricity consumption cost",
        unit_of_measurement=_UNIT_EUR,
        unit_class=None,
    ),
    STREAM_INJECTION_COST: _StreamSpec(
        item_path=("costs", "electricity", "injection", "amountSum"),
        display_name="electricity injection compensation",
        unit_of_measurement=_UNIT_EUR,
        unit_class=None,
    ),
    # costs.gas is a bare number, not a nested object (unlike costs.electricity).
    STREAM_GAS_COST: _StreamSpec(
        item_path=("costs", "gas"),
        display_name="gas consumption cost",
        unit_of_measurement=_UNIT_EUR,
        unit_class=None,
    ),
}


def statistic_id(business_agreement_number: str, stream: str) -> str:
    """Return the external statistic id for a given BAN and stream."""
    ban = business_agreement_number.replace(" ", "").replace("-", "_")
    return f"{DOMAIN}:{ban}_{stream}"


def _stream_division(stream: str) -> str:
    """Map an internal stream key to its ENGIE contract ``division`` value."""
    if stream in (STREAM_GAS, STREAM_GAS_COST):
        return "GAS"
    return "ELECTRICITY"


def _wanted_divisions(streams: frozenset[str]) -> set[str]:
    """Map internal stream keys to ENGIE contract ``division`` values."""
    return {_stream_division(s) for s in streams}


def earliest_contract_start_date(
    contracts_payload: dict[str, Any] | None,
    streams: frozenset[str],
) -> date | None:
    """
    Return the earliest ``legalContractStartDate`` across contracts.

    Considers every contract whose ``division`` matches the requested
    ``streams``, active and inactive alike. ENGIE retains hourly usage
    data across contract renewals and supplier switches, so the
    earliest known contract start on a BAN is the true lower bound
    of what we can pull. ``legalContractStartDate`` is preferred;
    falls back to ``startDate`` if only that is populated. Returns
    ``None`` when no matching contract carries a parseable date, so
    the caller can fall back to a fixed default.
    """
    if not isinstance(contracts_payload, dict):
        return None
    items = contracts_payload.get("items")
    if not isinstance(items, list):
        return None
    wanted = _wanted_divisions(streams)
    if not wanted:
        return None
    starts: list[date] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Include inactive/terminated contracts too: ENGIE keeps hourly
        # usage data across contract renewals and supplier switches, so
        # the earliest known contract start on a BAN is a better lower
        # bound than the currently-active contract's start.
        if item.get("division") not in wanted:
            continue
        raw = item.get("legalContractStartDate") or item.get("startDate")
        if not isinstance(raw, str):
            continue
        try:
            starts.append(date.fromisoformat(raw))
        except ValueError:
            continue
    earliest = min(starts) if starts else None
    LOGGER.debug(
        "earliest_contract_start_date: %d contract(s) for division(s) %s -> %s",
        len(starts),
        sorted(wanted),
        earliest.isoformat() if earliest is not None else "none",
    )
    return earliest


def _metadata(
    business_agreement_number: str,
    stream: str,
    device_name: str,
) -> StatisticMetaData:
    spec = _STREAM_SPECS[stream]
    # ``StatisticMetaData`` has no device-linkage field, so external
    # statistics can't inherit a device subtitle the way sensor entities
    # can. The convention across peer utility integrations (opower,
    # elvia, suez_water, mill) is to fold the disambiguating context
    # into the ``name`` itself. Format: primary descriptor, then the
    # consumption address, so multi-BAN users can scan a list in the
    # Energy dashboard source picker.
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"Historical {spec.display_name} - {device_name}",
        source=DOMAIN,
        statistic_id=statistic_id(business_agreement_number, stream),
        unit_class=spec.unit_class,
        unit_of_measurement=spec.unit_of_measurement,
    )


def _dig(payload: dict[str, Any] | None, path: tuple[str, ...]) -> float:
    """Walk ``path`` through nested dicts, returning 0.0 on any miss."""
    node: Any = payload
    for key in path:
        if not isinstance(node, dict):
            return 0.0
        node = node.get(key)
    try:
        return float(node) if node is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def usage_items_to_statistics(
    items: list[dict[str, Any]],
    initial_sums: dict[str, float],
    last_stats_time_utc: datetime | None,
) -> dict[str, list[StatisticData]]:
    """
    Convert ENGIE usage items to per-stream hour-aligned StatisticData.

    - Rows whose ``end`` is in the future are skipped so an in-progress
      or simulated hour never lands in permanent statistics. ENGIE also
      marks rows from expired contracts as ``partialData: true`` even
      though the values are final, so a plain ``partialData`` filter
      would drop real historical data. ``end > now`` catches only the
      truly not-yet-finalised rows.
    - Rows at or before ``last_stats_time_utc`` are skipped so re-runs
      don't double-count. ENGIE bucket starts equal the last recorded
      start on the boundary hour, so ``<=`` (not ``<``) is correct.
    - Sums are running cumulative totals seeded from ``initial_sums``;
      HA's Energy dashboard reads the ``sum`` column, not per-bucket
      ``state``, so the running total is what matters.
    """
    sums: dict[str, float] = {
        stream: initial_sums.get(stream, 0.0) for stream in _STREAMS
    }
    result: dict[str, list[StatisticData]] = {stream: [] for stream in _STREAMS}
    now_utc = dt_util.utcnow()
    malformed = 0

    for item in items:
        if not isinstance(item, dict):
            malformed += 1
            continue
        end_str = item.get("end")
        if isinstance(end_str, str):
            try:
                if dt_util.as_utc(datetime.fromisoformat(end_str)) > now_utc:
                    continue
            except ValueError:
                malformed += 1
                continue
        start_str = item.get("start")
        if not isinstance(start_str, str):
            malformed += 1
            continue
        try:
            start_local = datetime.fromisoformat(start_str)
        except ValueError:
            malformed += 1
            continue
        start_utc = dt_util.as_utc(start_local)
        if last_stats_time_utc is not None and start_utc <= last_stats_time_utc:
            continue
        for stream, spec in _STREAM_SPECS.items():
            delta = _dig(item, spec.item_path)
            sums[stream] += delta
            result[stream].append(
                StatisticData(start=start_utc, state=delta, sum=sums[stream])
            )
    if malformed:
        LOGGER.debug(
            "Skipped %d malformed row(s) in this chunk (missing/unparseable "
            "start or end timestamp)",
            malformed,
        )
    return result


async def _last_stats(
    hass: HomeAssistant,
    business_agreement_number: str,
    streams: frozenset[str],
) -> dict[str, dict[str, float | int]]:
    """Return ``{stream: {"start": ts, "sum": s}}`` for each requested stream."""
    out: dict[str, dict[str, float | int]] = {}
    recorder = get_instance(hass)
    for stream in _STREAMS:
        if stream not in streams:
            continue
        stat_id = statistic_id(business_agreement_number, stream)
        rows = await recorder.async_add_executor_job(
            get_last_statistics,
            hass,
            1,
            stat_id,
            True,  # noqa: FBT003 - positional signature imposed by get_last_statistics
            {"sum"},
        )
        entries = rows.get(stat_id) if rows else None
        if entries:
            out[stream] = entries[0]
    return out


async def _sums_before(
    hass: HomeAssistant,
    business_agreement_number: str,
    streams: frozenset[str],
    before_utc: datetime,
) -> dict[str, float]:
    """
    Return the last recorded cumulative sum strictly before ``before_utc``.

    Used to seed ``running_sums`` for an explicit re-import whose window
    starts earlier than the newest recorded statistic. Seeding from the
    newest lifetime row (as auto mode does, and as explicit mode used to
    do) makes the rows rewritten inside the window jump straight to the
    lifetime total, overshooting the untouched rows that immediately
    follow the window; HA then reads that drop back down as a meter
    reset. Seeding from the row immediately preceding the window keeps
    the boundary monotonic instead.

    Streams with no statistic recorded before ``before_utc`` (the window
    predates all existing data for that stream) default to 0.0.
    """
    out: dict[str, float] = {}
    recorder = get_instance(hass)
    epoch = dt_util.utc_from_timestamp(0)
    for stream in _STREAMS:
        if stream not in streams:
            continue
        stat_id = statistic_id(business_agreement_number, stream)
        rows = await recorder.async_add_executor_job(
            statistics_during_period,
            hass,
            epoch,
            before_utc,
            {stat_id},
            "hour",
            None,
            {"sum"},
        )
        entries = rows.get(stat_id) if rows else None
        if entries:
            out[stream] = float(entries[-1].get("sum") or 0.0)
    return out


async def async_import_usage_history(  # noqa: PLR0912, PLR0913, PLR0915 - orchestrator params + branches are all irreducible
    hass: HomeAssistant,
    client: EngieBeApiClient,
    subentry: ConfigSubentry,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    streams: frozenset[str] | None = None,
    contracts_payload: dict[str, Any] | None = None,
) -> int:
    """
    Import historical hourly usage for one business agreement.

    Auto mode (no ``start_date``/``end_date``): first import walks back to
    the earliest ``legalContractStartDate`` across active and inactive
    contracts returned by the ENGIE energy-contracts endpoint. Falls back to a
    ``HISTORY_BACKFILL_YEARS``-year window only if that endpoint fails or
    returns no usable date. Subsequent runs only fetch the delta since
    the last recorded statistic.

    Explicit mode (any date supplied): imports exactly the requested
    window. The last-stats cutoff is bypassed so re-imports overwrite
    (statistic_id, start) collisions in place. When ``start_date`` is
    given, cumulative ``sum`` values seed from the row immediately
    preceding the window (not the lifetime-newest row) so a re-import of
    an older window stays monotonic against the untouched rows that
    follow it. This does not rewrite that untouched tail, so if the
    re-imported deltas differ substantially from the original ones, the
    tail's sums may still understate or overstate the true lifetime
    total from that point on; only the window itself and the boundary
    into it are guaranteed correct.

    Chunks the fetch by ``HISTORY_CHUNK_DAYS`` and **persists each chunk
    immediately** rather than accumulating and writing at the end.  A
    failure partway through leaves earlier chunks safely in the
    statistics table, so a follow-up press resumes from
    ``_last_stats`` without redoing successful work.  Returns the total
    number of hourly rows written.

    ``contracts_payload`` may be passed in by callers who have already
    fetched contracts for this BAN (with ``include_inactive=True``).
    When provided, the orchestrator skips its own fetch. When ``None``,
    the orchestrator fetches fresh (fail-open on network error).
    """
    business_agreement_number = subentry.data[CONF_BUSINESS_AGREEMENT_NUMBER]
    masked_ban = business_agreement_number[-4:]
    device_name = subentry.title or f"BAN ***{masked_ban}"
    active_streams = streams or frozenset(_ENERGY_STREAMS)
    LOGGER.info(
        "Starting historical usage import for BAN ***%s (streams=%s)",
        masked_ban,
        sorted(active_streams),
    )
    LOGGER.debug(
        "BAN ***%s: active_streams resolved to %s",
        masked_ban,
        sorted(active_streams),
    )

    # Reuse a caller-provided contracts payload when available (setup and
    # service-action call sites already have this cached on
    # ``EngieBeSubentryData.energy_contracts_payload``). Fall back to a
    # fresh fetch when nothing was passed in.
    # include_inactive=True so a user who switched gas providers but kept ENGIE
    # electricity still gets the gas history imported for the inactive contract.
    if contracts_payload is None:
        try:
            contracts_payload = await client.async_get_energy_contracts(
                business_agreement_number,
                include_inactive=True,
            )
        except EngieBeApiClientError as err:
            LOGGER.debug(
                "BAN ***%s: could not fetch energy contracts (%s); "
                "skipping division filter (fail-open)",
                masked_ban,
                err,
            )

    # Filter active_streams to only those whose division has at least one
    # contract on this BAN (active or inactive). This prevents writing
    # all-zero statistic streams for divisions the BAN has never had.
    # Fail-open: if the fetch failed or the payload is malformed, skip
    # filtering so an ENGIE outage does not kill an import.
    contracted_divisions: set[str] | None = None
    if isinstance(contracts_payload, dict):
        items_list = contracts_payload.get("items")
        if isinstance(items_list, list):
            contracted_divisions = {
                item["division"]
                for item in items_list
                if isinstance(item, dict) and "division" in item
            }

    if contracted_divisions is None:
        LOGGER.debug(
            "BAN ***%s: division filter skipped "
            "(contracts payload unavailable or malformed)",
            masked_ban,
        )
    else:
        filtered = frozenset(
            s for s in active_streams if _stream_division(s) in contracted_divisions
        )
        if filtered != active_streams:
            dropped = sorted(active_streams - filtered)
            LOGGER.debug(
                "BAN ***%s: dropping streams with no contract on this BAN: %s",
                masked_ban,
                dropped,
            )
            active_streams = filtered
        if not active_streams:
            LOGGER.debug(
                "BAN ***%s: no contracted streams remain after division filter; "
                "nothing to import",
                masked_ban,
            )
            return 0

    last = await _last_stats(hass, business_agreement_number, active_streams)

    # Running sums are threaded across chunks by the orchestrator so per-
    # chunk writes are still monotonically correct.  Seeded from the
    # newest existing row per stream so a resumed import continues the
    # lifetime total rather than starting over.
    running_sums: dict[str, float] = {
        stream: float(entry.get("sum") or 0.0) for stream, entry in last.items()
    }
    last_stats_time_utc: datetime | None = None
    if last:
        newest = max(float(entry["start"]) for entry in last.values())
        last_stats_time_utc = dt_util.utc_from_timestamp(newest)
        LOGGER.debug(
            "BAN ***%s: resuming from last_stats at %s, running_sums seed=%s",
            masked_ban,
            last_stats_time_utc.isoformat(),
            {s: round(v, 4) for s, v in running_sums.items()},
        )

    if start_date is not None:
        # Explicit re-import with an explicit start: reseed running_sums
        # from the row immediately preceding the window instead of the
        # lifetime-newest row above. If the window lies at or after the
        # newest existing row, this resolves to the same value (no-op);
        # if it lies before (a re-import patching an older gap), this
        # keeps the sum series monotonic across the window boundary.
        # Streams with nothing recorded before the window default to 0.0,
        # matching a first-ever import of that range.
        window_start_utc = dt_util.as_utc(dt_util.start_of_local_day(start_date))
        seeded = await _sums_before(
            hass, business_agreement_number, active_streams, window_start_utc
        )
        for stream in active_streams:
            running_sums[stream] = seeded.get(stream, 0.0)
        LOGGER.debug(
            "BAN ***%s: explicit re-import starting %s; running_sums reseeded "
            "from row preceding window=%s",
            masked_ban,
            window_start_utc.isoformat(),
            {s: round(v, 4) for s, v in running_sums.items()},
        )

    # Query in local (Brussels) civil dates because ENGIE's startDate /
    # endDate params are civil-day boundaries; the response items carry
    # their own explicit +02:00 / +01:00 offsets so DST is handled
    # correctly downstream in ``usage_items_to_statistics``.
    now_local = dt_util.now()
    explicit_window = start_date is not None or end_date is not None
    if start_date is not None:
        window_start_date = start_date
        LOGGER.debug(
            "BAN ***%s: explicit start_date %s used as window start",
            masked_ban,
            window_start_date.isoformat(),
        )
    elif last_stats_time_utc is None:
        # First import: prefer the earliest contract start date so we
        # don't waste API calls on pre-contract empty windows. Fall back
        # to a fixed HISTORY_BACKFILL_YEARS-year default only when the
        # contracts endpoint failed or returned nothing usable.
        # Reuse the already-fetched contracts_payload; no second API call.
        contract_start = earliest_contract_start_date(contracts_payload, active_streams)
        if contract_start is not None:
            window_start_date = contract_start
            LOGGER.debug(
                "BAN ***%s: using contract start %s as import window start",
                masked_ban,
                contract_start.isoformat(),
            )
        else:
            window_start_date = (
                now_local - timedelta(days=365 * HISTORY_BACKFILL_YEARS)
            ).date()
            LOGGER.debug(
                "BAN ***%s: no contract start found; using %d-year fallback start %s",
                masked_ban,
                HISTORY_BACKFILL_YEARS,
                window_start_date.isoformat(),
            )
    else:
        # Round down to the day containing the last recorded bucket; the
        # ``<=`` guard in the pure converter drops the already-imported
        # rows without another API round trip.
        window_start_date = dt_util.as_local(
            last_stats_time_utc + timedelta(hours=1)
        ).date()
        LOGGER.debug(
            "BAN ***%s: resuming from last recorded hour; window start set to %s",
            masked_ban,
            window_start_date.isoformat(),
        )

    # endDate is exclusive; when auto, include tomorrow so today's
    # completed hours land regardless of the caller's civil day.
    window_end_date = (
        end_date if end_date is not None else (now_local + timedelta(days=1)).date()
    )
    LOGGER.debug(
        "BAN ***%s: import window %s..%s (explicit_window=%s)",
        masked_ban,
        window_start_date.isoformat(),
        window_end_date.isoformat(),
        explicit_window,
    )

    # In explicit mode, let ENGIE's rows overwrite (statistic_id, start)
    # collisions instead of dropping them at the cutoff.  In auto mode,
    # the cutoff only matters for the first chunk (which may overlap the
    # last already-recorded hour); later chunks are all strictly newer,
    # so re-applying the same cutoff is harmless.
    cutoff = None if explicit_window else last_stats_time_utc

    total = 0
    cursor_date = window_start_date
    chunk_days = timedelta(days=HISTORY_CHUNK_DAYS)
    while cursor_date < window_end_date:
        chunk_end_date = min(cursor_date + chunk_days, window_end_date)
        response = await client.async_get_usage_details(
            business_agreement_number=business_agreement_number,
            start_date=cursor_date,
            end_date=chunk_end_date,
            granularity="HOURLY",
            # ENGIE scopes the response to the currently-active contract
            # unless includeSimulation is true, in which case it serves
            # data across all past contracts on this BAN. Any projected
            # future rows carry partialData:true and get dropped by the
            # converter.
            include_simulation=True,
        )
        items = response.get("items") if isinstance(response, dict) else None
        if not isinstance(items, list):
            items = []

        per_stream = usage_items_to_statistics(items, running_sums, cutoff)
        for stream, rows in per_stream.items():
            if stream not in active_streams or not rows:
                continue
            async_add_external_statistics(
                hass,
                _metadata(business_agreement_number, stream, device_name),
                rows,
            )
            running_sums[stream] = float(rows[-1]["sum"])
            total += len(rows)
        LOGGER.debug(
            "Imported chunk %s..%s for BAN ***%s (running total: %d rows)",
            cursor_date.isoformat(),
            chunk_end_date.isoformat(),
            masked_ban,
            total,
        )
        cursor_date = chunk_end_date

    LOGGER.info(
        "Imported %d hourly statistic rows for BAN ***%s (window %s..%s)",
        total,
        masked_ban,
        window_start_date.isoformat(),
        window_end_date.isoformat(),
    )
    return total


async def async_clear_usage_history(
    hass: HomeAssistant,
    business_agreement_number: str,
    streams: frozenset[str] | None = None,
) -> list[str]:
    """
    Delete external statistic streams for one BAN.

    ``streams`` defaults to the three energy streams (consumption, injection,
    gas). Pass cost streams explicitly to also clear those. The next import
    for the same BAN and cleared streams will do a full backfill again.
    Returns the list of cleared statistic IDs.
    """
    active_streams = streams or frozenset(_ENERGY_STREAMS)
    stat_ids = [
        statistic_id(business_agreement_number, s)
        for s in _STREAMS
        if s in active_streams
    ]
    if not stat_ids:
        return []
    LOGGER.debug(
        "async_clear_usage_history: clearing %d statistic_id(s): %s",
        len(stat_ids),
        stat_ids,
    )
    recorder = get_instance(hass)
    # ``clear_statistics`` mutates the statistics_meta table and must run
    # on the recorder's own thread; the recorder asserts this and raises
    # ``RuntimeError: Detected unsafe call not in recorder thread`` when
    # invoked via ``async_add_executor_job``. Queue a ``ClearStatisticsTask``
    # so the recorder itself dequeues it on the correct thread.
    recorder.queue_task(ClearStatisticsTask(on_done=None, statistic_ids=stat_ids))
    LOGGER.info(
        "Cleared %d statistic streams for BAN ***%s",
        len(stat_ids),
        business_agreement_number[-4:],
    )
    return stat_ids
