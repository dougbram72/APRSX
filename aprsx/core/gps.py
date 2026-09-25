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
# Fewer satellites than this, or a speed above this, and the receiver's "fix" isn't
# trusted: with a poor sky view a u-blox can report a runaway 3-satellite solution
# hundreds of km off, moving at hundreds of km/h (seen on the test Pi, 2026-09-25).
MIN_SATS_USED = 4
MAX_SPEED_MS = 300 / 3.6


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
        # For the status page: the satellites and error estimates gpsd last sent.
        self.satellites: list[dict[str, Any]] = []
        self.dop: dict[str, float] = {}
        self.errors: dict[str, float] = {}  # TPV error estimates (m): epx, epy, epv
        self.gps_time: str | None = None
        self._writer: asyncio.StreamWriter | None = None

    def current(self) -> Fix | None:
        """The latest fix, or None if there is none, it has gone stale, or it
        looks bogus (too few satellites, or an impossible speed)."""
        return self.fix if self.fix is not None and self.rejected() is None else None

    def rejected(self) -> str | None:
        """Why the latest fix isn't used, or None if it is (or there is none)."""
        if self.fix is None:
            return None
        if self.clock() - self.fix.ts > FIX_STALE_S:
            return "stale"
        if self.sats_used is not None and self.sats_used < MIN_SATS_USED:
            return f"only {self.sats_used} satellites used (need {MIN_SATS_USED})"
        if self.fix.speed_ms is not None and self.fix.speed_ms > MAX_SPEED_MS:
            return f"impossible speed ({self.fix.speed_kmh:.0f} km/h)"
        return None

    def detail(self) -> dict[str, Any]:
        """Everything known about the GPS, for the status page."""
        fix = self.fix
        return {
            "connected": self.connected,
            "fix": None if fix is None else {
                "lat": fix.lat, "lon": fix.lon, "mode": fix.mode, "alt_m": fix.alt_m,
                "speed_kmh": fix.speed_kmh, "course": fix.course,
                "age_s": round(self.clock() - fix.ts, 1)},
            "used": self.current() is not None,
            "rejected": self.rejected(),
            "time": self.gps_time,
            "sats_used": self.sats_used,
            "sats_seen": self.sats_seen,
            "dop": self.dop,
            "errors": self.errors,
            "satellites": self.satellites,
        }

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
                self.satellites, self.dop, self.errors = [], {}, {}
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
            self.gps_time = msg.get("time", self.gps_time)
            self.errors = {k: msg[k] for k in ("epx", "epy", "epv", "eps") if k in msg}
        elif cls == "SKY":
            sats = msg.get("satellites")
            if isinstance(sats, list):
                self.sats_seen = len(sats)
                self.sats_used = sum(1 for s in sats if s.get("used"))
                self.satellites = [
                    {k: s.get(k) for k in ("PRN", "gnssid", "el", "az", "ss", "used")}
                    for s in sats if isinstance(s, dict)]
            dop = {k: msg[k] for k in ("hdop", "vdop", "pdop", "gdop") if k in msg}
            if dop:
                self.dop = dop
            if not isinstance(sats, list):  # gpsd sends counts without the list on some cycles
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
