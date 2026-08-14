"""Media player entity for the Fosi Audio S3."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FosiConfigEntry
from .api import NsdkError
from .const import (
    CONTROL_KEY_PLAY,
    CONTROL_KEY_PLAY_MODE,
    CONTROL_KEY_SEEK,
    CONTROL_NEXT,
    CONTROL_PLAY,
    CONTROL_PLAY_MODE,
    CONTROL_PREVIOUS,
    CONTROL_SEEK,
    CONTROL_STOP,
    CONTROL_TOGGLE,
    MUTE_PATH,
    PLAY_MODE_LOOKUP,
    PLAY_MODES,
    PLAYER_CONTROL_PATH,
    PLAYER_STATE_PAUSED,
    PLAYER_STATE_PLAYING,
    PLAYER_STATE_STOPPED,
    VOLUME_PATH,
)
from .coordinator import FosiCoordinator
from .entity import FosiSourceEntity

# All I/O goes through the coordinator, so HA need not serialise
# entity updates.
PARALLEL_UPDATES = 0

SERVICE_SEEK_RELATIVE = "seek_relative"
ATTR_OFFSET = "offset"

STATE_MAP: dict[str, MediaPlayerState] = {
    PLAYER_STATE_PLAYING: MediaPlayerState.PLAYING,
    PLAYER_STATE_PAUSED: MediaPlayerState.PAUSED,
    PLAYER_STATE_STOPPED: MediaPlayerState.IDLE,
}
DEVICE_STATE: dict[MediaPlayerState, str] = {
    MediaPlayerState.PLAYING: PLAYER_STATE_PLAYING,
    MediaPlayerState.PAUSED: PLAYER_STATE_PAUSED,
    MediaPlayerState.IDLE: PLAYER_STATE_STOPPED,
}


def dig(node: Any, *keys: str) -> Any:
    """Walk a nested dict, returning None at the first thing that is not one.

    The whole trackRoles tree is absent when the device is stopped, so every
    lookup has to tolerate the parent missing rather than the leaf.
    """
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def clean(value: Any) -> Any:
    """Empty strings render as blank rows on the media card - drop them.

    albumArtist comes back as "" on a live Cast stream.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


def ms_to_seconds(value: Any) -> float | None:
    """Device reports milliseconds; Home Assistant wants seconds."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return value / 1000


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FosiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([FosiMediaPlayer(entry.runtime_data.coordinator, entry)])

    # Home Assistant's own seek is absolute; the device also accepts a signed
    # relative seek, which is worth exposing.
    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_SEEK_RELATIVE,
        {vol.Required(ATTR_OFFSET): vol.Coerce(float)},
        "async_seek_relative",
    )


class FosiMediaPlayer(FosiSourceEntity, MediaPlayerEntity):
    """Source, volume, mute and transport.

    Transport works for whatever is streaming to the device - AirPlay,
    Spotify, Tidal, Bluetooth - none of which Home Assistant can otherwise
    control. For a Cast session the `cast` integration carries richer session
    metadata, but both control the same playback.
    """

    _attr_name = None  # use the device name
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, coordinator: FosiCoordinator, entry: FosiConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_media_player"

    # ------------------------------------------------------------ player data

    @property
    def _player(self) -> dict[str, Any]:
        player = self.coordinator.data.get("player")
        return player if isinstance(player, dict) else {}

    @property
    def _controls(self) -> dict[str, Any]:
        """What the *current source* supports, as the device reports it.

        Absent for HDMI, optical and line-in, which is exactly right - there
        is no player behind those, so no transport is offered.

        Values matter as well as keys: a truthy value means supported, and an
        empty object means the key exists but the action is not available.
        """
        controls = self._player.get("controls")
        return controls if isinstance(controls, dict) else {}

    @property
    def _metadata(self) -> dict[str, Any]:
        return dig(self._player, "trackRoles", "mediaData", "metaData") or {}

    # ------------------------------------------------------------ properties

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = MediaPlayerEntityFeature.SELECT_SOURCE
        if self.coordinator.data.get("mute") is not None:
            features |= MediaPlayerEntityFeature.VOLUME_MUTE
        if self.coordinator.data.get("volume") is not None:
            features |= (
                MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.VOLUME_STEP
            )

        # The transport row is ALWAYS advertised. Never gate it on anything.
        #
        # Home Assistant has no disabled state for transport buttons -
        # supported_features is binary - so any condition at all makes the row
        # change shape as playback changes. Two earlier attempts both failed
        # on this: gating per-key moved buttons between playing and paused,
        # because the device drops next_ while paused; gating on "is there a
        # player" then collapsed the whole row when a Cast session ended,
        # because `controls` disappears with it.
        #
        # Pressing one with no player raises a clear error rather than a
        # device-level 500 - see _async_control.
        features |= (
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
        )

        controls = self._controls

        # These stay honest, because they do not sit in the button row: SEEK
        # makes the progress bar draggable, and the play modes render their
        # own controls. Advertising those falsely would mislead rather than
        # merely shift.
        #
        # Test the VALUE, not just the key: a Cast source reports
        # "playMode": {}, where the key exists but the empty object means
        # unsupported - changePlayMode there is rejected with "Play mode is
        # not supported". Confirmed on hardware.
        if controls.get(CONTROL_KEY_SEEK):
            features |= MediaPlayerEntityFeature.SEEK
        if controls.get(CONTROL_KEY_PLAY_MODE):
            features |= (
                MediaPlayerEntityFeature.SHUFFLE_SET
                | MediaPlayerEntityFeature.REPEAT_SET
            )
        return features

    @property
    def state(self) -> MediaPlayerState:
        """Player state, or plain ON when the device has no player running.

        CoordinatorEntity already reports the entity unavailable when polling
        fails, so there is no off state to represent.
        """
        return STATE_MAP.get(self._player.get("state"), MediaPlayerState.ON)

    @property
    def source_list(self) -> list[str]:
        return self._source_options

    @property
    def source(self) -> str | None:
        return self._active_source

    @property
    def is_volume_muted(self) -> bool | None:
        return self.coordinator.data.get("mute")

    @property
    def volume_level(self) -> float | None:
        return self.coordinator.volume_to_level(self.coordinator.data.get("volume"))

    @property
    def media_title(self) -> str | None:
        return clean(dig(self._player, "trackRoles", "title"))

    @property
    def media_artist(self) -> str | None:
        return clean(self._metadata.get("artist"))

    @property
    def media_album_name(self) -> str | None:
        return clean(self._metadata.get("album"))

    @property
    def media_album_artist(self) -> str | None:
        return clean(self._metadata.get("albumArtist"))

    @property
    def media_image_url(self) -> str | None:
        # Skin references like "skin:iconGooglecast" are device-internal and
        # not fetchable by the frontend.
        icon = clean(dig(self._player, "trackRoles", "icon"))
        return icon if isinstance(icon, str) and icon.startswith("http") else None

    @property
    def app_name(self) -> str | None:
        """Which service is streaming - "YouTube Music", "Spotify"..."""
        return clean(self._metadata.get("externalAppName")) or clean(
            self._metadata.get("serviceName")
        )

    @property
    def media_duration(self) -> float | None:
        # NOT in metaData - the device puts it under status.
        return ms_to_seconds(dig(self._player, "status", "duration"))

    @property
    def media_position(self) -> float | None:
        return ms_to_seconds(self.coordinator.data.get("play_time"))

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self.coordinator.last_updated

    @property
    def shuffle(self) -> bool | None:
        entry = PLAY_MODE_LOOKUP.get(self.coordinator.data.get("play_mode"))
        return entry[0] if entry else None

    @property
    def repeat(self) -> RepeatMode | None:
        entry = PLAY_MODE_LOOKUP.get(self.coordinator.data.get("play_mode"))
        return RepeatMode(entry[1]) if entry else None

    # ------------------------------------------------------------ commands

    async def _async_control(self, **payload: Any) -> None:
        """Invoke player:player/control with a plain JSON argument.

        activate() passes dicts through encode() untouched, which is the shape
        this node wants.

        The transport buttons are always shown, so this is where "there is
        nothing to control" gets reported. A clear message beats the device's
        own HTTP 500, which for a missing player reads "Directory is empty.
        No playable items found."
        """
        if not self._controls:
            raise HomeAssistantError(
                "Nothing is playing on this device. Transport applies to a "
                "streaming source, not to HDMI, optical or line in."
            )
        try:
            await self.coordinator.client.activate(PLAYER_CONTROL_PATH, payload)
        except NsdkError as exc:
            raise HomeAssistantError(f"Command failed: {exc}") from exc

    def _optimistic_state(self, state: MediaPlayerState) -> None:
        player = dict(self._player)
        player["state"] = DEVICE_STATE[state]
        self.coordinator.apply_optimistic(player=player)

    async def async_media_play(self) -> None:
        # "pause" is a toggle, so playing again would pause. Guard on state to
        # keep the service idempotent.
        if self.state is MediaPlayerState.PLAYING:
            return
        verb = CONTROL_PLAY if CONTROL_KEY_PLAY in self._controls else CONTROL_TOGGLE
        await self._async_control(control=verb)
        self._optimistic_state(MediaPlayerState.PLAYING)

    async def async_media_pause(self) -> None:
        if self.state is not MediaPlayerState.PLAYING:
            return
        await self._async_control(control=CONTROL_TOGGLE)
        self._optimistic_state(MediaPlayerState.PAUSED)

    async def async_media_stop(self) -> None:
        await self._async_control(control=CONTROL_STOP)
        self._optimistic_state(MediaPlayerState.IDLE)

    async def async_media_next_track(self) -> None:
        await self._async_control(control=CONTROL_NEXT)

    async def async_media_previous_track(self) -> None:
        await self._async_control(control=CONTROL_PREVIOUS)

    async def async_media_seek(self, position: float) -> None:
        await self._async_control(control=CONTROL_SEEK, time=int(position * 1000))
        self.coordinator.apply_optimistic(play_time=int(position * 1000))

    async def async_seek_relative(self, offset: float) -> None:
        """Seek by a signed offset in seconds. Custom service."""
        await self._async_control(
            control=CONTROL_SEEK, timeRelative=int(offset * 1000)
        )

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._async_set_play_mode(shuffle=shuffle)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        await self._async_set_play_mode(repeat=str(repeat))

    async def _async_set_play_mode(
        self, shuffle: bool | None = None, repeat: str | None = None
    ) -> None:
        """Recombine shuffle and repeat into the device's single playMode."""
        current = PLAY_MODE_LOOKUP.get(
            self.coordinator.data.get("play_mode"), (False, "off")
        )
        key = (
            current[0] if shuffle is None else shuffle,
            current[1] if repeat is None else repeat,
        )
        mode = PLAY_MODES.get(key)
        if mode is None:
            raise HomeAssistantError(f"Unsupported shuffle/repeat combination {key}")
        await self._async_control(control=CONTROL_PLAY_MODE, playMode=mode)
        self.coordinator.apply_optimistic(play_mode=mode)

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
