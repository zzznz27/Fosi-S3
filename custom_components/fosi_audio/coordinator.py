"""Polling coordinator for the Fosi S3."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homeassistant.util import dt as dt_util

from .api import NsdkClient, NsdkConnectionError, NsdkError, NsdkPathError
from .const import (
    DEFAULT_VOLUME_STEPS,
    DOMAIN,
    PLAYER_STATE_PLAYING,
    POLL_PATHS,
    VOLUME_MAP_PATH,
)

_LOGGER = logging.getLogger(__name__)


class FosiCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll a fixed set of nSDK nodes.

    The device is frequently unreachable when idle on wifi, so a failed poll
    is treated as "temporarily unavailable" rather than an error worth
    logging loudly on every cycle.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: NsdkClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {client.host}",
            update_interval=timedelta(seconds=scan_interval),
            config_entry=entry,
        )
        self.client = client
        # Device identity as returned by async_verify. Deliberately not called
        # device_info - that name belongs to the entity-side DeviceInfo built
        # from this in entity.py, and confusing the two is easy.
        self.identity: dict[str, Any] = {}
        self.volume_map: list[float] = []
        # When the last successful poll landed, for media_position_updated_at.
        self.last_updated: datetime | None = None
        # Set once the listener exists; the poll uses it as a watchdog.
        self.event_listener: Any = None
        # False until we know whether this device has a usable volume curve.
        # A read that merely timed out must not be mistaken for "no curve".
        self._volume_map_settled = False
        # Paths that returned invalidPath - do not keep asking for them. Reset
        # on reload, which is the only cheap way to pick up a firmware update
        # that adds nodes.
        self._dead_paths: set[str] = set()

    async def async_load_static(self) -> None:
        """Fetch things that do not change: identity and the volume curve."""
        self.identity = await self.client.async_verify()
        await self._async_load_volume_map()
        # One line at setup so "why is there no volume slider" is answerable
        # without turning on debug logging.
        _LOGGER.info(
            "%s: identified as %s, volume curve %s",
            self.client.host,
            self.identity.get("model") or "unknown model",
            f"{len(self.volume_map)} points" if self.volume_map else "UNAVAILABLE",
        )

    async def _async_load_volume_map(self) -> None:
        """Read the device's own dB curve, if it has one.

        Absence is not an error - it only means volume is not advertised as a
        supported feature. Two entries are the minimum: the 0.0-1.0 mapping
        divides by len - 1.
        """
        try:
            volume_map = await self.client.read(VOLUME_MAP_PATH)
        except NsdkPathError:
            # Genuinely absent on this firmware. Settled - stop asking.
            self._volume_map_settled = True
            _LOGGER.debug("No volumeMap node; volume will not be offered")
            return
        except NsdkError as exc:
            # Transient. Deliberately NOT settled: a device asleep on wifi at
            # setup would otherwise never offer volume again for the life of
            # the config entry, with a reload the only way back.
            _LOGGER.debug("volumeMap read failed (%s); will retry", exc)
            return

        self._volume_map_settled = True
        if not isinstance(volume_map, list) or len(volume_map) < 2:
            _LOGGER.debug(
                "volumeMap unusable (%r); volume will not be offered", volume_map
            )
            return

        try:
            self.volume_map = [float(v) for v in volume_map]
        except (TypeError, ValueError):
            _LOGGER.debug("volumeMap is not numeric: %r", volume_map[:4])

    def _merge(self, values: dict[str, Any]) -> dict[str, Any]:
        data = dict(self.data or {})
        data.update(values)
        if "play_time" in values:
            # media_position_updated_at must move with the position, or Home
            # Assistant extrapolates from a stale stamp and runs the progress
            # bar past the end of the track.
            self.last_updated = dt_util.utcnow()
        return data

    def apply_update(self, **values: Any) -> None:
        """Publish values pushed by the device.

        Deliberately notifies listeners WITHOUT rescheduling the poll.

        async_set_updated_data resets the update interval, and the device
        pushes playTime about once a second - so using it here meant the
        scheduled poll never ran at all while anything was playing. That made
        the event stream the only path for every value, and a single missed
        event permanent: the input select sat on "HDMI In" through an entire
        AirPlay session because it never saw the one event that changed it,
        and nothing came along afterwards to correct it.

        Keeping the timer running means the poll stays a real safety net,
        which is the whole reason it is still here.
        """
        self.data = self._merge(values)
        self.async_update_listeners()

    def apply_optimistic(self, **values: Any) -> None:
        """Show an expected value immediately, before the device confirms it.

        The device reflects a write about half a second late, so a refresh
        fired straight after a command reads back the *old* value. Assume the
        command worked and publish that.

        Unlike a pushed event this DOES reset the interval, pushing the
        confirming read a full cycle away so it cannot race the value just
        published.
        """
        self.async_set_updated_data(self._merge(values))

    async def _async_update_data(self) -> dict[str, Any]:
        # Retry a curve that failed to load at setup. Costs one extra request
        # per cycle only while it is genuinely unknown, and stops for good
        # once the device has answered either way.
        if not self.volume_map and not self._volume_map_settled:
            await self._async_load_volume_map()

        data: dict[str, Any] = {}
        # Sequential on purpose. This is a small embedded webserver and the
        # settings tree is not worth hammering with concurrent requests.
        for key, path in POLL_PATHS.items():
            if path in self._dead_paths or self._skip(key, data):
                data[key] = None
                continue
            try:
                data[key] = await self.client.read(path)
            except NsdkPathError:
                _LOGGER.debug("Node %s absent on this firmware; skipping", path)
                self._dead_paths.add(path)
                data[key] = None
            except NsdkConnectionError as exc:
                # One unreachable node means the whole device is gone.
                raise UpdateFailed(str(exc)) from exc
            except NsdkError as exc:
                _LOGGER.debug("Read of %s failed: %s", path, exc)
                data[key] = None

        # Timestamp so media_position can be extrapolated between polls; the
        # progress bar sticks without it.
        self.last_updated = dt_util.utcnow()
        self._check_event_stream(data)
        return data

    def _check_event_stream(self, polled: dict[str, Any]) -> None:
        """Use the poll as a watchdog for the event stream.

        An event queue can stop delivering without ever failing - the device
        keeps answering pollQueue with nothing, which looks exactly like an
        idle system - so the listener cannot tell from silence alone. But if
        a real read disagrees with what events last reported, the stream has
        missed something and is not to be trusted.

        Seen in practice after switching inputs and resuming AirPlay without
        disconnecting the phone: the queue went quiet, and only the poll ever
        corrected the input.
        """
        if self.event_listener is None or not self.data:
            return
        stale = [
            key
            for key, value in polled.items()
            if key in self.data and self.data[key] != value
        ]
        # play_time always differs - it advances on its own - so it says
        # nothing about whether the stream is healthy.
        stale = [key for key in stale if key != "play_time"]
        if stale:
            _LOGGER.debug("Event stream missed %s; resubscribing", stale)
            self.event_listener.request_resubscribe()

    @staticmethod
    def _skip(key: str, data: dict[str, Any]) -> bool:
        """Whether this cycle can skip a request.

        Both player extras are meaningless unless something is actually
        playing, and every skipped key is one fewer round trip against a
        device that is often slow. Relies on POLL_PATHS reading "player"
        first.
        """
        if key != "play_time":
            return False
        player = data.get("player")
        if not isinstance(player, dict):
            return True
        return player.get("state") != PLAYER_STATE_PLAYING

    # ------------------------------------------------------------ mapping

    @property
    def volume_steps(self) -> int:
        """Highest valid player:volume value.

        The curve defines the range - 101 entries means 0..100 - so take it
        from the device rather than assuming a scale.
        """
        if len(self.volume_map) >= 2:
            return len(self.volume_map) - 1
        return DEFAULT_VOLUME_STEPS

    def volume_to_level(self, volume: int | None) -> float | None:
        """Device volume index -> HA's 0.0-1.0."""
        if volume is None:
            return None
        return max(0.0, min(1.0, volume / self.volume_steps))

    def level_to_volume(self, level: float) -> int:
        """HA's 0.0-1.0 -> device volume index."""
        return max(0, min(self.volume_steps, round(level * self.volume_steps)))

    def volume_to_db(self, volume: int | None) -> float | None:
        """dB the curve maps a volume index to. Display only."""
        if volume is None or not 0 <= volume < len(self.volume_map):
            return None
        return self.volume_map[volume]
