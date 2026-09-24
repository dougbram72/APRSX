"""The aprsx-core service: owns the radio link, state, and the event bus."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from . import aprs, ax25
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
    def __init__(self, store: Store, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.clock = clock
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
        self._tasks: list[asyncio.Task] = []

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self.kiss.run(), name="kiss"),
            asyncio.create_task(self._prune_loop(), name="prune"),
            asyncio.create_task(self._message_loop(), name="messages"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

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
            "unread": self.store.unread_count(),
            "uptime_s": round(time.time() - self.started),
            "position": dict(zip(("lat", "lon"), self.my_position())),
        }

    def stations(self) -> list[dict[str, Any]]:
        lat, lon = self.my_position()
        return [with_distance(s, lat, lon) for s in self.store.list_stations()]

    def _kiss_state(self, connected: bool) -> None:
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
        try:
            pkt = aprs.parse(tnc2)
        except aprs.ParseError as e:
            log.debug("unparsed packet (%s): %s", e, tnc2)
            pkt = None

        fmt = pkt.get("format") if pkt else None
        packet = self.store.add_packet(ts, tnc2, str(frame.source), fmt)
        self.bus.publish("packet", packet)

        station = self.store.upsert_station(station_record(frame, pkt, ts))
        self.bus.publish("station", with_distance(station, *self.my_position()))

        if fmt == "message":
            self.messenger.handle(str(frame.source), pkt, tnc2, ts)
        elif fmt == "thirdparty":
            # An iGate relaying from APRS-IS: "}WXBOT>APRS,TCPIP,IGATE*::ME :ack3".
            # aprslib parses the inner packet; the peer is its sender.
            inner = pkt.get("subpacket") or {}
            if inner.get("format") == "message" and inner.get("from"):
                self.messenger.handle(inner["from"].upper(), inner, inner.get("raw", ""), ts)

    # --- transmit path -----------------------------------------------------

    def transmit(self, info: str) -> bool:
        """Send one APRS packet from our station. False if it couldn't go out.

        This is the only way the core puts packets on the air, so another radio
        backend only has to replace it.
        """
        if self.config.callsign == "N0CALL":
            log.warning("not transmitting: callsign is not set")
            return False
        frame = ax25.Frame(
            source=ax25.Address.parse(self.config.station),
            dest=ax25.Address(aprs.TOCALL),
            path=[ax25.Address.parse(p) for p in self.config.path],
            info=info.encode("latin-1"),
        )
        try:
            self.kiss.write(ax25.encode(frame))
        except ConnectionError as e:
            log.warning("not transmitting: %s", e)
            return False
        self.tx_count += 1
        tnc2 = frame.to_tnc2()
        try:
            fmt = aprs.parse(tnc2).get("format")
        except aprs.ParseError:
            fmt = None
        packet = self.store.add_packet(
            self.clock(), tnc2, self.config.station, fmt, direction="tx"
        )
        self.bus.publish("packet", packet)
        return True
