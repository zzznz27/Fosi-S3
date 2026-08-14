"""A button to end whatever is streaming to the device.

The media player already carries a stop control, but only where a media card
is on screen. This puts the same action on the device page and makes it
targetable from a dashboard or a script without going through media_player
services.

Verified on hardware: {"control": "stop"} does more than pause - the player
state goes to "stopped", `controls` disappears and the track metadata clears,
which for a Cast source means the session is gone rather than paused. There is
no separate disconnect verb; the device's own web client has none either.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FosiConfigEntry
from .api import NsdkError
from .const import CONTROL_STOP, PLAYER_CONTROL_PATH
from .coordinator import FosiCoordinator
from .entity import FosiEntity

# All I/O goes through the coordinator, so HA need not serialise
# entity updates.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FosiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([FosiStopButton(entry.runtime_data.coordinator, entry)])


class FosiStopButton(FosiEntity, ButtonEntity):
    """Ends the current streaming session."""

    _attr_translation_key = "stop_streaming"
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, coordinator: FosiCoordinator, entry: FosiConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_stop_streaming"

    @property
    def _controls(self) -> dict[str, Any]:
        player = self.coordinator.data.get("player")
        if not isinstance(player, dict):
            return {}
        controls = player.get("controls")
        return controls if isinstance(controls, dict) else {}

    @property
    def available(self) -> bool:
        """Only while something is actually playing.

        `controls` is absent on HDMI, optical and line-in, and once a session
        has ended - there is nothing to stop in any of those cases. The button
        greys out rather than disappearing, so the device page keeps its shape.
        """
        return super().available and bool(self._controls)

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.activate(
                PLAYER_CONTROL_PATH, {"control": CONTROL_STOP}
            )
        except NsdkError as exc:
            raise HomeAssistantError(f"Could not stop playback: {exc}") from exc
        # The device reflects a write ~0.5s late, so publish the expected
        # state rather than re-reading and getting the old one.
        self.coordinator.apply_optimistic(player={})
