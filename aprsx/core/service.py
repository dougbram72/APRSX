"""The aprsx-core service: owns the radio link, state, and the event bus."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from . import aprs, aprsis, ax25
from .config import Config
from .direwolf import ApplyResult, DirewolfManager, needs_restart
from .events import EventBus
from .kiss import KissTcpClient
from .messaging import Messenger
from .stations import station_record, with_distance
from .store import Store

log = logging.getLogger(__name__)

PACKET_LOG_KEEP = 5000
PRUNE_INTERVAL_S = 600
MESSAGE_TICK_S = 1


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
            on_state=self._kiss_state,
        )
        self.messenger = Messenger(self)
        self.aprsis = aprsis.AprsIsClient(self._aprsis_login, self.handle_is_line,
                                          on_state=self._publish_status)
        self._aprsis_task: asyncio.Task | None = None
        self._gated = aprsis.Deduper(aprsis.GATE_DUPE_S, clock)
        self._is_tx_limit = aprsis.RateLimiter(clock=clock)
        self.gated = {"rf_to_is": 0, "is_to_rf": 0}
        self._tasks: list[asyncio.Task] = []

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self.config.direwolf_managed and self.direwolf and not self.direwolf.conf_path.exists():
            self.direwolf.write(self.config)  # first boot: the unit needs a config
        self._tasks = [
            asyncio.create_task(self.kiss.run(), name="kiss"),
            asyncio.create_task(self._prune_loop(), name="prune"),
            asyncio.create_task(self._message_loop(), name="messages"),
        ]
        self._sync_aprsis()

    async def stop(self) -> None:
        tasks = self._tasks + ([self._aprsis_task] if self._aprsis_task else [])
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks = []
        self._aprsis_task = None

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

    # --- state -------------------------------------------------------------

    def my_position(self) -> tuple[float | None, float | None]:
        # Phase 5 replaces this with the GPS fix when there is one.
        return self.config.fixed_lat, self.config.fixed_lon

    def status(self) -> dict[str, Any]:
        return {
            "station": self.config.station,
            "units": self.config.units,
            "kiss_connected": self.kiss.connected,
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
        result = None
        if new.direwolf_managed and self.direwolf and needs_restart(old, new):
            result = await self.direwolf.apply(new)
            self.direwolf_error = result.error
        login_changed = (old.station, old.aprsis.server, old.aprsis.port, old.aprsis.passcode,
                         old.aprsis.filter, old.fixed_lat, old.fixed_lon) != (
                         new.station, new.aprsis.server, new.aprsis.port, new.aprsis.passcode,
                         new.aprsis.filter, new.fixed_lat, new.fixed_lon)
        self._sync_aprsis(restart=login_changed)
        self._publish_status()
        return result

    def _kiss_state(self, connected: bool) -> None:
        self._publish_status()

    def _publish_status(self) -> None:
        self.bus.publish("status", self.status())

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

        This is the only way the core sends packets, so another radio backend
        only has to replace the RF half.
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
        try:
            self.kiss.write(ax25.encode(frame))
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
