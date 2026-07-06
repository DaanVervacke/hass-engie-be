"""Button platform for the ENGIE Belgium integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.components.persistent_notification import (
    async_create as async_create_notification,
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


# One button per energy type per BAN. Users can press only the one they care
# about; users without a solar meter or without gas can ignore or hide
# the other. Both flow through the same orchestrator with a different
# ``streams`` filter.
_ELECTRICITY_STREAMS: frozenset[str] = frozenset({STREAM_CONSUMPTION, STREAM_INJECTION})
_GAS_STREAMS: frozenset[str] = frozenset({STREAM_GAS})

_ELECTRICITY_DESCRIPTION = ButtonEntityDescription(
    key="import_electricity_history",
    translation_key="import_electricity_history",
    entity_category=EntityCategory.CONFIG,
)
_GAS_DESCRIPTION = ButtonEntityDescription(
    key="import_gas_history",
    translation_key="import_gas_history",
    entity_category=EntityCategory.CONFIG,
)


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
                    description=_ELECTRICITY_DESCRIPTION,
                    streams=_ELECTRICITY_STREAMS,
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
    Trigger a per-BAN, per-energy-type historical usage import into HA statistics.

    Two instances live per BAN: one for electricity (consumption +
    injection) and one for gas. Each walks back to the business
    agreement's start date on first press, then only fetches the delta
    since the last recorded statistic on subsequent presses. Idempotent
    by (statistic_id, start), so multiple presses do not double-count.
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

        Emits a persistent notification at start and updates it in place
        on success or failure so the user sees progress in the bell-icon
        tray. The notification id is stable per (subentry, key) so
        repeated presses replace instead of stacking.

        API-side failures raise :class:`HomeAssistantError` with a
        translated message so Home Assistant surfaces a toast on the UI
        instead of a bare stack trace. Unexpected exceptions still
        propagate so bugs stay loud.
        """
        client = self._entry.runtime_data.client
        ban = self._subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER, "")
        masked = mask_identifier(ban)
        # Not using ``self.name`` here: it resolves translation keys via
        # the platform registry, which is unavailable in unit-test contexts
        # where the button isn't attached to a live platform. The
        # description key is stable and log-friendly.  Sentence case
        # ("Import electricity history"), not title case, matches HA's
        # convention for notification titles and entity names.
        friendly = self.entity_description.key.replace("_", " ").capitalize()
        notification_id = (
            f"engie_be_{self._subentry.subentry_id}_{self.entity_description.key}"
        )

        async_create_notification(
            self.hass,
            f"Importing historical data for {self._subentry.title}. "
            "This can take a few minutes, the notification updates when done.",
            title=f"ENGIE Belgium: {friendly}",
            notification_id=notification_id,
        )

        try:
            count = await async_import_usage_history(
                self.hass, client, self._subentry, streams=self._streams
            )
        except EngieBeApiClientError as err:
            LOGGER.warning("Historical usage import failed for BAN %s: %s", masked, err)
            async_create_notification(
                self.hass,
                (
                    f"Import failed for {self._subentry.title}: {err}. "
                    "Press the button again to resume from the last completed hour."
                ),
                title=f"ENGIE Belgium: {friendly} - failed",
                notification_id=notification_id,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="import_history_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        async_create_notification(
            self.hass,
            (
                f"Imported {count} hourly statistic rows for {self._subentry.title}. "
                "You can now select the data in the Energy Dashboard "
                "(Settings > Dashboards > Energy)."
            ),
            title=f"ENGIE Belgium: {friendly} - done",
            notification_id=notification_id,
        )
