"""Config and options flow for Fosi S3."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import NsdkClient, NsdkConnectionError, NsdkError, NsdkUnsupportedError
from .const import (
    CONF_SOURCES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOURCES,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


def validate_sources(raw: str) -> dict[str, dict[str, Any]]:
    """Parse and check a user-supplied source map.

    An entry needs a "path" to be selectable, or a "state" to be reportable.
    Entries with only "state" are legitimate and shipped by default - the
    network sources cannot be commanded, only observed - so requiring "path"
    on everything would reject the integration's own defaults.
    """
    if not raw.strip():
        return DEFAULT_SOURCES

    sources = json.loads(raw)
    if not isinstance(sources, dict) or not sources:
        raise ValueError("must be a non-empty JSON object")

    for name, spec in sources.items():
        if not isinstance(spec, dict):
            raise ValueError(f"{name!r} must be an object")
        if "path" not in spec and "state" not in spec:
            raise ValueError(
                f"{name!r} needs a 'path' to be selectable or a 'state' to be reported"
            )
        role = spec.get("role", "value")
        if role not in ("value", "activate"):
            raise ValueError(f"{name!r} has unknown role {role!r}")
        if "path" in spec and role == "value" and "value" not in spec:
            raise ValueError(f"{name!r} uses role 'value' but has no 'value' to write")
    return sources


class FosiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_host: str = ""
        self._discovered_name: str = ""

    async def _async_probe(
        self, host: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Verify a host. Returns (identity, error_key)."""
        client = NsdkClient(host, async_get_clientsession(self.hass))
        try:
            return await client.async_verify(), None
        except NsdkUnsupportedError:
            return None, "not_streamsdk"
        except NsdkConnectionError:
            return None, "cannot_connect"
        except NsdkError:
            # A transient 500 from a half-woken device looks like this.
            return None, "cannot_connect"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            info, error = await self._async_probe(host)
            if error:
                errors["base"] = error
            else:
                # Fall back to the host so a device whose MAC node we cannot
                # read still cannot be added twice.
                mac = info.get("mac")
                await self.async_set_unique_id(
                    dr.format_mac(str(mac)) if mac else host
                )
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})

                title = info.get("name") or info.get("model") or host
                return self.async_create_entry(
                    title=str(title), data={CONF_HOST: host}
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Offer a device found over Cast's mDNS advertisement.

        The manifest matches on the Cast `md` (model) property, which narrows
        _googlecast._tcp down but does not prove anything - "S3" is a name
        another vendor could use, and a household typically has several Cast
        devices. So confirm over the nSDK API before showing the user
        anything; an abort here never reaches the UI.
        """
        host = str(discovery_info.host)
        info, error = await self._async_probe(host)
        if error:
            return self.async_abort(reason=error)

        mac = info.get("mac")
        await self.async_set_unique_id(dr.format_mac(str(mac)) if mac else host)
        # Also follows the device to a new address if DHCP moved it.
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._discovered_host = host
        self._discovered_name = str(
            info.get("name")
            or discovery_info.properties.get("fn")
            or info.get("model")
            or host
        )
        # Shown on the discovery card in the UI.
        self.context["title_placeholders"] = {"name": self._discovered_name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before adding a device we found rather than adding it silently."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered_name,
                data={CONF_HOST: self._discovered_host},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._discovered_name},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point an existing entry at a new address.

        DHCP moves devices. Without this the only way to follow is to delete
        and re-add, which mints a new entry_id and so renames every entity and
        breaks every automation referring to them.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            info, error = await self._async_probe(host)
            if error:
                errors["base"] = error
            else:
                # Refuse to repoint an entry at a *different* device - that
                # would silently rebind every entity to the wrong hardware.
                mac = info.get("mac")
                if mac:
                    await self.async_set_unique_id(dr.format_mac(str(mac)))
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FosiOptionsFlow()


class FosiOptionsFlow(OptionsFlow):
    """Let the user correct the source map without touching code."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                sources = validate_sources(user_input.get(CONF_SOURCES, ""))
            except (json.JSONDecodeError, ValueError) as exc:
                errors[CONF_SOURCES] = "invalid_sources"
                _LOGGER.debug("Rejected source map: %s", exc)
            else:
                return self.async_create_entry(
                    data={
                        CONF_SOURCES: sources,
                        CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    }
                )

        options = self.config_entry.options
        current = options.get(CONF_SOURCES) or DEFAULT_SOURCES
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SOURCES, default=json.dumps(current, indent=2)
                ): str,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
