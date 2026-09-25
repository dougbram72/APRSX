# APRS-X progress

Tracks the build against the phased plan in [DESIGN.md](DESIGN.md). Each phase is split
into work packages (WPs). Tick a box when the WP is done and verified. Record any
departure from DESIGN.md, and any choice DESIGN.md left open, in the decision log at
the bottom.

**Status (2026-09-24):** phases 1–4, 6 and 7 done. Phase 5 (GPS + beaconing) waits for the GPS hardware; phase 8 (packaging) can go next. Phase 9 (FTM-200D backend) was added 2026-09-24.

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

## Phase 4: Head unit v1 ✅

- [x] **Phase 4 complete**
- [x] WP4.1 PySide6/QML app skeleton, API/WebSocket client to aprsx-core
- [x] WP4.2 Message carousel with ◀ ▶, unread count, new messages to the front
- [x] WP4.3 Button bar: Beacon, Reply, Quick Msg (canned + favorites), Keyboard (Qt virtual keyboard), Stations
- [x] WP4.4 Status strip: GPS fix, TX/RX, APRS-IS online, last beacon
- [x] WP4.5 Resolution-independent layout (800×480 upward)
- [x] WP4.6 Full-screen on the 5" DSI through eglfs on the Pi; desktop run through xcb

## Phase 5: GPS + beaconing

- [ ] **Phase 5 complete**
- [x] WP5.1 UART GPS HAT on the 3B+ (`dtoverlay=miniuart-bt`/`disable-bt`, serial console off) + gpsd (u-blox 7 at 9600 baud; `deploy/gpsd.default`)
- [x] WP5.2 gpsd client in the core; `Core.my_position()` uses the GPS fix, falling back to `fixed_lat/lon`
- [x] WP5.3 Manual beacon (API + head/web button)
- [x] WP5.4 SmartBeaconing (speed/turn rates from `Config.smartbeacon`)
- [x] WP5.5 Tests for SmartBeaconing rate/turn math
- [ ] WP5.6 On-air check: beacon position seen on aprs.fi

## Phase 6: Web config + map ✅

- [x] **Phase 6 complete**
- [x] WP6.1 Settings pages (callsign/SSID, symbol, path, comment, SmartBeacon, canned messages, favorites, APRS-IS, digipeater, audio device)
- [x] WP6.2 Generate `direwolf.conf` from settings and restart Direwolf
- [x] WP6.3 Fill-in digipeater toggle (Direwolf `DIGIPEAT` rule); check WIDE1-1 repeated once, dupes suppressed
- [x] WP6.4 Leaflet map of stations heard
- [x] WP6.5 Offline tile cache (MBTiles or tile directory), OSM when online

## Phase 7: Online features ✅

- [x] **Phase 7 complete**
- [x] WP7.1 APRS-IS client (passcode, filter) with connectivity check
- [x] WP7.2 RF → IS iGate
- [x] WP7.3 IS → RF for messages to stations heard locally (standard iGate rules)
- [x] WP7.4 IS stations on the map, marked as `channel='is'`
- [x] WP7.5 Offline test: drop the hotspot, confirm RF messaging, map and head unit keep working

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

### 2026-09-24: Online features (phase 7)

- **APRS-IS client** (`aprsis.py`): one login (`user CALL pass N vers APRS-X 0.1 filter …`). It counts as disconnected after 90 s of silence (servers send a `#` keepalive about every 20 s) and reconnects with 5/10/30/60/120 s back-off. It only sends once the `# logresp` says *verified*. Status reports `aprsis_enabled/connected/verified/server` and gate counts; the head unit's IS pill and a new header pill on the web pages use them. The passcode is the standard algorithm (`/api/aprsis/passcode`, with a Calculate button in Settings). The login is repeated when the callsign, server, passcode, filter or fixed position changes.
- **`m/N` filters become `r/lat/lon/N`** when we know our position. The server only knows our position from packets we've sent to APRS-IS, and we don't beacon yet (phase 5).
- **RF → IS** (`aprsis.igate`): gates every received packet with `,qAR,<call>` added, except those with TCPIP/TCPXX/NOGATE/RFONLY in the path, third-party (`}`) packets and `?` queries. Info is cut at the first CR/LF/NUL. Digipeated copies are gated once (keyed on source, dest and info, 30 s).
- **IS → RF** (`aprsis.is_to_rf`, off by default because it transmits): only messages and acks, only to a station heard *directly* on RF in the last 30 min, and not when the sender itself was heard on RF in that time (it can reach the station directly). Not when the path has TCPXX/NOGATE/RFONLY or our call. Sent as `}SRC>DEST,TCPIP,OURCALL*:info` with no digipeater path (the target is in direct range). Limited to 6 per minute and 10 per 5 minutes, deduplicated, and never echoed back to APRS-IS.
- **Our own packets go to both RF and APRS-IS** when the login is verified (`Core.transmit(..., to_is=True)`); each copy is logged with its channel. Messages and acks to internet stations (WXBOT) therefore work without a local iGate, and a message counts as sent if either path worked. APRS-IS drops the duplicate if another iGate also gates our RF copy.
- **APRS-IS stations are stored with `channel='is'`.** Migration 4 adds `stations.channel`, `rf_heard` and `rf_direct`, so the IS→RF "local" test only uses RF sightings. APRS-IS paths contain non-AX.25 elements (`qAC`, `T2USA`, SSIDs like `-D`), so IS lines are handled as text, not as `ax25.Frame`s. `station_record()` now takes the source and path as strings.
- **An APRS-IS copy of a station heard on RF in the last 30 min doesn't turn it into an IS station** (channel, path and "direct" stay RF). Found live: our own RF→IS gating, or another iGate's, sends Graywolf's beacon straight back to us over APRS-IS.
- **Flaky WebSocket tests fixed** (`tests/conftest.py: close_ws`). Starlette's TestClient cancels the app right after sending the disconnect, so the `/ws` handler's cleanup sometimes raised CancelledError (about 50% of runs). The tests now close the socket and let the handler finish first. Real servers wait for the handler (`test_server.py`).
- **WXBOT through APRS-IS (14:31):** the message went out on RF and APRS-IS, the ack came back over APRS-IS in the same second (first try), and the forecast arrived over APRS-IS 7 s later. Graywolf's RF relay of it 2 s after that was dropped as a duplicate.
- **WP7.5 offline test (14:32–14:34):** the Pi's default route was removed, with a 10-minute auto-restore timer, and SSH kept working over the LAN. APRS-IS noticed within the 90 s timeout and kept retrying. RF messaging worked: `{0A}` to KF0KBP-1 was acked over RF on the first try. Core, Direwolf and the head unit kept running, and the head unit's IS pill went grey. Cached map tiles were served in about 15 ms, and uncached ones returned 404 in 0.24 s (then about 10 ms during the back-off) instead of hanging. After the route came back, APRS-IS logged in again within 25 s. Known limit: until the timeout, a message's APRS-IS copy goes into the dead TCP socket (and is logged as sent). The RF copy and later retries cover it.
- **On the test Pi:** APRS-IS is on, logged in as KF0KBP-7 (verified), with filter `r/39.78/-95.56/50` (Graywolf's area, narrowed) and RF→IS gating on. IS→RF is off. It gated Graywolf's 13:32 beacon.

### 2026-09-24: Web config + map (phase 6)

- **Phase 6 comes before phase 5** because the GPS hardware hasn't arrived. The SmartBeacon and APRS-IS settings can already be edited; they take effect in phases 5 and 7.
- **Offline tiles (user's choice): browse-cache plus MBTiles.** `tiles.py` serves `/tiles/{z}/{x}/{y}.png` from, in order: `*.mbtiles` packs in `<data dir>/tiles/` (raster only, TMS rows, rescanned when the directory changes), the on-disk cache (fresh for 30 days), OSM (when `tiles_online`), then a stale cached tile. OSM's usage policy forbids bulk downloads, so there's no region pre-fetch from OSM; only viewed tiles are fetched, at most 2 at a time, with an APRS-X User-Agent and a 60 s back-off after a failure. Region packs have to come from a provider that allows bulk use. There's no cache size cap yet.
- **Leaflet 1.9.4 is bundled** in `web/vendor/leaflet/` (BSD-2, with its LICENSE), because the map must work RF-only. Markers use the APRS symbol sprites. Stations not heard for 2 h are dimmed.
- **APRS-X manages Direwolf (user's choice)** when `direwolf_managed` is on. `direwolf.py` renders the config from settings (ADEVICE, MYCALL, PTT, KISSPORT, `AGWPORT 0`, and `DIGIPEAT 0 0 ^WIDE1-1$ ^WIDE1-1$` for the fill-in digi; Direwolf's default 30 s DEDUPE drops repeats). It writes `~/.config/aprsx/direwolf.conf` atomically and runs `systemctl --user restart aprsx-direwolf.service` (`deploy/aprsx-direwolf.service`), but only when a Direwolf-relevant field changes. The user's own `ht.conf` is never touched. A restart failure is reported on the settings page and in `status.direwolf_error`; the settings are still saved. Settings that go into direwolf.conf are validated to one line of safe characters, so they can't inject extra lines.
- **Optional settings password (user's choice).** It's a PBKDF2-SHA256 hash in `Config.admin_password_hash`, never returned by `GET /api/config`. When it's set, `PUT /api/config` and `POST /api/messages` need a session cookie (in memory, 30 days, HttpOnly, SameSite=Strict). Loopback clients (the head unit) are always trusted. Failed logins wait 1 s.
- **`PUT /api/config` merges into the current settings.** Fields left out keep their values and nested groups merge key by key. An early version replaced the whole config, so a partial request would have reset everything to defaults, callsign included.
- **Store access is serialised with a lock.** FastAPI runs plain `def` endpoints on worker threads that share the one SQLite connection with the event loop. The map page's parallel requests interleaved on it (`COUNT(*)` came back empty). This was possible since phase 2. SQLite now also runs `synchronous=NORMAL`, which is safe with WAL and uses far fewer fsyncs (kinder to the SD card, and the test suite went from 36 s to 20 s).
- **Settings page:** fields bind to config keys by `name` (`aprsis.port`), and validation errors come back per field and are shown on that field. It has a symbol picker (primary / alternate / overlay), sound card and PTT suggestions from `/api/system/devices` (ALSA cards as `plughw:CARD=…`, `/dev/serial/by-id` ports with RTS/DTR, CM108), and a preview of the generated direwolf.conf. Checked end to end in Chrome with Playwright.
- **WP6.3 on-air check (2026-09-24, 13:02):** with the digipeater turned on from the settings page, the Pi's Direwolf heard Graywolf's `KF0KBP-1>APGRWO,WIDE1-1` beacon and transmitted it once as `KF0KBP-1>APGRWO,KF0KBP-7*` (`[0H]`). That confirms the generated `DIGIPEAT` rule, the restart through the unit, and PTT through the managed Direwolf. Graywolf couldn't confirm hearing the repeat, because its receive was barely working at the time (2 decodes and about 150k audio errors in 25 min). Duplicate suppression wasn't exercised: only one copy arrived. It relies on Direwolf's standard 30 s DEDUPE. The digipeater was switched off again after the test.
- **Receive through the managed Direwolf** was confirmed by Graywolf's beacons at 11:02, 11:32, 12:02, 12:32 and 13:02.
- **On the test Pi:** Direwolf now runs as the enabled user unit `aprsx-direwolf.service`, with managed mode on (same ADEVICE and CP2102N RTS PTT as `ht.conf`), instead of the transient `aprsx-direwolf-test`.

### 2026-09-24: Head unit (phase 4)

- **APRS symbols use the aprs.fi set** (hessu/aprs-symbols, 64 px sprite sheets in `aprsx/symbols/` with its COPYRIGHT.md; the author asks for a link to the source). One copy serves both UIs: the head unit reads the files, and the core serves them at `/symbols/`. Cell for symbol code `c` = `ord(c) - 33` in a 16×6 grid. Table `/` = primary sheet, `\` = alternate, `0-9`/`A-Z` = alternate plus an overlay character from the third sheet. Shown in the head's Stations list and next to the callsign on message cards (from the station's last known symbol), and in the web stations table. The phase 6 map can use the same sheets.

- **The head unit runs on the Pi's system Python with Debian's PySide6 6.8.2** (`deploy/head-apt-packages.txt`), not in the core's venv. Debian's Qt uses the Pi's Mesa stack and includes the virtual keyboard; the pip wheels would be much bigger. The head imports nothing from `aprsx.core`, so it needs only PySide6. Run it with `PYTHONPATH=~/aprsx python3 -m aprsx.head --fullscreen`. Keep QML to Qt 6.8 features (the dev machine has 6.11).
- **Networking uses Qt only** (QNetworkAccessManager + QWebSocket on Qt's event loop), with no asyncio in the UI process. After every (re)connect it reloads status, config, the last 200 messages and the stations over REST, then follows `/ws`.
- **WP4.6: on this Pi it runs as a full-screen Wayland window under labwc**, because the desktop image's compositor owns the DSI display, so eglfs can't take it. eglfs is for a Lite/kiosk image and moves to phase 8 (boot and install setup). Qt's `-platform` options pass straight through.
- **Virtual keyboard:** Qt 6.8's Wayland plugin needs `QT_IM_MODULES` (plural) as well as `QT_IM_MODULE`; the key images are SVG, so `qt6-svg-plugins` is required. It's pinned to en_US with one layout (APRS text is ASCII). Focusing a field from code doesn't raise the keyboard, so Compose calls `Qt.inputMethod.show()`. Qt's `offscreen` platform loads no input method, so the keyboard can only be checked on a real display.
- **Scaling:** every size is `Theme.u` × a base value, where `u = min(w/800, h/480)`. Checked at 800×480 (on the DSI) and 1280×800.
- **Carousel:** newest first, and a new incoming message jumps to the front. A message counts as read after 1.5 s on screen while the window is active; that marks the whole conversation read (`/api/messages/read` is per peer). Beacon stays disabled until the core reports `can_beacon` (phase 5). GPS/IS/last-beacon status pills are placeholders for phases 5 and 7.
- **Multi-part replies are joined into one card** (`head/grouping.py`, display only). Bots like WXBOT cut long replies at 67 characters, often mid-word, and send the parts a few seconds apart. An incoming part joins the previous one from the same peer if that one was ≥55 characters and ≤60 s older, with no message to or from that peer in between. It joins with no space if the previous part was exactly 67 characters (a mid-word cut), otherwise with a space. The core still stores and acks each part.
- **Screenshots for review:** on the Pi use `grim` under the labwc session (`WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000`). On the dev machine render offscreen with `QQuickWindow.grabWindow()` (import `PySide6.QtQuick` first, or the root object comes back as a plain `QWindow`).

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
- **Third-party packets (`}`) are unwrapped for messaging.** An iGate gating IS→RF sends `IGATE>...:}WXBOT>APRS,TCPIP,IGATE*::KF0KBP-7 :ack4}`. aprslib parses the inner packet into `subpacket`, and `Core` passes an inner message to the messenger with the inner sender as the peer. The stations table still records only the RF sender (the iGate); showing IS stations is phase 7.
- **iGate round trip (2026-09-24):** message to WXBOT through the home iGate (Graywolf on takserver, KF0KBP-1). It was acked over RF in 3 s, and WXBOT's unnumbered forecast reply arrived 9 s after sending. Before the third-party fix, the relayed ack was received but ignored and the message failed after 5 tries. On takserver, Graywolf's IS→RF was turned on with a single `message_dest KF0KBP-7` allow rule (Graywolf denies anything with no matching rule) and a TX cap of 4/min and 10/5 min, because that radio has high SWR. The config backup is `/var/lib/graywolf/graywolf.db.bak-20260924-080126`.
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
- **GPS UART uses `dtoverlay=disable-bt`** (2026-09-24), not `miniuart-bt`: Bluetooth is off and the PL011 (`/dev/ttyAMA0`, `/dev/serial0`) is on GPIO 14/15, with `enable_uart=1` and `console=serial0` removed from cmdline.txt. Originals backed up as `*.pre-gps` in `/boot/firmware/`. The GPS module sends NMEA-0183 at 9600 baud (`$GPRMC/VTG/GGA/GSA/GSV/GLL`, u-blox style). gpsd reads only `/dev/serial0`, with `USBAUTO="false"` so it never probes the Digirig or SCU-66 USB serial ports (`deploy/gpsd.default`).
- **gpsd is read over its raw JSON protocol** (`gps.GpsdClient`, `?WATCH`), not the `gps` Python package: no extra dependency, and it reconnects like the KISS client. A fix counts as lost after 10 s without a 2D/3D TPV report (`gps.FIX_STALE_S`) and whenever the gpsd link drops.
- **`beacon_interval_s` setting (default 1800, 0 = manual only)** for timed beacons when there's no GPS fix or SmartBeaconing is off. With neither a fix nor a fixed position, nothing is sent. To stop automatic beacons completely, turn SmartBeaconing off and set the interval to 0.
- **The first automatic beacon waits 60 s after start** (as Direwolf's default), so the TNC, APRS-IS and gpsd can come up first. A failed automatic beacon is retried after 30 s rather than a full interval. A manual beacon restarts the timers.
- **SmartBeacon `turn_slope` is in degrees × mph** (HamHUD/Direwolf convention, default 255), while the speeds are in km/h. Corner pegging only applies at or above the slow speed.
- **Course/speed are sent only at or above the SmartBeacon slow speed.** Parked, the u-blox drifted ~2 knots on a random heading (`343/002` on the first test beacon), which would show as a moving arrow. Altitude (`/A=`) is sent with 3D fixes only.
- **Status carries `gps_connected`, `gps_fix`, `gps` {mode, lat, lon, alt_m, speed_kmh, course, sats}, `can_beacon`, `last_beacon`.** GPS status events go out when the link or fix changes, otherwise at most every 5 s (gpsd reports every second).
- **Follow-up (phase 8): the Pi has no RTC.** In the vehicle, with no network, the system clock is wrong until something sets it; gpsd has UTC, so wire it to chrony (SHM refclock) when packaging.
