"""Bass and treble tone controls.

These are ALSA mixer elements the device exposes directly - `alsaMixerElements`
maps them to the "Bass" and "Treble" controls - and they sit in the sound
server chain, so they apply whatever the current input is rather than only to
a network stream.

They are deliberately plain numbers rather than a "night mode" switch. A switch
would have to remember the level to restore and would go stale the moment
someone changed it from the Fosi app or the front panel. A number carries no
hidden state, and Home Assistant already has better ways to schedule one -
an automation that sets it, or a scene that captures and restores it.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FosiConfigEntry
from .api import NsdkError
from .const import BASS_PATH, TONE_MAX, TONE_MIN, TONE_STEP, TREBLE_PATH
from .coordinator import FosiCoordinator
from .entity import FosiEntity

# Writes go straight to the device rather than through the coordinator, so
# cap concurrency - this is a small embedded webserver.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class FosiToneDescription(NumberEntityDescription):
    """A tone control and the node behind it."""

    path: str


TONE_CONTROLS: tuple[FosiToneDescription, ...] = (
    FosiToneDescription(
        key="bass",
        translation_key="bass",
        path=BASS_PATH,
        icon="mdi:music-clef-bass",
    ),
    FosiToneDescription(
        key="treble",
        translation_key="treble",
        path=TREBLE_PATH,
        icon="mdi:music-clef-treble",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FosiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        FosiToneNumber(coordinator, entry, desc) for desc in TONE_CONTROLS
    )


class FosiToneNumber(FosiEntity, NumberEntity):
    """One tone control, in dB."""

    entity_description: FosiToneDescription

    _attr_entity_category = EntityCategory.CONFIG
    # No device class: SIGNAL_STRENGTH is the only dB one and it means
    # something else entirely. A bare unit is honest here.
    _attr_native_unit_of_measurement = "dB"
    _attr_native_min_value = TONE_MIN
    _attr_native_max_value = TONE_MAX
    _attr_native_step = TONE_STEP
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: FosiCoordinator,
        entry: FosiConfigEntry,
        description: FosiToneDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.get(self.entity_description.key)
        return value if isinstance(value, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        # Settle first and send that, so the wire and the UI carry the same
        # number. The device clamps beyond +/-10 dB and rounds to the 0.5 dB
        # grid anyway; doing it here means the optimistic value cannot differ
        # from what the device will hold. float() also keeps the node's
        # double_ encoding when a caller hands us a whole number.
        target = float(self._settle(value))
        try:
            await self.coordinator.client.write(self.entity_description.path, target)
        except NsdkError as exc:
            raise HomeAssistantError(
                f"Could not set {self.entity_description.key}: {exc}"
            ) from exc
        # Never re-read after a write - the device reflects one ~0.5s late,
        # which is the trap that broke volume.
        self.coordinator.apply_optimistic(**{self.entity_description.key: target})

    @staticmethod
    def _settle(value: float) -> float:
        """What the device will hold after being asked for `value`."""
        clamped = max(TONE_MIN, min(TONE_MAX, value))
        return round(clamped / TONE_STEP) * TONE_STEP
