"""APRS-IS client and iGate rules.

The client keeps one login to an APRS-IS server when enabled, reconnecting
with back-off. Gating follows the usual iGate rules (aprs-is.net/IGateDetails):

RF -> IS: every received packet except those whose path says not to
  (TCPIP, TCPXX, NOGATE, RFONLY), third-party packets and generic queries,
  with ",qAR,<our call>" added; digipeated copies are gated once.
IS -> RF: only messages (and acks) to a station heard *directly* on RF in the
  last 30 minutes, from a sender that isn't itself on RF, wrapped as a
  third-party packet and rate limited.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Callable

log = logging.getLogger(__name__)

VERSION = "0.1"
READ_TIMEOUT_S = 90         # servers send a "#" keepalive about every 20 s
CONNECT_TIMEOUT_S = 20
BACKOFF_S = (5, 10, 30, 60, 120)
GATE_DUPE_S = 30            # a packet (and its digipeated copies) is gated once
LOCAL_WINDOW_S = 30 * 60    # "heard on RF recently"
TX_LIMITS = ((60, 6), (300, 10))  # IS->RF: at most 6/min and 10 per 5 min

_NO_GATE = {"TCPIP", "TCPXX", "NOGATE", "RFONLY"}


def passcode(callsign: str) -> int:
    """The standard APRS-IS passcode for a callsign (the SSID doesn't matter)."""
    call = callsign.upper().split("-")[0]
    h = 0x73E2
    for i in range(0, len(call), 2):
        h ^= ord(call[i]) << 8
        if i + 1 < len(call):
            h ^= ord(call[i + 1])
    return h & 0x7FFF


def effective_filter(filter: str, lat: float | None, lon: float | None) -> str:
    """The server filter to log in with.

    "m/N" (range around our last position *as the server knows it*) finds
    nothing until we've sent a position to APRS-IS, so with a known position it
    becomes the equivalent "r/lat/lon/N".
    """
    if lat is None or lon is None:
        return filter
    return re.sub(r"(?<![^\s])m/(\d+)", lambda m: f"r/{lat:.3f}/{lon:.3f}/{m.group(1)}", filter)


def _hop(element: str) -> str:
    return element.rstrip("*").split("-")[0].upper()


def path_forbids_gating(path: list[str], to_rf: bool = False) -> bool:
    """TCPIP marks internet-originated packets: never gate those to IS, but
    they're exactly what goes IS -> RF."""
    blocked = _NO_GATE - {"TCPIP"} if to_rf else _NO_GATE
    return any(_hop(p) in blocked for p in path)


def rf_to_is_line(tnc2: str, my_call: str) -> str | None:
    """The APRS-IS line for a received RF packet, or None if it mustn't be gated."""
    header, sep, info = tnc2.partition(":")
    if not sep or not info:
        return None
    _, _, rest = header.partition(">")
    path = rest.split(",")[1:]
    if path_forbids_gating(path) or info[0] in "}?":
        return None
    info = re.split(r"[\r\n\x00]", info, maxsplit=1)[0]  # APRS-IS is line based
    return f"{header},qAR,{my_call}:{info}"


class Deduper:
    """Remembers keys for ``window`` seconds."""

    def __init__(self, window: float, clock: Callable[[], float] = time.time) -> None:
        self.window = window
        self.clock = clock
        self._seen: dict[object, float] = {}

    def seen(self, key: object) -> bool:
        """True if ``key`` was seen within the window; records it either way."""
        now = self.clock()
        if len(self._seen) > 1000:
            self._seen = {k: t for k, t in self._seen.items() if now - t < self.window}
        dup = now - self._seen.get(key, -self.window) < self.window
        if not dup:
            self._seen[key] = now
        return dup


class RateLimiter:
    def __init__(self, limits=TX_LIMITS, clock: Callable[[], float] = time.time) -> None:
        self.limits = limits
        self.clock = clock
        self._sent: deque[float] = deque()

    def allow(self) -> bool:
        now = self.clock()
        longest = max(w for w, _ in self.limits)
        while self._sent and now - self._sent[0] >= longest:
            self._sent.popleft()
        if any(sum(1 for t in self._sent if now - t < w) >= n for w, n in self.limits):
            return False
        self._sent.append(now)
        return True


class AprsIsClient:
    """One APRS-IS login. ``login()`` returns (server, port, callsign, passcode,
    filter) and is read at each (re)connect, so settings changes apply then."""

    def __init__(self, login: Callable[[], tuple[str, int, str, int, str]],
                 on_line: Callable[[str], None],
                 on_state: Callable[[], None] | None = None) -> None:
        self.login = login
        self.on_line = on_line
        self.on_state = on_state
        self.connected = False
        self.verified = False
        self.server: str | None = None  # server name from the login response
        self._writer: asyncio.StreamWriter | None = None

    def _set(self, connected: bool, verified: bool = False, server: str | None = None) -> None:
        if (connected, verified, server) != (self.connected, self.verified, self.server):
            self.connected, self.verified, self.server = connected, verified, server
            if self.on_state:
                self.on_state()

    async def run(self) -> None:
        """Connect and read forever; cancel the task to stop."""
        failures = -1
        while True:
            host, port, call, code, filt = self.login()
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), CONNECT_TIMEOUT_S)
            except (OSError, asyncio.TimeoutError) as e:
                log.info("APRS-IS connect to %s:%s failed: %s", host, port, e)
            else:
                try:
                    await self._session(reader, writer, call, code, filt)
                    failures = -1  # had a session: retry soon
                except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as e:
                    log.info("APRS-IS connection lost: %s", e)
                finally:
                    self._writer = None
                    writer.close()
                    self._set(False)
            failures += 1
            await asyncio.sleep(BACKOFF_S[min(failures, len(BACKOFF_S) - 1)])

    async def _session(self, reader, writer, call: str, code: int, filt: str) -> None:
        login = f"user {call} pass {code} vers APRS-X {VERSION}"
        if filt:
            login += f" filter {filt}"
        writer.write((login + "\r\n").encode())
        await writer.drain()
        self._writer = writer
        self._set(True)
        log.info("APRS-IS connected as %s", call)
        while raw := await asyncio.wait_for(reader.readline(), READ_TIMEOUT_S):
            line = raw.decode("latin-1").rstrip("\r\n")
            if not line:
                continue
            if line.startswith("#"):
                # "# logresp KF0KBP-7 verified, server T2USANE"
                if m := re.match(r"#\s*logresp\s+\S+\s+(\w+),?\s*(?:server\s+(\S+))?", line):
                    self._set(True, m.group(1) == "verified", m.group(2))
                    log.info("APRS-IS login: %s", line[1:].strip())
                continue
            try:
                self.on_line(line)
            except Exception:
                log.exception("APRS-IS line handler failed: %s", line)
        log.info("APRS-IS server closed the connection")

    def send(self, line: str) -> bool:
        """Send one packet line. Needs a verified login (servers drop the rest)."""
        if self._writer is None or not self.verified or "\n" in line or "\r" in line:
            return False
        self._writer.write(line.encode("latin-1", errors="replace") + b"\r\n")
        return True
