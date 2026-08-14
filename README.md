<img src="https://raw.githubusercontent.com/zzznz27/Fosi-S3/refs/heads/main/custom_components/fosi_audio/brand/icon.png" alt="Fosi Audio logo" title="Fosi Audio" align="right" height="60" />  

# Fosi Audio — Home Assistant integration

[![CI](https://github.com/zzznz27/Fosi-S3/actions/workflows/ci.yml/badge.svg)](https://github.com/zzznz27/Fosi-S3/actions/workflows/ci.yml)
[![hacs](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)

Local control for the Fosi Audio S3 network streamer over its own HTTP API.

The S3 already reaches Home Assistant through Cast and AirPlay, which give you
transport and volume for whatever they are streaming. Neither can **switch the
physical input** — HDMI eARC, optical, line in or Bluetooth. That is the gap
this fills.

Tested on the S3, a StreamUnlimited Stream1832 module running StreamSDK. Other
StreamSDK hardware may work with a different source map.

## Entities

| Entity | What it does |
|---|---|
| `select.<name>_input` | Bluetooth, Line In, HDMI In, Optical In |
| `select.<name>_output_mode` | RCA/XLR Out or Optical Out — the hardware allows one at a time |
| `media_player.<name>` | Source, volume, mute, transport, now-playing |
| `sensor.<name>_streaming_protocol` | How audio is arriving — Google Cast, AirPlay, Spotify Connect |
| `sensor.<name>_streaming_service` | What is playing it — YouTube Music, Spotify, Apple Music |

Transport is play/pause, next, previous, stop, and a power button that ends the
streaming session — the device is an amplifier with no standby, so there is no
matching "on". Now-playing gives title, artist, album, artwork and position.
This works for whatever is streaming to the device, notably AirPlay, which Home
Assistant otherwise cannot see at all.

The device reports which of those actions the current source supports and only
those are offered, so HDMI, optical and line in get no transport buttons —
there is no player behind them. Volume and mute likewise appear only if the
device answers on those nodes.

Protocol and service are separate facts, and the device reports both. Casting
Apple Music reads as protocol "Google Cast", service "Apple Music". AirPlay
names no app, so the service is genuinely unknown there while the protocol
still reports.

Cast, AirPlay, Roon and the Connect protocols seize the device when something
streams to them, so they can be reported but not selected. The input select
shows `Network` while one is live; its `selectable_sources` attribute lists the
inputs that can actually be switched to, which is what automations should read.

Updates are pushed by the device over its event API, so a track change or a
press on the remote appears in a fraction of a second. Polling continues
underneath as a safety net.

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
**Settings → Devices & Services**; it appears as a discovered device and you
only have to confirm it. No IP to type. Discovery is checked against the
device's own API before it is offered, so other Cast hardware on your network
is never mistaken for a Fosi.

If it does not appear — mDNS does not always cross VLANs or subnets — add it by
hand: **Settings → Devices & Services → Add Integration → Fosi Audio**, and
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

**Poll interval** (default 60s, 5–600 allowed) — a safety net only, since the
device pushes its own changes. A poll fills in values the event stream has not
supplied; it never overwrites something newer.

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

## Troubleshooting

**Entities drop out.** Expected on wifi — the S3 sleeps its radio when idle. It
has an RJ45, and Ethernet avoids this entirely.

**No log output.** If every line in `home-assistant.log` is WARNING or ERROR,
your instance is filtering INFO:

```yaml
logger:
  logs:
    custom_components.fosi_audio: debug
```

**Reporting a problem.** Download diagnostics from the device page (⋮ →
**Download diagnostics**) and attach it to the issue. It lists which nodes
answered, what the device said its current source supports, and which paths
came back as non-existent — almost always the reason a feature is missing.
Serial, MAC and the device name are redacted.

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
