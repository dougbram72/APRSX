"""gpsd client: the latest GPS fix, read over gpsd's JSON protocol (port 2947).

gpsd owns the serial port; we only ``?WATCH`` its report stream. TPV reports
carry the fix, SKY reports the satellite counts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

WATCH = b'?WATCH={"enable":true,"json":true};\n'
# A fix older than this counts as lost (gpsd reports once a second).
FIX_STALE_S = 10.0


@dataclass(frozen=True)
class Fix:
    lat: float
    lon: float
    mode: int  # 2 = 2D, 3 = 3D
    ts: float  # when we received it (core clock)
    alt_m: float | None = None  # above mean sea level, 3D fixes only
    speed_ms: float | None = None
    course: float | None = None  # degrees true; gpsd leaves it out when unknown

    @property
    def speed_kmh(self) -> float | None:
        return None if self.speed_ms is None else self.speed_ms * 3.6


def parse_tpv(msg: dict[str, Any], ts: float) -> Fix | None:
    """The fix in a TPV report, or None if it has no 2D/3D fix."""
    mode = msg.get("mode") or 0
    lat, lon = msg.get("lat"), msg.get("lon")
    if mode < 2 or lat is None or lon is None:
        return None
    # gpsd >= 3.20 reports altMSL; older versions only "alt" (also MSL).
    alt = msg.get("altMSL", msg.get("alt")) if mode >= 3 else None
    return Fix(lat=lat, lon=lon, mode=mode, ts=ts, alt_m=alt,
               speed_ms=msg.get("speed"), course=msg.get("track"))


class GpsdClient:
    """Keeps a connection to gpsd and the latest fix, reconnecting on failure.

    ``on_update()`` is called after every report and connection change.
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_update: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.time,
        reconnect_delay: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.on_update = on_update
        self.clock = clock
        self.reconnect_delay = reconnect_delay
        self.connected = False
        self.fix: Fix | None = None
        self.sats_used: int | None = None
        self.sats_seen: int | None = None
        self._writer: asyncio.StreamWriter | None = None

    def current(self) -> Fix | None:
        """The latest fix, or None if there is none or it has gone stale."""
        if self.fix is None or self.clock() - self.fix.ts > FIX_STALE_S:
            return None
        return self.fix

    async def run(self) -> None:
        """Connect and read forever; cancel the task to stop."""
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
            except OSError as e:
                log.warning("gpsd connect to %s:%s failed: %s", self.host, self.port, e)
                await asyncio.sleep(self.reconnect_delay)
                continue

            log.info("gpsd connected to %s:%s", self.host, self.port)
            self._writer = writer
            self.connected = True
            self._notify()
            try:
                writer.write(WATCH)
                while line := await reader.readline():
                    self.handle_line(line)
                log.warning("gpsd connection closed by peer")
            except (OSError, ValueError) as e:  # ValueError: line over the stream limit
                log.warning("gpsd connection lost: %s", e)
            finally:
                self.connected = False
                self._writer = None
                self.fix = None
                self.sats_used = self.sats_seen = None
                writer.close()
                self._notify()
            await asyncio.sleep(self.reconnect_delay)

    def handle_line(self, line: bytes | str) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            log.debug("bad gpsd line: %r", line)
            return
        if not isinstance(msg, dict):
            return
        cls = msg.get("class")
        if cls == "TPV":
            self.fix = parse_tpv(msg, self.clock())
        elif cls == "SKY":
            sats = msg.get("satellites")
            if isinstance(sats, list):
                self.sats_seen = len(sats)
                self.sats_used = sum(1 for s in sats if s.get("used"))
            else:  # gpsd sends counts without the list on some cycles
                self.sats_seen = msg.get("nSat", self.sats_seen)
                self.sats_used = msg.get("uSat", self.sats_used)
        else:
            return
        self._notify()

    def set_address(self, host: str, port: int) -> None:
        """Connect somewhere else: drops the current connection, which reconnects."""
        if (host, port) == (self.host, self.port):
            return
        self.host, self.port = host, port
        if self._writer is not None:
            self._writer.close()

    def _notify(self) -> None:
        if self.on_update is not None:
            try:
                self.on_update()
            except Exception:
                log.exception("gpsd update handler failed")
