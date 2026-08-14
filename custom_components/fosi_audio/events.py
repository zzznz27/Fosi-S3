"""Push updates via the device's event queue.

The nSDK exposes a subscribe-and-long-poll API. Subscribing to the nodes we
would otherwise poll turns a 15-second worst case into roughly 130 ms,
measured on hardware, and it covers changes the device makes on its own -
a track advancing, or someone using the remote.

    POST /api/event/modifyQueue  {queueId, subscribe[], unsubscribe[]}
    GET  /api/event/pollQueue?queueId=&timeout=

An empty queueId creates a queue. Events come back as a list of
{path, itemValue} where itemValue is the usual tagged union - the player
node uses a "playLogicData" type, which decode() already handles.

Polling continues underneath this as a safety net. If the stream dies
silently the entities degrade to the old behaviour rather than freezing,
which matters on a device that sleeps its radio on wifi.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from .api import NsdkClient, NsdkError, decode
from .const import POLL_PATHS

if TYPE_CHECKING:
    from .coordinator import FosiCoordinator

_LOGGER = logging.getLogger(__name__)

# How long the device holds an idle poll open before answering empty.
POLL_TIMEOUT_MS = 30000

# Reconnect backoff. The device's own web client waits a flat second; grow it
# so an unreachable device is not hammered, but stay short enough that a brief
# blip recovers quickly.
BACKOFF_START = 1.0
BACKOFF_MAX = 60.0

SUBSCRIBE_TYPE = "itemWithValue"

# Rebuild the queue this often even when nothing has gone wrong.
#
# A queue can stop delivering without ever failing: the device keeps answering
# pollQueue with nothing, which is indistinguishable from an idle system. That
# leaves no error to react to and makes silence useless as a signal - seen in
# practice after switching inputs and resuming AirPlay without disconnecting
# the phone, where the stream went quiet and only the poll ever corrected the
# input.
#
# A flat recycle needs no heuristic to decide whether the stream is healthy,
# and unlike watching for disagreement between the poll and the stream it
# cannot be fooled by an optimistic write the device has not reflected yet, or
# by an event that lands mid-poll. It also heals a queue that died while
# nothing was happening, which a change-triggered check never sees.
QUEUE_MAX_AGE = 900.0


class FosiEventListener:
    """Keeps an event subscription alive and feeds the coordinator."""

    def __init__(self, coordinator: FosiCoordinator, client: NsdkClient) -> None:
        self._coordinator = coordinator
        self._client = client
        self._task: asyncio.Task | None = None
        # Reverse of POLL_PATHS, so an event path maps back to a data key.
        self._paths = {path: key for key, path in POLL_PATHS.items()}

    def start(self) -> None:
        self._task = self._coordinator.hass.async_create_background_task(
            self._run(), name=f"fosi_audio events {self._client.host}"
        )

    async def async_stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    # ------------------------------------------------------------ internals

    @property
    def _subscriptions(self) -> list[dict[str, str]]:
        return [{"path": path, "type": SUBSCRIBE_TYPE} for path in POLL_PATHS.values()]

    @staticmethod
    def expired(created: float, now: float) -> bool:
        """Whether a queue created at `created` is due to be recycled."""
        return now - created >= QUEUE_MAX_AGE

    async def _release(self, queue_id: str) -> None:
        """Unsubscribe a queue we are about to abandon.

        Best effort only - it is being dropped either way, and a queue the
        device has already forgotten will refuse this. Worth attempting so
        recycled queues do not pile up on a small embedded webserver.
        """
        with contextlib.suppress(Exception):
            await self._client.modify_queue(queue_id, [], self._subscriptions)

    async def _run(self) -> None:
        """Subscribe, then long-poll forever, re-subscribing on failure."""
        backoff = BACKOFF_START
        queue_id: str | None = None
        created = 0.0

        while True:
            try:
                if queue_id is not None and self.expired(created, time.monotonic()):
                    _LOGGER.debug(
                        "%s: recycling event queue %s", self._client.host, queue_id
                    )
                    await self._release(queue_id)
                    queue_id = None

                if queue_id is None:
                    queue_id = await self._client.modify_queue(
                        "", self._subscriptions
                    )
                    created = time.monotonic()
                    _LOGGER.debug(
                        "%s: event queue %s subscribed to %d nodes",
                        self._client.host,
                        queue_id,
                        len(self._subscriptions),
                    )

                events = await self._client.poll_queue(queue_id, POLL_TIMEOUT_MS)
                backoff = BACKOFF_START
                if events:
                    self._apply(events)

            except asyncio.CancelledError:
                raise
            except NsdkError as exc:
                # Drop the queue: the device forgets it across a reboot or a
                # network drop, and polling a stale id never recovers.
                queue_id = None
                _LOGGER.debug(
                    "%s: event stream failed (%s); retrying in %.0fs",
                    self._client.host,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
            except Exception:  # noqa: BLE001 - a background task must not die
                queue_id = None
                _LOGGER.exception(
                    "%s: unexpected error in event stream; retrying in %.0fs",
                    self._client.host,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

    def _apply(self, events: Any) -> None:
        """Turn a batch of events into a coordinator update."""
        updates = self.parse(events, self._paths)
        if updates:
            _LOGGER.debug("%s: pushed %s", self._client.host, list(updates))
            self._coordinator.apply_update(**updates)

    @staticmethod
    def parse(events: Any, paths: dict[str, str]) -> dict[str, Any]:
        """Extract {data key: value} from a pollQueue reply.

        Events for paths we do not track are ignored rather than dropped
        loudly - subscribing is cheap and the device may volunteer extras.
        """
        if not isinstance(events, list):
            return {}
        updates: dict[str, Any] = {}
        for event in events:
            if not isinstance(event, dict):
                continue
            key = paths.get(event.get("path"))
            if key is None or "itemValue" not in event:
                continue
            updates[key] = decode(event["itemValue"])
        return updates
