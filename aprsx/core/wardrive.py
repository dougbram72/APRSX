"""MeshCore war-driving: ping the mesh while moving and record who hears us.

MeshCore nodes don't answer adverts, so a session takes turns between two
kinds of ping (the same "hybrid" approach as MeshMapper):

- discover: a zero-hop discovery request. Repeaters in direct range answer
  with their key, how well they heard us (``remote_snr``) and we record how
  well we heard them. Answers count only inside ``discover_window_s``.
- chan: a short message on ``ping_channel``. Repeaters that hear it flood it
  on; for ``echo_window_s`` we match those repeats in the RX log (same channel
  and sender timestamp) and record the repeater we heard it from (the last
  hash in its path) and the signal.

Every other packet heard during a session is kept as an ``rx`` observation,
against our position then (at most ``RX_KEEP`` per session). Pings need a
GPS fix and go out at most every ``interval_s``, and only after moving
``min_distance_m``. Nothing is sent unless a session was started.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape

from .meshlink import MeshError, last_hop
from .stations import distance_bearing

if TYPE_CHECKING:
    from .mesh import MeshService

log = logging.getLogger(__name__)

RX_KEEP = 20000
PING_PREFIX = "@wd"


class Wardriver:
    def __init__(self, mesh: MeshService) -> None:
        self.mesh = mesh
        self.store = mesh.store
        self.session_id: int | None = None
        self.started: float | None = None
        self.paused: str | None = None  # why pings aren't going out
        self._reset()

    def _reset(self) -> None:
        self.pings = 0
        self.pings_heard = 0
        self.rx = 0
        self._nodes: set[str] = set()
        self._next_kind = "discover"
        self._last_ping: tuple[float, float, float] | None = None  # ts, lat, lon
        self._last_advert: float | None = None
        self._open: dict[str, Any] | None = None  # the ping still collecting answers

    @property
    def cfg(self):
        return self.mesh.core.config.meshcore.wardrive

    @property
    def active(self) -> bool:
        return self.session_id is not None

    def status(self) -> dict[str, Any]:
        return {"active": self.active, "session_id": self.session_id, "started": self.started,
                "paused": self.paused, "pings": self.pings, "pings_heard": self.pings_heard,
                "nodes": len(self._nodes), "rx": self.rx,
                "listening": self._open["kind"] if self._open else None}

    def _publish(self) -> None:
        self.mesh.core.bus.publish("wardrive", self.status())

    def start(self) -> dict[str, Any]:
        if not self.active:
            self._reset()
            self.started = self.mesh.core.clock()
            self.session_id = self.store.start_wd_session(self.started)
            log.info("war-drive session %d started", self.session_id)
            self._publish()
        return self.status()

    def stop(self) -> dict[str, Any]:
        if self.active:
            self._close()
            self.store.end_wd_session(self.session_id, self.mesh.core.clock())
            log.info("war-drive session %d stopped: %d pings, %d heard",
                     self.session_id, self.pings, self.pings_heard)
            self.session_id = self.started = self.paused = None
            self._publish()
        return self.status()

    # --- pinging -------------------------------------------------------------

    async def tick(self, now: float) -> None:
        if not self.active:
            return
        if self._open and now >= self._open["until"]:
            self._close()
        fix = self.mesh.core.gps.current()
        if not self.mesh.link.connected:
            paused = "MeshCore device not connected"
        elif fix is None:
            paused = "waiting for a GPS fix"
        else:
            paused = None
            await self._advert(now, fix.lat, fix.lon)
            if self._open is None and self._due(now, fix.lat, fix.lon):
                await self._ping(now, fix.lat, fix.lon)
        if paused != self.paused:
            self.paused = paused
            self._publish()

    def _due(self, now: float, lat: float, lon: float) -> bool:
        if self._last_ping is None:
            return True
        ts, plat, plon = self._last_ping
        return (now - ts >= self.cfg.interval_s
                and distance_bearing(plat, plon, lat, lon)[0] * 1000 >= self.cfg.min_distance_m)

    def _channel(self) -> int | None:
        name = self.cfg.ping_channel.strip().lower()
        return next((c["idx"] for c in self.mesh.channels if c["name"].lower() == name), None)

    async def _ping(self, now: float, lat: float, lon: float) -> None:
        kind = self._next_kind
        chan = self._channel()
        if kind == "chan" and chan is None:
            kind = "discover"  # the ping channel isn't set up on the device
        self._last_ping = (now, lat, lon)  # a failed ping also waits for the next slot
        try:
            if kind == "discover":
                tag = await self.mesh.link.discover()
                window = self.cfg.discover_window_s
            else:
                ts = int(now)
                await self.mesh.link.send_chan(chan, f"{PING_PREFIX} {lat:.5f},{lon:.5f}", ts)
                tag, window = str(ts), self.cfg.echo_window_s
        except MeshError as e:
            log.info("war-drive %s ping failed: %s", kind, e)
            return
        ping = self.store.add_wd_ping(self.session_id, now, kind, lat, lon, tag)
        self._open = {**ping, "until": now + window}
        self._next_kind = "chan" if kind == "discover" else "discover"
        self.pings += 1
        self.mesh.core.bus.publish("wardrive_ping", ping)
        self._publish()

    async def _advert(self, now: float, lat: float, lon: float) -> None:
        every = self.cfg.advert_interval_s
        if not every or (self._last_advert is not None and now - self._last_advert < every):
            return
        self._last_advert = now
        try:
            await self.mesh.link.send_advert(flood=False)
        except MeshError as e:
            log.info("war-drive advert failed: %s", e)
            return
        self.mesh.core.bus.publish(
            "wardrive_ping", self.store.add_wd_ping(self.session_id, now, "advert", lat, lon))

    def _close(self) -> None:
        if self._open is not None:
            ping = self.store.get_wd_ping(self._open["id"])
            self._open = None
            if ping["heard"]:
                self.pings_heard += 1
            self.mesh.core.bus.publish("wardrive_ping", ping)
            self._publish()

    # --- what we hear ----------------------------------------------------------

    def on_discover(self, d: dict[str, Any], now: float) -> None:
        p = self._open
        if not (self.active and p and p["kind"] == "discover" and d["tag"] == p["tag"]
                and now < p["until"]):
            return
        self._answer(p, now, "discover", d["pubkey"], d.get("node_type"), d.get("snr"),
                     d.get("rssi"), remote_snr=d.get("remote_snr"), hops=0)

    def on_rx(self, d: dict[str, Any], now: float) -> None:
        if not self.active:
            return
        p = self._open
        node = last_hop(d.get("path", ""), d.get("hash_size", 1))
        if (p and p["kind"] == "chan" and now < p["until"] and d.get("sender_ts") is not None
                and str(d["sender_ts"]) == p["tag"]
                and (d.get("chan_name") or "").lower() == self.cfg.ping_channel.strip().lower()
                and PING_PREFIX in (d.get("message") or "")):
            # A repeat of our own ping. Unrepeated copies (no path) can't be ours.
            if node:
                self._answer(p, now, "echo", node, None, d.get("snr"), d.get("rssi"),
                             hops=d.get("hops"))
            return
        fix = self.mesh.core.gps.current()
        if fix is None or self.rx >= RX_KEEP:
            return
        if node is None and d.get("adv_key"):
            node = d["adv_key"]  # an advert heard straight from its sender
        self.rx += 1
        self.store.add_wd_obs(session_id=self.session_id, ts=now, kind="rx", node=node,
                              node_type=d.get("adv_type") if node == d.get("adv_key") else None,
                              snr=d.get("snr"), rssi=d.get("rssi"), hops=d.get("hops"),
                              my_lat=fix.lat, my_lon=fix.lon)

    def _answer(self, ping: dict[str, Any], now: float, kind: str, node: str | None,
                node_type: int | None, snr: float | None, rssi: int | None, **extra: Any) -> None:
        obs = self.store.add_wd_obs(session_id=self.session_id, ping_id=ping["id"], ts=now,
                                    kind=kind, node=node, node_type=node_type, snr=snr,
                                    rssi=rssi, my_lat=ping["lat"], my_lon=ping["lon"], **extra)
        self.store.count_wd_response(ping["id"], snr)
        # Echoes name a repeater by its first key byte only (discovery by a
        # longer prefix), so count nodes by that byte, as list_wd_sessions does.
        if node:
            self._nodes.add(node[:2])
        self.mesh.core.bus.publish("wardrive_obs", resolve(self.store, [obs])[0])
        self._publish()


# --- reading sessions back ---------------------------------------------------------


def resolve(store, obs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add the node's name, type and last known position to observations."""
    cache: dict[str, dict | None] = {}
    out = []
    for o in obs:
        key = o.get("node")
        if key and key not in cache:
            cache[key] = store.find_mesh_node(key)
        n = cache.get(key) or {}
        out.append({**o, "node_name": n.get("name"),
                    "node_type": o.get("node_type") or n.get("type"),
                    "node_lat": n.get("lat"), "node_lon": n.get("lon")})
    return out


def session(store, id: int, rx: bool = True) -> dict[str, Any] | None:
    s = store.wd_session(id)
    if s is None:
        return None
    s["obs"] = resolve(store, [o for o in s["obs"] if rx or o["kind"] != "rx"])
    return s


def geojson(s: dict[str, Any]) -> dict[str, Any]:
    """Pings as points, answers as lines from where we were to the node (when
    its position is known)."""
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
        "properties": {"record": "ping", **{k: p[k] for k in
                                           ("id", "ts", "kind", "heard", "best_snr")}},
    } for p in s["pings"]]
    for o in s["obs"]:
        props = {"record": o["kind"], **{k: o[k] for k in (
            "ts", "ping_id", "node", "node_name", "node_type", "snr", "rssi", "remote_snr",
            "hops")}}
        if o["node_lat"] is not None and o["kind"] != "rx":
            geom = {"type": "LineString", "coordinates": [[o["my_lon"], o["my_lat"]],
                                                          [o["node_lon"], o["node_lat"]]]}
        else:
            geom = {"type": "Point", "coordinates": [o["my_lon"], o["my_lat"]]}
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": features,
            "properties": {"session": s["id"], "started": s["started"], "ended": s["ended"]}}


CSV_COLUMNS = ("record", "ts", "ping_id", "kind", "my_lat", "my_lon", "heard", "best_snr",
               "node", "node_name", "node_type", "node_lat", "node_lon", "snr", "rssi",
               "remote_snr", "hops")


def to_csv(s: dict[str, Any]) -> str:
    """One row per ping, then one per observation."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for p in s["pings"]:
        w.writerow({**p, "record": "ping", "ping_id": p["id"],
                    "my_lat": p["lat"], "my_lon": p["lon"]})
    for o in s["obs"]:
        w.writerow({**o, "record": "obs"})
    return buf.getvalue()


def to_gpx(s: dict[str, Any]) -> str:
    """The pings as a track, and each ping as a waypoint describing its answers."""
    from datetime import datetime, timezone

    def t(ts: float) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    by_ping: dict[int, list[str]] = {}
    for o in s["obs"]:
        if o["ping_id"] is not None:
            by_ping.setdefault(o["ping_id"], []).append(
                f"{o['node_name'] or o['node']} {o['snr']} dB")
    wpts = "".join(
        f'<wpt lat="{p["lat"]}" lon="{p["lon"]}"><time>{t(p["ts"])}</time>'
        f"<name>{escape(p['kind'])} {p['heard']}</name>"
        f"<desc>{escape(', '.join(by_ping.get(p['id'], [])) or 'no answer')}</desc></wpt>"
        for p in s["pings"])
    trkpts = "".join(f'<trkpt lat="{p["lat"]}" lon="{p["lon"]}"><time>{t(p["ts"])}</time></trkpt>'
                     for p in s["pings"])
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="APRS-X" xmlns="http://www.topografix.com/GPX/1/1">'
            f"{wpts}<trk><name>war-drive {s['id']}</name><trkseg>{trkpts}</trkseg></trk></gpx>\n")


def coverage(store, session_id: int | None = None) -> dict[str, Any]:
    c = store.wd_coverage(session_id)
    c["obs"] = resolve(store, c["obs"])
    return c
