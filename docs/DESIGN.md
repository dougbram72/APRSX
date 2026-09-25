# APRS-X — Raspberry Pi APRS Mobile Appliance (design plan)

## Context
Build a vehicle-mounted APRS station on a Raspberry Pi (initially a Pi 3B+ with a 5" DSI touch screen; bigger screens and newer Pis should work too). A Digirig soundcard interface connects it to a transceiver. The touch screen works as a "radio head" in the style of the Yaesu FTM-200: incoming messages on a carousel with prev/next buttons, plus quick-action buttons (send beacon, quick message/reply). The Pi also serves a web page for settings, a map, message logs and APRS chat. The project folder is empty (greenfield).

Decisions made with the user:
- Stack: **Python 3 + FastAPI** backend; **native Python touch UI** for the head unit, since Chromium is too heavy for the 3B+.
- GPS: **UART GPS HAT** read through gpsd.
- Internet: sometimes available through a phone hotspot. The station must work RF-only; APRS-IS and iGate are used only when online.
- Features: messaging, beaconing, smart beaconing, fill-in digipeater, iGate when online, list of stations heard.
- Quick message: canned presets, reply to the message on screen, on-screen keyboard, and favorite recipients.
- Power: always on / manual (no ignition-sense work for now).

## Architecture

```
 Transceiver ── Digirig (USB audio + serial PTT) ── Direwolf (soft TNC, digipeater)
                                                        │ KISS TCP :8001
 GPS HAT ── /dev/serial0 ── gpsd :2947                  │
                               │                        │
                        ┌──────▼────────────────────────▼──────┐
                        │  aprsx-core  (Python, asyncio)        │
                        │  FastAPI: REST + WebSocket on :8080   │
                        │  SQLite: messages, stations, config   │
                        │  APRS-IS client (when online)         │
                        └──────▲───────────────────▲───────────┘
                               │ WebSocket/REST    │ HTTP
                  aprsx-head (PySide6/QML,    Browser on phone/laptop
                  full-screen on DSI)         (settings, map, logs, chat)
```

Key principle: **aprsx-core owns all state and radio I/O.** The head unit and the web page are both thin clients of the same local API. So the UI can crash or restart without dropping packets, a future UI can be swapped in easily, and every feature is built once in the core.

### Components
1. **Direwolf** (not our code): audio modem, PTT through the Digirig's CM108/RTS, and AX.25. It also does the WIDE1-1 fill-in digipeating (built-in `DIGIPEAT` rule). Its config file is generated from a template by aprsx-core.
2. **gpsd** (not our code): reads the UART GPS. On the 3B+, the boot config needs `dtoverlay=miniuart-bt` (or `disable-bt`) and the serial console disabled, so `/dev/serial0` maps to the PL011 UART.
3. **aprsx-core** (Python package):
   - `kiss.py`: async KISS-over-TCP client to Direwolf, with reconnect.
   - `aprs/`: packet parsing (use `aprslib`) and encoding of position, message, ack/rej, and status packets.
   - `messaging.py`: APRS message protocol. Message numbers, ack/rej handling, retries with backoff (e.g. 30/60/120 s, 5 tries), duplicate suppression, and reply-ack support.
   - `beacon.py`: manual beacon plus SmartBeaconing (speed/turn-based rates, configurable parameters), fed by gpsd (`gps` python client or a raw JSON socket). Falls back to a fixed position when there's no GPS fix.
   - `aprsis.py`: APRS-IS client (aprslib or raw asyncio socket) with a passcode and filter. Checks connectivity periodically. When online it gates RF to IS, and optionally IS to RF for messages addressed to stations heard locally, following standard iGate rules.
   - `stations.py`: stations-heard table (callsign, last position, distance/bearing from us, path, last heard, symbol).
   - `store.py`: SQLite through a light layer (stdlib `sqlite3` or SQLModel). Tables: messages, stations, packets (raw log, capped), settings, canned_messages, favorites.
   - `api.py`: FastAPI routes plus a `/ws` WebSocket that pushes events (`message`, `ack`, `station`, `position`, `status`).
   - `web/`: static pages served by FastAPI. Plain HTML/JS or a small framework such as Alpine.js/htmx to keep the Pi load low. **Leaflet** map. Tiles come from OSM when online and from a locally cached MBTiles or tile directory when offline.
4. **aprsx-head** (PySide6 + QML, or Kivy):
   - Runs full-screen through Qt's `eglfs` platform, with no desktop needed. That gives a fast boot and low RAM use on the 3B+.
   - Layout scales from 800×480 (5") upward; sizes are set relative to the screen, not in fixed pixels.
   - Main area: a message carousel (from, to, text, time, ack state) with ◀ ▶ buttons. New messages jump to the front and an unread count is shown.
   - Button bar: **Beacon**, **Reply**, **Quick Msg** (canned text + favorite recipients), **Keyboard** (free-form text through the Qt virtual keyboard), **Stations** (list of stations heard), status strip (GPS fix, TX/RX indicator, APRS-IS online, last beacon).
   - Talks only to aprsx-core through `ws://localhost:8080/ws` and REST.
5. **Deployment**: systemd units `direwolf.service`, `gpsd`, `aprsx-core.service`, `aprsx-head.service` (with dependency ordering), plus an install script for Raspberry Pi OS 64-bit (trixie), desktop or Lite. See `docs/INSTALL.md`.

## Repository layout (proposed)
```
aprsx/
  core/            # aprsx-core package (kiss, aprs, messaging, beacon, aprsis, stations, store, api)
  core/web/        # web UI static files
  head/            # PySide6/QML head unit
  deploy/          # systemd units, direwolf.conf template, install.sh, boot config notes
  tests/           # pytest: encoding/parsing, messaging state machine, smartbeaconing math
pyproject.toml
CLAUDE.md
```

## Build phases
1. **Foundation**: repo skeleton, config model (pydantic settings stored in SQLite), KISS client, and packet encode/decode, with unit tests using recorded packets.
2. **RX path on hardware**: Direwolf + Digirig on the 3B+, core receives and logs packets, stations-heard list works, basic web page shows the live packet log.
3. **Messaging**: send/receive with ack/retry, message store, web chat page.
4. **Head unit v1**: QML carousel, Beacon/Reply/Quick Msg/Keyboard buttons, status strip, running full-screen through eglfs on the 5" DSI screen.
5. **GPS + beaconing**: UART GPS through gpsd, manual beacon, SmartBeaconing.
6. **Web config + map**: settings pages (callsign/SSID, symbol, path, beacon comment, SmartBeacon parameters, canned messages, favorites, APRS-IS, digipeater on/off, audio device) that regenerate direwolf.conf and restart it. Leaflet map with an offline tile cache.
7. **Online features**: APRS-IS connection, iGate, and IS stations shown on the map.
8. **Packaging**: install script, systemd units, one-command setup on a fresh SD card.
9. **Yaesu FTM-200D backend** (added 2026-09-24): the FTM-200D has a built-in APRS modem and connects through its data port with a Yaesu SCU-66 data-to-USB cable. With this radio, a serial bridge in aprsx-core takes the place of Direwolf in the receive path and converts the radio's data-port output to standard TNC2 packets. The rest of the core and the UIs stay the same. The radio backend (Direwolf/KISS or FTM-200 serial) is chosen in settings. What can be transmitted through the data port is still to be found out. If it can't transmit, the UIs show that TX isn't available and the radio's own APRS functions handle beacons and messages.
10. **MeshCore companion + war-driving** (added 2026-09-24): a LoRa board (Heltec V4) running MeshCore USB-serial companion firmware, through the `meshcore` Python library. It is a separate channel beside RF and APRS-IS, with its own nodes, messages and tables, and never goes through the APRS transmit path. It covers direct and channel messages, the nodes heard, and a war-driving mode that pings the mesh while driving and records who answers, where we were and the signal both ways, for a coverage map and GeoJSON/CSV/GPX export. MeshCore nodes don't answer adverts, so pings take turns between a zero-hop discovery request (repeaters in range answer) and a short channel message (repeaters that pass it on are heard). The data stays local.

## Verification
- `pytest` for parsing/encoding, the messaging retry/ack state machine, and SmartBeaconing rate/turn math (no hardware needed).
- Develop on a PC with Direwolf running there against a Digirig (or with Direwolf's audio loopback). Core and head unit both run on the desktop (Qt `xcb` platform) against the same API.
- On the air: send a message from another station or aprs.fi to our callsign, confirm it shows in the carousel and that an ack goes out and is seen on aprs.fi. Send a beacon and confirm the position on aprs.fi (through a nearby iGate or our own iGate when online).
- Digipeater: check with Direwolf's log that WIDE1-1 packets are repeated once and duplicates are suppressed.
- Offline test: disconnect the hotspot and confirm RF messaging, the map (cached tiles) and the head unit keep working.
