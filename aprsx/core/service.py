"""The aprsx-core service: owns the radio link, state, and the event bus."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import aprs, ax25
from .events import EventBus
from .kiss import KissTcpClient
from .stations import station_record, with_distance
from .store import Store

log = logging.getLogger(__name__)

PACKET_LOG_KEEP = 5000
PRUNE_INTERVAL_S = 600


class Core:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.config = store.load_config()
        self.bus = EventBus()
        self.started = time.time()
        self.rx_count = 0
        self.kiss = KissTcpClient(
            self.config.direwolf_host,
            self.config.direwolf_kiss_port,
            self.handle_frame,
            on_state=self._kiss_state,
        )
        self._tasks: list[asyncio.Task] = []

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self.kiss.run(), name="kiss"),
            asyncio.create_task(self._prune_loop(), name="prune"),
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
        ts = time.time()
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
