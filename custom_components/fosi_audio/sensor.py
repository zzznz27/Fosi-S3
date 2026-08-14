"""Sensor for whatever is currently streaming to the device."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FosiConfigEntry
from .coordinator import FosiCoordinator
from .entity import FosiEntity, player_metadata, streaming_app, streaming_service

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
    """Which protocol is streaming - Google Cast, AirPlay, Roon.

    The input select only ever says "Network" for any of the streaming
    protocols, deliberately, so that its state stays a fixed set automations
    can rely on. This is where the varying part lives.

    The state is always the protocol. The app behind it - "YouTube Music" -
    is an attribute, because not every protocol reports one and a state that
    is sometimes a protocol and sometimes an app is not something you can
    write a condition against.
    """

    _attr_translation_key = "streaming_service"
    _attr_icon = "mdi:cast-audio"

    def __init__(self, coordinator: FosiCoordinator, entry: FosiConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_streaming_service"

    @property
    def native_value(self) -> str | None:
        """The protocol, or None when nothing is streaming.

        None rather than a placeholder: the sensor is genuinely unknown on
        HDMI or when idle, and inventing "Idle" would make it a value
        automations have to special-case.
        """
        return streaming_service(self.coordinator.data.get("player"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """serviceID is the stable machine-readable one to key automations on.

        serviceName is prose the device composes for its own display and
        changes with the app, so it is reported but not relied on.
        """
        player = self.coordinator.data.get("player")
        meta = player_metadata(player)
        service_name = meta.get("serviceName")
        return {
            "service_id": (meta.get("serviceID") or "").strip() or None,
            "service_name": (
                service_name.strip() or None
                if isinstance(service_name, str)
                else None
            ),
            "app_name": streaming_app(player),
        }
