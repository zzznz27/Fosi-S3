"""The device's 10-band graphic equaliser, one entity per band.

These live in the `ui:` tree at `ui:/settings/audio/eq/N`, which is the same
menu the device renders on its own web UI - so unlike the vestigial
`mediaPlayer/bass` and `/treble` nodes, changing one here is visibly the same
control the device itself offers.

The Fosi app's named presets - standard, rock, jazz and the rest - are not a
device concept. There is no preset node anywhere in the tree. The app simply
writes these ten values, which is why selecting a preset in the app and
setting bands from here overwrite each other: they are the same ten numbers.

That is also why there is no "night mode" switch here. Home Assistant scenes
already capture and restore a set of values, so a Night scene and a Normal
scene do the job with no state held in the integration - and nothing to go
stale when the curve is changed from the app.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FosiConfigEntry
from .api import NsdkError
from .const import EQ_BANDS, EQ_MAX, EQ_MIN, EQ_STEP, eq_key, eq_path
from .coordinator import FosiCoordinator
from .entity import FosiEntity

# Writes go straight to the device rather than through the coordinator, so
# cap concurrency - this is a small embedded webserver.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class FosiEqDescription(NumberEntityDescription):
    """One equaliser band and the node behind it."""

    path: str


EQ_CONTROLS: tuple[FosiEqDescription, ...] = tuple(
    FosiEqDescription(
        key=eq_key(index),
        translation_key=eq_key(index),
        path=eq_path(index),
        icon="mdi:tune-vertical",
    )
    for index, _label in enumerate(EQ_BANDS)
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FosiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(FosiEqBand(coordinator, entry, desc) for desc in EQ_CONTROLS)


class FosiEqBand(FosiEntity, NumberEntity):
    """One equaliser band, in dB."""

    entity_description: FosiEqDescription

    _attr_entity_category = EntityCategory.CONFIG
    # No device class: SIGNAL_STRENGTH is the only dB one and it means
    # something else entirely. A bare unit is honest here.
    _attr_native_unit_of_measurement = "dB"
    _attr_native_min_value = EQ_MIN
    _attr_native_max_value = EQ_MAX
    _attr_native_step = EQ_STEP
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: FosiCoordinator,
        entry: FosiConfigEntry,
        description: FosiEqDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.get(self.entity_description.key)
        # bool is an int subclass and would sail through untouched.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        # Settle first and send that, so the wire and the UI carry the same
        # number. The device clamps beyond +/-10 dB and snaps to the 0.5 dB
        # grid anyway; doing it here means the optimistic value cannot differ
        # from what the device will hold.
        target = settle(value)
        try:
            await self.coordinator.client.write(self.entity_description.path, target)
        except NsdkError as exc:
            raise HomeAssistantError(
                f"Could not set {self.entity_description.key}: {exc}"
            ) from exc
        # Never re-read after a write - the device reflects one ~0.5s late,
        # which is the trap that broke volume.
        self.coordinator.apply_optimistic(**{self.entity_description.key: target})


def settle(value: float) -> float:
    """What the device will hold after being asked for `value`.

    Measured on hardware: it clamps to the range rather than erroring, and
    snaps to the 0.5 dB grid rounding **half up** - 1.25 came back as 1.5 and
    -2.25 as -2.0. Python's round() is banker's rounding and disagrees on both,
    so floor(x + 0.5) is used instead.
    """
    clamped = max(EQ_MIN, min(EQ_MAX, float(value)))
    return math.floor(clamped / EQ_STEP + 0.5) * EQ_STEP
