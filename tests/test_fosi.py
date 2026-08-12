"""Behavioural checks against the real fosi_audio modules, Home Assistant stubbed.

Runs with no dependencies at all - `stubs` fabricates just enough of
homeassistant, aiohttp and voluptuous for the integration to import, so the
code under test is the code that ships.

    python tests/test_fosi.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
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
from fosi_audio.entity import (  # noqa: E402
    FosiSourceEntity,
    async_apply_source,
    match_source,
    model_name,
    selectable,
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

    Borrows the real apply_optimistic so the shipped implementation is what
    gets exercised, not a reimplementation of it.
    """

    apply_optimistic = FosiCoordinator.apply_optimistic

    def __init__(self, source=0) -> None:
        self.data = {"source": source, "mute": False}
        self.refreshes = 0
        self.client = FakeClient()

    def async_set_updated_data(self, data) -> None:
        self.data = data

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


def main() -> int:
    for test in (
        test_packaging_metadata,
        test_value_encoding,
        test_activate_payload,
        test_url_building,
        test_options_flow_validation,
        test_source_mapping,
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
