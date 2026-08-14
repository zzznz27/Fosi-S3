# Fosi Audio — Home Assistant integration

[![CI](https://github.com/zzznz27/Fosi-S3/actions/workflows/ci.yml/badge.svg)](https://github.com/zzznz27/Fosi-S3/actions/workflows/ci.yml)
[![hacs](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)

Local control for the Fosi Audio S3 network streamer over its own HTTP API.

The S3 already works in Home Assistant through the built-in `cast` integration,
which gives you transport, volume and now-playing metadata. What Cast cannot do
is **switch the physical input**. That is what this adds.

Tested on the S3. The device is a StreamUnlimited Stream1832 module running
StreamSDK, so other StreamSDK hardware may work with a different source map.

## Entities

| Entity | Does |
|---|---|
| `select.<name>_input` | Bluetooth, Line In, HDMI In, Optical In |
| `select.<name>_output_mode` | RCA/XLR Out or Optical Out (mutually exclusive) |
| `media_player.<name>` | Source, volume, mute, transport, now-playing |

Transport covers play/pause, next, previous, stop and — where the source
supports it — seek and shuffle/repeat. It works for whatever is streaming,
including AirPlay, Spotify Connect, Tidal Connect and Bluetooth, none of which
Home Assistant can otherwise control.

Now-playing gives title, artist, album, artwork and position.

Updates are pushed by the device rather than polled, so a track change or a
press on the remote shows up in well under a second.

### Buttons match what the source can actually do

The device reports which transport actions the current source supports, and
the entity advertises only those. So HDMI, optical and line-in get no
transport buttons at all — there is no player behind them — and a Cast stream
gets play/pause, next, previous and stop but not seek.

Volume and mute likewise only appear if the device answers on those nodes. The
entity degrades rather than offering controls that fail.

### Volume is the device's own scale, and it is not linear

Home Assistant's 0–100% maps 1:1 onto the device's own 0–100 volume, so the
slider always matches the front panel and the Fosi app. That scale is heavily
weighted towards the top:

| Slider | dB | Roughly |
|---|---|---|
| 50% | −30 dB | an eighth of full loudness |
| 60% | −20 dB | a quarter |
| 80% | −10 dB | half |
| 100% | 0 dB | full |

Sixty of the device's hundred steps sit below −20 dB, so most of the usable
range is in the top half of the slider. This is the hardware's curve, not a
fault in the integration — the `volume_db` attribute on the media player
reports the actual dB if you want to see it.

## Requirements

- Home Assistant 2024.12 or newer
- The device reachable over HTTP on port 80 from wherever HA runs
- No Python dependencies

## Install

**HACS:** ⋮ → Custom repositories → add this repo as category **Integration** →
install → restart.

**Manual:**

```bash
cp -r custom_components/fosi_audio /config/custom_components/
```

Restart, then **Settings → Devices & Services → Add Integration → Fosi Audio**
and enter the device's IP.

> Copy the `fosi_audio` folder itself. Do not drop loose `.py` files into
> `/config/` — HA puts that directory on `sys.path`, and a stray `select.py`
> there shadows Python's standard-library `select` module, killing the
> interpreter before HA can log anything.

If the device's IP changes, use **Reconfigure** rather than deleting the
integration — that keeps your entity IDs and history.

## Configuration

From **Configure** on the integration card. No YAML.

**Poll interval** (default 60s) — only a safety net. The device pushes changes
over its event API, so track changes, input switches from the remote and knob
turns all appear within a fraction of a second. Polling exists so that a
dropped event stream degrades to slow updates rather than stale ones.

**Source map** — the input list is configuration, so other StreamSDK hardware
can be supported without touching code:

```json
{
  "HDMI In":    {"path": "ui:/hdmi",    "role": "activate", "state": 3},
  "Optical In": {"path": "ui:/spdifin", "role": "activate", "state": 4},
  "Network":    {"state": 0}
}
```

| Key | Meaning |
|---|---|
| `path` | Node to act on. Omit to make the entry report-only. |
| `role` | `activate` to invoke an action node, `value` to write a value. |
| `value` | Payload for `role: value`; optional for `activate`. |
| `state` | What `settings:/custom/lastAudioSource` reads when this input is live. |

Leave the field blank to restore the defaults.

## Services

**`fosi_audio.seek_relative`** — jump forward or back from the current
position, since Home Assistant's built-in seek is absolute only.

```yaml
action: fosi_audio.seek_relative
target:
  entity_id: media_player.living_room
data:
  offset: -30        # seconds; negative seeks backwards
```

## Using it alongside Cast

Both this integration and the `cast` integration can control a Cast stream.
Cast carries richer session metadata; this one switches inputs. A built-in
`universal` media player combines them into one card:

```yaml
media_player:
  - platform: universal
    name: Living Room Receiver
    children:
      - media_player.living_room_reciever      # the cast entity
    commands:
      select_source:
        action: select.select_option
        target:
          entity_id: select.living_room_input
        data:
          option: "{{ source }}"
    attributes:
      source: select.living_room_input|state
      source_list: select.living_room_input|attribute.selectable_sources
```

## Network sources

Cast, AirPlay, Roon and the Connect protocols seize the device when something
streams to them. They can be reported but not selected, so automations must
assume the input can change on its own.

"Network" appears in `options` only while it is live. Use `selectable_sources`
in automations that pick an input:

```yaml
{{ state_attr('select.living_room_input', 'selectable_sources') }}
```

## Troubleshooting

**No log output.** If every line in `home-assistant.log` is WARNING or ERROR,
your instance filters INFO:

```yaml
logger:
  logs:
    custom_components.fosi_audio: debug
```

**Entities drop out.** Expected on wifi — the S3 sleeps its radio when idle.
It has an RJ45, and Ethernet avoids this entirely.

## Security

This device ships with `authMode` set to `none`. Every node on it is readable
and writable by anything on your LAN, with no credentials. The integration does
not create that exposure, but you should know about it — put the device on an
IoT VLAN, or set `authMode` to `setData`.

## Development

```bash
python tests/test_fosi.py
ruff check custom_components tests
```

The suite stubs Home Assistant, so it runs with no dependencies.

## License

MIT — see [LICENSE](LICENSE).
