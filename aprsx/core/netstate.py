"""Wi-Fi state from NetworkManager, for the head unit's status strip.

The Pi joins a known network when one is in range, and otherwise runs its own
access point (the ``aprsx-hotspot`` profile, brought up by deploy/aprsx-hotspot).
We poll ``nmcli`` so the head unit can say which, and where to point a tablet.
Reading needs no root. Without nmcli (a PC without NetworkManager) the state
stays None and polling stops.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

POLL_S = 10.0

Runner = Callable[..., Awaitable[str]]


def split_terse(line: str) -> list[str]:
    """Split an ``nmcli -t`` line on ':', honouring its backslash escapes."""
    fields, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def parse_devices(text: str) -> tuple[str, str, str] | None:
    """(device, state, connection) of the first Wi-Fi device in
    ``nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev`` output, or None."""
    for line in text.splitlines():
        f = split_terse(line)
        if len(f) >= 4 and f[1] == "wifi":
            return f[0], f[2], f[3]
    return None


def parse_values(text: str) -> list[str]:
    """The lines of ``nmcli -g`` output (backslash escapes removed)."""
    return [v.replace("\\:", ":").replace("\\\\", "\\") for v in text.splitlines()]


async def run_nmcli(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "nmcli", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await proc.communicate()
    finally:
        if proc.returncode is None:  # cancelled
            proc.kill()
            await proc.wait()
    if proc.returncode:
        raise OSError(f"nmcli {' '.join(args)}: exit {proc.returncode}")
    return out.decode(errors="replace")


class WifiState:
    """The Wi-Fi mode: ``{"mode": "client"|"hotspot"|"off", "ssid", "ip"}``, or
    None when there's no NetworkManager or Wi-Fi device.

    ``on_change()`` is called when it changes.
    """

    def __init__(self, on_change: Callable[[], None] | None = None,
                 run: Runner = run_nmcli, poll_s: float = POLL_S) -> None:
        self.on_change = on_change
        self.run = run
        self.poll_s = poll_s
        self.state: dict[str, Any] | None = None

    async def read(self) -> dict[str, Any] | None:
        dev = parse_devices(await self.run("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"))
        if dev is None:
            return None
        name, state, con = dev
        if state != "connected" or not con:
            return {"mode": "off", "ssid": None, "ip": None}
        ssid, mode = (parse_values(await self.run(
            "-g", "802-11-wireless.ssid,802-11-wireless.mode", "con", "show", con)) + ["", ""])[:2]
        addrs = parse_values(await self.run("-g", "IP4.ADDRESS", "dev", "show", name))
        ip = addrs[0].split(" | ")[0].split("/")[0] if addrs and addrs[0] else None
        return {"mode": "hotspot" if mode == "ap" else "client", "ssid": ssid or con, "ip": ip}

    async def poll(self) -> None:
        state = await self.read()
        if state != self.state:
            self.state = state
            if self.on_change:
                self.on_change()

    async def run_forever(self) -> None:
        """Poll until cancelled; stop quietly if nmcli isn't installed."""
        while True:
            try:
                await self.poll()
            except FileNotFoundError:
                log.info("wifi: no nmcli, not reporting Wi-Fi state")
                return
            except OSError as e:
                log.debug("wifi: %s", e)
            await asyncio.sleep(self.poll_s)


# --- settings ---------------------------------------------------------------------

HOTSPOT = "aprsx-hotspot"
HELPER = "/usr/local/sbin/aprsx-wifi"
HOTSPOT_UNIT = "aprsx-hotspot.service"


async def run_helper(*args: str, stdin: str = "") -> str:
    """``sudo -n aprsx-wifi ...`` (the sudoers rule install.sh adds)."""
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", HELPER, *args, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(stdin.encode() + b"\n"), 90)
    if proc.returncode:
        raise RuntimeError(err.decode(errors="replace").strip() or f"exit {proc.returncode}")
    return out.decode(errors="replace")


async def run_systemctl(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "systemctl", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return out.decode(errors="replace").strip()


def parse_scan(text: str) -> list[dict[str, Any]]:
    """``SSID:SIGNAL:SECURITY`` lines to networks, strongest first, one per SSID."""
    best: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        f = split_terse(line)
        if len(f) < 3 or not f[0]:
            continue
        signal = int(f[1]) if f[1].isdigit() else 0
        if f[0] not in best or signal > best[f[0]]["signal"]:
            best[f[0]] = {"ssid": f[0], "signal": signal, "security": f[2]}
    return sorted(best.values(), key=lambda n: -n["signal"])


class WifiSettings:
    """Known networks and the fallback hotspot, for the web settings page.

    Reading uses nmcli and systemctl directly; changes go through the root helper
    deploy/aprsx-wifi. All three runners can be replaced in tests.
    """

    def __init__(self, run: Runner = run_nmcli, helper: Runner = run_helper,
                 systemctl: Runner = run_systemctl) -> None:
        self.run = run
        self.helper = helper
        self.systemctl = systemctl

    async def read(self) -> dict[str, Any]:
        networks, hotspot = [], None
        for line in (await self.run("-t", "-f", "NAME,TYPE,AUTOCONNECT-PRIORITY,ACTIVE",
                                    "con", "show")).splitlines():
            f = split_terse(line)
            if len(f) < 4 or f[1] != "802-11-wireless":
                continue
            name, prio, active = f[0], f[2], f[3] == "yes"
            ssid, mode = (parse_values(await self.run(
                "-g", "802-11-wireless.ssid,802-11-wireless.mode", "con", "show", name))
                + ["", ""])[:2]
            if name == HOTSPOT:
                hotspot = {"ssid": ssid, "active": active}
            elif mode != "ap":
                networks.append({"name": name, "ssid": ssid or name, "active": active,
                                 "priority": int(prio) if prio.lstrip("-").isdigit() else 0})
        networks.sort(key=lambda n: (-n["priority"], n["ssid"].lower()))
        enabled = await self.systemctl("is-enabled", HOTSPOT_UNIT) == "enabled"
        return {"networks": networks,
                "hotspot": {"configured": hotspot is not None,
                            "enabled": enabled and hotspot is not None,
                            "ssid": hotspot["ssid"] if hotspot else "",
                            "active": bool(hotspot and hotspot["active"])}}

    async def scan(self) -> list[dict[str, Any]]:
        return parse_scan(await self.helper("scan"))

    async def set_network(self, ssid: str, password: str = "", priority: int = 0) -> None:
        await self.helper("network-set", ssid, str(priority), stdin=password)

    async def delete_network(self, name: str) -> None:
        await self.helper("network-delete", name)

    async def set_hotspot(self, enabled: bool, ssid: str, password: str = "") -> None:
        await self.helper("hotspot-set", "on" if enabled else "off", ssid, stdin=password)
