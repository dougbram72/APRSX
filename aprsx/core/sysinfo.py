"""Raspberry Pi health for the status page: CPU, temperature, memory, disk, power.

Everything is read from /proc and /sys, plus ``vcgencmd`` for the firmware's
undervoltage/throttling flags and ``systemctl`` for the services. Each part is
optional: on a PC (or without vcgencmd) it's simply left out.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
from pathlib import Path
from typing import Any

# vcgencmd get_throttled bits: now (low bits) and since boot (bits 16+).
THROTTLE_FLAGS = {
    0: "undervoltage", 1: "arm_freq_capped", 2: "throttled", 3: "soft_temp_limit",
}
USER_UNITS = ("aprsx-core", "aprsx-direwolf", "aprsx-head")
SYSTEM_UNITS = ("gpsd",)
CMD_TIMEOUT_S = 3


def decode_throttled(value: int) -> dict[str, list[str]]:
    """``get_throttled`` bits as {now: [...], since_boot: [...]}."""
    return {"now": [n for b, n in THROTTLE_FLAGS.items() if value & (1 << b)],
            "since_boot": [n for b, n in THROTTLE_FLAGS.items() if value & (1 << (b + 16))]}


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo as {field: bytes}."""
    out = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[key] = int(parts[0]) * (1024 if len(parts) > 1 and parts[1] == "kB" else 1)
    return out


def parse_cpu_times(text: str) -> tuple[int, int]:
    """(busy, total) jiffies from the first line of /proc/stat."""
    fields = [int(x) for x in text.splitlines()[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
    return sum(fields) - idle, sum(fields)


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


async def _run(*cmd: str) -> str | None:
    """stdout of a quick command, or None if it's missing, fails or hangs."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), CMD_TIMEOUT_S)
    except (OSError, asyncio.TimeoutError):
        return None
    return out.decode(errors="replace").strip()


class SysInfo:
    """Keeps the previous CPU sample, so CPU use is measured between calls."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or Path.home()
        self._cpu: tuple[int, int] | None = None

    def _cpu_percent(self) -> float | None:
        text = _read("/proc/stat")
        if text is None:
            return None
        busy, total = parse_cpu_times(text)
        prev, self._cpu = self._cpu, (busy, total)
        if prev is None or total == prev[1]:
            return None
        return round(100 * (busy - prev[0]) / (total - prev[1]), 1)

    async def collect(self) -> dict[str, Any]:
        out: dict[str, Any] = {"hostname": socket.gethostname(), "cpus": os.cpu_count()}
        if (t := _read("/sys/class/thermal/thermal_zone0/temp")) and t.strip().isdigit():
            out["temp_c"] = round(int(t) / 1000, 1)
        out["cpu_percent"] = self._cpu_percent()
        try:
            out["load"] = [round(x, 2) for x in os.getloadavg()]
        except OSError:
            pass
        if up := _read("/proc/uptime"):
            out["uptime_s"] = round(float(up.split()[0]))
        if mem := _read("/proc/meminfo"):
            m = parse_meminfo(mem)
            out["mem"] = {"total": m.get("MemTotal"), "available": m.get("MemAvailable"),
                          "swap_total": m.get("SwapTotal"), "swap_free": m.get("SwapFree")}
        if status := _read("/proc/self/status"):
            rss = parse_meminfo(status).get("VmRSS")
            out["core_rss"] = rss
        try:
            du = shutil.disk_usage(self.data_dir)
            out["disk"] = {"total": du.total, "free": du.free}
        except OSError:
            pass
        throttled, volts, clock, user, system = await asyncio.gather(
            _run("vcgencmd", "get_throttled"), _run("vcgencmd", "measure_volts", "core"),
            _run("vcgencmd", "measure_clock", "arm"),
            _run("systemctl", "--user", "is-active", *USER_UNITS),
            _run("systemctl", "is-active", *SYSTEM_UNITS))
        if throttled and "=" in throttled:
            value = int(throttled.split("=")[1], 16)
            out["throttled"] = {"raw": hex(value), **decode_throttled(value)}
        if volts and "=" in volts:
            out["core_volts"] = float(volts.split("=")[1].rstrip("V"))
        if clock and "=" in clock:
            out["arm_mhz"] = round(int(clock.split("=")[1]) / 1e6)
        services = {}
        for names, states in ((USER_UNITS, user), (SYSTEM_UNITS, system)):
            if states is not None:
                services.update(zip(names, states.split()))
        out["services"] = services
        return out
