<img src="https://raw.githubusercontent.com/zzznz27/Fosi-S3/refs/heads/main/custom_components/fosi_audio/brand/icon.png" alt="Fosi Audio logo" title="Fosi Audio" align="right" height="60" />  

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
| `media_player.<name>` | Source, volume, mute, transport (incl. stop), now-playing |
| `sensor.<name>_streaming_protocol` | How audio is arriving — Google Cast, AirPlay, Spotify Connect |
| `sensor.<name>_streaming_service` | What is playing it — YouTube Music, Spotify, Apple Music |

Transport covers play/pause, next, previous and stop. It works for whatever is
streaming, including AirPlay, Spotify Connect, Tidal Connect and Bluetooth,
none of which Home Assistant can otherwise control.

Now-playing gives title, artist, album, artwork and position.

**Protocol and service are separate.** How audio arrives and what is playing it
are different facts, and the device reports both. Casting Apple Music gives
protocol "Google Cast" and service "Apple Music". AirPlay names no app, so the
service is genuinely unknown there while the protocol still reports. The
Connect protocols only carry one service, so Spotify Connect implies Spotify.

Updates are pushed by the device rather than polled, so a track change or a
press on the remote shows up in well under a second.

### Buttons match what the source can actually do

The device reports which transport actions the current source supports, and
the entity advertises only those. So HDMI, optical and line-in get no
transport buttons at all — there is no player behind them.

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

### HACS

This is not in the HACS default store, so it has to be added as a custom
repository first. That is a one-off; updates arrive normally afterwards.

You need [HACS](https://hacs.xyz) already installed.

1. Open **HACS** in the Home Assistant sidebar.
2. Click the **⋮** menu, top right, and choose **Custom repositories**.
3. Paste the repository URL:

   ```
   https://github.com/zzznz27/Fosi-S3
   ```

4. Set **Type** (or **Category**) to **Integration**, then click **Add**.
5. Close the dialog. Search HACS for **Fosi Audio** and open it.
6. Click **Download**, accept the version offered, and **restart Home
   Assistant**.

The restart is not optional — Home Assistant only scans for new custom
integrations at startup.

#### Then add the device

After the restart the S3 is usually **discovered automatically**. Look under
**Settings → Devices & Services**; it appears as a discovered device, and you
only have to confirm it. No IP to type.

Discovery is confirmed against the device's own API before it is offered, so
other Cast hardware on your network is never mistaken for a Fosi.

If it does not appear — mDNS does not always cross VLANs or subnets — add it
by hand: **Settings → Devices & Services → Add Integration → Fosi Audio**, and
enter the device's IP.

#### Updating

HACS shows an update on its own dashboard when a new version is released. Open
the integration in HACS, click **Update**, then **restart Home Assistant**
again. Your configuration and entity IDs are preserved.

### Manual

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

## Reporting a problem

Download diagnostics from the device page (⋮ → **Download diagnostics**) and
attach it to the issue. It lists which nodes answered, what the device said
its current source supports, and which paths came back as non-existent — which
is almost always the reason a feature is missing. Serial, MAC and the device
name are redacted.

This matters most for hardware nobody here owns: every node path was worked
out from one S3, and the settings tree already mentions an S3 Lite and an S5.

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
