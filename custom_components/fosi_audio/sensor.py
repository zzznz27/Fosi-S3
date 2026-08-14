"""Sensors for what is streaming to the device, and how."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FosiConfigEntry
from .coordinator import FosiCoordinator
from .entity import (
    FosiEntity,
    player_metadata,
    streaming_protocol,
    streaming_service,
)

# All I/O goes through the coordinator, so HA need not serialise
# entity updates.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FosiSensorDescription(SensorEntityDescription):
    """Pairs a sensor with the function that reads it out of the player."""

    value: Callable[[Any], str | None]


SENSORS: tuple[FosiSensorDescription, ...] = (
    # Two entities on purpose. The protocol is how audio arrives and comes
    # from a small stable set; the service is what is playing it and varies
    # per app. Collapsing them into one gave "AirPlay" in one case and
    # "YouTube Music" in another - a protocol and an app in the same state,
    # which nothing can be written against.
    FosiSensorDescription(
        key="streaming_protocol",
        translation_key="streaming_protocol",
        icon="mdi:cast-audio",
        value=streaming_protocol,
    ),
    FosiSensorDescription(
        key="streaming_service",
        translation_key="streaming_service",
        icon="mdi:music-box-multiple",
        value=streaming_service,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FosiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        FosiPlayerSensor(coordinator, entry, description) for description in SENSORS
    )


class FosiPlayerSensor(FosiEntity, SensorEntity):
    """One field read out of the player's metadata.

    The input select only ever says "Network" for any streaming protocol,
    deliberately, so its state stays a fixed set automations can rely on.
    These are where the varying part lives.
    """

    entity_description: FosiSensorDescription

    def __init__(
        self,
        coordinator: FosiCoordinator,
        entry: FosiConfigEntry,
        description: FosiSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> str | None:
        """None when nothing is streaming, rather than a placeholder.

        The value is genuinely unknown on HDMI or when idle, and inventing
        "Idle" would make it something automations have to special-case.
        AirPlay names no app, so the service sensor is legitimately unknown
        there while the protocol sensor still reports.
        """
        return self.entity_description.value(self.coordinator.data.get("player"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """serviceID is the stable machine-readable one to key automations on.

        serviceName is prose the device composes for its own display and
        changes with the app, so it is reported but not relied on.
        """
        meta = player_metadata(self.coordinator.data.get("player"))

        def text(key: str) -> str | None:
            value = meta.get(key)
            return value.strip() or None if isinstance(value, str) else None

        return {
            "service_id": text("serviceID"),
            "service_name": text("serviceName"),
            "app_name": text("externalAppName"),
        }
