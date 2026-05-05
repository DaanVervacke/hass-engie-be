"""Custom integration to integrate ENGIE Belgium with Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from ._relations import (
    RELATIONS_BACKFILLABLE_KEYS,
    find_account_for_customer_number,
    subentry_title,
)
from .api import (
    EngieBeApiClient,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_BUSINESS_AGREEMENT_NUMBER,
    CONF_CLIENT_ID,
    CONF_CUSTOMER_NUMBER,
    CONF_REFRESH_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    LOGGER,
    SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
    TOKEN_REFRESH_INTERVAL_SECONDS,
)
from .coordinator import EngieBeDataUpdateCoordinator, EngieBeEpexCoordinator
from .data import EngieBeData, EngieBeSubentryData
from .diagnostics import _hash_ean
from .store import EngieBePeaksStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import EngieBeConfigEntry

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
]

_HOURS_TO_MINUTES = 60


async def async_migrate_entry(
    hass: HomeAssistant,
    config_entry: EngieBeConfigEntry,
) -> bool:
    """Migrate config entry to a new version."""
    if config_entry.version == 1:
        # v1 stored update_interval in hours; v2 stores it in minutes.
        old_interval = config_entry.options.get(CONF_UPDATE_INTERVAL)
        if old_interval is not None:
            new_options = {**config_entry.options}
            new_options[CONF_UPDATE_INTERVAL] = old_interval * _HOURS_TO_MINUTES
            hass.config_entries.async_update_entry(
                config_entry,
                options=new_options,
                version=2,
            )
        else:
            hass.config_entries.async_update_entry(config_entry, version=2)

        LOGGER.info(
            "Migrated config entry %s from version 1 to 2",
            config_entry.entry_id,
        )

    if config_entry.version == 2:  # noqa: PLR2004 - migration source version
        await _async_migrate_v2_to_v3(hass, config_entry)
        LOGGER.info(
            "Migrated config entry %s from version 2 to 3",
            config_entry.entry_id,
        )

    return True


def _async_promote_device_to_subentry(
    hass: HomeAssistant,
    device_id: str,
    *,
    entry_id: str,
    subentry_id: str,
    new_identifiers: set[tuple[str, str]] | None = None,
) -> None:
    """
    Move a v2-shaped device onto a v3 customer-account subentry.

    A v2 device row carries the bare ``(entry_id, None)`` config-entry
    link. The v3 device row must carry ``(entry_id, subentry_id)``
    instead, otherwise Home Assistant renders the device twice in the
    integration card: once under the parent entry's "no sub-item"
    group and once under the subentry's group.

    Home Assistant's ``async_update_device`` cannot atomically swap
    one for the other: ``add_config_subentry_id`` set-unions onto the
    existing entry, and combining ``add_*`` and ``remove_*`` in a
    single call makes the remove path read pre-add state and overwrite
    the add. The safe sequence is two calls: first add the new link
    (and rewrite identifiers), then drop the legacy ``None`` link.

    Before the legacy link is dropped any entity still attached to the
    device with ``config_subentry_id=None`` (the v2 login-scoped energy
    sensor and the auth binary sensor) is reparented onto a dedicated
    login device. Home Assistant's entity registry would otherwise
    auto-remove every such entity when the ``None`` link disappears
    from the device's ``config_entries_subentries``, taking entity-id
    customisations and statistics history with them.

    No-op when the legacy link is already absent, so this is idempotent
    and safe to run on already-healed devices.
    """
    device_reg = dr.async_get(hass)

    if new_identifiers is not None:
        device_reg.async_update_device(
            device_id,
            new_identifiers=new_identifiers,
            add_config_entry_id=entry_id,
            add_config_subentry_id=subentry_id,
        )
    else:
        device_reg.async_update_device(
            device_id,
            add_config_entry_id=entry_id,
            add_config_subentry_id=subentry_id,
        )

    device = device_reg.async_get(device_id)
    if device is None:
        return
    legacy_subentries = device.config_entries_subentries.get(entry_id, set())
    if None not in legacy_subentries:
        return

    _async_reparent_login_scoped_entities(
        hass,
        device_id=device_id,
        entry_id=entry_id,
    )

    device_reg.async_update_device(
        device_id,
        remove_config_entry_id=entry_id,
        remove_config_subentry_id=None,
    )


def _async_reparent_login_scoped_entities(
    hass: HomeAssistant,
    *,
    device_id: str,
    entry_id: str,
) -> None:
    """
    Move login-scoped entities off a customer-account device.

    Walks every entity registered against ``device_id`` and reparents
    those whose ``config_subentry_id`` is ``None`` onto a dedicated
    login device identified by ``(DOMAIN, f"login_{entry_id}")``,
    creating that device on demand. Subentry-scoped entities are left
    untouched.

    Used as a pre-step before stripping the legacy ``(entry_id, None)``
    link from a customer-account device: without it, Home Assistant's
    entity registry auto-removes every entity whose ``config_subentry_id``
    was in the device's old subentry set for the entry but not in the new
    one, which would silently delete the v2 energy + auth entities.
    """
    entity_reg = er.async_get(hass)
    orphans = [
        ent
        for ent in er.async_entries_for_device(
            entity_reg,
            device_id,
            include_disabled_entities=True,
        )
        if ent.config_subentry_id is None
    ]
    if not orphans:
        return

    device_reg = dr.async_get(hass)
    login_device = device_reg.async_get_or_create(
        config_entry_id=entry_id,
        identifiers={(DOMAIN, f"login_{entry_id}")},
        manufacturer="ENGIE Belgium",
        name="Account",
    )
    for ent in orphans:
        entity_reg.async_update_entity(
            ent.entity_id,
            device_id=login_device.id,
        )
        LOGGER.debug(
            "Reparented login-scoped entity %s from %s onto login device %s",
            ent.entity_id,
            device_id,
            login_device.id,
        )


async def _async_migrate_v2_to_v3(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Promote a v2 single-account entry to a v3 entry with one ``ConfigSubentry``.

    v2 stored ``customer_number`` directly on ``entry.data`` and held all
    entities under one ``(DOMAIN, entry_id)`` device. v3 introduces a
    ``ConfigSubentry`` per customer account: device identifiers move to
    ``(DOMAIN, subentry_id)`` and a subset of entity ``unique_id`` values
    gain a subentry-id segment so that descriptors that repeat across
    accounts (peak/EPEX/calendar) do not collide on a single login.

    Migration is best-effort but state-preserving:

    1. Read the v2 ``customer_number`` from ``entry.data``. If absent
       (corrupt entry), bump version and return; setup will fail loudly.
    2. Try a relations backfill for the optional display fields. Failures
       are tolerated; the coordinator's first refresh will retry.
    3. Create one ``ConfigSubentry`` of type
       ``customer_account``.
    4. Mutate the existing v2 device in place via ``new_identifiers`` so
       ``name_by_user``, ``area_id``, ``labels`` and history survive.
    5. Rename the 9 affected entity ``unique_id`` values via
       ``async_migrate_entries`` so the entity_id stays stable
       (preserving history, dashboards, automations).
    6. Rename the peaks-history store file on disk from
       ``engie_be.peaks_history.{entry_id}`` to
       ``engie_be.peaks_history.{subentry_id}``.
    7. Drop ``customer_number`` from ``entry.data`` and bump version.
    """
    customer_number = entry.data.get(CONF_CUSTOMER_NUMBER)
    if not customer_number:
        # Corrupt or already-partially-migrated entry: just bump version
        # so HA stops re-running this migration. Setup will fail loudly
        # if the user has no customer subentries to fall back on.
        hass.config_entries.async_update_entry(entry, version=3)
        LOGGER.warning(
            "Config entry %s missing customer_number during v2->v3 migration; "
            "bumping version without creating a subentry",
            entry.entry_id,
        )
        return

    # Build the subentry payload, optionally enriched with relations data.
    subentry_data: dict[str, Any] = {CONF_CUSTOMER_NUMBER: customer_number}
    relations_account = await _async_try_relations_backfill(
        hass,
        entry,
        customer_number,
    )
    if relations_account:
        for key in RELATIONS_BACKFILLABLE_KEYS:
            value = relations_account.get(key)
            if value:
                subentry_data[key] = value
        title = subentry_title(relations_account)
    else:
        title = customer_number

    # Reuse an existing subentry from a previously-failed migration attempt
    # so the v2->v3 path is idempotent. Match on unique_id (the customer
    # number), which is the stable identity for a customer-account subentry.
    existing_subentry = next(
        (
            s
            for s in entry.subentries.values()
            if s.subentry_type == SUBENTRY_TYPE_CUSTOMER_ACCOUNT
            and s.unique_id == customer_number
        ),
        None,
    )
    if existing_subentry is not None:
        subentry = existing_subentry
        LOGGER.debug(
            "Reusing existing subentry %s for customer %s during v2->v3 migration",
            subentry.subentry_id,
            customer_number,
        )
    else:
        subentry = ConfigSubentry(
            data=MappingProxyType(subentry_data),
            subentry_type=SUBENTRY_TYPE_CUSTOMER_ACCOUNT,
            title=title,
            unique_id=customer_number,
        )
        hass.config_entries.async_add_subentry(entry, subentry)

    # Reconcile the device registry with the new subentry. Migration may
    # be replayed if a previous attempt failed partway through, so the
    # logic here must be idempotent across four possible registry states:
    #
    # State A (fresh v2):       v2 device exists, v3 device does not.
    #                           Rename the v2 device's identifier in place
    #                           so user customisations (name_by_user,
    #                           area_id, labels) and history survive.
    #
    # State B (half-migrated):  Both devices exist. A previous run created
    #                           the v3 device via entity registration
    #                           before the v2 rename completed. Reparent
    #                           every v2 entity onto the v3 device and
    #                           delete the v2 device row.
    #
    # State C (already done):   Only the v3 device exists. No-op.
    #
    # State D (no devices):     Neither exists. No-op; the platform setup
    #                           will create the v3 device on first entity
    #                           registration.
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)
    v2_device = device_reg.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)},
    )
    v3_device = device_reg.async_get_device(
        identifiers={(DOMAIN, subentry.subentry_id)},
    )

    # Rewrite v2 unique_ids to the v3 subentry-scoped shape before touching
    # the device registry. ``_migrate_entity_unique_ids`` stamps
    # ``config_subentry_id=subentry_id`` on every renamed entity, so that
    # when the device-link swap below strips the legacy
    # ``(entry_id, None)`` link, Home Assistant's entity registry only
    # auto-removes entities whose ``config_subentry_id`` is still ``None``.
    # The only such entity is the auth binary sensor (login-scoped, kept
    # at the v2 unique_id shape on purpose), which the promote helper
    # reparents onto a dedicated login device first so nothing is lost.
    await _migrate_entity_unique_ids(hass, entry.entry_id, subentry.subentry_id)

    if v2_device is not None and v3_device is None:
        # State A
        _async_promote_device_to_subentry(
            hass,
            v2_device.id,
            entry_id=entry.entry_id,
            subentry_id=subentry.subentry_id,
            new_identifiers={(DOMAIN, subentry.subentry_id)},
        )
    elif v2_device is not None and v3_device is not None:
        # State B
        for entity_entry in er.async_entries_for_device(
            entity_reg,
            v2_device.id,
            include_disabled_entities=True,
        ):
            entity_reg.async_update_entity(
                entity_entry.entity_id,
                device_id=v3_device.id,
                config_subentry_id=subentry.subentry_id,
            )
        device_reg.async_remove_device(v2_device.id)
        LOGGER.debug(
            "Merged orphan v2 device %s into v3 device %s during migration",
            v2_device.id,
            v3_device.id,
        )

    # Rename the peaks-history store file on disk from the old per-entry
    # key to the new per-subentry key. Done via ``Store`` so the store
    # manager's cache stays consistent.
    await _async_rename_peaks_store(hass, entry.entry_id, subentry.subentry_id)

    # Drop the now-redundant top-level customer_number from entry.data and
    # bump the version. ``customer_number`` lives on the subentry from v3
    # onwards.
    new_data = {k: v for k, v in entry.data.items() if k != CONF_CUSTOMER_NUMBER}
    hass.config_entries.async_update_entry(entry, data=new_data, version=3)


async def _async_try_relations_backfill(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
    customer_number: str,
) -> dict[str, Any] | None:
    """
    Try to fetch the relations record for ``customer_number``.

    Returns the matching account dict or ``None``. Failures are swallowed
    and logged: the coordinator's first refresh retries this same backfill
    via the same shared helper, so a one-off migration-time failure is not
    fatal.
    """
    access_token = entry.data.get(CONF_ACCESS_TOKEN)
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    if not refresh_token:
        return None

    client = EngieBeApiClient(
        session=async_get_clientsession(hass),
        client_id=entry.data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
        access_token=access_token,
        refresh_token=refresh_token,
    )
    try:
        await client.async_refresh_token()
        relations = await client.async_get_customer_account_relations()
    except EngieBeApiClientError as err:
        LOGGER.debug(
            "Relations backfill skipped during v2->v3 migration of %s: %s",
            entry.entry_id,
            err,
        )
        return None

    return find_account_for_customer_number(relations, customer_number)


async def _migrate_entity_unique_ids(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
) -> None:
    """
    Rename v2 unique_ids that became subentry-scoped in v3.

    In v2 the integration only supported one customer account, so every
    entity's unique_id was simply ``{entry_id}_{key}``. In v3 the same
    descriptors repeat across customer accounts (multiple subentries on
    one login), so the unique_id schema gained a subentry-id segment:
    ``{entry_id}_{subentry_id}_{key}``.

    This helper finds every v2-shaped unique_id on the entry and rewrites
    it to the v3 shape, also stamping ``config_subentry_id=subentry_id``
    on the registry entry so the entity is correctly attributed to the
    customer-account subentry.

    The only entity intentionally kept at the v2 shape is the auth
    binary sensor (``{entry_id}_authentication``), which is login-scoped
    and lives on a dedicated login device, not a customer-account device.

    Entity ids are preserved (the registry retains the old ``entity_id``
    when ``new_unique_id`` is set), so history, dashboards, automations,
    and statistics continue to work.

    The helper is idempotent: entities whose unique_id already carries
    the v3 shape (or whose ``config_subentry_id`` is already set) are
    skipped. This means it is safe to re-run during recovery sweeps.
    """
    v2_prefix = f"{entry_id}_"
    v3_prefix = f"{entry_id}_{subentry_id}_"
    keep_login_scoped = {f"{entry_id}_authentication"}

    entity_reg = er.async_get(hass)
    existing_v3_uids = {
        ent.unique_id
        for ent in entity_reg.entities.values()
        if ent.platform == DOMAIN and ent.unique_id.startswith(v3_prefix)
    }

    @callback
    def _migrate(entity_entry: er.RegistryEntry) -> dict[str, Any] | None:
        unique_id = entity_entry.unique_id
        if unique_id in keep_login_scoped:
            return None
        if not unique_id.startswith(v2_prefix):
            return None
        if unique_id.startswith(v3_prefix):
            # Already migrated; just make sure the subentry attribution
            # is set (defensive against partially migrated state).
            if entity_entry.config_subentry_id == subentry_id:
                return None
            return {"config_subentry_id": subentry_id}

        suffix = unique_id[len(v2_prefix) :]
        new_unique_id = f"{v3_prefix}{suffix}"
        if new_unique_id in existing_v3_uids:
            # A v3-uid sibling already owns the entity_id slot. Drop the
            # v2-uid orphan so its entity_id can be reclaimed by the
            # surviving v3 entity (or simply disappears if unused).
            entity_reg.async_remove(entity_entry.entity_id)
            LOGGER.debug(
                "Removed orphan v2 entity %s during unique_id migration "
                "(v3 sibling %s already exists)",
                entity_entry.entity_id,
                new_unique_id,
            )
            return None
        return {
            "new_unique_id": new_unique_id,
            "config_subentry_id": subentry_id,
        }

    await er.async_migrate_entries(hass, entry_id, _migrate)


async def _async_rename_peaks_store(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
) -> None:
    """
    Rename the persisted peaks-history file from per-entry to per-subentry key.

    Reads via a transient ``Store`` keyed on the v2 filename, writes via
    the production ``EngieBePeaksStore`` (which uses the v3 filename),
    then removes the v2 file. Failures are logged but never raise: a
    missing peaks file is benign and the store self-heals on next save.
    """
    old_key = f"{DOMAIN}.peaks_history.{entry_id}"
    old_store: Store[dict[str, Any]] = Store(hass, 1, old_key)
    try:
        old_data = await old_store.async_load()
    except Exception:  # noqa: BLE001 - migration must never abort on store IO
        LOGGER.warning(
            "Could not read v2 peaks store %s during migration; starting fresh",
            old_key,
        )
        return

    if not isinstance(old_data, dict):
        # Nothing persisted in v2 yet; nothing to rename.
        return

    peaks = old_data.get("peaks")
    if not isinstance(peaks, list) or not peaks:
        # File existed but held no peaks; just remove the empty file.
        await old_store.async_remove()
        return

    new_store = EngieBePeaksStore(hass, subentry_id)
    await new_store.async_load()  # populate _loaded so saves work
    migrated = 0
    for peak in peaks:
        if (
            isinstance(peak, dict)
            and isinstance(peak.get("year"), int)
            and isinstance(peak.get("month"), int)
        ):
            new_store.upsert(
                year=peak["year"],
                month=peak["month"],
                start=peak.get("start", ""),
                end=peak.get("end", ""),
                peak_kw=peak.get("peakKW"),
                peak_kwh=peak.get("peakKWh"),
            )
            migrated += 1

    # Remove the v2 file so the next ``Store`` cache cycle does not pick
    # it up. ``async_remove`` is idempotent (suppresses FileNotFoundError).
    if migrated:
        await new_store.async_save_now()
    await old_store.async_remove()
    LOGGER.debug(
        "Migrated %d peak(s) from v2 store %s to v3 store for subentry %s",
        migrated,
        old_key,
        subentry_id,
    )


async def _async_cleanup_orphan_v2_device(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Drop a stale v2 device left over from a partial v2->v3 migration.

    Earlier versions of ``_async_migrate_v2_to_v3`` could leave the
    registry in an inconsistent state when the entity unique_id rename
    silently failed: the v2 device kept its ``(DOMAIN, entry_id)``
    identifier and continued to own a handful of entities while the
    platform setup created a parallel v3 device under
    ``(DOMAIN, subentry_id)``. The migration replay path now handles
    this in-line, but production entries that already booted past the
    migration step need the same reconciliation at setup time.

    For each customer-account subentry on ``entry``, if both the v2 and
    the v3 device exist, reparent every v2-owned entity onto the v3
    device (renaming subentry-scoped unique_ids in the process) and
    then remove the v2 device row. No-op when no v2 device is present.
    """
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)
    v2_device = device_reg.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)},
    )
    if v2_device is None:
        return

    customer_subentries = [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_CUSTOMER_ACCOUNT
    ]
    if len(customer_subentries) != 1:
        # Ambiguous: the v2 entry only ever had one customer account, so
        # if more than one customer subentry exists we cannot safely pick
        # the migration target. Leave the orphan in place and warn.
        LOGGER.warning(
            "Found orphan v2 device for entry %s but %d customer subentries "
            "exist; manual cleanup required",
            entry.entry_id,
            len(customer_subentries),
        )
        return

    subentry = customer_subentries[0]
    v3_device = device_reg.async_get_device(
        identifiers={(DOMAIN, subentry.subentry_id)},
    )
    if v3_device is None:
        # Only the v2 device exists: rename its identifier in place so
        # user customisations survive. Mirrors migration State A.
        _async_promote_device_to_subentry(
            hass,
            v2_device.id,
            entry_id=entry.entry_id,
            subentry_id=subentry.subentry_id,
            new_identifiers={(DOMAIN, subentry.subentry_id)},
        )
        LOGGER.info(
            "Promoted orphan v2 device %s to v3 subentry %s",
            v2_device.id,
            subentry.subentry_id,
        )
        return

    # Both devices exist: rename any v2-uid entities onto v3 ids (or
    # remove them if a v3 sibling already owns the new uid), reparent
    # the survivors onto the v3 device, then drop the v2 device row.
    await _migrate_entity_unique_ids(hass, entry.entry_id, subentry.subentry_id)
    for entity_entry in er.async_entries_for_device(
        entity_reg,
        v2_device.id,
        include_disabled_entities=True,
    ):
        entity_reg.async_update_entity(
            entity_entry.entity_id,
            device_id=v3_device.id,
            config_subentry_id=subentry.subentry_id,
        )
    device_reg.async_remove_device(v2_device.id)
    LOGGER.info(
        "Removed orphan v2 device %s after merging entities into v3 device %s",
        v2_device.id,
        v3_device.id,
    )


async def _async_heal_stale_subentry_links(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Drop a stale ``(entry_id, None)`` link from each customer-account device.

    Releases up to and including 0.8.0b1 ran the v2->v3 migration with a
    single ``async_update_device(..., add_config_subentry_id=...)`` call,
    which set-unioned the new subentry onto the legacy ``{None}`` set
    instead of replacing it. The device ended up with both
    ``(entry_id, None)`` and ``(entry_id, subentry_id)`` links and Home
    Assistant rendered it twice in the integration card: once under the
    parent entry's "no sub-item" group and once under the subentry's group.

    The migration helper now does the right thing for fresh upgrades, but
    entries that already booted past 0.8.0b1 still carry the duplicated
    link. This pass walks every customer-account subentry and, when its
    device still has the legacy ``None`` link alongside the subentry link,
    reparents any login-scoped entity off the device first (so HA's entity
    registry does not auto-remove them when the ``None`` link disappears)
    and then removes the legacy link. Idempotent and safe on healthy
    installs.
    """
    device_reg = dr.async_get(hass)
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_CUSTOMER_ACCOUNT:
            continue
        device = device_reg.async_get_device(
            identifiers={(DOMAIN, subentry.subentry_id)},
        )
        if device is None:
            continue
        entry_subentries = device.config_entries_subentries.get(entry.entry_id)
        if entry_subentries is None:
            continue
        if None not in entry_subentries:
            continue
        if subentry.subentry_id not in entry_subentries:
            # The device is somehow only linked via the bare entry; do
            # not touch it. Promotion is the orphan-cleanup helper's job.
            continue
        _async_reparent_login_scoped_entities(
            hass,
            device_id=device.id,
            entry_id=entry.entry_id,
        )
        device_reg.async_update_device(
            device.id,
            remove_config_entry_id=entry.entry_id,
            remove_config_subentry_id=None,
        )
        LOGGER.info(
            "Removed stale entry-only link from device %s (subentry %s)",
            device.id,
            subentry.subentry_id,
        )


async def _async_migrate_legacy_subentry_unique_ids(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Rewrite legacy BAN-shaped subentry unique_ids to canonical CAN values.

    A v2 entry stored ``customer_number`` as whatever identifier the user
    typed at setup time, which was usually a ``businessAgreementNumber``
    (BAN) because that is what ENGIE printed on bills. The v2->v3
    migration faithfully copied that BAN onto the new subentry's
    ``unique_id`` and ``data[customer_number]`` fields.

    From v3 onwards subentries are keyed by the canonical
    ``customerAccountNumber`` (CAN) returned by the relations endpoint.
    Subentries created via the picker already carry the CAN; subentries
    that came in via v2->v3 migration are still BAN-shaped and would
    duplicate when the user opens the picker (the picker dedupes by CAN
    OR BAN, but downstream tooling -- including HA's own
    ``already_configured`` semantics -- expects ``unique_id`` to be the
    canonical identifier).

    This helper is idempotent: already-canonical subentries are skipped,
    subentries whose stored identifier no longer maps to any account are
    left alone (the user may have moved the customer to a different
    login). A relations fetch failure is logged at debug and the
    migration is retried on next setup.
    """
    customer_subentries = [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_CUSTOMER_ACCOUNT
        and sub.unique_id is not None
    ]
    if not customer_subentries:
        return

    # If every subentry already carries its CAN as unique_id, skip the
    # network fetch entirely. The CAN is always present in the stored
    # data dict when the subentry was created via the v3 picker; legacy
    # subentries that need the rewrite typically have a BAN-shaped
    # unique_id matching their stored business_agreement_number.
    needs_rewrite = [
        sub
        for sub in customer_subentries
        if sub.data.get(CONF_CUSTOMER_NUMBER) != sub.unique_id
        or sub.unique_id == sub.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
    ]
    if not needs_rewrite:
        return

    relations: dict[str, Any] | None = None
    for subentry in needs_rewrite:
        if relations is None:
            relations = await _async_fetch_relations_for_setup(hass, entry)
            if relations is None:
                return

        match = find_account_for_customer_number(relations, subentry.unique_id)
        if match is None:
            continue

        canonical_can = match.get(CONF_CUSTOMER_NUMBER)
        if not canonical_can or canonical_can == subentry.unique_id:
            continue

        new_data = {**subentry.data, CONF_CUSTOMER_NUMBER: canonical_can}
        previous_unique_id = subentry.unique_id
        hass.config_entries.async_update_subentry(
            entry,
            subentry,
            unique_id=canonical_can,
            data=new_data,
        )
        LOGGER.info(
            "Rewrote legacy subentry unique_id %s -> %s for entry %s",
            previous_unique_id,
            canonical_can,
            entry.entry_id,
        )


async def _async_fetch_relations_for_setup(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> dict[str, Any] | None:
    """
    Fetch the customer-account-relations payload for a setup-time helper.

    Returns ``None`` and logs at debug on any error. Performs its own
    token refresh so it can run before the main coordinator setup wires
    up the persistent client/coordinators.
    """
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    if not refresh_token:
        return None

    client = EngieBeApiClient(
        session=async_get_clientsession(hass),
        client_id=entry.data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=refresh_token,
    )
    try:
        await client.async_refresh_token()
        return await client.async_get_customer_account_relations()
    except EngieBeApiClientError as err:
        LOGGER.debug(
            "Relations fetch skipped during legacy unique_id migration "
            "for entry %s: %s",
            entry.entry_id,
            err,
        )
        return None


async def _async_migrate_entity_id_slugs(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Rewrite existing customer-account entity_ids to carry the CAN prefix.

    From v3 onwards every customer-account entity exposes a
    ``_attr_suggested_object_id`` of the form
    ``engie_belgium_{CAN}_{key}`` so that two customer accounts on the
    same login do not collide on their translated friendly name and
    end up auto-suffixed with ``_2``. ``suggested_object_id`` only
    takes effect on first registration, so installs that already have
    entities in the registry need a one-shot rewrite.

    The rewrite is best-effort: failures are logged and the next
    entity is processed. ``unique_id`` values are left untouched, so
    state history is preserved across the rename. Idempotent: any
    entity already at its target slug is skipped.

    Login-scoped entities (those with ``config_subentry_id is None``,
    e.g. the authentication binary sensor) are intentionally left
    alone since they have no per-account context.
    """
    registry = er.async_get(hass)
    entry_id = entry.entry_id

    # Build a set of currently-used entity_ids per domain so collision
    # checks are O(1) without re-scanning the registry on every loop
    # iteration. The set is mutated as renames land.
    used_by_domain: dict[str, set[str]] = {}
    for reg_entry in registry.entities.values():
        used_by_domain.setdefault(reg_entry.domain, set()).add(reg_entry.entity_id)

    for reg_entry in list(er.async_entries_for_config_entry(registry, entry_id)):
        if reg_entry.config_subentry_id is None:
            continue

        subentry = entry.subentries.get(reg_entry.config_subentry_id)
        if subentry is None or subentry.subentry_type != SUBENTRY_TYPE_CUSTOMER_ACCOUNT:
            continue

        can = subentry.data.get(CONF_CUSTOMER_NUMBER)
        if not can:
            continue

        # Derive the descriptor key from unique_id by stripping the
        # entry_id and (when present) the subentry_id prefixes.
        unique_id = reg_entry.unique_id
        prefix = f"{entry_id}_"
        if not unique_id.startswith(prefix):
            continue
        suffix = unique_id[len(prefix) :]
        sub_prefix = f"{subentry.subentry_id}_"
        suffix = suffix.removeprefix(sub_prefix)

        # Calendar entities are one-per-subentry; drop the trailing
        # ``calendar`` token so the slug stays compact.
        if reg_entry.domain == "calendar" and suffix == "calendar":
            target_object_id = f"engie_belgium_{can}"
        else:
            target_object_id = f"engie_belgium_{can}_{suffix}"

        target_entity_id = f"{reg_entry.domain}.{target_object_id}"
        if reg_entry.entity_id == target_entity_id:
            continue

        domain_used = used_by_domain.setdefault(reg_entry.domain, set())
        # Collision-aware: walk numeric suffixes until a free slug is
        # found. The original entity_id is excluded because it is
        # about to be vacated by the rename itself.
        candidate = target_entity_id
        index = 2
        while candidate in domain_used and candidate != reg_entry.entity_id:
            candidate = f"{reg_entry.domain}.{target_object_id}_{index}"
            index += 1

        if candidate != target_entity_id:
            LOGGER.warning(
                "Target entity_id %s already taken; using %s instead for unique_id %s",
                target_entity_id,
                candidate,
                unique_id,
            )

        old_entity_id = reg_entry.entity_id
        try:
            registry.async_update_entity(old_entity_id, new_entity_id=candidate)
        except Exception as err:  # noqa: BLE001 - best-effort rename
            LOGGER.warning(
                "Failed to rename entity_id %s to %s: %s",
                old_entity_id,
                candidate,
                err,
            )
            continue

        domain_used.discard(old_entity_id)
        domain_used.add(candidate)
        LOGGER.info(
            "Renamed entity_id %s -> %s (unique_id=%s)",
            old_entity_id,
            candidate,
            unique_id,
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    # Defensive cleanup for entries that survived a partial v2->v3
    # migration before the merge logic above existed. Safe to run on
    # every setup: it is a no-op when no orphan v2 device is present.
    await _async_cleanup_orphan_v2_device(hass, entry)

    # Heal customer-account devices that 0.8.0b1 left with both an
    # entry-only and an entry+subentry link, which Home Assistant
    # rendered as two duplicate device rows. Idempotent and a no-op
    # on healthy installs.
    await _async_heal_stale_subentry_links(hass, entry)

    # One-shot migration of legacy BAN-shaped subentry unique_ids to
    # canonical CAN values. Idempotent and tolerant of relations API
    # failures: it retries on the next setup. Must run before the
    # picker-dedupe set is computed elsewhere, but the picker itself
    # is BAN-aware so the order is not strictly load-bearing.
    await _async_migrate_legacy_subentry_unique_ids(hass, entry)

    # One-shot rewrite of customer-account entity_ids to the
    # CAN-prefixed slug. Must run after the unique_id migration so
    # the CAN read here is canonical, and before the platforms are
    # forwarded so the renamed entity_ids are in the registry by the
    # time ``async_add_entities`` looks them up.
    await _async_migrate_entity_id_slugs(hass, entry)

    client = EngieBeApiClient(
        session=async_get_clientsession(hass),
        client_id=entry.data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
    )

    epex_coordinator = EngieBeEpexCoordinator(hass=hass, config_entry=entry)

    entry.runtime_data = EngieBeData(
        client=client,
        epex_coordinator=epex_coordinator,
        last_options=dict(entry.options),
        last_subentry_ids={
            sub.subentry_id
            for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_CUSTOMER_ACCOUNT
        },
    )

    # Initial token refresh so per-subentry coordinators have a valid
    # access token to make their first authenticated request with.
    try:
        new_access, new_refresh = await client.async_refresh_token()
    except EngieBeApiClientAuthenticationError as err:
        msg = "Stored ENGIE credentials are no longer valid"
        raise ConfigEntryAuthFailed(msg) from err
    except EngieBeApiClientError as err:
        msg = "Unable to refresh ENGIE access token; will retry"
        raise ConfigEntryNotReady(msg) from err

    _persist_tokens(hass, entry, new_access, new_refresh)
    entry.runtime_data.authenticated = True

    # Recurring token refresh (one timer per parent entry, not per
    # subentry: tokens are login-scoped, not account-scoped).
    async def _refresh_token_callback(_now: object) -> None:
        """Refresh the access token periodically."""
        try:
            new_access, new_refresh = await client.async_refresh_token()
        except EngieBeApiClientAuthenticationError:
            entry.runtime_data.authenticated = False
            LOGGER.warning(
                "Scheduled token refresh rejected by ENGIE; starting reauth flow"
            )
            entry.async_start_reauth(hass)
            return
        except EngieBeApiClientError as err:
            entry.runtime_data.authenticated = False
            # The API client embeds HTTP status / underlying exception class
            # into the message (see api.py: "HTTP {status}: {body_preview}",
            # "Timeout communicating ... ({TimeoutError})", etc.), so logging
            # the exception type plus its message is enough to diagnose
            # transient upstream failures without enabling debug logging.
            LOGGER.warning(
                "Scheduled token refresh failed (%s: %s); will retry",
                type(err).__name__,
                err,
            )
            return

        _persist_tokens(hass, entry, new_access, new_refresh)
        entry.runtime_data.authenticated = True
        LOGGER.debug("Token refreshed successfully")

    cancel_refresh = async_track_time_interval(
        hass,
        _refresh_token_callback,
        timedelta(seconds=TOKEN_REFRESH_INTERVAL_SECONDS),
    )
    entry.async_on_unload(cancel_refresh)

    # Build per-subentry coordinators, peak stores and service-points
    # lookups, then do their initial refreshes in parallel so that a
    # user with N customer accounts does not pay sum(latency) at setup.
    subentries: list[ConfigSubentry] = [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_CUSTOMER_ACCOUNT
    ]

    for subentry in subentries:
        coordinator = EngieBeDataUpdateCoordinator(
            hass=hass,
            config_entry=entry,
            subentry=subentry,
        )
        peaks_store = await _async_init_peaks_store(hass, subentry.subentry_id)
        entry.runtime_data.subentry_data[subentry.subentry_id] = EngieBeSubentryData(
            coordinator=coordinator,
            peaks_store=peaks_store,
        )

    # Refresh EPEX once at startup alongside the per-subentry customer
    # data; EPEX is shared across subentries so this is one fetch total.
    refresh_calls = [epex_coordinator.async_config_entry_first_refresh()]
    refresh_calls.extend(
        sub_data.coordinator.async_config_entry_first_refresh()
        for sub_data in entry.runtime_data.subentry_data.values()
    )
    await asyncio.gather(*refresh_calls)

    # Resolve energy type for each EAN per subentry. Service-point lookups
    # are fanned out across all subentries' EANs in a single gather so
    # multi-account customers do not pay sum(latency) for setup.
    await _async_populate_service_points(client, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Reload on options change or customer-account subentry add/remove.

    Token rotation also fires this listener (it writes to ``entry.data``)
    but neither options nor the customer-account subentry id set change
    on token rotation, so the no-op short-circuit holds.
    """
    options_changed = dict(entry.options) != entry.runtime_data.last_options
    current_subentry_ids = {
        sub.subentry_id
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_CUSTOMER_ACCOUNT
    }
    subentries_changed = current_subentry_ids != entry.runtime_data.last_subentry_ids
    if options_changed or subentries_changed:
        await hass.config_entries.async_reload(entry.entry_id)


def _persist_tokens(
    hass: HomeAssistant,
    entry: EngieBeConfigEntry,
    access_token: str,
    refresh_token: str,
) -> None:
    """Persist refreshed tokens to the config entry data."""
    updated_data = {**entry.data}
    updated_data[CONF_ACCESS_TOKEN] = access_token
    updated_data[CONF_REFRESH_TOKEN] = refresh_token
    hass.config_entries.async_update_entry(entry, data=updated_data)


async def _async_init_peaks_store(
    hass: HomeAssistant,
    subentry_id: str,
) -> EngieBePeaksStore:
    """Build and load the persistent peaks-history store for one subentry."""
    store = EngieBePeaksStore(hass, subentry_id)
    await store.async_load()
    return store


async def _async_populate_service_points(
    client: EngieBeApiClient,
    entry: EngieBeConfigEntry,
) -> None:
    """
    Resolve EAN-to-energy-type for every subentry in one fan-out call.

    EAN-to-division mapping is per-EAN (and therefore inherently
    per-subentry, since EANs belong to one customer account). Lookups
    are issued in parallel across all subentries' EANs so a multi-account
    user does not pay sum(latency) at setup. Failures degrade gracefully:
    a missing service-point falls back to the heuristic in the sensor
    layer, exactly as for single-account setups.
    """
    eans_by_subentry: dict[str, list[str]] = {}
    flat_eans: list[tuple[str, str]] = []
    for subentry_id, sub_data in entry.runtime_data.subentry_data.items():
        coordinator_data = sub_data.coordinator.data or {}
        eans = [
            item.get("ean", "")
            for item in coordinator_data.get("items", [])
            if item.get("ean")
        ]
        eans_by_subentry[subentry_id] = eans
        flat_eans.extend((subentry_id, ean) for ean in eans)

    if not flat_eans:
        return

    results = await asyncio.gather(
        *(client.async_get_service_point(ean) for _, ean in flat_eans),
        return_exceptions=True,
    )

    for (subentry_id, ean), result in zip(flat_eans, results, strict=True):
        if isinstance(result, EngieBeApiClientError):
            LOGGER.warning(
                "Failed to fetch service-point for EAN %s; using fallback",
                _hash_ean(ean),
            )
            continue
        if isinstance(result, BaseException):
            # Re-raise unexpected exceptions; only API errors are tolerated.
            raise result
        division: str = result.get("division", "")
        if division:
            entry.runtime_data.subentry_data[subentry_id].service_points[ean] = division
            LOGGER.debug("Service-point %s: division=%s", _hash_ean(ean), division)
