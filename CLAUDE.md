# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

APRS-X is a mobile APRS appliance for a Raspberry Pi (target: Pi 3B+ with a 5" DSI touch screen; must scale to larger screens and newer Pis). A Digirig soundcard interface connects it to a transceiver. The full design and phased roadmap are in `docs/DESIGN.md`. Phases 1 (foundation), 2 (receive path, stations heard, live web packet log), 3 (messaging, web chat), 4 (touch-screen head unit), 5 (GPS + beaconing), 6 (settings pages, managed Direwolf, map with offline tiles) and 7 (APRS-IS client and iGate) are done. SmartBeaconing hasn't been road-tested yet.

Progress is tracked in `docs/PROGRESS.md`. Tick off work packages as they are finished, and add design changes and choices to its decision log.

## Commands

Python is managed with `uv`.

```bash
uv sync                                   # install core + dev deps
uv sync --extra head                      # also PySide6 (head unit)
uv run pytest                             # all tests
uv run pytest tests/test_ax25.py::test_roundtrip -q   # single test
uv run aprsx-core [--db PATH] [--port N]  # the service; web UI at http://<host>:8080/
uv run aprsx-monitor --host 127.0.0.1 --port 8001     # print packets from Direwolf KISS
uv run aprsx-head [--url http://host:8080] [--fullscreen] [--no-keyboard]  # head unit (needs --extra head)
direwolf -c deploy/direwolf.conf          # software TNC for the Digirig
```

## Architecture

```
Radio ─ Digirig ─ Direwolf ──KISS TCP:8001──┐
GPS HAT ─ /dev/serial0 ─ gpsd:2947 ─────────┤
                                     aprsx-core (asyncio, FastAPI :8080, SQLite)
                                       ├─ WebSocket/REST → aprsx-head (PySide6/QML on the DSI screen, eglfs)
                                       └─ HTTP → browser (settings, Leaflet map, logs, chat)
```

- **aprsx-core owns all radio I/O and state.** The touch-screen head unit (`aprsx/head/`) and the web UI (`aprsx/core/web/`) are thin clients of the same local API. Put features in the core, never in a UI.
- **Direwolf does the modem, PTT and digipeating.** We never touch audio. The fill-in digipeater is a Direwolf `DIGIPEAT` rule, not our code. Beaconing, messaging and iGate logic *are* ours, so the UIs can control them.
- Phase 9 adds a Yaesu FTM-200D backend: the radio's own APRS modem, read through its data port over an SCU-66 USB cable. It replaces Direwolf in the receive path, so keep radio I/O behind a narrow interface and don't let features assume KISS/Direwolf.
- `Core.transmit(info)` is the only way packets go on the air (sync; `KissTcpClient.write()`, logged as `direction='tx'`, refuses as N0CALL). Tests capture TX by replacing `core.kiss.write`.
- Third-party packets (`}` from an iGate's IS→RF) parse as `format == 'thirdparty'` with the inner packet in `subpacket`; `Core` unwraps inner messages for the messenger.
- The head unit (`aprsx/head/`) is a thin client: `client.CoreClient` mirrors the core over REST + `/ws` into list models; QML is in `head/qml/` with sizes from the `Theme` singleton. It must not import `aprsx.core`.
- `messaging.Messenger` (`core.messenger`) runs the message protocol: `send()`, `handle()` for received `format == 'message'` packets, and `tick()` for retries (called every second by `Core`). Retry state lives in the `messages` table. `Core(store, clock=...)` takes a fake clock for tests.
- Two channels: RF (KISS) and APRS-IS (`aprsis.AprsIsClient`, lines handled as text by `Core.handle_is_line`). Both feed `Core._receive()`; packets and stations carry `channel` ('rf'/'is'), and `stations.rf_heard`/`rf_direct` record RF sightings only. `Core.transmit()` sends on RF and, when logged in, straight to APRS-IS. The iGate rules are in `aprsis.py`.
- Receive path: `kiss.KissTcpClient` → raw AX.25 → `ax25.decode()` → TNC2 text → `aprs.parse()` (aprslib dict). Transmit path: `aprs.encode.*()` builds the info field → `ax25.Frame(...)` with dest `aprs.TOCALL` → `ax25.encode()` → `KissTcpClient.send()`.
- `service.Core` is the hub: `handle_frame()` decodes, parses, stores (packets log capped at 5000, stations upserted) and publishes `packet`/`station`/`status` events on `events.EventBus`, which `/ws` forwards to clients. `api.create_app(core, run_core=False)` lets tests drive `core.handle_frame()` without a TNC. FastAPI's `TestClient` bypasses uvicorn, so anything server-level (WebSocket support, shutdown) must be covered by `tests/test_server.py`, which runs real uvicorn.
- Stations are keyed by callsign-SSID, or by object/item name for objects. A packet without a position keeps the last known position. `heard_direct` means no digipeater in the path has its H bit set.
- `Core.my_position()` is the GPS fix (`gps.GpsdClient`, gpsd JSON on :2947) when fresh, else `fixed_lat/lon`. Distances and beacons use it.
- Beaconing: `Core.beacon_tick()` (every second) asks `beacon.Scheduler` whether a beacon is due (SmartBeaconing with a GPS fix, else `beacon_interval_s`); `Core.beacon()` sends one (`POST /api/beacon`). Tests set `core.gps.fix` directly and drive `beacon_tick()` with the fake clock.
- aprslib parse results use its own keys, including the misspelled `addresse` for message recipients and `response` = `ack`/`rej`.
- `TOCALL` is `APZAPX` (experimental APZ range) until we register one.
- Config is a single pydantic `Config` model (`aprsx/core/config.py`), stored as JSON under the `config` key in the SQLite `settings` table. To add a setting, add a field with a default; old stored configs still load. Apply changes through `Core.update_config()` (reconnects KISS, rewrites and restarts Direwolf when managed). `PUT /api/config` merges; add a field to the settings page by giving an input `name="<key>"`.
- `Store` methods are serialised with a lock: sync FastAPI endpoints run on worker threads sharing the one connection. Decorate new `Store` methods with `@_locked`.
- The SQLite schema is versioned through `PRAGMA user_version`. Add new tables by **appending** to `_MIGRATIONS` in `aprsx/core/store.py`; never edit existing entries.

## Target Pi (test unit)

- `pi@192.168.50.232` (hostname `aprs-pi`): Pi 3B+, Debian 13 trixie arm64, Python 3.13, desktop image. SSH key auth works, and `pi` has passwordless sudo.
- The project is copied to `~/aprsx`, with its own venv at `~/aprsx/.venv` (plain venv + pip, no uv on the Pi). Sync by piping a tarball over ssh.
- Station: KF0KBP-7, Baofeng UV-5R through a Digirig. The working Direwolf config is `~/.config/direwolf/ht.conf` (ALSA card `Device`, PTT on the CP2102 `/dev/serial/by-id/...` path, RTS). It has no beacons or digipeating set up, so running it only receives.
- The head unit runs on the system python3 with Debian's PySide6 (`deploy/head-apt-packages.txt`), not the venv: `systemd-run --user --unit=aprsx-head-test -E WAYLAND_DISPLAY=wayland-0 -E XDG_RUNTIME_DIR=/run/user/1000 -E QT_QPA_PLATFORM=wayland -E PYTHONPATH=/home/pi/aprsx /usr/bin/python3 -m aprsx.head --fullscreen`. Screenshot the DSI screen with `grim` using the same env.
- Direwolf runs as the enabled user unit `aprsx-direwolf.service` (`deploy/`), with its config written by the core to `~/.config/aprsx/direwolf.conf` (managed mode). Don't start other Direwolf instances; they'd fight over the Digirig.
- The core still runs as a transient unit: `systemd-run --user --unit=aprsx-core-test ~/aprsx/.venv/bin/aprsx-core` (gone after reboot until phase 8). The DB is `~/.local/share/aprsx/aprsx.db`. The user journal has no storage on this Pi, so read user-unit output from the system journal: `sudo journalctl _SYSTEMD_USER_UNIT=aprsx-direwolf.service` (the unit uses `stdbuf -oL`, since Direwolf block-buffers off a TTY). It's lost on reboot.
- Graywolf, YAAC and Java were removed from the Pi (2026-09-23). Direwolf is the only APRS package installed; don't install other APRS software.
- GPS: a u-blox 7 on the GPIO UART (`/dev/serial0` → `ttyAMA0`, 9600 baud) via `dtoverlay=disable-bt` (Bluetooth is off) with the serial console removed; originals are `/boot/firmware/*.pre-gps`. gpsd is the system service, configured from `deploy/gpsd.default` (`USBAUTO="false"` so it never touches the Digirig or SCU-66). `gpspipe -w` shows its output.
- The core on the Pi has automatic beacons paused (`smartbeacon.enabled=false`, `beacon_interval_s=0`) until the user turns them on; don't re-enable them without asking, since they transmit.
- PipeWire/WirePlumber would otherwise grab the Digirig. `deploy/wireplumber-disable-digirig.conf` is installed at `~/.config/wireplumber/wireplumber.conf.d/51-disable-digirig.conf` to prevent that.
- The user's FTM-200 receive-only setup stays installed: `~/.local/bin/ftm200_aprs_bridge.py`, `aprs-configure-ftm200`, `aprs-mode-rx`/`aprs-mode-trx`, the user units `ftm200-aprs-bridge.service` and `direwolf-ht.service` (both disabled), and their desktop launchers. `~/install-aprs-pi-fresh.sh` is the older installer that created them (it also installs YAAC). Treat these as the user's own; don't change or remove them.

## Constraints

- Must run well on a Pi 3B+ (1 GB RAM). Avoid heavy dependencies; the head unit is native Qt for this reason, not a browser.
- Must work fully RF-only. APRS-IS, iGating and online map tiles are optional and only used when a phone hotspot is up.
- UI layouts must scale from 800×480 upward; don't use fixed pixel sizes.
- On the 3B+ the UART GPS needs `dtoverlay=miniuart-bt` (or `disable-bt`) and the serial console disabled.
