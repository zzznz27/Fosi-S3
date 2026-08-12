"""Media player entity for the Fosi S3."""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FosiConfigEntry
from .api import NsdkError
from .const import MUTE_PATH, VOLUME_PATH
from .coordinator import FosiCoordinator
from .entity import FosiSourceEntity


# All I/O goes through the coordinator, so HA need not serialise
# entity updates.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FosiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([FosiMediaPlayer(entry.runtime_data.coordinator, entry)])


class FosiMediaPlayer(FosiSourceEntity, MediaPlayerEntity):
    """Volume, mute and source selection.

    Transport controls are deliberately absent: playback on this device is
    driven by whichever Connect protocol is streaming to it (Cast, AirPlay,
    Spotify, Tidal), and those own their own transport. Control playback in
    the source app, not here.
    """

    _attr_name = None  # use the device name

    def __init__(self, coordinator: FosiCoordinator, entry: FosiConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_media_player"

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = MediaPlayerEntityFeature.SELECT_SOURCE
        if self.coordinator.data.get("mute") is not None:
            features |= MediaPlayerEntityFeature.VOLUME_MUTE
        # No longer gated on volumeMap: the curve only sizes the range, and
        # there is a sane fallback for it. player:volume answering is enough.
        if self.coordinator.data.get("volume") is not None:
            features |= (
                MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.VOLUME_STEP
            )
        return features

    @property
    def state(self) -> MediaPlayerState:
        """Always on while reachable.

        The device exposes no standby node - maxIdleTime is 0, so SDK-level
        auto-standby is off - and CoordinatorEntity already reports the entity
        as unavailable when polling fails, so there is no off state to report.
        """
        return MediaPlayerState.ON

    @property
    def source_list(self) -> list[str]:
        return self._source_options

    @property
    def source(self) -> str | None:
        # Same rule as the select entity: source_list carries the live source
        # even when it cannot be commanded, so this never has to go blank.
        return self._active_source

    @property
    def is_volume_muted(self) -> bool | None:
        return self.coordinator.data.get("mute")

    @property
    def volume_level(self) -> float | None:
        return self.coordinator.volume_to_level(self.coordinator.data.get("volume"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Source attributes, plus the dB the current volume maps to.

        Derived from the curve rather than polled: volumeMap[volume] is
        exactly what settings:/mediaPlayer/attenuation reports, so reading it
        as well would be a wasted round trip per cycle.
        """
        volume = self.coordinator.data.get("volume")
        return {
            **super().extra_state_attributes,
            "volume_index": volume,
            "volume_db": self.coordinator.volume_to_db(volume),
        }

    # ------------------------------------------------------------ commands

    async def async_select_source(self, source: str) -> None:
        await self._async_apply_source(source)

    async def async_mute_volume(self, mute: bool) -> None:
        try:
            await self.coordinator.client.write(MUTE_PATH, mute)
        except NsdkError as exc:
            raise HomeAssistantError(f"Could not set mute: {exc}") from exc
        self.coordinator.apply_optimistic(mute=mute)

    async def async_set_volume_level(self, volume: float) -> None:
        await self._async_write_volume(self.coordinator.level_to_volume(volume))

    async def async_volume_up(self) -> None:
        await self._step(1)

    async def async_volume_down(self) -> None:
        await self._step(-1)

    async def _step(self, direction: int) -> None:
        """Move one step along the device's own volume scale."""
        current = self.coordinator.data.get("volume")
        if current is None:
            return
        await self._async_write_volume(
            max(0, min(self.coordinator.volume_steps, current + direction))
        )

    async def _async_write_volume(self, target: int) -> None:
        try:
            await self.coordinator.client.write(VOLUME_PATH, target)
        except NsdkError as exc:
            raise HomeAssistantError(f"Could not set volume: {exc}") from exc
        # The device reflects a write ~0.5s late, so never re-read here.
        self.coordinator.apply_optimistic(volume=target)
