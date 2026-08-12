"""Select entities for the Fosi S3: audio input and analogue/digital output."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FosiConfigEntry
from .api import NsdkError
from .const import OUTPUT_MODE_ACTIONS, OUTPUT_MODE_OPTICAL, OUTPUT_MODE_RCA
from .coordinator import FosiCoordinator
from .entity import FosiEntity, FosiSourceEntity


# All I/O goes through the coordinator, so HA need not serialise
# entity updates.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FosiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    # Both entities are created unconditionally. Deciding from the first poll
    # whether the device has an output-mode node would mean one unlucky read
    # during setup permanently hides the entity until the next reload.
    async_add_entities(
        [FosiInputSelect(coordinator, entry), FosiOutputModeSelect(coordinator, entry)]
    )


class FosiInputSelect(FosiSourceEntity, SelectEntity):
    """Selects the active audio input."""

    _attr_translation_key = "input"
    _attr_icon = "mdi:audio-input-rca"

    def __init__(self, coordinator: FosiCoordinator, entry: FosiConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_input"

    @property
    def options(self) -> list[str]:
        return self._source_options

    @property
    def current_option(self) -> str | None:
        return self._active_source

    async def async_select_option(self, option: str) -> None:
        await self._async_apply_source(option)


class FosiOutputModeSelect(FosiEntity, SelectEntity):
    """Analogue or digital output.

    settings:/custom/audioOutputMode is a boolean, and the two ui: actions
    set it: false = RCA/XLR Out, true = Optical Out. These appear to be
    mutually exclusive on this hardware rather than simultaneous outputs.
    """

    _attr_translation_key = "output_mode"
    _attr_icon = "mdi:audio-video"
    _attr_options = list(OUTPUT_MODE_ACTIONS)

    def __init__(self, coordinator: FosiCoordinator, entry: FosiConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_output_mode"

    @property
    def available(self) -> bool:
        """Unavailable if the node is missing, rather than absent entirely."""
        return (
            super().available
            and self.coordinator.data.get("output_mode") is not None
        )

    @property
    def current_option(self) -> str | None:
        mode = self.coordinator.data.get("output_mode")
        if mode is None:
            return None
        return OUTPUT_MODE_OPTICAL if mode else OUTPUT_MODE_RCA

    async def async_select_option(self, option: str) -> None:
        path = OUTPUT_MODE_ACTIONS.get(option)
        if path is None:
            raise HomeAssistantError(f"Unknown output mode {option!r}")
        try:
            await self.coordinator.client.activate(path)
        except NsdkError as exc:
            raise HomeAssistantError(f"Could not set output mode: {exc}") from exc
        self.coordinator.apply_optimistic(output_mode=option == OUTPUT_MODE_OPTICAL)
