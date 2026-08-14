"""Like and dislike buttons.

Both are confirmed verbs on player:player/control, but only some sources
accept them - a Cast stream advertises neither. The buttons are created
regardless and go unavailable when the current source does not offer them,
rather than appearing and disappearing, which keeps the device page a fixed
shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FosiConfigEntry
from .api import NsdkError
from .const import CONTROL_DISLIKE, CONTROL_LIKE, PLAYER_CONTROL_PATH
from .coordinator import FosiCoordinator
from .entity import FosiEntity

# All I/O goes through the coordinator, so HA need not serialise
# entity updates.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FosiButtonDescription(ButtonEntityDescription):
    """A control verb and the `controls` key that gates it."""

    control: str
    controls_key: str


BUTTONS: tuple[FosiButtonDescription, ...] = (
    FosiButtonDescription(
        key="like",
        translation_key="like",
        icon="mdi:thumb-up",
        control=CONTROL_LIKE,
        controls_key="like",
    ),
    FosiButtonDescription(
        key="dislike",
        translation_key="dislike",
        icon="mdi:thumb-down",
        control=CONTROL_DISLIKE,
        controls_key="dislike",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FosiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        FosiControlButton(coordinator, entry, description) for description in BUTTONS
    )


class FosiControlButton(FosiEntity, ButtonEntity):
    """Fires one control verb at the player."""

    entity_description: FosiButtonDescription

    def __init__(
        self,
        coordinator: FosiCoordinator,
        entry: FosiConfigEntry,
        description: FosiButtonDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def _controls(self) -> dict[str, Any]:
        player = self.coordinator.data.get("player")
        if not isinstance(player, dict):
            return {}
        controls = player.get("controls")
        return controls if isinstance(controls, dict) else {}

    @property
    def available(self) -> bool:
        """Only where the source offers it.

        Unlike the transport row, an unavailable button here still occupies
        its place on the device page - so gating honestly costs no layout
        stability and avoids a button that always errors.
        """
        return super().available and bool(
            self._controls.get(self.entity_description.controls_key)
        )

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.activate(
                PLAYER_CONTROL_PATH, {"control": self.entity_description.control}
            )
        except NsdkError as exc:
            raise HomeAssistantError(
                f"Could not {self.entity_description.key}: {exc}"
            ) from exc
