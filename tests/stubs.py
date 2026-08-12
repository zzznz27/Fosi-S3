"""Minimal stand-ins for homeassistant / aiohttp / voluptuous.

Just enough surface for fosi_audio to import so the pure logic can be exercised
without a Home Assistant install.
"""

import sys
import types
from enum import IntFlag


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _Generic:
    def __class_getitem__(cls, item):
        return cls


# ----------------------------------------------------------------- aiohttp
class ClientError(Exception): ...
class ClientTimeout:
    def __init__(self, total=None): self.total = total
class ClientSession: ...

_mod("aiohttp", ClientError=ClientError, ClientTimeout=ClientTimeout,
     ClientSession=ClientSession)


# -------------------------------------------------------------- voluptuous
class Invalid(Exception): ...
class Schema:
    def __init__(self, schema, **kw): self.schema = schema
class Required:
    def __init__(self, key, default=None): self.key, self.default = key, default
class All:
    def __init__(self, *validators): self.validators = validators
class Coerce:
    def __init__(self, type_): self.type = type_
class Range:
    def __init__(self, min=None, max=None): self.min, self.max = min, max

_mod("voluptuous", Schema=Schema, Required=Required, All=All, Coerce=Coerce,
     Range=Range, Invalid=Invalid)


# --------------------------------------------------------- homeassistant.*
class Platform:
    MEDIA_PLAYER = "media_player"
    SELECT = "select"

class ConfigEntry(_Generic):
    def __init__(self, entry_id="e1", title="S3", data=None, options=None):
        self.entry_id, self.title = entry_id, title
        self.data, self.options = data or {}, options or {}

class ConfigFlowResult(dict): ...
class ConfigFlow:
    def __init_subclass__(cls, **kw): super().__init_subclass__()
class OptionsFlow:
    config_entry = None

class HomeAssistantError(Exception): ...
class ConfigEntryNotReady(HomeAssistantError): ...
class ConfigEntryError(HomeAssistantError): ...

class DeviceInfo(dict):
    def __init__(self, **kw): super().__init__(**kw)

def format_mac(mac): return str(mac).lower().replace("-", ":")

class UpdateFailed(Exception): ...

class DataUpdateCoordinator(_Generic):
    def __init__(
        self, hass, logger, name=None, update_interval=None, config_entry=None
    ):
        self.hass, self.logger, self.name = hass, logger, name
        self.config_entry = config_entry
        self.data = {}
        self.last_update_success = True
        self.refreshes = 0
    async def async_request_refresh(self): self.refreshes += 1
    def async_set_updated_data(self, data): self.data = data

class CoordinatorEntity(_Generic):
    def __init__(self, coordinator): self.coordinator = coordinator
    @property
    def available(self): return self.coordinator.last_update_success

class SelectEntity: ...
class MediaPlayerEntity: ...
class MediaPlayerState:
    ON, OFF, IDLE = "on", "off", "idle"

class MediaPlayerEntityFeature(IntFlag):
    SELECT_SOURCE = 1
    VOLUME_MUTE = 2
    VOLUME_SET = 4
    VOLUME_STEP = 8

_mod("homeassistant")
_mod("homeassistant.config_entries", ConfigEntry=ConfigEntry, ConfigFlow=ConfigFlow,
     ConfigFlowResult=ConfigFlowResult, OptionsFlow=OptionsFlow)
_mod("homeassistant.const", CONF_HOST="host", CONF_SCAN_INTERVAL="scan_interval",
     Platform=Platform)
_mod("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
_mod("homeassistant.exceptions", HomeAssistantError=HomeAssistantError,
     ConfigEntryNotReady=ConfigEntryNotReady, ConfigEntryError=ConfigEntryError)
_mod("homeassistant.helpers")
_dr = _mod("homeassistant.helpers.device_registry", DeviceInfo=DeviceInfo,
           format_mac=format_mac, CONNECTION_NETWORK_MAC="mac")
sys.modules["homeassistant.helpers"].device_registry = _dr
_mod("homeassistant.helpers.aiohttp_client",
     async_get_clientsession=lambda hass: ClientSession())
_mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
_mod("homeassistant.helpers.update_coordinator",
     DataUpdateCoordinator=DataUpdateCoordinator, UpdateFailed=UpdateFailed,
     CoordinatorEntity=CoordinatorEntity)
_mod("homeassistant.components")
_mod("homeassistant.components.select", SelectEntity=SelectEntity)
_mod("homeassistant.components.media_player", MediaPlayerEntity=MediaPlayerEntity,
     MediaPlayerEntityFeature=MediaPlayerEntityFeature,
     MediaPlayerState=MediaPlayerState)
