"""The Fosi S3 (StreamUnlimited StreamSDK) integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NsdkClient, NsdkError, NsdkUnsupportedError
from .const import DEFAULT_SCAN_INTERVAL
from .coordinator import FosiCoordinator
from .events import FosiEventListener

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
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


async def _async_reload_entry(hass: HomeAssistant, entry: FosiConfigEntry) -> None:
    """Reload when options (source map, poll interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)
