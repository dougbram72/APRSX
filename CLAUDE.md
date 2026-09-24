# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

APRS-X is a mobile APRS appliance for a Raspberry Pi (target: Pi 3B+ with a 5" DSI touch screen; must scale to larger screens and newer Pis). A Digirig soundcard interface connects it to a transceiver. The full design and phased roadmap are in `docs/DESIGN.md`. Phases 1 (foundation) and 2 (receive path, stations heard, live web packet log) are done; phase 3 (messaging) is next.

## Commands

Python is managed with `uv`.

```bash
uv sync                                   # install core + dev deps
uv sync --extra head                      # also PySide6 (head unit)
uv run pytest                             # all tests
uv run pytest tests/test_ax25.py::test_roundtrip -q   # single test
uv run aprsx-core [--db PATH] [--port N]  # the service; web UI at http://<host>:8080/
uv run aprsx-monitor --host 127.0.0.1 --port 8001     # print packets from Direwolf KISS
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
- Receive path: `kiss.KissTcpClient` → raw AX.25 → `ax25.decode()` → TNC2 text → `aprs.parse()` (aprslib dict). Transmit path: `aprs.encode.*()` builds the info field → `ax25.Frame(...)` with dest `aprs.TOCALL` → `ax25.encode()` → `KissTcpClient.send()`.
- `service.Core` is the hub: `handle_frame()` decodes, parses, stores (packets log capped at 5000, stations upserted) and publishes `packet`/`station`/`status` events on `events.EventBus`, which `/ws` forwards to clients. `api.create_app(core, run_core=False)` lets tests drive `core.handle_frame()` without a TNC. FastAPI's `TestClient` bypasses uvicorn, so anything server-level (WebSocket support, shutdown) must be covered by `tests/test_server.py`, which runs real uvicorn.
- Stations are keyed by callsign-SSID, or by object/item name for objects. A packet without a position keeps the last known position. `heard_direct` means no digipeater in the path has its H bit set.
- Distances use `Core.my_position()`, which is only `fixed_lat/lon` until the GPS work in phase 5.
- aprslib parse results use its own keys, including the misspelled `addresse` for message recipients and `response` = `ack`/`rej`.
- `TOCALL` is `APZAPX` (experimental APZ range) until we register one.
- Config is a single pydantic `Config` model (`aprsx/core/config.py`), stored as JSON under the `config` key in the SQLite `settings` table. To add a setting, add a field with a default; old stored configs still load.
- The SQLite schema is versioned through `PRAGMA user_version`. Add new tables by **appending** to `_MIGRATIONS` in `aprsx/core/store.py`; never edit existing entries.

## Target Pi (test unit)

- `pi@192.168.50.232` (hostname `aprs-pi`): Pi 3B+, Debian 13 trixie arm64, Python 3.13, desktop image. Only password SSH is enabled (no key installed).
- The project is copied to `~/aprsx`, with its own venv at `~/aprsx/.venv` (plain venv + pip, no uv on the Pi). Sync by piping a tarball over ssh.
- Station: KF0KBP-7, Baofeng UV-5R through a Digirig. The working Direwolf config is `~/.config/direwolf/ht.conf` (ALSA card `Device`, PTT on the CP2102 `/dev/serial/by-id/...` path, RTS). It has no beacons or digipeating set up, so running it only receives.
- Test runs: `systemd-run --user --unit=aprsx-direwolf-test ...` and `--unit=aprsx-core-test ~/aprsx/.venv/bin/aprsx-core` (transient, gone after reboot). The DB is `~/.local/share/aprsx/aprsx.db`. The user journal isn't persistent, and Direwolf block-buffers when it isn't on a TTY, so run it with `stdbuf -oL` and `-p StandardOutput=truncate:/tmp/direwolf.log` to see its output.
- A separate APRS package, `graywolf`, is installed. Its `graywolf.service` was **disabled** because it grabs the Digirig and port 8080. Don't re-enable it.
- PipeWire/WirePlumber would otherwise grab the Digirig. `deploy/wireplumber-disable-digirig.conf` is installed at `~/.config/wireplumber/wireplumber.conf.d/51-disable-digirig.conf` to prevent that.
- An existing `~/install-aprs-pi-fresh.sh` sets up an older YAAC-based setup (with user units `direwolf-ht.service` and `ftm200-aprs-bridge.service`). Treat it as the user's own; don't change it.

## Constraints

- Must run well on a Pi 3B+ (1 GB RAM). Avoid heavy dependencies; the head unit is native Qt for this reason, not a browser.
- Must work fully RF-only. APRS-IS, iGating and online map tiles are optional and only used when a phone hotspot is up.
- UI layouts must scale from 800×480 upward; don't use fixed pixel sizes.
- On the 3B+ the UART GPS needs `dtoverlay=miniuart-bt` (or `disable-bt`) and the serial console disabled.
