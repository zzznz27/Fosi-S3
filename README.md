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
| `media_player.<name>` | Source, volume, mute |

No transport controls — playback belongs to whichever protocol is streaming to
the device. Use the `cast` entity for play/pause/next.

Volume and mute are only advertised if the device answers on those nodes.

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

**Poll interval** (default 15s) — how quickly HA notices changes made on the
remote or front panel. Commands sent from HA apply immediately regardless.

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
