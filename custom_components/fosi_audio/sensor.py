"""Sensor for whatever is currently streaming to the device."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FosiConfigEntry
from .coordinator import FosiCoordinator
from .entity import FosiEntity, streaming_service

# All I/O goes through the coordinator, so HA need not serialise
# entity updates.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FosiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([FosiServiceSensor(entry.runtime_data.coordinator, entry)])


class FosiServiceSensor(FosiEntity, SensorEntity):
    """Which app is streaming - "YouTube Music", "Spotify", "Tidal"...

    The input select only ever says "Network" for any of the streaming
    protocols, deliberately, so that its state stays a fixed set automations
    can rely on. This is where the varying part lives, which makes it the
    thing to put on a dashboard or trigger from.
    """

    _attr_translation_key = "streaming_service"
    _attr_icon = "mdi:cast-audio"

    def __init__(self, coordinator: FosiCoordinator, entry: FosiConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_streaming_service"

    @property
    def _metadata(self) -> dict[str, Any]:
        player = self.coordinator.data.get("player")
        if not isinstance(player, dict):
            return {}
        node: Any = player
        for key in ("trackRoles", "mediaData", "metaData"):
            if not isinstance(node, dict):
                return {}
            node = node.get(key)
        return node if isinstance(node, dict) else {}

    @property
    def native_value(self) -> str | None:
        """The app name, or None when nothing is streaming.

        None rather than a placeholder string: the sensor is genuinely
        unknown on HDMI or when idle, and inventing "None"/"Idle" would make
        it a value automations have to special-case.
        """
        return streaming_service(self.coordinator.data.get("player"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The rest of what the device says about the service.

        serviceID is the stable machine-readable one ("googlecast",
        "airplay") and is the right thing to write automations against;
        serviceName is prose that varies with the app.
        """
        meta = self._metadata

        def clean(key: str) -> str | None:
            value = meta.get(key)
            return value.strip() or None if isinstance(value, str) else None

        return {
            "service_id": clean("serviceID"),
            "service_name": clean("serviceName"),
            "app_name": clean("externalAppName"),
        }
