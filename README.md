# APRS-X

A mobile APRS station built on a Raspberry Pi. It has a touch-screen "radio head" in the
style of the Yaesu FTM-200 for the car, and a web interface for settings, a map and chat
that works from any phone or laptop on the same network.

It's designed to work **RF-only**. APRS-IS, iGating and online map tiles are used when a
phone hotspot is up, and everything else keeps working without them.

## Features

- **Receive and decode** APRS from Direwolf (soft TNC) or from a Yaesu FTM-200D's own
  modem, with a stations-heard list showing distance and bearing, and a live packet log.
- **Messaging**: numbered messages with acks, retries and duplicate suppression.
  Messages show on a carousel on the touch screen and in a web chat. You can reply,
  send canned messages, and keep favorite recipients.
- **Beaconing**: manual, timed, or SmartBeaconing from a gpsd GPS fix, with a fixed
  position as the fallback.
- **Fill-in digipeater** (WIDE1-1, done by Direwolf) and an **iGate** when online:
  RF→IS, and IS→RF for messages to stations heard locally.
- **Map** of stations using Leaflet and the APRS symbol set. It fetches OpenStreetMap
  tiles when online, caches them, and can also use offline MBTiles packs.
- **Touch-screen head unit** (PySide6/QML, full screen through eglfs, no desktop
  needed):
  - a message carousel;
  - Beacon, Reply, Quick Msg, Keyboard and Stations buttons;
  - a status strip showing the radio, GPS, APRS-IS and TX/RX;
  - a safe shutdown button.
- **Web UI**: stations and packets, Messages, Mesh, Map, Status (Pi health, links, GPS
  detail, audio levels) and Settings, with an optional password.
- **MeshCore** (optional): a LoRa companion radio on USB (e.g. a Heltec V4) as a separate
  channel. It supports direct messages, channel messages and a nodes-heard list, plus
  war-driving: coverage pings logged against GPS and shown on the map, with
  GeoJSON/CSV/GPX export.

## Hardware

| Part | Used here |
|---|---|
| Raspberry Pi | Pi 3B+ (1 GB) and up |
| Screen | 5" DSI touch screen (800×480); layouts scale up to bigger screens |
| Radio interface | [Digirig](https://digirig.net/) (USB audio + PTT) with any transceiver, **or** a Yaesu FTM-200D through a Yaesu SCU-66 data cable |
| GPS | A UART GPS on the GPIO pins, read by gpsd (tested with a u-blox 7) |
| MeshCore (optional) | Heltec WiFi LoRa 32 V4 with the MeshCore **USB** companion firmware |

The FTM-200D's data port only outputs, so with that radio APRS-X can **receive only**.
The radio does its own beaconing and messaging, and APRS-X sends its replies, acks and
manual beacons through APRS-IS when it's logged in.

## Install

On Raspberry Pi OS (Debian 13 trixie), 64-bit, desktop or Lite, as the normal user:

```bash
curl -fsSL https://raw.githubusercontent.com/dougbram72/APRSX/main/deploy/install.sh | bash -s -- --callsign N0CALL-9
sudo reboot
```

This installs Direwolf, gpsd and APRS-X, sets up the UART GPS and the systemd units,
and starts everything at boot. Then open `http://<pi>:8080/settings`.

> **Heads-up:** once a callsign is set, a fresh install beacons by default
> (SmartBeaconing with a GPS fix, otherwise every 30 minutes). Check the beacon
> settings first if you don't want it to transmit yet.

The installer options, boot configuration, audio levels, the FTM-200D and MeshCore are
covered in [docs/INSTALL.md](docs/INSTALL.md).

## How it fits together

```
Radio ─ Digirig ─ Direwolf ──KISS TCP:8001──┐
  (or FTM-200D ─ SCU-66, receive only) ─────┤
GPS ─ /dev/serial0 ─ gpsd:2947 ─────────────┤
MeshCore node ─ USB (optional) ─────────────┤
                                     aprsx-core (asyncio, FastAPI :8080, SQLite)
                                       ├─ WebSocket/REST → aprsx-head (touch screen)
                                       └─ HTTP → browser (settings, map, chat, status)
```

`aprsx-core` owns all radio I/O and state. The head unit and the web UI are thin
clients of the same local API, so a UI can restart without dropping packets. Direwolf
does the modem, PTT and digipeating. Beaconing, messaging and iGating are done in the
core, so the UIs can control them.

## Development

Python is managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                          # core + dev dependencies
uv sync --extra head             # also PySide6, for the head unit
uv run pytest                    # tests (no hardware needed)
uv run aprsx-core --port 8080    # the service; web UI at http://localhost:8080/
uv run aprsx-head --url http://localhost:8080   # head unit in a window
```

The design is in [docs/DESIGN.md](docs/DESIGN.md), and progress and the decision log are
in [docs/PROGRESS.md](docs/PROGRESS.md). The FTM-200D's data-port format is documented in
[docs/FTM200.md](docs/FTM200.md).

## Status

Phases 1–9 are done and running on a Pi 3B+:
- receive, messaging, the head unit, GPS and beaconing, settings, the map, APRS-IS and
  iGate;
- packaging, tested on a freshly flashed SD card;
- the FTM-200D backend.

MeshCore works on the air. Its war-driving hasn't had a road test yet, and neither has
SmartBeaconing.

APRS-X transmits with the experimental tocall `APZAPX` until one is registered.

## Credits

- [Direwolf](https://github.com/wb2osz/direwolf) by WB2OSZ, [aprslib](https://github.com/rossengeorgiev/aprs-python),
  [FastAPI](https://fastapi.tiangolo.com/), [Qt for Python](https://doc.qt.io/qtforpython/),
  and the [meshcore](https://pypi.org/project/meshcore/) Python library.
- APRS symbols from [hessu/aprs-symbols](https://github.com/hessu/aprs-symbols); see
  [aprsx/symbols/COPYRIGHT.md](aprsx/symbols/COPYRIGHT.md) for their licenses.
- [Leaflet](https://leafletjs.com/) (BSD-2), bundled so the map works offline.
- Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.

No license has been chosen for APRS-X itself yet.
