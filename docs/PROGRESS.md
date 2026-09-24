# APRS-X progress

Tracks the build against the phased plan in [DESIGN.md](DESIGN.md). Each phase is split
into work packages (WPs). Tick a box when the WP is done and verified. Record any
departure from DESIGN.md, and any choice DESIGN.md left open, in the decision log at
the bottom.

**Status (2026-09-24):** phases 1–3 done. Phase 4 (head unit) is next. Phase 9 (FTM-200D backend) was added 2026-09-24.

---

## Phase 1: Foundation ✅

- [x] **Phase 1 complete** (commit `c1bf3f3`)
- [x] WP1.1 Repo skeleton: `pyproject.toml` (uv, hatchling), `aprsx/core`, `aprsx/head`, `tests/`, `deploy/`
- [x] WP1.2 Config model: pydantic `Config`, stored as JSON in the SQLite `settings` table
- [x] WP1.3 SQLite store with versioned migrations (`PRAGMA user_version`)
- [x] WP1.4 KISS framing and a reconnecting async KISS-over-TCP client
- [x] WP1.5 AX.25 UI frame ↔ TNC2 text conversion
- [x] WP1.6 APRS encoding (position, message, ack/rej, status) and aprslib parsing
- [x] WP1.7 `aprsx-monitor` bring-up tool
- [x] WP1.8 Unit tests for KISS, AX.25, APRS, store

## Phase 2: RX path on hardware ✅

- [x] **Phase 2 complete** (commit `c1bf3f3`)
- [x] WP2.1 Direwolf + Digirig on the Pi 3B+ (starter `deploy/direwolf.conf`, WirePlumber rule to free the card)
- [x] WP2.2 `service.Core`: decode → parse → store, packet log capped at 5000
- [x] WP2.3 Stations-heard table (position carry-over, distance/bearing, `heard_direct`)
- [x] WP2.4 Event bus and FastAPI REST (`/api/status`, `/api/packets`, `/api/stations`) + `/ws`
- [x] WP2.5 Live web page: status, stations heard, packet log
- [x] WP2.6 Real-uvicorn server tests (`tests/test_server.py`)
- [x] WP2.7 On-air check: UV-5R beacons decoded and shown in the web UI

## Phase 3: Messaging ✅

- [x] **Phase 3 complete**
- [x] WP3.1 `messages` table (migration appended to `_MIGRATIONS`)
- [x] WP3.2 Transmit path in the core: encode → AX.25 → `KissTcpClient.send()`, with TX packets logged (`direction='tx'`)
- [x] WP3.3 Receive messages addressed to us; send acks; duplicate suppression
- [x] WP3.4 Outgoing messages: message numbers, retry with backoff (30/60/120 s, 5 tries), ack/rej handling
- [x] WP3.5 Reply-ack support
- [x] WP3.6 REST + `/ws` events for messages (`message`, `ack`)
- [x] WP3.7 Web chat page
- [x] WP3.8 Tests for the messaging state machine (retries, acks, dupes)
- [x] WP3.9 On-air check: message to/from another station or aprs.fi, ack seen

## Phase 4: Head unit v1

- [ ] **Phase 4 complete**
- [ ] WP4.1 PySide6/QML app skeleton, API/WebSocket client to aprsx-core
- [ ] WP4.2 Message carousel with ◀ ▶, unread count, new messages to the front
- [ ] WP4.3 Button bar: Beacon, Reply, Quick Msg (canned + favorites), Keyboard (Qt virtual keyboard), Stations
- [ ] WP4.4 Status strip: GPS fix, TX/RX, APRS-IS online, last beacon
- [ ] WP4.5 Resolution-independent layout (800×480 upward)
- [ ] WP4.6 Full-screen on the 5" DSI through eglfs on the Pi; desktop run through xcb

## Phase 5: GPS + beaconing

- [ ] **Phase 5 complete**
- [ ] WP5.1 UART GPS HAT on the 3B+ (`dtoverlay=miniuart-bt`/`disable-bt`, serial console off) + gpsd
- [ ] WP5.2 gpsd client in the core; `Core.my_position()` uses the GPS fix, falling back to `fixed_lat/lon`
- [ ] WP5.3 Manual beacon (API + head/web button)
- [ ] WP5.4 SmartBeaconing (speed/turn rates from `Config.smartbeacon`)
- [ ] WP5.5 Tests for SmartBeaconing rate/turn math
- [ ] WP5.6 On-air check: beacon position seen on aprs.fi

## Phase 6: Web config + map

- [ ] **Phase 6 complete**
- [ ] WP6.1 Settings pages (callsign/SSID, symbol, path, comment, SmartBeacon, canned messages, favorites, APRS-IS, digipeater, audio device)
- [ ] WP6.2 Generate `direwolf.conf` from settings and restart Direwolf
- [ ] WP6.3 Fill-in digipeater toggle (Direwolf `DIGIPEAT` rule); check WIDE1-1 repeated once, dupes suppressed
- [ ] WP6.4 Leaflet map of stations heard
- [ ] WP6.5 Offline tile cache (MBTiles or tile directory), OSM when online

## Phase 7: Online features

- [ ] **Phase 7 complete**
- [ ] WP7.1 APRS-IS client (passcode, filter) with connectivity check
- [ ] WP7.2 RF → IS iGate
- [ ] WP7.3 IS → RF for messages to stations heard locally (standard iGate rules)
- [ ] WP7.4 IS stations on the map, marked as `channel='is'`
- [ ] WP7.5 Offline test: drop the hotspot, confirm RF messaging, map and head unit keep working

## Phase 8: Packaging

- [ ] **Phase 8 complete**
- [ ] WP8.1 systemd units: `direwolf`, `gpsd`, `aprsx-core`, `aprsx-head`, with ordering
- [ ] WP8.2 Install script for a fresh SD card (one command)
- [ ] WP8.3 Boot config notes/automation (UART, DSI, eglfs)
- [ ] WP8.4 Fresh-install test on the Pi

## Phase 9: Yaesu FTM-200D radio backend

The FTM-200D has its own APRS modem. Its data port connects to the Pi through a
Yaesu SCU-66 data-to-USB cable. With this radio, the radio does the modem work and a
serial bridge in the core stands in for Direwolf.

- [ ] **Phase 9 complete**
- [ ] WP9.1 Capture and document the FTM-200D data-port output over the SCU-66 (serial settings, line format, which packet types are sent, radio menu settings). Use the user's existing `ftm200_aprs_bridge.py` on the Pi as a reference, but don't change it.
- [ ] WP9.2 Radio-backend interface in the core, with Direwolf/KISS as one backend. Select the backend in `Config` (e.g. `radio_backend: "direwolf" | "ftm200"`, serial device path).
- [ ] WP9.3 FTM-200 serial backend: async reader with reconnect on the SCU-66 port (`/dev/serial/by-id/...`)
- [ ] WP9.4 Parser that turns FTM-200 data-port output into standard TNC2 packets, fed into the same parse → store → publish path as KISS frames
- [ ] WP9.5 Work out what can be transmitted through the data port. Where the core can't transmit (messages, beacons), report it to the UIs through `status` and disable those controls.
- [ ] WP9.6 Tests with recorded FTM-200 output (no hardware needed)
- [ ] WP9.7 Settings page and install support: choose the backend, and don't start Direwolf when the FTM-200 is selected
- [ ] WP9.8 On-air check: FTM-200D + SCU-66 on the Pi, received stations shown in the web UI and head unit

---

## Decision log

Newest first. Note the date, the phase/WP, what was decided, and why.

### 2026-09-24: Messaging (phase 3)

- **Retry schedule:** 5 tries, sent at 0, 30, 90, 210 and 330 s (`RETRY_DELAYS_S = 30, 60, 120, 120, 120`). With no ack 120 s after the last try, the message is marked `failed`. Retry state (`tries`, `next_try`) is kept in the `messages` table and handled by `Messenger.tick()` once a second, so pending messages survive a restart.
- **No TNC doesn't use up a try.** If KISS is down, the message stays `pending` and is tried again 10 s later, with no limit, so it goes out once Direwolf is back.
- **Message numbers are two characters** (base 36, a persistent counter in `settings` under `msg_seq`). **We always send the reply-ack form `{MM}`**, which tells the peer we support reply-acks. To a peer that has used that form, we send `{MM}AA` with its last message number. We still send a separate ack for every numbered message we receive. A legacy ack like `ackMM}` is matched after stripping the `}`.
- **Duplicates:** a numbered message with the same peer, number and text within 30 min is acked again but not stored again. For unnumbered messages the window is 60 s, because a user may send "ok" twice on purpose. Acks for the same (peer, number) are held off for 20 s, so digipeated copies of one transmission get one ack but a real retry (≥30 s later) gets another.
- **Only messages to our exact callsign-SSID** are handled. Bulletins, messages between other stations, and our own packets heard back through a digi are not stored as messages (they still show in the packet log).
- **`Core.transmit(info)` is synchronous** and is the only TX entry point: it builds the frame (TOCALL, `Config.path`), writes it with the new `KissTcpClient.write()` (no drain), and logs it as `direction='tx'`. Being sync lets the receive handler send acks inline. The FTM-200 backend (phase 9) only has to replace this method. **It refuses to transmit as N0CALL.**
- **Outgoing message text must be printable ASCII** (APRS spec), up to 67 characters, without `|~{`.
- **WebSocket events:** `message` (new row), `ack` (outgoing row changed: tries, or acked/rejected/failed) and `read` ({peer, unread}). Both message events carry the full row, and clients upsert by id. `status` gained `tx_count` and `unread`.
- **Added a read-only `GET /api/config`** so the chat page can show canned messages and favorites. Phase 6 adds editing.
- **Web:** chat is a separate page (`chat.html`), with shared JS helpers in `common.js`. Both pages have nav with an unread badge.
- **On-air check (WP3.9)** on the test Pi with KF0KBP-1 (direct RF, no digipeater): we acked its message `{011` within a second. Our `{01}` went unacked on try 1, was retried 31 s later, and was acked 8 s after that. Only the direct path was tested; a message through a digipeater or iGate (e.g. from aprs.fi) is still worth doing.

### 2026-09-24: FTM-200D backend (phase 9)

- **Added phase 9: Yaesu FTM-200D as a second radio backend.** The radio connects through its data port with an SCU-66 USB cable. It decodes APRS itself, so with this radio **Direwolf is not in the receive path.** A bridge in the core converts the radio's data-port output to standard TNC2 packets.
- **This makes Direwolf one backend among others** instead of a fixed part of the stack. The core gets a radio-backend interface (Direwolf/KISS, FTM-200 serial) chosen in settings, and everything after it (parse, store, events, UIs) stays shared.
- **TX with the FTM-200 is an open question** (WP9.5). If the data port can't transmit, messaging and beaconing fall back to the radio's own APRS functions, and the UIs must show that TX isn't available.
- **Phase 9 goes after packaging** (phase 8), so the Digirig path is finished first. Code in phases 3–8 should still keep radio I/O behind `KissTcpClient`/`Core.handle_frame()`, so adding a second backend later isn't a big refactor.

### 2026-09-23: Phases 1–2

- **Own AX.25 codec** (`ax25.py`) instead of a library. The core gets raw AX.25 from KISS and converts to/from TNC2 text so aprslib can parse it. Keeps dependencies small.
- **TOCALL `APZAPX`** (experimental APZ range) until a real one is registered.
- **Stations keyed by callsign-SSID, or by object/item name** for objects and items (`is_object` flag). A packet without a position keeps the last known position.
- **`heard_direct`** = no digipeater in the path has its H bit set.
- **Canned messages and favorites live in `Config`** (JSON in `settings`), not in separate `canned_messages`/`favorites` tables as DESIGN.md proposed. They are small lists edited as settings.
- **Schema migrations are append-only** in `_MIGRATIONS`; config gains fields with defaults so old stored configs still load.
- **Packets table has `direction` (rx/tx) and `channel` (rf/is)** from the start, so TX logging (phase 3) and APRS-IS (phase 7) need no schema change.
- **`api.create_app(core, run_core=False)`** so tests can drive `core.handle_frame()` without a TNC. Server-level behaviour (WebSocket support, shutdown) is tested against real uvicorn in `tests/test_server.py`, because `TestClient` bypasses it. `websockets` is a direct dependency for that reason.
- **Repo layout:** `deploy/` and `tests/` sit at the repo root, not under `aprsx/` as DESIGN.md sketched.
- **Distances** use `Core.my_position()`, which returns `fixed_lat/lon` until GPS lands in phase 5.
- **Test Pi runs Debian 13 trixie (desktop image), Python 3.13**, not Raspberry Pi OS Lite Bookworm as DESIGN.md assumed. PipeWire/WirePlumber grabs the Digirig on the desktop image, so `deploy/wireplumber-disable-digirig.conf` excludes it. Phase 8 must handle both Lite and desktop images.
- **Other APRS software removed from the Pi** (Graywolf, YAAC, Java, and dependencies nothing else needed) at the end of the 2026-09-23 session. Graywolf grabbed the Digirig and port 8080. The user's FTM-200 receive-only setup stays. Direwolf is the only APRS package on the Pi.
