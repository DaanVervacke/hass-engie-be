"""Button platform for the ENGIE Belgium integration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.components.persistent_notification import (
    async_create as async_create_notification,
)
from homeassistant.components.persistent_notification import (
    async_dismiss as async_dismiss_notification,
)
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from ._statistics import (
    STREAM_CONSUMPTION,
    STREAM_GAS,
    STREAM_INJECTION,
    async_import_usage_history,
)
from .api import EngieBeApiClientError, mask_identifier
from .const import (
    CONF_BUSINESS_AGREEMENT_NUMBER,
    DOMAIN,
    LOGGER,
    SUBENTRY_TYPE_BUSINESS_AGREEMENT,
)
from .entity import EngieBeEntity

# Coordinator centralises updates; button entities don't consume its data.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import EngieBeDataUpdateCoordinator
    from .data import EngieBeConfigEntry


# One button per energy stream per BAN. Users can press only the one(s) they
# care about; users without solar or without gas can ignore or hide the others.
# All three flow through the same orchestrator with a different ``streams``
# filter.
_CONSUMPTION_STREAMS: frozenset[str] = frozenset({STREAM_CONSUMPTION})
_INJECTION_STREAMS: frozenset[str] = frozenset({STREAM_INJECTION})
_GAS_STREAMS: frozenset[str] = frozenset({STREAM_GAS})

_CONSUMPTION_DESCRIPTION = ButtonEntityDescription(
    key="import_consumption_history",
    translation_key="import_consumption_history",
    entity_category=EntityCategory.CONFIG,
)
_INJECTION_DESCRIPTION = ButtonEntityDescription(
    key="import_injection_history",
    translation_key="import_injection_history",
    entity_category=EntityCategory.CONFIG,
)
_GAS_DESCRIPTION = ButtonEntityDescription(
    key="import_gas_history",
    translation_key="import_gas_history",
    entity_category=EntityCategory.CONFIG,
)

# Static map from description.key to the human label used in notification
# titles. Matches the entity ``name`` strings in ``strings.json`` so users see
# the same label in the sidebar as on the button itself. Cannot use
# ``self.name`` because it resolves via the platform registry, which is
# unavailable during unit tests.
#
# Keep in sync with ``entity.button.import_*_history.name`` in
# ``strings.json`` / ``translations/en.json``.
_BUTTON_LABELS: dict[str, str] = {
    "import_consumption_history": "Import historical electricity consumption",
    "import_injection_history": "Import historical electricity injection",
    "import_gas_history": "Import historical gas consumption",
}


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EngieBeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register per-energy-type import buttons for every active business agreement."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_BUSINESS_AGREEMENT:
            continue
        sub_data = entry.runtime_data.subentry_data.get(subentry.subentry_id)
        if sub_data is None:
            LOGGER.warning(
                "No runtime data for subentry %s; skipping button setup",
                subentry.subentry_id,
            )
            continue
        async_add_entities(
            [
                EngieBeImportHistoryButton(
                    coordinator=sub_data.coordinator,
                    subentry=subentry,
                    entry=entry,
                    description=_CONSUMPTION_DESCRIPTION,
                    streams=_CONSUMPTION_STREAMS,
                ),
                EngieBeImportHistoryButton(
                    coordinator=sub_data.coordinator,
                    subentry=subentry,
                    entry=entry,
                    description=_INJECTION_DESCRIPTION,
                    streams=_INJECTION_STREAMS,
                ),
                EngieBeImportHistoryButton(
                    coordinator=sub_data.coordinator,
                    subentry=subentry,
                    entry=entry,
                    description=_GAS_DESCRIPTION,
                    streams=_GAS_STREAMS,
                ),
            ],
            config_subentry_id=subentry.subentry_id,
        )


class EngieBeImportHistoryButton(EngieBeEntity, ButtonEntity):
    """
    Trigger a per-BAN, per-stream historical usage import into HA statistics.

    Three instances live per BAN: consumption, injection, and gas. Each
    walks back to the business agreement's start date on first press,
    then only fetches the delta since the last recorded statistic on
    subsequent presses. Idempotent by (statistic_id, start), so multiple
    presses do not double-count.
    """

    def __init__(
        self,
        coordinator: EngieBeDataUpdateCoordinator,
        subentry: ConfigSubentry,
        entry: EngieBeConfigEntry,
        description: ButtonEntityDescription,
        streams: frozenset[str],
    ) -> None:
        """Initialise a per-energy-type import-history button."""
        super().__init__(coordinator, subentry)
        self.entity_description = description
        self._entry = entry
        self._streams = streams
        self._import_lock = asyncio.Lock()
        self._attr_unique_id = (
            f"{entry.entry_id}_{subentry.subentry_id}_{description.key}"
        )
        ban = subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)
        if ban:
            # Normalise like ``_statistics.statistic_id`` does: spaces
            # and dashes are invalid in HA entity IDs. ENGIE BANs are
            # 12 digits in practice, but the normalisation keeps the
            # entity_id URL-safe if the source ever changes shape.
            normalised = ban.replace(" ", "").replace("-", "_")
            self.entity_id = f"button.engie_belgium_{normalised}_{description.key}"

    async def async_press(self) -> None:
        """
        Fetch and import historical usage for this subentry's BAN.

        Emits a "started" persistent notification (which HA also renders
        as a briefly-visible toast in the top corner) and, when the
        import finishes, dismisses that one and creates a separate
        "finished" notification so a second toast fires. Two distinct
        notification_ids make sure both events reach the user; a single
        update-in-place notification would silently overwrite the first
        toast without a fresh pop.

        Concurrent presses (rapid double-click) are serialised through
        an ``asyncio.Lock`` so the running-sum threading in the
        orchestrator cannot race with itself. HA's button entity also
        rate-limits, but the lock is cheap defence-in-depth.

        API-side failures raise :class:`HomeAssistantError` with a
        translated message so HA surfaces a toast on the UI instead of a
        bare stack trace. Unexpected exceptions still propagate so bugs
        stay loud.
        """
        async with self._import_lock:
            client = self._entry.runtime_data.client
            ban = self._subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
            masked = mask_identifier(ban)
            key = self.entity_description.key
            friendly = _BUTTON_LABELS.get(key, key.replace("_", " ").capitalize())
            title = f"ENGIE Belgium: {friendly}"
            base = f"engie_be_{self._subentry.subentry_id}_{key}"
            start_id = f"{base}_start"
            done_id = f"{base}_done"

            async_create_notification(
                self.hass,
                (
                    f"Started importing historical data for "
                    f"{self._subentry.title}. This can take a few minutes."
                ),
                title=title,
                notification_id=start_id,
            )

            try:
                count = await async_import_usage_history(
                    self.hass, client, self._subentry, streams=self._streams
                )
            except EngieBeApiClientError as err:
                LOGGER.warning(
                    "Historical usage import failed for BAN %s: %s", masked, err
                )
                async_dismiss_notification(self.hass, start_id)
                async_create_notification(
                    self.hass,
                    (
                        f"Import failed for {self._subentry.title}: {err}. "
                        "Press the button again to resume from the last "
                        "completed hour."
                    ),
                    title=f"{title} - failed",
                    notification_id=done_id,
                )
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="import_history_failed",
                    translation_placeholders={"error": str(err)},
                ) from err

            # Dismiss the "started" toast first so the "finished" one fires
            # as a fresh notification event and pops the toast again.
            async_dismiss_notification(self.hass, start_id)
            async_create_notification(
                self.hass,
                (
                    f"Imported {count} hourly statistic rows for "
                    f"{self._subentry.title}. Select the data in the "
                    "Energy Dashboard (Settings > Dashboards > Energy)."
                ),
                title=f"{title} - done",
                notification_id=done_id,
            )
