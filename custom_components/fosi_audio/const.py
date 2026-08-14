"""Constants for the Fosi S3 integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "fosi_audio"
MANUFACTURER: Final = "Fosi Audio"
DEFAULT_MODEL: Final = "S3"

# CONF_SCAN_INTERVAL comes from homeassistant.const - no need to redefine it.
CONF_SOURCES: Final = "sources"

DEFAULT_SCAN_INTERVAL: Final = 60
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
#
# Order matters: "player" must be read before "play_time", which the
# coordinator skips based on what the player just reported.
POLL_PATHS: Final[dict[str, str]] = {
    "source": SOURCE_STATE_PATH,
    "mute": "settings:/mediaPlayer/mute",
    "volume": "player:volume",
    "output_mode": "settings:/custom/audioOutputMode",
    "player": "player:player/data/value",
    "play_time": "player:player/data/playTime",
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

# --------------------------------------------------------------------------
# Transport.
#
# player:player/control is a single action node taking a plain JSON object -
# NOT a child node per verb, and NOT a tagged union. Names came from
# /webclient/index.js; player: does not enumerate, so getRows on the control
# node returns invalidPath and none of this is discoverable by crawling.
#
# CONFIRMED on hardware:
#   {"control": "pause"} is a play/pause TOGGLE. The device's own web client
#   sends it for both directions of its play/pause button, which is why the
#   controls object below advertises "pause" and never "play".
#
#   {"control": "play"} returns HTTP 500 "Directory is empty. No playable
#   items found." on a Cast source - it is the wrong verb there, not proof
#   that resuming is impossible.
#
#   The web client also sends a top-level {"platform": "windows"} beside
#   path/role/value. Verified optional; we do not send it.
# --------------------------------------------------------------------------

# The device distinguishes two things and so do we:
#
#   protocol - how audio is arriving: Google Cast, AirPlay, Spotify Connect
#   service  - what is playing it:    YouTube Music, Spotify, Apple Music
#
# serviceID -> protocol display name.
PROTOCOL_NAMES: Final[dict[str, str]] = {
    "googlecast": "Google Cast",
    "airplay": "AirPlay",
    "spotify": "Spotify Connect",
    "spotifyconnect": "Spotify Connect",
    "tidal": "Tidal Connect",
    "tidalconnect": "Tidal Connect",
    "qobuz": "Qobuz Connect",
    "qobuzconnect": "Qobuz Connect",
    "roon": "Roon",
    "bluetooth": "Bluetooth",
    "upnp": "UPnP",
    "dlna": "DLNA",
}

# serviceID -> the service it implies, for protocols that only ever carry one.
#
# Cast and AirPlay carry anything, so the service has to come from
# externalAppName; Cast populates it, AirPlay does not. The Connect protocols
# are single-service by definition, so the protocol name tells us the service
# even when the device names no app.
IMPLIED_SERVICES: Final[dict[str, str]] = {
    "spotify": "Spotify",
    "spotifyconnect": "Spotify",
    "tidal": "Tidal",
    "tidalconnect": "Tidal",
    "qobuz": "Qobuz",
    "qobuzconnect": "Qobuz",
    "roon": "Roon",
}

PLAYER_CONTROL_PATH: Final = "player:player/control"
PLAYER_DATA_PATH: Final = "player:player/data/value"
PLAY_TIME_PATH: Final = "player:player/data/playTime"

# Values seen in player:player/data/value["state"].
PLAYER_STATE_PLAYING: Final = "playing"
PLAYER_STATE_PAUSED: Final = "paused"
PLAYER_STATE_STOPPED: Final = "stopped"

CONTROL_TOGGLE: Final = "pause"  # toggles play/pause - see above
CONTROL_PLAY: Final = "play"
CONTROL_STOP: Final = "stop"
CONTROL_NEXT: Final = "next"
CONTROL_PREVIOUS: Final = "previous"
# Confirmed verbs, deliberately not exposed as entities: no source tested so
# far advertises them, so the buttons were unavailable in every real state.
CONTROL_LIKE: Final = "like"
CONTROL_DISLIKE: Final = "dislike"

# Keys of the device's "controls" object, which advertises what the *current
# source* supports. Note next_ carries a trailing underscore, mirroring the
# i32_/bool_ convention - getting this wrong silently drops the next button.
CONTROL_KEY_PAUSE: Final = "pause"
CONTROL_KEY_PLAY: Final = "play"
CONTROL_KEY_NEXT: Final = "next_"
CONTROL_KEY_PREVIOUS: Final = "previous"
CONTROL_KEY_STOP: Final = "stop"

# Analogue vs digital output - an either/or toggle on this hardware.
# settings:/custom/audioOutputMode: false = RCA/XLR Out, true = Optical Out.
OUTPUT_MODE_PATH: Final = "settings:/custom/audioOutputMode"
OUTPUT_MODE_RCA: Final = "RCA/XLR Out"
OUTPUT_MODE_OPTICAL: Final = "Optical Out"
OUTPUT_MODE_ACTIONS: Final[dict[str, str]] = {
    OUTPUT_MODE_RCA: "ui:/custom/audioOutputModeFalse",
    OUTPUT_MODE_OPTICAL: "ui:/custom/audioOutputModeTrue",
}
