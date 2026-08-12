"""Shared entity plumbing and source-map handling.

Lives here rather than in select.py so media_player.py does not have to import
another platform module to get at it.
"""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FosiConfigEntry
from .api import NsdkError
from .const import CONF_SOURCES, DEFAULT_MODEL, DEFAULT_SOURCES, DOMAIN, MANUFACTURER
from .coordinator import FosiCoordinator


def match_source(sources: dict, state: Any) -> str | None:
    """Map the polled lastAudioSource value back to a source name.

    Activate-style entries carry no readable state of their own, so they
    declare it explicitly with "state". Value-style entries fall back to
    the value they write.
    """
    if state is None:
        return None
    for name, spec in sources.items():
        expected = spec.get("state")
        if expected is None and spec.get("role", "value") == "value":
            expected = spec.get("value")
        if expected is not None and expected == state:
            return name
    return None


def selectable(sources: dict) -> list[str]:
    """Sources that can actually be commanded. Excludes state-only entries."""
    return [name for name, spec in sources.items() if spec.get("path")]


def resolve_sources(entry: FosiConfigEntry) -> dict[str, dict[str, Any]]:
    """Source map from options, falling back to the shipped defaults."""
    return entry.options.get(CONF_SOURCES) or DEFAULT_SOURCES


async def async_apply_source(
    coordinator: FosiCoordinator, spec: dict[str, Any], label: str
) -> None:
    """Apply one source-map entry.

      {"path": ..., "role": "activate"} -> setData role "activate"
      {"path": ..., "value": n}         -> setData role "value"
    """
    path = spec.get("path")
    if not path:
        raise HomeAssistantError(f"{label} cannot be selected, only reported")

    role = spec.get("role", "value")
    try:
        if role == "activate":
            await coordinator.client.activate(path, spec.get("value"))
        elif "value" in spec:
            await coordinator.client.write(path, spec["value"])
        else:
            raise HomeAssistantError(
                f"{label} uses role 'value' but has no 'value' to write"
            )
    except NsdkError as exc:
        raise HomeAssistantError(f"Could not select {label}: {exc}") from exc

    # Show the new input at once. Same precedence match_source uses, so the
    # optimistic value is guaranteed to map back to this same entry.
    expected = spec.get("state")
    if expected is None and role == "value":
        expected = spec.get("value")
    if expected is not None:
        coordinator.apply_optimistic(source=expected)
    else:
        # Nothing to predict - fall back to asking the device.
        await coordinator.async_request_refresh()


def model_name(identity: dict[str, Any]) -> str:
    """Model with the brand stripped off the front.

    The device reports productName as "Fosi S3" while the manufacturer is
    "Fosi Audio", so the device card would read "Fosi S3 by Fosi Audio". HA
    shows the manufacturer separately, so the model only needs to be "S3".
    """
    model = str(identity.get("model") or DEFAULT_MODEL).strip()
    manufacturer = str(identity.get("manufacturer") or MANUFACTURER).strip()
    brand = manufacturer.split()[0] if manufacturer else ""
    if brand and model.lower().startswith(f"{brand.lower()} "):
        model = model[len(brand) + 1:].strip()
    return model or DEFAULT_MODEL


class FosiEntity(CoordinatorEntity[FosiCoordinator]):
    """Shared device wiring."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FosiCoordinator, entry: FosiConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        identity = coordinator.identity

        # Identify the device by entry_id, not by MAC. primaryMacAddress is an
        # unconfirmed node name, so it may resolve on one setup and not the
        # next - keying identity on it would strand entities on a new device.
        # The MAC goes in `connections` instead, where it is additive: it lets
        # HA merge this device with the Cast device for the same S3, so input
        # switching and Cast transport land on one device card.
        mac = identity.get("mac")
        formatted_mac = dr.format_mac(str(mac)) if mac else None

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections=(
                {(dr.CONNECTION_NETWORK_MAC, formatted_mac)} if formatted_mac else set()
            ),
            manufacturer=identity.get("manufacturer") or MANUFACTURER,
            model=model_name(identity),
            name=identity.get("name") or entry.title,
            sw_version=identity.get("sw_version"),
            serial_number=identity.get("serial"),
            configuration_url=f"http://{coordinator.client.host}/",
        )


class FosiSourceEntity(FosiEntity):
    """Source-map behaviour shared by the input select and the media player.

    Both entities have to answer the same three questions - what can be
    selected, what is actually live, and what of that HA is allowed to show as
    the current option - so they answer them the same way.
    """

    def __init__(self, coordinator: FosiCoordinator, entry: FosiConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._sources = resolve_sources(entry)

    @property
    def _commandable_sources(self) -> list[str]:
        """Sources with a path - the ones that can actually be switched to."""
        return selectable(self._sources)

    @property
    def _active_source(self) -> str | None:
        """The real input, including ones that cannot be commanded."""
        return match_source(self._sources, self.coordinator.data.get("source"))

    @property
    def _source_options(self) -> list[str]:
        """Commandable sources, plus the live one when it is not commandable.

        When a streaming protocol seizes the device the real source is
        "Network", which has no action node. HA requires the reported option
        to be a member of the option list, so leaving it out makes the entity
        read as blank exactly when it has the most to say.

        Adding it only while it is live means the list never offers something
        that cannot be acted on: if it is present, it is already current, so
        selecting it is a no-op rather than an error.
        """
        options = self._commandable_sources
        active = self._active_source
        if active and active not in options:
            options = [*options, active]
        return options

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "raw_source_index": self.coordinator.data.get("source"),
            "active_source": self._active_source,
            # Which of the options can actually be commanded right now.
            "selectable_sources": self._commandable_sources,
        }

    async def _async_apply_source(self, name: str) -> None:
        if name == self._active_source and name not in self._commandable_sources:
            # Already on it, and there is no action node to invoke. Selecting
            # the live network source is a no-op, not a failure.
            return
        spec = self._sources.get(name)
        if spec is None:
            raise HomeAssistantError(f"Unknown source {name!r}")
        await async_apply_source(self.coordinator, spec, name)
