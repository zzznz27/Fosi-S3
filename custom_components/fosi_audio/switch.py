"""The equaliser master switch.

Worth exposing rather than assuming: with this off, every band is inert. That
is exactly the failure mode the vestigial bass/treble nodes produced - a
control that accepts values and changes nothing - so the state that decides
whether the bands do anything belongs in Home Assistant too.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FosiConfigEntry
from .api import NsdkError
from .const import EQ_ENABLED_KEY, EQ_ENABLED_PATH
from .coordinator import FosiCoordinator
from .entity import FosiEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FosiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([FosiEqualizerSwitch(entry.runtime_data.coordinator, entry)])


class FosiEqualizerSwitch(FosiEntity, SwitchEntity):
    """Turns the 10-band equaliser as a whole on or off."""

    _attr_translation_key = "equalizer"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:equalizer"

    def __init__(self, coordinator: FosiCoordinator, entry: FosiConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{EQ_ENABLED_KEY}"

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get(EQ_ENABLED_KEY)
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(False)

    async def _async_write(self, state: bool) -> None:
        try:
            await self.coordinator.client.write(EQ_ENABLED_PATH, state)
        except NsdkError as exc:
            raise HomeAssistantError(f"Could not switch the equaliser: {exc}") from exc
        # The device reflects a write ~0.5s late, so never re-read here.
        self.coordinator.apply_optimistic(**{EQ_ENABLED_KEY: state})
