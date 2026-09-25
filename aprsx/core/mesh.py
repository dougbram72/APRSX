"""MeshCore: nodes heard, contacts, direct and channel messages (see meshlink.py
for the radio side and wardrive.py for coverage pings).

MeshCore is a separate channel from APRS: nodes are public keys, not
callsigns, so it keeps its own tables and events and never goes through
``Core.transmit()``. Conversations are keyed ``dm:<pubkey prefix>`` (12 hex
digits, what the device reports for a sender) or ``ch:<channel index>``.

The device does the routing; we retry direct messages that go unacked
(``tick()``, like ``messaging.Messenger``), flooding the last try.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from .meshlink import MeshError, MeshLink
from .stations import distance_bearing
from .wardrive import Wardriver

if TYPE_CHECKING:
    from .service import Core

log = logging.getLogger(__name__)

MAX_TEXT_BYTES = 140
DM_TRIES = 3
# Wait at least this long for an ack, whatever the device suggests.
MIN_ACK_WAIT_S = 10
# How soon to try again when the device refused a send (doesn't count as a try).
TX_WAIT_S = 10
# After connecting, sync the clock, contacts and channels; retry this often on failure.
SYNC_RETRY_S = 30
CONTACTS_REFRESH_S = 600
# Keep the node's advert position near our GPS fix.
POSITION_MOVE_M = 100
POSITION_MAX_AGE_S = 300
DUPE_KEEP = 200

_CONV_RE = re.compile(r"^(dm:[0-9a-f]{12}|ch:\d{1,2})$")


class MeshService:
    def __init__(self, core: Core) -> None:
        self.core = core
        self.store = core.store
        self.link = MeshLink(self._address, self.on_event, on_state=self._link_state)
        self.wardrive = Wardriver(self)
        self.self_info: dict[str, Any] | None = None
        self.channels: list[dict[str, Any]] = []
        self._next_sync: float | None = None  # when to (re)sync; None = synced
        self._next_contacts = 0.0
        self._position: tuple[float, float, float] | None = None  # lat, lon, when pushed
        self._name_tried: str | None = None  # last name we set, so a refusal isn't retried
        self._seen: list[tuple] = []  # recent received messages, to drop duplicates

    def _address(self) -> tuple[str, int]:
        c = self.core.config.meshcore
        return c.device, c.baud

    def _link_state(self) -> None:
        if self.link.connected:
            self._next_sync = self.core.clock()
        else:
            self.self_info = None
            self._position = None
            self._name_tried = None
        self.core._publish_status()

    def status(self) -> dict[str, Any]:
        info = self.self_info or {}
        return {
            "enabled": self.core.config.meshcore.enabled,
            "connected": self.link.connected,
            "error": self.link.error,
            "name": info.get("name"),
            "pubkey": info.get("pubkey"),
            "radio": {k: info.get(k) for k in ("freq", "bw", "sf", "cr", "tx_power")}
                     if info else None,
            "unread": self.store.mesh_unread_count(),
            "wardrive": self.wardrive.status(),
        }

    # --- periodic work -----------------------------------------------------

    async def tick(self) -> None:
        """Sync after connecting, keep our position on the node, retry direct
        messages and run war-driving. Called every second."""
        now = self.core.clock()
        if self.link.connected:
            if self._next_sync is not None and now >= self._next_sync:
                await self._sync(now)
            elif now >= self._next_contacts:
                await self._sync_contacts(now)
            await self._apply_name()
            await self._share_position(now)
        for msg in self.store.due_mesh_messages(now):
            await self._try(msg, now)
        await self.wardrive.tick(now)

    async def _sync(self, now: float) -> None:
        try:
            await self.link.set_time(int(now))
            self.channels = await self.link.get_channels()
        except MeshError as e:
            log.warning("MeshCore sync failed: %s", e)
            self._next_sync = now + SYNC_RETRY_S
            return
        self._next_sync = None
        await self._sync_contacts(now)
        self.core._publish_status()

    async def _sync_contacts(self, now: float) -> None:
        self._next_contacts = now + CONTACTS_REFRESH_S
        try:
            contacts = await self.link.get_contacts()
        except MeshError as e:
            log.warning("MeshCore contacts: %s", e)
            return
        for c in contacts:
            # A contact's last advert may be old: don't let it bump last_heard.
            node = self.store.get_mesh_node(c["pubkey"])
            self.store.upsert_mesh_node(
                c["pubkey"], node["last_heard"] if node else now, is_contact=1,
                **{k: c[k] for k in ("name", "type", "lat", "lon", "hops")})
        self.core.bus.publish("mesh_nodes", {"count": len(contacts)})

    async def _apply_name(self) -> None:
        name = self.core.config.meshcore.name.strip()
        if not name or self.self_info is None or name in (self.name, self._name_tried):
            return
        self._name_tried = name
        try:
            await self.link.set_name(name)
        except MeshError as e:
            log.warning("MeshCore name: %s", e)
            return
        log.info("MeshCore node name set to %s", name)
        self.self_info = {**self.self_info, "name": name}
        self.core._publish_status()

    async def _share_position(self, now: float) -> None:
        if not self.core.config.meshcore.share_position:
            return
        fix = self.core.gps.current()
        if fix is None:
            return
        if self._position is not None:
            lat, lon, ts = self._position
            moved_m = distance_bearing(lat, lon, fix.lat, fix.lon)[0] * 1000
            if moved_m < POSITION_MOVE_M and now - ts < POSITION_MAX_AGE_S:
                return
        try:
            await self.link.set_coords(fix.lat, fix.lon)
        except MeshError as e:
            log.info("MeshCore position: %s", e)
        self._position = (fix.lat, fix.lon, now)

    # --- events from the device ---------------------------------------------

    def on_event(self, kind: str, data: dict[str, Any]) -> None:
        now = self.core.clock()
        if kind == "self_info":
            self.self_info = data
            self.core._publish_status()
        elif kind == "contact":
            self._node(data["pubkey"], now, name=data["name"], type=data["type"],
                       lat=data["lat"], lon=data["lon"], hops=data["hops"])
        elif kind == "dm":
            self._received(f"dm:{data['prefix']}", None, data, now)
        elif kind == "chan_msg":
            sender, sep, text = data["text"].partition(": ")
            if not sep:
                sender, text = None, data["text"]
            self._received(f"ch:{data['channel_idx']}", sender, {**data, "text": text}, now)
        elif kind == "ack":
            self._acked(data["code"], now)
        elif kind == "rx_log":
            if data.get("adv_key"):
                self._node(data["adv_key"], now, name=data.get("adv_name"),
                           type=data.get("adv_type"), lat=data.get("adv_lat"),
                           lon=data.get("adv_lon"), snr=data.get("snr"),
                           rssi=data.get("rssi"), hops=data.get("hops"))
            self.wardrive.on_rx(data, now)
        elif kind == "discover_resp":
            self.wardrive.on_discover(data, now)

    def _node(self, pubkey: str, ts: float, **fields: Any) -> None:
        node = self.store.upsert_mesh_node(pubkey, ts, **fields)
        self.core.bus.publish("mesh_node", node)

    def _received(self, conv: str, sender: str | None, data: dict[str, Any], now: float) -> None:
        key = (conv, data.get("sender_ts"), data["text"])
        if key in self._seen:
            return
        self._seen = [*self._seen[-DUPE_KEEP:], key]
        msg = self.store.add_mesh_message(
            ts=now, direction="in", conv=conv, sender=sender, text=data["text"],
            state="received", snr=data.get("snr"), hops=data.get("hops"))
        self.core.bus.publish("mesh_message", msg)

    def _acked(self, code: str, now: float) -> None:
        msg = self.store.find_mesh_by_ack(code)
        if msg is not None:
            msg = self.store.update_mesh_message(msg["id"], state="acked", next_try=None)
            self.core.bus.publish("mesh_ack", msg)

    # --- sending -----------------------------------------------------------

    async def send(self, conv: str, text: str) -> dict[str, Any]:
        """Send to a contact (``dm:<prefix>``) or a channel (``ch:<index>``)."""
        conv = conv.strip().lower()
        if not _CONV_RE.match(conv):
            raise MeshError(f"invalid conversation: {conv!r}")
        text = text.strip()
        if not text:
            raise MeshError("message text is empty")
        if len(text.encode()) > MAX_TEXT_BYTES:
            raise MeshError(f"message is longer than {MAX_TEXT_BYTES} bytes")
        if not self.link.connected:
            raise MeshError("MeshCore device not connected")
        now = self.core.clock()
        if conv.startswith("ch:"):
            ts = int(now)
            await self.link.send_chan(int(conv[3:]), text, ts)
            msg = self.store.add_mesh_message(ts=now, direction="out", conv=conv,
                                              sender=self.name, text=text, state="sent")
            self.core.bus.publish("mesh_message", msg)
            return msg
        msg = self.store.add_mesh_message(ts=now, direction="out", conv=conv, text=text,
                                          state="pending", next_try=now)
        self.core.bus.publish("mesh_message", msg)
        return await self._try(msg, now)

    @property
    def name(self) -> str | None:
        return (self.self_info or {}).get("name")

    async def _try(self, msg: dict[str, Any], now: float) -> dict[str, Any]:
        if msg["attempts"] >= DM_TRIES:
            msg = self.store.update_mesh_message(msg["id"], state="failed", next_try=None)
            self.core.bus.publish("mesh_ack", msg)
            return msg
        prefix = msg["conv"][3:]
        node = self.store.find_mesh_node(prefix)
        try:
            if msg["attempts"] == DM_TRIES - 1 and node:
                await self.link.reset_path(node["pubkey"])  # last try floods
            # The same sender timestamp on every try lets the peer drop repeats.
            sent = await self.link.send_dm(node["pubkey"] if node else prefix, msg["text"],
                                           msg["attempts"], int(msg["ts"]))
        except MeshError as e:
            log.info("MeshCore DM to %s: %s", prefix, e)
            return self.store.update_mesh_message(msg["id"], next_try=now + TX_WAIT_S)
        acks = " ".join(filter(None, [msg["expected_ack"], sent["expected_ack"]]))
        msg = self.store.update_mesh_message(
            msg["id"], attempts=msg["attempts"] + 1, expected_ack=acks,
            next_try=now + max(MIN_ACK_WAIT_S, sent["timeout_s"] * 1.5))
        self.core.bus.publish("mesh_ack", msg)
        return msg

    async def advert(self, flood: bool = False) -> None:
        await self.link.send_advert(flood)
