"""Diagnostics for the Fosi Audio integration.

The point of this file is supporting hardware nobody here owns. Every node
path in this integration was reverse-engineered from one S3, and the settings
tree already leaks an S3 Lite and an S5. When someone reports that their
device half-works, this dump answers "which nodes actually responded and what
did they say" in one click, without asking them to run scripts.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import FosiConfigEntry
from .const import POLL_PATHS

# The serial and MAC identify the owner's specific unit, and deviceName is
# often a room or a person. None of it is needed to debug node paths.
REDACT = {"serial", "mac", "name", "deviceName"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FosiConfigEntry
) -> dict[str, Any]:
    """Dump what the device told us, minus anything identifying."""
    coordinator = entry.runtime_data.coordinator
    data = dict(coordinator.data or {})
    player = data.get("player") if isinstance(data.get("player"), dict) else {}

    return {
        "entry": {
            "options": dict(entry.options),
            # Host is a private LAN address, but redact it anyway - it says
            # something about the reporter's network and nothing about the bug.
            "host_configured": bool(entry.data.get("host")),
            "version": entry.version,
        },
        "identity": async_redact_data(coordinator.identity, REDACT),
        "polled": async_redact_data(data, REDACT),
        # Pulled out because it is the first thing to look at: it decides
        # which transport features are offered.
        "controls": player.get("controls"),
        "player_state": player.get("state"),
        "volume": {
            "steps": coordinator.volume_steps,
            "curve_points": len(coordinator.volume_map),
            # The whole curve is 101 floats; the ends and the shape are what
            # matter and the rest is noise in a bug report.
            "curve_range": (
                [coordinator.volume_map[0], coordinator.volume_map[-1]]
                if coordinator.volume_map
                else None
            ),
        },
        "paths": {
            "polled": POLL_PATHS,
            # A node in here returned invalidPath and is not asked for again,
            # which is the usual reason a feature is silently missing.
            "dead": sorted(coordinator._dead_paths),
        },
        "last_update_success": coordinator.last_update_success,
    }
