"""Behavioural checks against the real fosi_audio modules, Home Assistant stubbed.

Runs with no dependencies at all - `stubs` fabricates just enough of
homeassistant, aiohttp and voluptuous for the integration to import, so the
code under test is the code that ships.

    python tests/test_fosi.py
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Must be imported before anything that pulls in the integration.
import stubs  # noqa: E402, F401

sys.path.insert(0, str(HERE.parent / "custom_components"))

from fosi_audio import api, const  # noqa: E402
from fosi_audio.api import NsdkConnectionError, NsdkPathError  # noqa: E402
from fosi_audio.config_flow import validate_sources  # noqa: E402
from fosi_audio.coordinator import FosiCoordinator  # noqa: E402
from fosi_audio import events as events_mod  # noqa: E402
from fosi_audio.events import FosiEventListener  # noqa: E402
from fosi_audio.sensor import SENSORS, FosiPlayerSensor  # noqa: E402
from fosi_audio import diagnostics as diag  # noqa: E402
from homeassistant.components.media_player import (  # noqa: E402
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from fosi_audio.media_player import (  # noqa: E402
    FosiMediaPlayer,
)
from fosi_audio.entity import (  # noqa: E402
    FosiSourceEntity,
    async_apply_source,
    match_source,
    model_name,
    selectable,
    streaming_protocol,
    streaming_service,
)

SOURCES = const.DEFAULT_SOURCES
COMMANDABLE = ["Bluetooth", "Line In", "HDMI In", "Optical In"]


class Results:
    """Minimal assertion recorder - keeps the suite dependency-free."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, label, got, want) -> None:
        if got == want:
            self.ok(label)
        else:
            self.failed += 1
            print(f"  FAIL  {label}")
            print(f"          got  {got!r}")
            print(f"          want {want!r}")

    def raises(self, label, fn, exc) -> None:
        try:
            fn()
        except exc:
            self.ok(label)
        except Exception as err:  # noqa: BLE001 - any other type is a failure
            self.failed += 1
            print(f"  FAIL  {label}: raised {type(err).__name__}: {err}")
        else:
            self.failed += 1
            print(f"  FAIL  {label}: did not raise")

    def ok(self, label) -> None:
        self.passed += 1
        print(f"  PASS  {label}")


R = Results()


# ------------------------------------------------------------------- fakes


class FakeClient(api.NsdkClient):
    """Records setData calls instead of sending them."""

    def __init__(self) -> None:
        self.sent = []

    async def set_data(self, path, value, role="value"):
        self.sent.append((path, value, role))
        return None


class FakeCoordinator:
    """Enough coordinator for the entity helpers.

    Borrows the real methods so the shipped implementations are what get
    exercised, not reimplementations of them.
    """

    _merge = FosiCoordinator._merge
    apply_update = FosiCoordinator.apply_update
    apply_optimistic = FosiCoordinator.apply_optimistic
    volume_to_level = FosiCoordinator.volume_to_level
    level_to_volume = FosiCoordinator.level_to_volume
    volume_to_db = FosiCoordinator.volume_to_db
    volume_steps = FosiCoordinator.volume_steps

    def __init__(self, source=0) -> None:
        self.data = {"source": source, "mute": False}
        self.refreshes = 0
        self.client = FakeClient()
        self.volume_map = []
        self.last_updated = None
        self.last_update_success = True
        self._poll_in_flight = False
        self._pushed_during_poll = set()
        # async_set_updated_data reschedules the poll; async_update_listeners
        # does not. Which one gets called is the whole point of the fix.
        self.reschedules = 0
        self.notifications = 0

    def __init_subclass__(cls, **kw):  # noqa: D105
        super().__init_subclass__(**kw)

    def async_set_updated_data(self, data) -> None:
        self.data = data
        self.reschedules += 1

    def async_update_listeners(self) -> None:
        self.notifications += 1

    async def async_request_refresh(self) -> None:
        self.refreshes += 1


class FakeSourceEntity(FosiSourceEntity):
    """FosiSourceEntity without the CoordinatorEntity constructor."""

    def __init__(self, source_index) -> None:
        self._sources = SOURCES
        self.coordinator = FakeCoordinator(source_index)


class CurveLoader:
    """Drives the real _async_load_volume_map against a failing read."""

    _async_load_volume_map = FosiCoordinator._async_load_volume_map

    def __init__(self, exc) -> None:
        self.volume_map = []
        self._volume_map_settled = False
        self.client = _FailingClient(exc)


class _FailingClient:
    def __init__(self, exc) -> None:
        self._exc = exc

    async def read(self, path):
        raise self._exc


class FakePlayer(FosiMediaPlayer):
    """FosiMediaPlayer without the CoordinatorEntity constructor."""

    def __init__(self, **data) -> None:
        self._sources = SOURCES
        self.coordinator = FakeCoordinator()
        self.coordinator.data = {"source": 0, "mute": False, **data}

    @property
    def sent(self):
        return self.coordinator.client.sent


# Captured verbatim off the device while casting YouTube Music. Extraction is
# tested against real output rather than an invented shape.
LIVE_PAYLOAD = {
    "trackRoles": {
        "icon": "https://yt3.googleusercontent.com/cyn5uOK6=w544-h544-l90-rj",
        "mediaData": {
            "metaData": {
                "externalAppName": "YouTube Music",
                "serviceID": "googlecast",
                "serviceName": "Casting YouTube Music",
                "albumArtist": "",
                "album": "Lean Into Life",
                "originalTrackNumber": -1,
                "artist": "Petey USA",
                "composer": "",
            }
        },
        "title": "Lean Into Life",
        "audioType": "musicTrack",
    },
    "controls": {
        "pause": True,
        "next_": True,
        "playMode": {},
        "previous": True,
    },
    "status": {"duration": 327281},
    "state": "playing",
}

STOPPED_PAYLOAD = {
    "trackRoles": {"mediaData": {"metaData": {"serviceID": "", "serviceName": ""}}},
    "state": "stopped",
}


# ------------------------------------------------------------------- tests


def test_packaging_metadata() -> None:
    """hacs.json, the manifest and the folder name must agree.

    Three files carry the same facts, so a rename can update some and miss
    others - which is exactly what happened once already.
    """
    print("\n== packaging metadata is consistent ==")
    root = HERE.parent
    component = root / "custom_components" / "fosi_audio"
    manifest = json.loads((component / "manifest.json").read_text(encoding="utf-8"))
    hacs = json.loads((root / "hacs.json").read_text(encoding="utf-8"))

    R.check("hacs name matches manifest", hacs["name"], manifest["name"])
    R.check("manifest domain matches folder", manifest["domain"], component.name)
    R.check("domain matches const.DOMAIN", manifest["domain"], const.DOMAIN)
    R.check("codeowners set", bool(manifest.get("codeowners")), True)
    R.check(
        "no placeholder left in urls",
        "OWNER" in manifest["documentation"] + manifest["issue_tracker"],
        False,
    )

    strings = json.loads((component / "strings.json").read_text(encoding="utf-8"))
    english = (component / "translations" / "en.json").read_text(encoding="utf-8")
    R.check("en.json matches strings.json", json.loads(english), strings)


def test_translation_placeholders() -> None:
    """Braces in translation strings are placeholders, not literal text.

    Home Assistant parses {name} in any translated string and requires the
    contents to be a valid identifier, so a JSON example pasted into a
    description fails hassfest. Caught in CI once; caught here now.
    """
    print("\n== translation placeholders are valid identifiers ==")
    component = HERE.parent / "custom_components" / "fosi_audio"
    ident = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    bad = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, str):
            for found in re.findall(r"\{([^}]*)\}", node):
                if not ident.match(found):
                    bad.append(f"{path}: {found!r}")

    for name in ("strings.json", "translations/en.json"):
        walk(json.loads((component / name).read_text(encoding="utf-8")), name)

    R.check("no invalid placeholders", bad, [])


def test_value_encoding() -> None:
    print("\n== value encoding ==")
    R.check("bool before int", api.encode(True), {"type": "bool_", "bool_": True})
    R.check("int", api.encode(3), {"type": "i32_", "i32_": 3})
    R.check("float", api.encode(-40.5), {"type": "double_", "double_": -40.5})
    R.check("str", api.encode("hi"), {"type": "string_", "string_": "hi"})
    R.check(
        "passthrough dict",
        api.encode({"type": "i32_", "i32_": 1}),
        {"type": "i32_", "i32_": 1},
    )
    R.check("decode tagged", api.decode({"type": "i32_", "i32_": 4}), 4)
    R.check(
        "decode doubleList",
        api.decode({"type": "doubleList", "doubleList": [-1.0, 0.0]}),
        [-1.0, 0.0],
    )


def test_activate_payload() -> None:
    print("\n== activate payload (was sent unencoded) ==")
    client = FakeClient()
    asyncio.run(client.activate("ui:/hdmi"))
    R.check("no-arg activate sends {}", client.sent[-1], ("ui:/hdmi", {}, "activate"))
    asyncio.run(client.activate("ui:/x", 3))
    R.check(
        "valued activate is encoded",
        client.sent[-1],
        ("ui:/x", {"type": "i32_", "i32_": 3}, "activate"),
    )


def test_url_building() -> None:
    print("\n== URL building (no private aiohttp API) ==")
    client = api.NsdkClient.__new__(api.NsdkClient)
    client._base = "http://h/api"
    url = client._url(
        "/getData", {"path": "settings:/mediaPlayer/mute", "roles": "value"}
    )
    R.check(
        "path percent-encoded",
        url.split("path=")[1].split("&")[0],
        "settings%3A%2FmediaPlayer%2Fmute",
    )


def test_options_flow_validation() -> None:
    print("\n== options flow accepts the shipped defaults ==")
    R.check(
        "DEFAULT_SOURCES round-trips",
        validate_sources(json.dumps(SOURCES)),
        SOURCES,
    )
    R.check("blank restores defaults", validate_sources("   "), SOURCES)
    R.check(
        "state-only entry allowed",
        validate_sources('{"Net": {"state": 0}}'),
        {"Net": {"state": 0}},
    )
    R.raises(
        "role value without value rejected",
        lambda: validate_sources('{"X": {"path": "a:/b"}}'),
        ValueError,
    )
    R.raises(
        "unknown role rejected",
        lambda: validate_sources('{"X": {"path": "a:/b", "role": "poke"}}'),
        ValueError,
    )
    R.raises("non-object rejected", lambda: validate_sources("[1,2]"), ValueError)
    R.raises(
        "bad JSON rejected",
        lambda: validate_sources("{nope"),
        json.JSONDecodeError,
    )


def test_source_mapping() -> None:
    print("\n== source mapping ==")
    R.check("hdmi state -> name", match_source(SOURCES, 3), "HDMI In")
    R.check("network state 0 -> name", match_source(SOURCES, 0), "Network")
    R.check("unknown index", match_source(SOURCES, 9), None)
    R.check("None state", match_source(SOURCES, None), None)
    R.check("selectable excludes state-only", selectable(SOURCES), COMMANDABLE)


def test_model_name() -> None:
    print("\n== model name does not repeat the brand ==")
    R.check(
        "brand stripped",
        model_name({"model": "Fosi S3", "manufacturer": "Fosi Audio"}),
        "S3",
    )
    R.check(
        "left alone when it does not repeat",
        model_name({"model": "S5", "manufacturer": "Fosi Audio"}),
        "S5",
    )
    R.check(
        "case insensitive",
        model_name({"model": "FOSI S3 Lite", "manufacturer": "Fosi Audio"}),
        "S3 Lite",
    )
    R.check(
        "no false prefix match",
        model_name({"model": "Fosimatic", "manufacturer": "Fosi Audio"}),
        "Fosimatic",
    )
    R.check("missing model falls back", model_name({}), const.DEFAULT_MODEL)
    R.check(
        "model that is only the brand falls back",
        model_name({"model": "Fosi", "manufacturer": "Fosi Audio"}),
        "Fosi",
    )


def test_volume_scale() -> None:
    print("\n== volume: player:volume index, not dB ==")
    co = FosiCoordinator.__new__(FosiCoordinator)
    co.volume_map = [float(-120 + i * 1.2) for i in range(101)]
    R.check("steps taken from the curve", co.volume_steps, 100)
    R.check("index 100 -> 1.0", co.volume_to_level(100), 1.0)
    R.check("index 0 -> 0.0", co.volume_to_level(0), 0.0)
    R.check("index 50 -> 0.5", co.volume_to_level(50), 0.5)
    R.check("1.0 -> index 100", co.level_to_volume(1.0), 100)
    R.check("0.5 -> index 50", co.level_to_volume(0.5), 50)
    R.check("index is an int", isinstance(co.level_to_volume(0.37), int), True)
    R.check("None volume", co.volume_to_level(None), None)
    R.check("dB derived from the curve", co.volume_to_db(50), co.volume_map[50])
    R.check("dB out of range", co.volume_to_db(999), None)
    R.check("level clamps high", co.level_to_volume(5.0), 100)
    R.check("level clamps low", co.level_to_volume(-1.0), 0)
    R.check("round trip", co.volume_to_level(co.level_to_volume(0.73)), 0.73)

    print("\n-- no curve: falls back, still controllable --")
    co.volume_map = []
    R.check("fallback scale", co.volume_steps, 100)
    R.check("still maps a level", co.level_to_volume(0.5), 50)
    R.check("still reports a level", co.volume_to_level(50), 0.5)
    R.check("no dB without a curve", co.volume_to_db(50), None)


def test_optimistic_updates() -> None:
    print("\n== optimistic updates (the input-switch delay) ==")
    co = FakeCoordinator()
    asyncio.run(async_apply_source(co, SOURCES["HDMI In"], "HDMI In"))
    R.check("source shown immediately", co.data["source"], 3)
    R.check("no racing refresh queued", co.refreshes, 0)
    R.check("other keys preserved", co.data["mute"], False)
    R.check(
        "maps back to the same name",
        match_source(SOURCES, co.data["source"]),
        "HDMI In",
    )

    blind = FakeCoordinator()
    asyncio.run(
        async_apply_source(blind, {"path": "x:/y", "role": "activate"}, "No State")
    )
    R.check("unpredictable command falls back to refresh", blind.refreshes, 1)

    R.raises(
        "state-only entry cannot be commanded",
        lambda: asyncio.run(
            async_apply_source(FakeCoordinator(), {"state": 0}, "Network")
        ),
        Exception,
    )


def test_uncommandable_live_source() -> None:
    print("\n== input reflects an uncommandable live source ==")
    on_hdmi = FakeSourceEntity(3)
    on_net = FakeSourceEntity(0)
    unknown = FakeSourceEntity(7)

    R.check("HDMI is current", on_hdmi._active_source, "HDMI In")
    R.check("Network reported, not blank", on_net._active_source, "Network")
    R.check("Network is a valid option", "Network" in on_net._source_options, True)
    R.check("...only while it is live", "Network" in on_hdmi._source_options, False)
    R.check("commandable list excludes it", on_net._commandable_sources, COMMANDABLE)
    R.check(
        "current is always a member of options",
        on_net._active_source in on_net._source_options,
        True,
    )
    R.check("unknown index stays blank", unknown._active_source, None)
    R.check("blank state adds no phantom option", unknown._source_options, COMMANDABLE)

    # Already on it, so this must be a silent no-op rather than an error.
    asyncio.run(on_net._async_apply_source("Network"))
    R.ok("selecting the live Network source is a no-op")


def test_volume_curve_retry() -> None:
    print("\n== volume curve retry (setup on a sleeping device) ==")
    transient = CurveLoader(NsdkConnectionError("asleep"))
    asyncio.run(transient._async_load_volume_map())
    R.check(
        "timeout leaves curve unsettled (will retry)",
        transient._volume_map_settled,
        False,
    )

    absent = CurveLoader(NsdkPathError("no such node"))
    asyncio.run(absent._async_load_volume_map())
    R.check("absent node settles (stops asking)", absent._volume_map_settled, True)


def test_now_playing_extraction() -> None:
    print("\n== now-playing extraction, against the real payload ==")
    p = FakePlayer(player=LIVE_PAYLOAD, play_time=6800)
    R.check("state", p.state, MediaPlayerState.PLAYING)
    R.check("title", p.media_title, "Lean Into Life")
    R.check("artist", p.media_artist, "Petey USA")
    R.check("album", p.media_album_name, "Lean Into Life")
    R.check("app name", p.app_name, "YouTube Music")
    R.check("art url", p.media_image_url, LIVE_PAYLOAD["trackRoles"]["icon"])
    R.check("duration from .status", p.media_duration, 327.281)
    R.check("position from playTime", p.media_position, 6.8)
    R.check("empty albumArtist -> None", p.media_album_artist, None)
    R.check("content type", p.media_content_type, "music")

    print("\n-- stopped: nothing raises, everything degrades --")
    s = FakePlayer(player=STOPPED_PAYLOAD)
    R.check("state", s.state, MediaPlayerState.IDLE)
    R.check("title", s.media_title, None)
    R.check("artist", s.media_artist, None)
    R.check("duration", s.media_duration, None)
    R.check("position", s.media_position, None)
    R.check("app name", s.app_name, None)

    print("\n-- no player at all (HDMI / optical / line-in) --")
    n = FakePlayer()
    R.check("state falls back to ON", n.state, MediaPlayerState.ON)
    R.check("title", n.media_title, None)

    print("\n-- skin: icons are device-internal, not fetchable --")
    skin = FakePlayer(player={"trackRoles": {"icon": "skin:iconGooglecast"}})
    R.check("skin icon rejected", skin.media_image_url, None)


def test_controls_drive_features() -> None:
    """Features follow the device's `controls` object, as originally shipped.

    Three attempts at pinning the button row to a fixed shape all failed in
    Home Assistant, so this reports exactly what the device says the current
    source supports and nothing more.
    """
    print("\n== controls gates supported_features ==")
    F = MediaPlayerEntityFeature
    live = FakePlayer(player=LIVE_PAYLOAD).supported_features
    R.check("pause grants PLAY", bool(live & F.PLAY), True)
    R.check("pause grants PAUSE", bool(live & F.PAUSE), True)
    R.check("next_ grants NEXT_TRACK", bool(live & F.NEXT_TRACK), True)
    R.check("previous grants PREVIOUS_TRACK", bool(live & F.PREVIOUS_TRACK), True)
    # controls under-reports: stop is accepted on a Cast source that never
    # lists it, verified on hardware.
    R.check("stop offered whenever a player runs", bool(live & F.STOP), True)

    print("\n-- the power icon, as other Cast devices have --")
    R.check("TURN_OFF offered while playing", bool(live & F.TURN_OFF), True)
    R.check(
        "no TURN_ON to pair with it - there is no standby",
        bool(live & F.TURN_ON),
        False,
    )
    powered = FakePlayer(player=LIVE_PAYLOAD)
    asyncio.run(powered.async_turn_off())
    R.check(
        "power ends the session with the stop verb",
        powered.sent[-1],
        ("player:player/control", {"control": "stop"}, "activate"),
    )

    print("\n-- the trailing-underscore trap --")
    wrong = FakePlayer(player={"controls": {"next": True}}).supported_features
    R.check(
        "'next' without underscore grants nothing", bool(wrong & F.NEXT_TRACK), False
    )
    right = FakePlayer(player={"controls": {"next_": True}}).supported_features
    R.check("'next_' grants NEXT_TRACK", bool(right & F.NEXT_TRACK), True)

    print("\n-- values matter, not just keys --")
    false_valued = FakePlayer(player={"controls": {"next_": False}})
    R.check(
        "next_: false grants nothing",
        bool(false_valued.supported_features & F.NEXT_TRACK),
        False,
    )

    print("\n-- no player -> no transport --")
    none = FakePlayer(player=STOPPED_PAYLOAD).supported_features
    for name in ("PLAY", "PAUSE", "STOP", "NEXT_TRACK", "PREVIOUS_TRACK"):
        R.check(f"no {name}", bool(none & getattr(F, name)), False)
    R.check("source select still offered", bool(none & F.SELECT_SOURCE), True)

    print("\n-- seek and play modes were dropped entirely --")
    for name in ("SEEK", "SHUFFLE_SET", "REPEAT_SET"):
        R.check(f"{name} never advertised", bool(live & getattr(F, name)), False)
    seekish = FakePlayer(player={"controls": {"seek": True, "playMode": {"x": 1}}})
    R.check(
        "not even when the device offers them",
        bool(seekish.supported_features & (F.SEEK | F.SHUFFLE_SET | F.REPEAT_SET)),
        False,
    )


def test_transport_commands() -> None:
    print("\n== transport payloads and toggle guards ==")
    CTRL = "player:player/control"

    playing = FakePlayer(player=LIVE_PAYLOAD)
    asyncio.run(playing.async_media_play())
    R.check("play while playing sends nothing", playing.sent, [])

    asyncio.run(playing.async_media_pause())
    R.check("pause while playing toggles", playing.sent[-1],
            (CTRL, {"control": "pause"}, "activate"))
    R.check("state optimistically paused", playing.state, MediaPlayerState.PAUSED)

    paused = FakePlayer(player={**LIVE_PAYLOAD, "state": "paused"})
    asyncio.run(paused.async_media_pause())
    R.check("pause while paused sends nothing", paused.sent, [])
    asyncio.run(paused.async_media_play())
    R.check("play while paused toggles", paused.sent[-1],
            (CTRL, {"control": "pause"}, "activate"))
    R.check("state optimistically playing", paused.state, MediaPlayerState.PLAYING)

    print("\n-- explicit play verb preferred when the source offers it --")
    q = FakePlayer(
        player={"controls": {"play": True, "pause": True}, "state": "paused"}
    )
    asyncio.run(q.async_media_play())
    R.check("uses 'play' not the toggle", q.sent[-1],
            (CTRL, {"control": "play"}, "activate"))

    print("\n-- the rest --")
    p = FakePlayer(player=LIVE_PAYLOAD)
    asyncio.run(p.async_media_next_track())
    R.check("next", p.sent[-1], (CTRL, {"control": "next"}, "activate"))
    asyncio.run(p.async_media_previous_track())
    R.check("previous", p.sent[-1], (CTRL, {"control": "previous"}, "activate"))
    asyncio.run(p.async_media_stop())
    R.check("stop", p.sent[-1], (CTRL, {"control": "stop"}, "activate"))
    R.check(
        "seek was removed with the rest",
        hasattr(p, "async_media_seek"),
        False,
    )


class PollClient:
    """A client whose reads can be scripted, including failures.

    on_read fires before each read resolves, which is how a test drops an
    event into the middle of a poll.
    """

    def __init__(self, values, on_read=None) -> None:
        self.values = values
        self.on_read = on_read
        self.reads: list[str] = []

    async def read(self, path):
        self.reads.append(path)
        if self.on_read is not None:
            self.on_read(path)
        value = self.values.get(path)
        if isinstance(value, Exception):
            raise value
        return value


def poll_coordinator(client, data=None):
    """A real FosiCoordinator with only the poll's dependencies filled in."""
    co = FosiCoordinator.__new__(FosiCoordinator)
    co.client = client
    co.data = dict(data or {})
    co.volume_map = []
    co.last_updated = None
    co.last_update_success = True
    co._volume_map_settled = True
    co._dead_paths = set()
    co._poll_in_flight = False
    co._pushed_during_poll = set()
    co.async_update_listeners = lambda: None
    return co


def test_poll_merges_rather_than_replacing() -> None:
    """The poll must never wipe a key it could not supply this cycle.

    Home Assistant assigns _async_update_data's return value straight onto
    coordinator.data, so a bare dict of "what this cycle read" silently
    deletes everything it did not. Three ways that bit:

      - a failed read published None, which dropped VOLUME_SET out of
        supported_features and took the slider off the card for a cycle
      - a dead path published None forever
      - an event arriving during the six sequential reads was reverted to
        whatever the poll had already read, and stayed wrong until the next
        one
    """
    print("\n== the poll merges into what is already known ==")
    live = {path: 0 for path in const.POLL_PATHS.values()}

    print("\n-- a transient failure leaves the previous value alone --")
    failing = dict(live)
    failing[const.VOLUME_PATH] = api.NsdkError("HTTP 503 busy")
    co = poll_coordinator(PollClient(failing), data={"volume": 40})
    data = asyncio.run(co._async_update_data())
    R.check("volume survives a failed read", data["volume"], 40)
    R.check("slider stays offered", data.get("volume") is not None, True)

    print("\n-- and so does a node this firmware does not have --")
    absent = dict(live)
    absent[const.OUTPUT_MODE_PATH] = NsdkPathError("does not exist")
    co = poll_coordinator(PollClient(absent), data={"output_mode": True})
    data = asyncio.run(co._async_update_data())
    R.check("dead path does not null the value", data["output_mode"], True)
    R.check("path recorded as dead", const.OUTPUT_MODE_PATH in co._dead_paths, True)

    print("\n-- an event landing mid-poll is newer, so the poll yields --")
    # The poll reads source first and gets 0. While it is still working
    # through the other five nodes, the device pushes source=3.
    def push_once(path):
        if path == const.POLL_PATHS["mute"]:
            co.apply_update(source=3)

    co = poll_coordinator(PollClient(live, on_read=push_once), data={"source": 0})
    data = asyncio.run(co._async_update_data())
    R.check("event wins over the older polled value", data["source"], 3)
    R.check("other keys still come from the poll", data["volume"], 0)

    print("\n-- an optimistic write is not clobbered either --")
    def command_once(path):
        if path == const.POLL_PATHS["mute"]:
            co.data = co._merge({"volume": 70})

    co = poll_coordinator(PollClient(live, on_read=command_once), data={"volume": 20})
    data = asyncio.run(co._async_update_data())
    R.check("slider does not snap back", data["volume"], 70)


def test_volume_db_attribute() -> None:
    """README documents volume_db, so something has to assert it exists.

    It was documented for two releases without being implemented - the
    coordinator could compute it and nothing ever exposed it.
    """
    print("\n== volume_db explains the steep curve ==")
    p = FakePlayer(volume=52)
    p.coordinator.volume_map = [float(-120 + n) for n in range(101)]
    attrs = p.extra_state_attributes
    R.check("dB exposed", attrs["volume_db"], -68.0)
    R.check("source attributes still there", attrs["active_source"], "Network")

    print("\n-- and degrades quietly with no curve to read --")
    p.coordinator.volume_map = []
    R.check("no curve, no dB", p.extra_state_attributes["volume_db"], None)


def test_pushes_restore_availability() -> None:
    """A push proves the device is reachable and must clear the failed flag.

    CoordinatorEntity.available is coordinator.last_update_success and
    nothing else. apply_update deliberately avoids async_set_updated_data to
    keep from starving the poll - but that method was also the only thing
    setting the flag, so after one failed poll every entity stayed greyed out
    while correct data streamed in behind it.
    """
    print("\n== a pushed event clears the unavailable flag ==")
    co = FakeCoordinator()
    co.data = {}
    co.last_update_success = False

    co.apply_update(volume=40)
    R.check("available again", co.last_update_success, True)
    R.check("still no reschedule", co.reschedules, 0)


def test_event_queue_is_recycled() -> None:
    """A queue that stops delivering never errors, so it is replaced on age.

    pollQueue keeps answering with nothing, which is exactly what an idle
    system looks like - there is no failure to react to and silence cannot be
    the signal. Rebuilding on a timer needs no heuristic, and unlike watching
    for the poll and the stream to disagree it cannot be fooled by an
    optimistic write the device has not reflected yet.
    """
    print("\n== the event queue is recycled on age ==")
    expired = FosiEventListener.expired
    R.check("fresh queue kept", expired(1000.0, 1000.0), False)
    R.check("still kept just under the limit", expired(1000.0, 1899.0), False)
    R.check("recycled at the limit", expired(1000.0, 1900.0), True)
    R.check("and well past it", expired(1000.0, 5000.0), True)
    R.check(
        "long enough to be cheap",
        events_mod.QUEUE_MAX_AGE >= 300,
        True,
    )


def test_events_do_not_starve_the_poll() -> None:
    """A pushed event must not reschedule the poll.

    The device pushes playTime about once a second. Rescheduling on every one
    meant the poll never ran while anything played, so events became the only
    path for every value - and a single missed event was permanent. The input
    select sat on "HDMI In" through a whole AirPlay session because it never
    saw the one event that changed it.
    """
    print("\n== pushed events leave the poll timer alone ==")
    co = FakeCoordinator()
    co.data = {}

    co.apply_update(source=0, volume=40)
    R.check("listeners notified", co.notifications, 1)
    R.check("poll NOT rescheduled", co.reschedules, 0)
    R.check("value published", co.data["source"], 0)

    for _ in range(50):
        co.apply_update(play_time=1000)
    R.check("50 playTime pushes still reschedule nothing", co.reschedules, 0)
    R.check("but every one notifies", co.notifications, 51)

    print("\n-- a command still resets it, so the confirming read cannot race --")
    cmd = FakeCoordinator()
    cmd.data = {}
    cmd.apply_optimistic(source=3)
    R.check("command reschedules", cmd.reschedules, 1)
    R.check("and publishes", cmd.data["source"], 3)


def test_position_timestamp() -> None:
    """media_position_updated_at has to move whenever the position does.

    The device pushes playTime about once a second and every push resets the
    coordinator's poll timer, so the scheduled poll never runs while music is
    playing. Stamping the timestamp only in the poll left it frozen, and HA
    added the elapsed wall-clock to a correct position - the progress bar ran
    minutes past the end of the track.
    """
    print("\n== position timestamp tracks the position ==")
    co = FakeCoordinator()
    co.data = {}
    R.check("starts unstamped", co.last_updated, None)

    co.apply_update(volume=40)
    R.check("unrelated update does not stamp", co.last_updated, None)

    co.apply_update(play_time=1000)
    stamped = co.last_updated
    R.check("play_time stamps it", stamped is not None, True)

    co.apply_update(mute=True)
    R.check("still not restamped by others", co.last_updated, stamped)

    print("\n-- and the player reports it --")
    p = FakePlayer(player=LIVE_PAYLOAD, play_time=90000)
    p.coordinator.last_updated = stamped
    R.check("position in seconds", p.media_position, 90.0)
    R.check("timestamp exposed", p.media_position_updated_at, stamped)


def test_network_service_attribute() -> None:
    print("\n== network service exposed without destabilising the input ==")
    e = FakeSourceEntity(0)
    e.coordinator.data["player"] = LIVE_PAYLOAD
    R.check("input option stays stable", e._active_source, "Network")
    R.check("protocol surfaced separately", e._network_protocol, "Google Cast")
    R.check("service surfaced separately", e._network_service, "YouTube Music")
    attrs = e.extra_state_attributes
    R.check("protocol attribute", attrs["network_protocol"], "Google Cast")
    R.check("service attribute", attrs["network_service"], "YouTube Music")

    idle = FakeSourceEntity(3)
    R.check("no player -> no service", idle._network_service, None)


def test_event_parsing() -> None:
    print("\n== event stream parsing ==")
    paths = {path: key for key, path in const.POLL_PATHS.items()}

    # Shape captured off the device: the player node pushes a playLogicData
    # tagged union, which decode() unwraps like any other.
    events = [
        {
            "path": "player:player/data/value",
            "itemValue": {"type": "playLogicData", "playLogicData": LIVE_PAYLOAD},
        },
        {"path": "player:volume", "itemValue": {"type": "i32_", "i32_": 42}},
        {
            "path": "settings:/custom/lastAudioSource",
            "itemValue": {"type": "i32_", "i32_": 3},
        },
    ]
    updates = FosiEventListener.parse(events, paths)
    R.check("player payload unwrapped", updates["player"], LIVE_PAYLOAD)
    R.check("volume decoded", updates["volume"], 42)
    R.check("source decoded", updates["source"], 3)
    R.check("maps to HDMI", match_source(SOURCES, updates["source"]), "HDMI In")

    print("\n-- malformed input is ignored, never raises --")
    R.check("unknown path skipped", FosiEventListener.parse(
        [{"path": "who:/knows", "itemValue": {"type": "i32_", "i32_": 1}}], paths), {})
    R.check("missing itemValue skipped", FosiEventListener.parse(
        [{"path": "player:volume"}], paths), {})
    R.check("non-dict event skipped", FosiEventListener.parse(["nope"], paths), {})
    R.check("non-list reply", FosiEventListener.parse(None, paths), {})
    R.check("empty reply", FosiEventListener.parse([], paths), {})

    print("\n-- every polled node is subscribed --")
    listener = FosiEventListener.__new__(FosiEventListener)
    subs = listener._subscriptions
    R.check("one subscription per polled path", len(subs), len(const.POLL_PATHS))
    R.check(
        "paths match POLL_PATHS",
        sorted(s["path"] for s in subs),
        sorted(const.POLL_PATHS.values()),
    )
    R.check("all itemWithValue", {s["type"] for s in subs}, {"itemWithValue"})


def test_streaming_service() -> None:
    """Protocol and service are separate things and get separate sensors.

    Protocol is how audio arrives - Google Cast, AirPlay, Spotify Connect.
    Service is what is playing it - YouTube Music, Spotify, Apple Music.
    Reporting one field for both gave "AirPlay" in one case and "YouTube
    Music" in another, which nothing can be written against.
    """
    print("\n== protocol and service are distinguished ==")

    def meta(**fields):
        return {"trackRoles": {"mediaData": {"metaData": fields}}}

    cases = [
        # payload, protocol, service
        (LIVE_PAYLOAD, "Google Cast", "YouTube Music"),
        (
            meta(serviceID="googlecast", externalAppName="Apple Music"),
            "Google Cast",
            "Apple Music",
        ),
        # AirPlay names no app, so the service is genuinely unknown there.
        (meta(serviceID="airplay", serviceName="AirPlay"), "AirPlay", None),
        # Connect protocols only carry one service, so it is implied.
        (meta(serviceID="spotifyconnect"), "Spotify Connect", "Spotify"),
        (meta(serviceID="tidalconnect"), "Tidal Connect", "Tidal"),
        (meta(serviceID="roon"), "Roon", "Roon"),
    ]
    for payload, protocol, service in cases:
        label = protocol or "idle"
        R.check(f"{label}: protocol", streaming_protocol(payload), protocol)
        R.check(f"{label}: service", streaming_service(payload), service)

    print("\n-- edges --")
    R.check(
        "unknown protocol is title-cased, not dropped",
        streaming_protocol(meta(serviceID="deezer")),
        "Deezer",
    )
    R.check(
        "unknown protocol implies no service",
        streaming_service(meta(serviceID="deezer")),
        None,
    )
    R.check(
        "serviceName only, no id",
        streaming_protocol(meta(serviceName="Roon")),
        "Roon",
    )
    for fn, name in ((streaming_protocol, "protocol"), (streaming_service, "service")):
        R.check(f"stopped -> no {name}", fn(STOPPED_PAYLOAD), None)
        R.check(f"no player -> no {name}", fn(None), None)
        R.check(f"garbage -> no {name}", fn("nope"), None)
    R.check(
        "whitespace only -> None",
        streaming_protocol(meta(serviceID="  ")),
        None,
    )

    print("\n-- the entities --")
    R.check("two of them", [d.key for d in SENSORS],
            ["streaming_protocol", "streaming_service"])

    def sensor(description, player):
        s = FosiPlayerSensor.__new__(FosiPlayerSensor)
        s.entity_description = description
        s.coordinator = FakeCoordinator()
        s.coordinator.data = {"player": player}
        return s

    protocol_desc, service_desc = SENSORS
    R.check("protocol state", sensor(protocol_desc, LIVE_PAYLOAD).native_value,
            "Google Cast")
    R.check("service state", sensor(service_desc, LIVE_PAYLOAD).native_value,
            "YouTube Music")

    attrs = sensor(protocol_desc, LIVE_PAYLOAD).extra_state_attributes
    R.check("service_id is the machine-readable one", attrs["service_id"], "googlecast")
    R.check("service_name", attrs["service_name"], "Casting YouTube Music")
    R.check("app_name", attrs["app_name"], "YouTube Music")

    idle = sensor(service_desc, STOPPED_PAYLOAD)
    R.check("idle state is None, not a placeholder", idle.native_value, None)
    R.check("idle attrs are None", idle.extra_state_attributes["service_id"], None)



def test_stale_entity_sweep() -> None:
    """Entities from platforms and betas that are gone must be swept.

    Registry entries outlive the code that created them, so the like/dislike
    buttons lingered as permanently unavailable rows. A domain check alone
    stopped being enough once the button platform came back for stop.
    """
    print("\n== stale entities are named, not just domain-checked ==")
    import fosi_audio

    R.check(
        "button is not a live platform",
        "button" in {p for p in fosi_audio.PLATFORMS},
        False,
    )
    suffixes = fosi_audio.REMOVED_ENTITY_SUFFIXES
    for gone in ("_like", "_dislike", "_stop_streaming"):
        R.check(f"{gone} is swept by name", f"abc123{gone}".endswith(suffixes), True)
    R.check(
        "live entities are not swept",
        "abc123_media_player".endswith(suffixes),
        False,
    )


def test_zeroconf_matcher() -> None:
    """The manifest matcher must not claim every Cast device on the network.

    Real `md` values captured by browsing _googlecast._tcp on a household
    with four Cast devices.
    """
    print("\n== zeroconf matcher is narrow enough ==")
    manifest = json.loads(
        (HERE.parent / "custom_components" / "fosi_audio" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    matchers = manifest.get("zeroconf") or []
    R.check("declares zeroconf", bool(matchers), True)
    R.check(
        "all target the cast service",
        {m["type"] for m in matchers},
        {"_googlecast._tcp.local."},
    )
    R.check(
        "none match on type alone",
        all(m.get("properties") for m in matchers),
        True,
    )

    def matches(md: str) -> bool:
        return any(
            fnmatch.fnmatch(md.lower(), m["properties"]["md"].lower())
            for m in matchers
        )

    R.check("matches the S3", matches("S3"), True)
    R.check("would match an S3 Lite", matches("S3 Lite"), True)
    R.check("would match an S5", matches("S5"), True)
    print("\n-- and rejects the neighbours, captured from a real network --")
    neighbours = (
        "Google Nest Hub",
        "Google Home Mini",
        "Chromecast",
        "Google Cast Group",
    )
    for md in neighbours:
        R.check(f"ignores {md!r}", matches(md), False)


def test_diagnostics_redaction() -> None:
    print("\n== diagnostics redact identifying values ==")
    R.check(
        "serial, mac and name are redacted",
        {"serial", "mac", "name"} <= diag.REDACT,
        True,
    )
    identity = {
        "model": "Fosi S3",
        "manufacturer": "Fosi Audio",
        "serial": "S3304CAGA1797",
        "mac": "50:1E:2D:95:1B:F4",
        "name": "Living room reciever",
    }
    redacted = stubs.async_redact_data(identity, diag.REDACT)
    R.check("serial hidden", redacted["serial"], "**REDACTED**")
    R.check("mac hidden", redacted["mac"], "**REDACTED**")
    R.check("room name hidden", redacted["name"], "**REDACTED**")
    R.check("model kept - it is the whole point", redacted["model"], "Fosi S3")
    R.check("manufacturer kept", redacted["manufacturer"], "Fosi Audio")


def main() -> int:
    for test in (
        test_packaging_metadata,
        test_translation_placeholders,
        test_value_encoding,
        test_activate_payload,
        test_url_building,
        test_options_flow_validation,
        test_source_mapping,
        test_now_playing_extraction,
        test_controls_drive_features,
        test_transport_commands,
        test_poll_merges_rather_than_replacing,
        test_volume_db_attribute,
        test_pushes_restore_availability,
        test_event_queue_is_recycled,
        test_event_parsing,
        test_events_do_not_starve_the_poll,
        test_position_timestamp,
        test_network_service_attribute,
        test_streaming_service,
        test_stale_entity_sweep,
        test_zeroconf_matcher,
        test_diagnostics_redaction,
        test_model_name,
        test_volume_scale,
        test_optimistic_updates,
        test_uncommandable_live_source,
        test_volume_curve_retry,
    ):
        test()
    print(f"\n{R.passed} passed, {R.failed} failed")
    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(main())
