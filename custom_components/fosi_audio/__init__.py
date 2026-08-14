"""The Fosi S3 (StreamUnlimited StreamSDK) integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NsdkClient, NsdkError, NsdkUnsupportedError
from .const import DEFAULT_SCAN_INTERVAL
from .coordinator import FosiCoordinator
from .events import FosiEventListener

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
]


@dataclass
class FosiRuntimeData:
    """Objects shared across platforms for one config entry."""

    client: NsdkClient
    coordinator: FosiCoordinator
    listener: FosiEventListener


type FosiConfigEntry = ConfigEntry[FosiRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: FosiConfigEntry) -> bool:
    """Set up from a config entry."""
    session = async_get_clientsession(hass)
    client = NsdkClient(entry.data[CONF_HOST], session)

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = FosiCoordinator(hass, entry, client, scan_interval)

    try:
        await coordinator.async_load_static()
    except NsdkUnsupportedError as exc:
        # Not a StreamSDK device. Retrying will never fix that.
        raise ConfigEntryError(
            f"{client.host} is not a StreamSDK device: {exc}"
        ) from exc
    except NsdkError as exc:
        # Very likely just asleep on wifi - HA will retry.
        raise ConfigEntryNotReady(f"Cannot reach {client.host}: {exc}") from exc

    await coordinator.async_config_entry_first_refresh()

    # Push updates. Polling stays on underneath as a safety net, so a stream
    # that dies silently degrades to slow rather than freezing the entities.
    listener = FosiEventListener(coordinator, client)
    listener.start()

    _async_remove_stale_entities(hass, entry)

    entry.runtime_data = FosiRuntimeData(
        client=client, coordinator=coordinator, listener=listener
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FosiConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.listener.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def _async_remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop entities from platforms this integration no longer provides.

    Home Assistant keeps registry entries for entities that stop being
    created, so they linger as unavailable rows forever. The like/dislike
    buttons were removed in 0.5.0 and would otherwise haunt every device page
    that ever ran a build containing them.
    """
    registry = er.async_get(hass)
    live = {platform.value for platform in PLATFORMS}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain not in live:
            _LOGGER.debug("Removing stale entity %s", entity.entity_id)
            registry.async_remove(entity.entity_id)


async def _async_reload_entry(hass: HomeAssistant, entry: FosiConfigEntry) -> None:
    """Reload when options (source map, poll interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)
