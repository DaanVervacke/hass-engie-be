"""Historical usage import into Home Assistant long-term statistics."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    StatisticsRow,
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.components.recorder.tasks import ClearStatisticsTask
from homeassistant.const import UnitOfEnergy
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.recorder import get_instance
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .api import EngieBeApiClientError
from .const import (
    CLEAR_STATISTICS_TIMEOUT_SECONDS,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    DOMAIN,
    ENERGY_TYPE_CONSUMPTION,
    ENERGY_TYPE_GAS,
    ENERGY_TYPE_INJECTION,
    HISTORY_BACKFILL_YEARS,
    HISTORY_CHUNK_DAYS,
    HISTORY_HEAL_LOOKBACK_DAYS,
    LOGGER,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from .api import EngieBeApiClient

_UNIT_EUR = "EUR"

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

_ENERGY_TYPE_TO_STREAMS: dict[str, frozenset[str]] = {
    ENERGY_TYPE_CONSUMPTION: frozenset({STREAM_CONSUMPTION}),
    ENERGY_TYPE_INJECTION: frozenset({STREAM_INJECTION}),
    ENERGY_TYPE_GAS: frozenset({STREAM_GAS}),
}

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
    Return the internal streams for a list of energy-type selectors.

    ``None`` or empty expands to all energy streams. Unknown values are
    ignored so a future ENGIE-side addition never breaks older calls.
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
    # ENGIE reports gas in kWh directly, no m3-to-kWh conversion runs.
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
    # costs.gas is a bare number, not a nested object like costs.electricity.
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


def _filter_by_contract(
    streams: frozenset[str],
    contracts_payload: dict[str, Any] | None,
) -> frozenset[str]:
    """Narrow ``streams`` to divisions with a contract on this BAN (fail-open)."""
    if not isinstance(contracts_payload, dict):
        return streams
    items_list = contracts_payload.get("items")
    if not isinstance(items_list, list):
        return streams
    contracted_divisions = {
        item["division"]
        for item in items_list
        if isinstance(item, dict) and "division" in item
    }
    return frozenset(s for s in streams if _stream_division(s) in contracted_divisions)


def earliest_contract_start_date(
    contracts_payload: dict[str, Any] | None,
    streams: frozenset[str],
) -> date | None:
    """
    Return the earliest ``legalContractStartDate`` across matching contracts.

    Considers active and inactive alike because ENGIE keeps usage data
    across renewals. Falls back to ``startDate`` when the legal field is
    missing. Returns ``None`` when no parseable date is found.
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
    # StatisticMetaData has no device-linkage field, so the address is
    # folded into ``name`` for multi-BAN users, matching peer utilities.
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"Historical {spec.display_name} - {device_name}",
        source=DOMAIN,
        statistic_id=statistic_id(business_agreement_number, stream),
        unit_class=spec.unit_class,
        unit_of_measurement=spec.unit_of_measurement,
    )


def _dig(payload: dict[str, Any] | None, path: tuple[str, ...]) -> float | None:
    """
    Walk ``path`` and return the leaf as float, or ``None`` when unresolved.

    Distinguishes ENGIE's placeholder rows from genuine zero-value hours.
    """
    node: Any = payload
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    try:
        return float(node) if node is not None else None
    except TypeError, ValueError:
        return None


def usage_items_to_statistics(
    items: list[dict[str, Any]],
    initial_sums: dict[str, float],
    cutoffs: dict[str, datetime | None],
) -> dict[str, list[StatisticData]]:
    """
    Convert ENGIE usage items to per-stream hour-aligned StatisticData.

    Skips rows whose ``end`` is in the future (in-progress / simulated).
    Skips a stream on missing/unparseable value so the resume cutoff
    never advances past unpublished data. Each stream keeps its own
    cutoff, compared with ``<=`` because ENGIE bucket starts equal the
    last recorded start on the boundary hour. Sums are running totals
    seeded from ``initial_sums``.
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
        for stream, spec in _STREAM_SPECS.items():
            stream_cutoff = cutoffs.get(stream)
            if stream_cutoff is not None and start_utc <= stream_cutoff:
                continue
            delta = _dig(item, spec.item_path)
            if delta is None:
                continue
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


def _sum_or_zero(row: StatisticsRow) -> float:
    """Return a statistics row's cumulative sum, or ``0.0`` when the value is null."""
    value = row.get("sum")
    return value if value is not None else 0.0


async def _last_stats(
    hass: HomeAssistant,
    business_agreement_number: str,
    streams: frozenset[str],
) -> dict[str, StatisticsRow]:
    """Return the newest recorder row per stream, omitting streams with none."""
    out: dict[str, StatisticsRow] = {}
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

    Seeds ``running_sums`` for a re-import whose window starts earlier
    than the newest recorded statistic so the boundary stays monotonic.
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
            out[stream] = _sum_or_zero(entries[-1])
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

    Auto mode walks back to the earliest contract start, then heals the
    last ``HISTORY_HEAL_LOOKBACK_DAYS`` on every run. Explicit mode
    imports exactly the requested window, bypassing the resume cutoff
    and reseeding sums from the row preceding the window. Chunks are
    persisted immediately so a failure resumes cleanly. Returns rows
    written.
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

    # include_inactive=True so history from prior terminated contracts is imported.
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

    # Drop streams whose division has no contract on this BAN.
    filtered = _filter_by_contract(active_streams, contracts_payload)
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

    # Seed running sums from the newest existing row per stream so a
    # resumed import continues the lifetime total.
    running_sums: dict[str, float] = {
        stream: _sum_or_zero(entry) for stream, entry in last.items()
    }

    # Per-stream resume points: absent means never imported.
    cutoffs: dict[str, datetime | None] = {
        stream: dt_util.utc_from_timestamp(float(entry["start"]))
        for stream, entry in last.items()
    }
    for stream in active_streams:
        cutoffs.setdefault(stream, None)

    any_stream_missing = any(cutoffs[stream] is None for stream in active_streams)

    if last:
        LOGGER.debug(
            "BAN ***%s: per-stream resume cutoffs=%s, running_sums seed=%s",
            masked_ban,
            {
                s: (
                    cutoff_value.isoformat()
                    if (cutoff_value := cutoffs[s]) is not None
                    else None
                )
                for s in active_streams
            },
            {s: round(v, 4) for s, v in running_sums.items()},
        )

    # ENGIE startDate/endDate are civil-day boundaries. Response rows
    # carry offsets so DST is handled downstream.
    now_local = dt_util.now()
    explicit_window = start_date is not None or end_date is not None
    # Hoisted so ``heal_active`` below can tell a heal-widened window
    # apart from a genuine resume.
    resume_start_date: date | None = None
    if start_date is not None:
        window_start_date = start_date
        LOGGER.debug(
            "BAN ***%s: explicit start_date %s used as window start",
            masked_ban,
            window_start_date.isoformat(),
        )
    elif any_stream_missing:
        # Widen the window to full history for any stream never imported.
        # Caught-up streams keep their own cutoff and re-skip their rows.
        contract_start = earliest_contract_start_date(contracts_payload, active_streams)
        if contract_start is not None:
            window_start_date = contract_start
            LOGGER.debug(
                "BAN ***%s: using contract start %s as import window start "
                "(at least one requested stream has never been imported)",
                masked_ban,
                contract_start.isoformat(),
            )
        else:
            window_start_date = (
                now_local - timedelta(days=365 * HISTORY_BACKFILL_YEARS)
            ).date()
            LOGGER.debug(
                "BAN ***%s: no contract start found; using %d-year fallback "
                "start %s (at least one requested stream has never been "
                "imported)",
                masked_ban,
                HISTORY_BACKFILL_YEARS,
                window_start_date.isoformat(),
            )
    else:
        # Resume from the EARLIEST per-stream cutoff so no gap is skipped.
        known_cutoffs = [
            cutoffs[stream] for stream in active_streams if cutoffs[stream] is not None
        ]
        earliest_cutoff = min(cast("list[datetime]", known_cutoffs))
        resume_start_date = dt_util.as_local(
            earliest_cutoff + timedelta(hours=1)
        ).date()
        # Pull the window back to the rolling heal floor so caught-up
        # accounts still overwrite the last few days on every run.
        heal_floor_date = (
            now_local - timedelta(days=HISTORY_HEAL_LOOKBACK_DAYS)
        ).date()
        window_start_date = min(resume_start_date, heal_floor_date)
        if window_start_date < resume_start_date:
            LOGGER.debug(
                "BAN ***%s: rolling-heal re-import from %s (resume point was %s, "
                "re-fetching the last %d days to overwrite stale or late data)",
                masked_ban,
                window_start_date.isoformat(),
                resume_start_date.isoformat(),
                HISTORY_HEAL_LOOKBACK_DAYS,
            )
        else:
            LOGGER.debug(
                "BAN ***%s: resuming from the earliest per-stream cutoff %s "
                "(all requested streams have prior data)",
                masked_ban,
                earliest_cutoff.isoformat(),
            )

    # endDate is exclusive. When auto, include tomorrow so today's rows land.
    window_end_date = (
        end_date if end_date is not None else (now_local + timedelta(days=1)).date()
    )

    # Reject only when the user-supplied start/end are contradictory.
    # end_date-only calls compute their start from the resume logic, so
    # an empty window there is legitimate, not a user error.
    if start_date is not None and window_start_date >= window_end_date:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="import_window_empty",
            translation_placeholders={
                "start": window_start_date.isoformat(),
                "end": window_end_date.isoformat(),
            },
        )

    LOGGER.debug(
        "BAN ***%s: import window %s..%s (explicit_window=%s)",
        masked_ban,
        window_start_date.isoformat(),
        window_end_date.isoformat(),
        explicit_window,
    )

    # overwrite_in_place pairs the running-sum reseed with disabling the
    # per-stream cutoff. Both effects MUST stay paired on this one flag
    # or cumulative sums double-count. Deliberately not keyed on
    # ``explicit_window``: an end_date-only call must trigger neither
    # effect or the boundary day double-counts.
    heal_active = (
        start_date is None
        and not any_stream_missing
        and resume_start_date is not None
        and window_start_date < resume_start_date
    )
    overwrite_in_place = start_date is not None or heal_active

    if overwrite_in_place:
        # Reseed from the row preceding the window so the sum series
        # stays monotonic across the window boundary.
        window_start_utc = dt_util.as_utc(dt_util.start_of_local_day(window_start_date))
        seeded = await _sums_before(
            hass, business_agreement_number, active_streams, window_start_utc
        )
        for stream in active_streams:
            running_sums[stream] = seeded.get(stream, 0.0)
        LOGGER.debug(
            "BAN ***%s: overwrite-in-place from %s; running_sums reseeded from "
            "the row preceding the window=%s",
            masked_ban,
            window_start_utc.isoformat(),
            {s: round(v, 4) for s, v in running_sums.items()},
        )

    cutoffs_for_converter: dict[str, datetime | None] = (
        dict.fromkeys(active_streams) if overwrite_in_place else cutoffs
    )

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
            # includeSimulation=True to get rows from past contracts.
            # Projected rows are dropped by the converter's future filter.
            include_simulation=True,
        )
        items = response.get("items") if isinstance(response, dict) else None
        if not isinstance(items, list):
            items = []

        per_stream = usage_items_to_statistics(
            items, running_sums, cutoffs_for_converter
        )
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
    Delete external statistic streams for one BAN and await the delete.

    Raises ``HomeAssistantError`` when the recorder does not signal
    completion within ``CLEAR_STATISTICS_TIMEOUT_SECONDS``.
    """
    active_streams = streams or frozenset(_ENERGY_STREAMS)
    stat_ids = [
        statistic_id(business_agreement_number, s)
        for s in _STREAMS
        if s in active_streams
    ]
    if not stat_ids:
        return []
    masked_ban = business_agreement_number[-4:]
    LOGGER.debug(
        "async_clear_usage_history: clearing %d statistic_id(s): %s",
        len(stat_ids),
        stat_ids,
    )
    recorder = get_instance(hass)
    # ``clear_statistics`` mutates the statistics_meta table and must run
    # on the recorder's own thread. The recorder asserts this and raises
    # ``RuntimeError: Detected unsafe call not in recorder thread`` when
    # invoked via ``async_add_executor_job``. Queue a ``ClearStatisticsTask``
    # so the recorder itself dequeues it on the correct thread, and bridge
    # its ``on_done`` callback (fired on the recorder thread after the
    # delete) back to this event loop so we can await completion.
    future: asyncio.Future[None] = hass.loop.create_future()

    def _on_done() -> None:
        if not future.done():
            hass.loop.call_soon_threadsafe(future.set_result, None)

    recorder.queue_task(ClearStatisticsTask(on_done=_on_done, statistic_ids=stat_ids))
    try:
        async with asyncio.timeout(CLEAR_STATISTICS_TIMEOUT_SECONDS):
            await future
    except TimeoutError as err:
        # ``on_done`` is skipped when ``clear_statistics`` raises on the
        # recorder thread, so a hang here means the delete failed or the
        # recorder queue is wedged. Surface it rather than block forever.
        LOGGER.error(
            "Clearing statistics for BAN ***%s did not complete within %ds",
            masked_ban,
            CLEAR_STATISTICS_TIMEOUT_SECONDS,
        )
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="clear_history_timeout",
            translation_placeholders={"ban": masked_ban},
        ) from err
    LOGGER.info(
        "Cleared %d statistic streams for BAN ***%s",
        len(stat_ids),
        masked_ban,
    )
    return stat_ids
