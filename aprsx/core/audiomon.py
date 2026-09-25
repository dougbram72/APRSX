"""Receive audio levels from Direwolf's log, for the status page.

Direwolf prints a level for every packet it decodes, e.g.

    KF0KBP-1 audio level = 48(24/12)   [NONE]   __|||||__
    Digipeater WIDE2 (probably W0NH-1) audio level = 0(0/0)    __|||||__

and, when run with ``-a N``, the noise level every N seconds:

    ADEVICE0: Sample rate approx. 44.1 k, 0 errors, receive audio level CH0 9

Around 50 is right for packets; the idle level only shows the channel noise. The
unit's output goes to the journal (see deploy/aprsx-direwolf.service), which we
follow with ``journalctl``. Without it (not managed, or on a PC) the monitor just
reports that it isn't available.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

UNIT = "aprsx-direwolf.service"
KEEP = 50
RETRY_S = 60

_PACKET_RE = re.compile(
    r"^(?P<who>.+?) audio level = (?P<level>\d+)\((?P<mark>\d+)/(?P<space>\d+)\)")
_PROBABLY_RE = re.compile(r"\(probably (?P<call>[^)]+)\)")
_STATS_RE = re.compile(
    r"^ADEVICE(?P<dev>\d+): Sample rate approx\. (?P<rate>[\d.]+) k, (?P<errors>\d+) errors?, "
    r"receive audio level CH(?P<ch>\d+) (?P<level>\d+)")
_ADVICE_RE = re.compile(r"^Audio input level is too (?P<which>low|high)")


def parse_line(line: str) -> tuple[str, dict[str, Any]] | None:
    """('packet' | 'stats' | 'advice', data) for a Direwolf output line, else None."""
    line = line.strip()
    if m := _PACKET_RE.match(line):
        who = m["who"].strip()
        if p := _PROBABLY_RE.search(who):
            station, via = p["call"], who.split(" (")[0]  # "Digipeater WIDE2"
        else:
            station, via = who, None
        return "packet", {"station": station, "via": via, "level": int(m["level"]),
                          "mark": int(m["mark"]), "space": int(m["space"])}
    if m := _STATS_RE.match(line):
        return "stats", {"rate_k": float(m["rate"]), "errors": int(m["errors"]),
                         "level": int(m["level"])}
    if m := _ADVICE_RE.match(line):
        return "advice", {"too": m["which"]}
    return None


class AudioMonitor:
    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.available = False
        self.error: str | None = None
        self.packets: deque[dict[str, Any]] = deque(maxlen=KEEP)
        self.stats: dict[str, Any] | None = None
        self.advice: dict[str, Any] | None = None

    def handle(self, line: str, ts: float) -> None:
        parsed = parse_line(line)
        if parsed is None:
            return
        kind, data = parsed
        data["ts"] = ts
        if kind == "packet":
            self.packets.appendleft(data)
        elif kind == "stats":
            self.stats = data
        else:
            self.advice = data

    def status(self) -> dict[str, Any]:
        levels = [p["level"] for p in self.packets]
        return {"available": self.available, "error": self.error, "stats": self.stats,
                "advice": self.advice, "packets": list(self.packets),
                "median_level": sorted(levels)[len(levels) // 2] if levels else None}

    async def run(self) -> None:
        """Follow Direwolf's journal forever; cancel the task to stop."""
        while True:
            try:
                await self._follow()
            except (OSError, ValueError) as e:
                self.error = str(e)
            self.available = False
            await asyncio.sleep(RETRY_S)

    async def _follow(self) -> None:
        # The recent history first, so the page isn't empty after a restart.
        proc = await asyncio.create_subprocess_exec(
            "journalctl", f"_SYSTEMD_USER_UNIT={UNIT}", "-f", "-n", "300", "-o", "json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1 << 20)
        try:
            self.available, self.error = True, None
            assert proc.stdout is not None
            while raw := await proc.stdout.readline():
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                msg = entry.get("MESSAGE")
                if isinstance(msg, list):  # journald sends non-UTF-8 text as bytes
                    msg = bytes(msg).decode(errors="replace")
                if isinstance(msg, str):
                    ts = int(entry.get("__REALTIME_TIMESTAMP", 0)) / 1e6 or self.clock()
                    self.handle(msg, ts)
            err = (await proc.stderr.read()).decode(errors="replace").strip() if proc.stderr else ""
            self.error = err or "journalctl stopped"
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
