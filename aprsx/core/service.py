"""The aprsx-core service: owns the radio link, state, and the event bus."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from . import aprs, aprsis, ax25
from .audiomon import AudioMonitor
from .beacon import Scheduler
from .config import Config
from .direwolf import ApplyResult, DirewolfManager, needs_restart
from .events import EventBus
from .ftm200 import Ftm200Reader
from .gps import Fix, GpsdClient
from .kiss import KissTcpClient
from .mesh import MeshService
from .messaging import Messenger
from .stations import station_record, with_distance
from .store import Store

log = logging.getLogger(__name__)

PACKET_LOG_KEEP = 5000
PRUNE_INTERVAL_S = 600
MESSAGE_TICK_S = 1
BEACON_TICK_S = 1
MESH_TICK_S = 1
# The first automatic beacon waits this long, for the TNC, APRS-IS and a GPS fix.
BEACON_STARTUP_DELAY_S = 60
# After an automatic beacon fails to go out, try again this much later.
BEACON_RETRY_S = 30
# While the fix state is unchanged, GPS updates go out as status events this often.
GPS_STATUS_S = 5
KNOTS_PER_MS = 1.943844
FEET_PER_M = 3.280840
# A power-off waits this long so the HTTP reply and the "power" event reach clients.
POWER_DELAY_S = 1.0
POWER_COMMANDS = {"shutdown": "poweroff", "reboot": "reboot"}


class Core:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time,
                 direwolf: DirewolfManager | None = None) -> None:
        self.store = store
        self.clock = clock
        self.direwolf = direwolf
        self.direwolf_error: str | None = None
        self.config = store.load_config()
        self.bus = EventBus()
        self.started = time.time()
        self.rx_count = 0
        self.tx_count = 0
        self.kiss = KissTcpClient(
            self.config.direwolf_host,
            self.config.direwolf_kiss_port,
            self.handle_frame,
            on_state=self._radio_state,
        )
        self.ftm200 = Ftm200Reader(self.config.ftm200.device, self.config.ftm200.baud,
                                   self.handle_tnc2, on_state=self._radio_state)
        self._radio_task: asyncio.Task | None = None
        self._radio_running: str | None = None
        self.messenger = Messenger(self)
        self.aprsis = aprsis.AprsIsClient(self._aprsis_login, self.handle_is_line,
                                          on_state=self._publish_status)
        self._aprsis_task: asyncio.Task | None = None
        self._gated = aprsis.Deduper(aprsis.GATE_DUPE_S, clock)
        self._is_tx_limit = aprsis.RateLimiter(clock=clock)
        self.gated = {"rf_to_is": 0, "is_to_rf": 0}
        self.gps = GpsdClient(self.config.gpsd_host, self.config.gpsd_port,
                              on_update=self._gps_update, clock=clock)
        self._gps_published: tuple[bool, bool, float] = (False, False, 0.0)
        self.beacons = Scheduler()
        self.last_beacon: float | None = None
        self.mesh = MeshService(self)
        self.audio = AudioMonitor(clock)
        self._mesh_task: asyncio.Task | None = None
        self._tasks: list[asyncio.Task] = []
        self.power_command = run_power_command  # tests replace this
        self._power_task: asyncio.Task | None = None

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self.config.direwolf_managed and self.direwolf:
            if self.config.radio != "direwolf":
                # The unit starts at boot whatever the backend; it mustn't hold the Digirig.
                self.direwolf_error = (await self.direwolf.stop()).error
            elif not self.direwolf.conf_path.exists():
                self.direwolf.write(self.config)  # first boot: the unit needs a config
        self.beacons.hold(self.clock() + BEACON_STARTUP_DELAY_S)
        self.store.end_open_wd_sessions(self.clock())
        self._tasks = [
            asyncio.create_task(self._prune_loop(), name="prune"),
            asyncio.create_task(self._message_loop(), name="messages"),
            asyncio.create_task(self.gps.run(), name="gps"),
            asyncio.create_task(self._beacon_loop(), name="beacon"),
            asyncio.create_task(self._mesh_loop(), name="mesh"),
            asyncio.create_task(self.audio.run(), name="audio"),
        ]
        self._sync_radio(start=True)
        self._sync_aprsis()
        self._sync_mesh()

    async def stop(self) -> None:
        self.mesh.wardrive.stop()
        tasks = self._tasks + [t for t in (self._aprsis_task, self._mesh_task, self._radio_task)
                               if t]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks = []
        self._aprsis_task = self._mesh_task = self._radio_task = None
        self._radio_running = None

    @property
    def radio(self) -> KissTcpClient | Ftm200Reader:
        """The backend that does the modem: Direwolf over KISS, or the FTM-200."""
        return self.ftm200 if self.config.radio == "ftm200" else self.kiss

    def _sync_radio(self, start: bool = False) -> None:
        """Run the selected radio backend's link, and only that one. Until
        ``start``, there's nothing running to switch (tests without a radio)."""
        if not start and self._radio_running is None:
            return
        want = self.config.radio
        if self._radio_running == want and self._radio_task and not self._radio_task.done():
            return
        if self._radio_task is not None:
            self._radio_task.cancel()
        self._radio_task = asyncio.create_task(self.radio.run(), name=f"radio-{want}")
        self._radio_running = want

    def _sync_aprsis(self, restart: bool = False) -> None:
        """Run the APRS-IS client exactly when it's enabled; ``restart`` re-logs in."""
        running = self._aprsis_task is not None and not self._aprsis_task.done()
        if running and (restart or not self.config.aprsis.enabled):
            self._aprsis_task.cancel()
            self._aprsis_task = None
            self.aprsis._set(False)
            running = False
        if not running and self.config.aprsis.enabled:
            self._aprsis_task = asyncio.create_task(self.aprsis.run(), name="aprsis")

    def _sync_mesh(self, restart: bool = False) -> None:
        """Run the MeshCore link exactly when it's enabled; ``restart`` reconnects."""
        running = self._mesh_task is not None and not self._mesh_task.done()
        if running and (restart or not self.config.meshcore.enabled):
            self._mesh_task.cancel()
            self._mesh_task = None
            self.mesh.link._set(False)
            running = False
        if not running and self.config.meshcore.enabled:
            self._mesh_task = asyncio.create_task(self.mesh.link.run(), name="meshcore")

    def _aprsis_login(self) -> tuple[str, int, str, int, str]:
        c = self.config
        return (c.aprsis.server, c.aprsis.port, c.station, c.aprsis.passcode,
                aprsis.effective_filter(c.aprsis.filter, *self.my_position()))

    async def _prune_loop(self) -> None:
        while True:
            if n := self.store.prune_packets(PACKET_LOG_KEEP):
                log.info("pruned %d old packets", n)
            await asyncio.sleep(PRUNE_INTERVAL_S)

    async def _message_loop(self) -> None:
        while True:
            try:
                self.messenger.tick()
            except Exception:
                log.exception("message retry tick failed")
            await asyncio.sleep(MESSAGE_TICK_S)

    async def _beacon_loop(self) -> None:
        while True:
            try:
                self.beacon_tick()
            except Exception:
                log.exception("beacon tick failed")
            await asyncio.sleep(BEACON_TICK_S)

    async def _mesh_loop(self) -> None:
        while True:
            try:
                await self.mesh.tick()
            except Exception:
                log.exception("MeshCore tick failed")
            await asyncio.sleep(MESH_TICK_S)

    # --- state -------------------------------------------------------------

    def my_position(self) -> tuple[float | None, float | None]:
        """The GPS fix when there is one, else the fixed position (or None, None)."""
        if fix := self.gps.current():
            return fix.lat, fix.lon
        return self.config.fixed_lat, self.config.fixed_lon

    @property
    def rf_tx(self) -> bool:
        """Whether the radio backend can transmit at all (the FTM-200 can't)."""
        return self.radio.can_transmit

    def can_transmit(self) -> bool:
        """Whether a packet has anywhere to go: the radio, or APRS-IS when logged in."""
        return self.rf_tx or self.aprsis.verified

    def can_beacon(self) -> bool:
        return (self.config.callsign != "N0CALL" and None not in self.my_position()
                and self.can_transmit())

    def gps_status(self) -> dict[str, Any] | None:
        fix = self.gps.current()
        if fix is None:
            return None
        return {"mode": fix.mode, "lat": fix.lat, "lon": fix.lon, "alt_m": fix.alt_m,
                "speed_kmh": fix.speed_kmh, "course": fix.course, "sats": self.gps.sats_used}

    def status(self) -> dict[str, Any]:
        return {
            "station": self.config.station,
            "units": self.config.units,
            "radio": self.config.radio,
            "radio_connected": self.radio.connected,
            "kiss_connected": self.kiss.connected,
            "rf_tx": self.rf_tx,
            "can_transmit": self.can_transmit(),
            "rx_count": self.rx_count,
            "tx_count": self.tx_count,
            "direwolf_managed": self.config.direwolf_managed,
            "direwolf_error": self.direwolf_error,
            "aprsis_enabled": self.config.aprsis.enabled,
            "aprsis_connected": self.aprsis.connected,
            "aprsis_verified": self.aprsis.verified,
            "aprsis_server": self.aprsis.server,
            "gated": dict(self.gated),
            "unread": self.store.unread_count(),
            "uptime_s": round(time.time() - self.started),
            "position": dict(zip(("lat", "lon"), self.my_position())),
            "gps_connected": self.gps.connected,
            "gps_fix": self.gps.current() is not None,
            "gps": self.gps_status(),
            "can_beacon": self.can_beacon(),
            "last_beacon": self.last_beacon,
            "mesh": self.mesh.status(),
        }

    def stations(self) -> list[dict[str, Any]]:
        lat, lon = self.my_position()
        return [with_distance(s, lat, lon) for s in self.store.list_stations()]

    async def update_config(self, new: Config) -> ApplyResult | None:
        """Save and apply new settings. Returns the Direwolf result if it was touched."""
        old = self.config
        self.store.save_config(new)
        self.config = new
        self.kiss.set_address(new.direwolf_host, new.direwolf_kiss_port)
        self.ftm200.set_device(new.ftm200.device, new.ftm200.baud)
        self.gps.set_address(new.gpsd_host, new.gpsd_port)
        result = None
        if new.direwolf_managed and self.direwolf:
            if new.radio != "direwolf":
                if old.radio == "direwolf" or not old.direwolf_managed:
                    result = await self.direwolf.stop()
                    self.direwolf_error = result.error
            elif needs_restart(old, new) or old.radio != "direwolf":
                result = await self.direwolf.apply(new)
                self.direwolf_error = result.error
        self._sync_radio()
        login_changed = (old.station, old.aprsis.server, old.aprsis.port, old.aprsis.passcode,
                         old.aprsis.filter, old.fixed_lat, old.fixed_lon) != (
                         new.station, new.aprsis.server, new.aprsis.port, new.aprsis.passcode,
                         new.aprsis.filter, new.fixed_lat, new.fixed_lon)
        self._sync_aprsis(restart=login_changed)
        if not new.meshcore.enabled:
            self.mesh.wardrive.stop()
        self._sync_mesh(restart=(old.meshcore.device, old.meshcore.baud) != (
            new.meshcore.device, new.meshcore.baud))
        self._publish_status()
        return result

    def power(self, action: str) -> None:
        """Shut down or reboot the Pi, shortly. systemd stops the services cleanly first."""
        if action not in POWER_COMMANDS:
            raise ValueError(f"unknown power action: {action}")
        log.warning("power: %s requested", action)
        self.bus.publish("power", {"action": action})
        self._power_task = asyncio.create_task(self._power(action), name="power")

    async def _power(self, action: str) -> None:
        await asyncio.sleep(POWER_DELAY_S)
        try:
            await self.power_command(POWER_COMMANDS[action])
        except Exception as e:
            log.error("power: %s failed: %s", action, e)
            self.bus.publish("power", {"action": action, "error": str(e)})

    def _radio_state(self, connected: bool) -> None:
        self._publish_status()

    def _publish_status(self) -> None:
        self.bus.publish("status", self.status())

    def _gps_update(self) -> None:
        """Publish GPS changes: at once when the link or fix comes or goes,
        otherwise every GPS_STATUS_S (gpsd reports every second)."""
        connected, has_fix = self.gps.connected, self.gps.current() is not None
        was_connected, had_fix, last = self._gps_published
        now = self.clock()
        if (connected, has_fix) != (was_connected, had_fix) or (
                has_fix and now - last >= GPS_STATUS_S):
            self._gps_published = (connected, has_fix, now)
            self._publish_status()

    # --- beaconing ---------------------------------------------------------

    def beacon_tick(self) -> None:
        """Send an automatic beacon if one is due. Called every second."""
        self._gps_update()  # notices a fix going stale without new reports
        if not self.can_beacon() or not self.auto_beacon_on():
            return
        now = self.clock()
        fix = self.gps.current()
        sb = self.config.smartbeacon
        if fix and sb.enabled:
            due = self.beacons.due_smart(sb, now, fix.speed_kmh, fix.course)
        else:
            due = self.beacons.due_fixed(self.config.beacon_interval_s, now)
        if due and not self.beacon():
            self.beacons.hold(now + BEACON_RETRY_S)

    def auto_beacon_on(self) -> bool:
        """False with the FTM-200, which beacons itself, unless asked for."""
        return self.config.radio != "ftm200" or self.config.ftm200.auto_beacon

    def beacon(self) -> bool:
        """Send our position now: the GPS fix (with course, speed and altitude
        when known), else the fixed position. True if it went out."""
        if not self.can_beacon():
            return False
        c = self.config
        fix = self.gps.current()
        now = self.clock()
        info = aprs.encode.position(*self.my_position(), **self._motion(fix, c),
                                    symbol_table=c.symbol_table, symbol=c.symbol,
                                    comment=c.beacon_comment)
        if not self.transmit(info):
            return False
        self.beacons.sent(now, fix.course if fix else None)
        self.last_beacon = now
        self._publish_status()
        return True

    @staticmethod
    def _motion(fix: Fix | None, c: Config) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if fix is None:
            return out
        # A parked GPS drifts a few km/h on a random course; below the
        # SmartBeacon slow speed, send a plain position instead.
        if (fix.course is not None and fix.speed_ms is not None
                and fix.speed_kmh >= c.smartbeacon.slow_speed_kmh):
            out["course"] = round(fix.course)
            out["speed_knots"] = fix.speed_ms * KNOTS_PER_MS
        if fix.alt_m is not None:
            out["altitude_ft"] = fix.alt_m * FEET_PER_M
        return out

    # --- receive path ------------------------------------------------------

    def handle_frame(self, port: int, raw: bytes) -> None:
        ts = self.clock()
        try:
            frame = ax25.decode(raw)
        except ax25.AX25Error as e:
            log.debug("undecodable frame (%s): %s", e, raw.hex())
            return
        tnc2 = frame.to_tnc2()
        self.handle_packet(frame, tnc2, ts)

    def handle_tnc2(self, line: str) -> None:
        """A packet the radio decoded itself, as TNC2 text (the FTM-200 backend)."""
        ts = self.clock()
        try:
            frame = ax25.Frame.from_tnc2(line)
        except ax25.AX25Error as e:
            log.debug("unusable packet from the radio (%s): %r", e, line)
            return
        self.handle_packet(frame, line, ts)

    def handle_packet(self, frame: ax25.Frame, tnc2: str, ts: float) -> None:
        self.rx_count += 1
        path = [str(d) + ("*" if d.repeated else "") for d in frame.path]
        pkt = self._receive(str(frame.source), path, tnc2, ts, "rf")
        self._gate_rf_to_is(frame, tnc2)
        if pkt and pkt.get("format") == "thirdparty":
            # An iGate relaying from APRS-IS: "}WXBOT>APRS,TCPIP,IGATE*::ME :ack3".
            # aprslib parses the inner packet; the peer is its sender.
            inner = pkt.get("subpacket") or {}
            if inner.get("format") == "message" and inner.get("from"):
                self.messenger.handle(inner["from"].upper(), inner, inner.get("raw", ""), ts)

    def handle_is_line(self, line: str) -> None:
        """A packet line from APRS-IS (not a # comment)."""
        ts = self.clock()
        header = line.partition(":")[0]
        source, _, rest = header.partition(">")
        path = rest.split(",")[1:]
        if not source or source.upper() == self.config.station:
            return
        pkt = self._receive(source.upper(), path, line, ts, "is")
        if pkt and pkt.get("format") == "message":
            self._gate_is_to_rf(source.upper(), path, line, pkt)

    def _receive(self, source: str, path: list[str], tnc2: str, ts: float,
                 channel: str) -> dict[str, Any] | None:
        """Parse, log, update the station and hand messages to the messenger."""
        try:
            pkt = aprs.parse(tnc2)
        except aprs.ParseError as e:
            log.debug("unparsed packet (%s): %s", e, tnc2)
            pkt = None
        fmt = pkt.get("format") if pkt else None
        packet = self.store.add_packet(ts, tnc2, source, fmt, channel=channel)
        self.bus.publish("packet", packet)
        station = self.store.upsert_station(station_record(source, path, pkt, ts, channel))
        self.bus.publish("station", with_distance(station, *self.my_position()))
        if fmt == "message":
            self.messenger.handle(source, pkt, tnc2, ts)
        return pkt

    # --- iGate -------------------------------------------------------------

    def _gate_rf_to_is(self, frame: ax25.Frame, tnc2: str) -> None:
        if not (self.config.aprsis.igate and self.aprsis.verified):
            return
        line = aprsis.rf_to_is_line(tnc2, self.config.station)
        # Digipeated copies differ only in the path: key on source, dest and info.
        if line and not self._gated.seen((str(frame.source), str(frame.dest), frame.info)):
            if self.aprsis.send(line):
                self.gated["rf_to_is"] += 1

    def _gate_is_to_rf(self, source: str, path: list[str], line: str, pkt: dict) -> None:
        """Pass an APRS-IS message on to a station that's local on RF."""
        me = self.config.station
        to = pkt.get("addresse", "").strip().upper()
        now = self.clock()
        if (not self.config.aprsis.is_to_rf or not to or to == me
                or not self.store.heard_on_rf(to, now - aprsis.LOCAL_WINDOW_S, direct=True)
                or self.store.heard_on_rf(source, now - aprsis.LOCAL_WINDOW_S)
                or aprsis.path_forbids_gating(path, to_rf=True)
                or any(p.rstrip("*").upper() == me for p in path)):
            return
        header, _, info = line.partition(":")
        dest = header.partition(">")[2].split(",")[0]
        if self._gated.seen(("is2rf", source, to, info)):
            return
        if not self._is_tx_limit.allow():
            log.warning("IS->RF rate limit: not gating %s", line)
            return
        # Target was heard directly, so no digipeater path is needed.
        if self.transmit(f"}}{source}>{dest},TCPIP,{me}*:{info}", path=[], to_is=False):
            self.gated["is_to_rf"] += 1
    # --- transmit path -----------------------------------------------------

    def transmit(self, info: str, *, path: list[str] | None = None, to_is: bool = True) -> bool:
        """Send one APRS packet from our station, on RF and (when logged in and
        ``to_is``) straight to APRS-IS. True if it went out on either.

        This is the only way the core sends packets. The RF half goes through the
        radio backend, and is skipped when it can't transmit (the FTM-200).
        """
        if self.config.callsign == "N0CALL":
            log.warning("not transmitting: callsign is not set")
            return False
        frame = ax25.Frame(
            source=ax25.Address.parse(self.config.station),
            dest=ax25.Address(aprs.TOCALL),
            path=[ax25.Address.parse(p) for p in (self.config.path if path is None else path)],
            info=info.encode("latin-1"),
        )
        sent = False
        if self.rf_tx:
            try:
                self.radio.write(ax25.encode(frame))
            except ConnectionError as e:
                log.warning("not transmitting on RF: %s", e)
            else:
                self._log_tx(frame.to_tnc2(), "rf")
                sent = True
        if to_is:
            line = f"{self.config.station}>{aprs.TOCALL},TCPIP*:{info}"
            if self.aprsis.send(line):
                self._log_tx(line, "is")
                sent = True
        return sent

    def _log_tx(self, tnc2: str, channel: str) -> None:
        self.tx_count += 1
        try:
            fmt = aprs.parse(tnc2).get("format")
        except aprs.ParseError:
            fmt = None
        packet = self.store.add_packet(self.clock(), tnc2, self.config.station, fmt,
                                       direction="tx", channel=channel)
        self.bus.publish("packet", packet)


async def run_power_command(command: str) -> None:
    """``systemctl poweroff|reboot`` through the sudoers rule install.sh adds."""
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", "/usr/bin/systemctl", command,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), 30)
    if proc.returncode != 0:
        raise RuntimeError(out.decode(errors="replace").strip() or f"exit {proc.returncode}")
