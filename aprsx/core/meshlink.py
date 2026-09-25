"""Link to a MeshCore companion radio (USB serial), through the meshcore library.

This is the only module that imports ``meshcore``, and only inside ``run()``,
so the core runs without it (it's the optional ``mesh`` extra). Library events
are turned into plain dicts by ``translate()`` and handed to ``on_event(kind,
data)``; the kinds are:

    self_info      {name, pubkey, lat, lon, freq, bw, sf, cr, tx_power}
    contact        {pubkey, name, type, lat, lon, hops}  (contact sync, or a new advert)
    advert         {pubkey}                              (a known contact advertised)
    dm             {prefix, text, sender_ts, snr, hops}
    chan_msg       {channel_idx, text, sender_ts, snr, hops}
    ack            {code}
    rx_log         {snr, rssi, ptype, route, path, hash_size, hops, pkt_hash,
                    chan_name?, sender_ts?, message?,
                    adv_key?, adv_name?, adv_type?, adv_lat?, adv_lon?}
    discover_resp  {tag, pubkey, node_type, snr, rssi, remote_snr}

Positions of 0,0 mean "none" in MeshCore and come through as None. Tests use
``FakeMeshLink`` (tests/meshfake.py), which has the same methods.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

BACKOFF_S = (5, 10, 30, 60, 120)
# Poll the device this often; two failures in a row drop the link.
HEALTH_S = 60
# The companion firmware has at most this many channel slots (v1.15 reports 40).
MAX_CHANNELS = 40
# Node types in adverts and discovery (ADV_TYPE_*).
NODE_TYPES = {1: "companion", 2: "repeater", 3: "room", 4: "sensor"}
# Discovery asks these types to answer (a bit per type).
DISCOVER_FILTER = (1 << 2) | (1 << 3)


class MeshError(Exception):
    pass


def _pos(lat: float | None, lon: float | None) -> tuple[float | None, float | None]:
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None, None
    return lat, lon


def _hops(path_len: int | None) -> int | None:
    """Hop count from a message's path length; 255 means sent direct (no path)."""
    return None if path_len is None or path_len == 255 else path_len


def translate(event_type: str, p: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """(kind, data) for a meshcore event (its ``EventType`` value and payload),
    or None for events the core doesn't use."""
    if event_type == "self_info":
        lat, lon = _pos(p.get("adv_lat"), p.get("adv_lon"))
        return "self_info", {
            "name": p.get("name", ""), "pubkey": p.get("public_key"), "lat": lat, "lon": lon,
            "freq": p.get("radio_freq"), "bw": p.get("radio_bw"), "sf": p.get("radio_sf"),
            "cr": p.get("radio_cr"), "tx_power": p.get("tx_power")}
    if event_type == "new_contact":  # contact syncs come back from get_contacts() instead
        return "contact", contact(p)
    if event_type == "advertisement":
        return "advert", {"pubkey": p["public_key"]}
    if event_type == "contact_message":
        return "dm", {"prefix": p["pubkey_prefix"], "text": p.get("text", ""),
                      "sender_ts": p.get("sender_timestamp"), "snr": p.get("SNR"),
                      "hops": _hops(p.get("path_len"))}
    if event_type == "channel_message":
        return "chan_msg", {"channel_idx": p["channel_idx"], "text": p.get("text", ""),
                            "sender_ts": p.get("sender_timestamp"), "snr": p.get("SNR"),
                            "hops": _hops(p.get("path_len"))}
    if event_type == "acknowledgement":
        return ("ack", {"code": p["code"]}) if p.get("code") else None
    if event_type == "rx_log_data":
        return "rx_log", rx_log(p)
    if event_type == "discover_response":
        return "discover_resp", {
            "tag": p["tag"], "pubkey": p.get("pubkey"), "node_type": p.get("node_type"),
            "snr": p.get("SNR"), "rssi": p.get("RSSI"), "remote_snr": p.get("SNR_in")}
    return None


def contact(p: dict[str, Any]) -> dict[str, Any]:
    lat, lon = _pos(p.get("adv_lat"), p.get("adv_lon"))
    hops = p.get("out_path_len")
    return {"pubkey": p["public_key"], "name": p.get("adv_name") or None,
            "type": p.get("type"), "lat": lat, "lon": lon,
            "hops": hops if hops is not None and hops >= 0 else None}


def rx_log(p: dict[str, Any]) -> dict[str, Any]:
    out = {"snr": p.get("snr"), "rssi": p.get("rssi"),
           "ptype": p.get("payload_typename"), "route": p.get("route_typename"),
           "path": p.get("path", ""), "hash_size": p.get("path_hash_size", 1),
           "hops": p.get("path_len", 0), "pkt_hash": p.get("pkt_hash")}
    if "chan_name" in p:
        out["chan_name"] = p["chan_name"]
    if "message" in p:  # decrypted channel text: "<sender name>: <text>"
        out["message"] = p["message"]
        out["sender_ts"] = p.get("sender_timestamp")
    if "adv_key" in p:
        lat, lon = _pos(p.get("adv_lat"), p.get("adv_lon"))
        out.update(adv_key=p["adv_key"], adv_name=p.get("adv_name"),
                   adv_type=p.get("adv_type"), adv_lat=lat, adv_lon=lon)
    return out


def last_hop(path: str, hash_size: int) -> str | None:
    """The hash (hex) of the last node in a packet's path: the one we heard it from."""
    n = 2 * max(hash_size, 1)
    return path[-n:] if len(path) >= n else None


class MeshLink:
    """One companion radio. ``address()`` returns (device, baud) and is read at
    each (re)connect, so settings changes apply then."""

    def __init__(self, address: Callable[[], tuple[str, int]],
                 on_event: Callable[[str, dict[str, Any]], None],
                 on_state: Callable[[], None] | None = None) -> None:
        self.address = address
        self.on_event = on_event
        self.on_state = on_state
        self.connected = False
        self.error: str | None = None  # why it isn't connected, for the UI
        self._mc: Any = None

    def _set(self, connected: bool, error: str | None = None) -> None:
        if (connected, error) != (self.connected, self.error):
            self.connected, self.error = connected, error
            if self.on_state:
                self.on_state()

    async def run(self) -> None:
        """Connect and stay connected; cancel the task to stop."""
        try:
            from meshcore import EventType, MeshCore, SerialConnection
        except ImportError:
            self._set(False, "meshcore library not installed (install the 'mesh' extra)")
            log.error("MeshCore: %s", self.error)
            return
        failures = -1
        while True:
            device, baud = self.address()
            mc = None
            try:
                if not device:
                    raise MeshError("no serial device set")
                mc = await self._open(MeshCore, SerialConnection, device, baud)
                await self._session(mc, EventType)
                failures = -1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.info("MeshCore on %s: %s", device or "(none)", e)
                self._set(False, "device not found (unplugged?)"
                          if "No such file or directory" in str(e) else str(e))
            finally:
                self._mc = None
                if self.connected:
                    self._set(False, self.error)
                if mc is not None:
                    await self._close(mc)
            failures += 1
            await asyncio.sleep(BACKOFF_S[min(failures, len(BACKOFF_S) - 1)])

    @staticmethod
    async def _open(MeshCore: Any, SerialConnection: Any, device: str, baud: int) -> Any:
        """Like ``MeshCore.create_serial()``, but a failed attempt is always shut
        down: that one leaves the dispatcher task running when the port won't
        open (unplugged), leaking a task per retry."""
        for dtr in (True, False):  # some boards answer only with DTR inverted
            mc = MeshCore(SerialConnection(device, baud, cx_dly=0.1, rts=False, dtr=dtr))
            try:
                if await mc.connect() is not None:
                    return mc
            except BaseException:
                await MeshLink._close(mc)
                raise
            await MeshLink._close(mc)
        raise MeshError("no answer from the device (is it a USB companion?)")

    @staticmethod
    async def _close(mc: Any) -> None:
        try:
            await asyncio.wait_for(mc.disconnect(), 5)
        except Exception:
            mc.stop()  # at least end the dispatcher task

    async def _session(self, mc: Any, EventType: Any) -> None:
        lost = asyncio.Event()

        async def forward(event: Any) -> None:
            if event.type == EventType.DISCONNECTED:
                lost.set()
                return
            try:
                out = translate(event.type.value, event.payload or {})
            except (KeyError, TypeError, ValueError) as e:
                log.debug("MeshCore: bad %s event (%s): %r", event.type, e, event.payload)
                return
            if out:
                try:
                    self.on_event(*out)
                except Exception:
                    log.exception("MeshCore event handler failed: %s", out[0])

        sub = mc.subscribe(None, forward)
        mc.decrypt_channels = True  # rx log gets channel text, to match our pings' echoes
        await mc.start_auto_message_fetching()
        self._mc = mc
        self._set(True)
        log.info("MeshCore connected: %s", (mc.self_info or {}).get("name"))
        if mc.self_info:
            await forward(_Event(EventType.SELF_INFO, mc.self_info))
        try:
            failed = 0
            while failed < 2:
                try:
                    await asyncio.wait_for(lost.wait(), HEALTH_S)
                    log.info("MeshCore device disconnected")
                    return
                except asyncio.TimeoutError:
                    res = await mc.commands.get_bat()
                    failed = failed + 1 if res is None or res.is_error() else 0
            log.info("MeshCore device stopped answering")
        finally:
            mc.unsubscribe(sub)

    # --- commands ------------------------------------------------------------

    def _need(self) -> Any:
        if self._mc is None:
            raise MeshError("MeshCore device not connected")
        return self._mc

    @staticmethod
    def _check(res: Any, what: str) -> Any:
        if res is None:
            raise MeshError(f"{what}: no answer from the device")
        if res.is_error():
            raise MeshError(f"{what}: {res.payload.get('reason') or res.payload}")
        return res

    async def send_dm(self, pubkey: str, text: str, attempt: int, ts: int) -> dict[str, Any]:
        """Send a direct message. Returns {expected_ack, timeout_s}."""
        res = self._check(await self._need().commands.send_msg(
            pubkey, text, timestamp=ts, attempt=attempt), "send")
        return {"expected_ack": res.payload["expected_ack"].hex(),
                "timeout_s": res.payload["suggested_timeout"] / 1000}

    async def reset_path(self, pubkey: str) -> None:
        """Flood the next message to this contact (its stored route stopped working)."""
        self._check(await self._need().commands.reset_path(pubkey), "reset path")

    async def send_chan(self, idx: int, text: str, ts: int) -> None:
        self._check(await self._need().commands.send_chan_msg(idx, text, ts), "send")

    async def send_advert(self, flood: bool = False) -> None:
        self._check(await self._need().commands.send_advert(flood=flood), "advert")

    async def discover(self) -> str:
        """Ask nearby repeaters to identify themselves. Returns the tag (hex)
        their ``discover_resp`` events carry."""
        tag = random.randint(1, 0xFFFFFFFF)
        self._check(await self._need().commands.send_node_discover_req(
            DISCOVER_FILTER, prefix_only=True, tag=tag), "discover")
        return tag.to_bytes(4, "little").hex()

    async def set_coords(self, lat: float, lon: float) -> None:
        self._check(await self._need().commands.set_coords(lat, lon), "set position")

    async def set_name(self, name: str) -> None:
        self._check(await self._need().commands.set_name(name), "set name")

    async def set_time(self, ts: int) -> None:
        self._check(await self._need().commands.set_time(ts), "set time")

    async def get_contacts(self) -> list[dict[str, Any]]:
        res = self._check(await self._need().commands.get_contacts(), "contacts")
        return [contact(c) for c in res.payload.values()]

    async def get_channels(self) -> list[dict[str, Any]]:
        """The channels set on the device: [{idx, name}]."""
        out = []
        mc = self._need()
        for idx in range(MAX_CHANNELS):
            res = await mc.commands.get_channel(idx)
            if res is None or res.is_error():
                break
            if name := res.payload.get("channel_name"):
                out.append({"idx": idx, "name": name})
        return out


class _Event:
    """Stand-in for a library event, to replay the stored self info."""

    def __init__(self, type: Any, payload: dict[str, Any]) -> None:
        self.type, self.payload = type, payload
