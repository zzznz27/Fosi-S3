"""Constants for the Fosi S3 integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "fosi_audio"
MANUFACTURER: Final = "Fosi Audio"
DEFAULT_MODEL: Final = "S3"

# CONF_SCAN_INTERVAL comes from homeassistant.const - no need to redefine it.
CONF_SOURCES: Final = "sources"

DEFAULT_SCAN_INTERVAL: Final = 15
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 600

# --------------------------------------------------------------------------
# Source map.
#
# CONFIRMED: inputs are `action` nodes in the ui: namespace - the same tree
# the device renders on its front panel. Discovered via getRows on "ui:":
#
#   ui:/hdmi            "Hdmi In"
#   ui:/spdifin         "Optical In"
#   ui:/aux             "Line In"
#   ui:/shortBluetooth  "Bluetooth"
#
# Actions are invoked with setData role "activate" (NSDK_Activate).
#
# NOT the mechanism: settings:/custom/lastAudioSource. It accepts any i32,
# including out-of-range values, and switches nothing - the host MCU writes
# it, it does not read it. It may still be a usable *state* source, which is
# what the optional "state" key below is for.
#
# Each entry is:
#   path   - node to act on. Omit it for an input that can be reported but
#            not commanded; such an entry never appears as an option.
#   role   - "activate" for action nodes, "value" to write a value
#   value  - payload for role "value" (required); optional for "activate"
#   state  - value of settings:/custom/lastAudioSource when this input is
#            live, used only to report the current source. Populate by
#            activating each input and reading that node back.
# --------------------------------------------------------------------------

SOURCE_STATE_PATH: Final = "settings:/custom/lastAudioSource"

DEFAULT_SOURCES: Final[dict[str, dict]] = {
    # "state" values confirmed on hardware by activating each action and
    # reading settings:/custom/lastAudioSource back. The numbering is the
    # host MCU's own and does not follow the ui: tree order.
    "Bluetooth": {"path": "ui:/shortBluetooth", "role": "activate", "state": 1},
    "Line In": {"path": "ui:/aux", "role": "activate", "state": 2},
    "HDMI In": {"path": "ui:/hdmi", "role": "activate", "state": 3},
    "Optical In": {"path": "ui:/spdifin", "role": "activate", "state": 4},
    # No action node exists for the network sources - Cast, AirPlay, Roon and
    # the Connect protocols seize the device when something streams to them,
    # they are not selected. State-only: reported, never offered as an option.
    "Network": {"state": 0},
}

# Nodes polled every update. Missing nodes are tolerated and reported as None.
# Keep this list short: every entry is one HTTP round trip per cycle against a
# small embedded webserver that is often slow to answer.
POLL_PATHS: Final[dict[str, str]] = {
    "source": SOURCE_STATE_PATH,
    "mute": "settings:/mediaPlayer/mute",
    "volume": "player:volume",
    "output_mode": "settings:/custom/audioOutputMode",
}

VOLUME_MAP_PATH: Final = "settings:/mediaPlayer/volumeMap"
MUTE_PATH: Final = "settings:/mediaPlayer/mute"

# Volume lives in the player: namespace, which does not enumerate - the name
# came out of /webclient/index.js. player:volume is the device's own 0-100
# volume index, and volumeMap[volume] is the dB it produces (verified exactly
# on hardware: volume 52 -> attenuation -28 == volumeMap[52]).
#
# DO NOT write settings:/mediaPlayer/attenuation instead. It is writable, and
# it does change the output, but it is a *reflection* of player:volume - so
# writing it leaves the device's own volume index stale and the web UI, front
# panel and MCU then disagree with Home Assistant. Kept here only to name the
# trap.
VOLUME_PATH: Final = "player:volume"
ATTENUATION_PATH: Final = "settings:/mediaPlayer/attenuation"  # read-only, derived

# Fallback scale when the device gives us no volumeMap to size the range from.
DEFAULT_VOLUME_STEPS: Final = 100

# Analogue vs digital output - an either/or toggle on this hardware.
# settings:/custom/audioOutputMode: false = RCA/XLR Out, true = Optical Out.
OUTPUT_MODE_PATH: Final = "settings:/custom/audioOutputMode"
OUTPUT_MODE_RCA: Final = "RCA/XLR Out"
OUTPUT_MODE_OPTICAL: Final = "Optical Out"
OUTPUT_MODE_ACTIONS: Final[dict[str, str]] = {
    OUTPUT_MODE_RCA: "ui:/custom/audioOutputModeFalse",
    OUTPUT_MODE_OPTICAL: "ui:/custom/audioOutputModeTrue",
}
