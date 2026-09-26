"""HTTP/WebSocket API shared by the web UI and the touch-screen head unit.

REST:  GET  /api/status, /api/config, /api/packets?limit=&before=, /api/stations
       GET  /api/messages?peer=&limit=&before=, /api/conversations
       POST /api/messages {to, text}         -> queued outgoing message       [auth]
       POST /api/messages/read {peer}        -> mark a conversation read
       POST /api/beacon                      -> send a position beacon now    [auth]
       PUT  /api/config {settings..., admin_password?} -> save and apply  [auth]
            (fields left out keep their values; nested groups merge)
       GET  /api/session; POST /api/login {password}; POST /api/logout
       GET  /api/system/devices              -> sound cards and serial ports
       GET  /api/system/status               -> Pi health, GPS detail, audio levels, links
       POST /api/system/power {action}       -> "shutdown" or "reboot" the Pi [auth]
       GET  /api/aprsis/passcode?callsign=   -> APRS-IS passcode
       GET  /api/direwolf.conf               -> the config Direwolf gets
       GET  /tiles/{z}/{x}/{y}.png           -> map tiles (see tiles.py)
MeshCore (see mesh.py, wardrive.py); conv is 'dm:<pubkey prefix>' or 'ch:<index>':
       GET  /api/mesh/nodes, /api/mesh/channels, /api/mesh/conversations
       GET  /api/mesh/messages?conv=&limit=&before=
       POST /api/mesh/messages {conv, text}  -> send a DM or channel message  [auth]
       POST /api/mesh/messages/read {conv}   -> mark a conversation read
       POST /api/mesh/advert {flood}         -> send our advert now            [auth]
       POST /api/wardrive/start, /api/wardrive/stop                           [auth]
       GET  /api/wardrive/sessions, /api/wardrive/sessions/{id}?rx=
       GET  /api/wardrive/sessions/{id}/export?fmt=geojson|csv|gpx
       GET  /api/wardrive/coverage?session= -> pings and answers, for the map
[auth]: needs a session when a settings password is set, except from loopback.
WS:    /ws  -> {"type": ..., "data": {...}} where type is
       status | packet | station
       message  a new message row (in or out)
       ack      an outgoing message changed (tries, or acked/rejected/failed)
       read     {peer, unread}: a conversation was marked read
       power    {action, error?}: the Pi is shutting down / rebooting (or failed to)
       mesh_message  a MeshCore message row (in or out); mesh_ack  an outgoing one changed
       mesh_read     {conv, unread}; mesh_node  a node row; mesh_nodes  contacts were synced
       wardrive      session state and counters
       wardrive_ping a ping row (sent, and again when its answer window closes)
       wardrive_obs  an answer to a ping, with the node's name and position
Message events carry the full row; clients upsert by id.
"""

from __future__ import annotations

import asyncio
import glob
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import (Body, Depends, FastAPI, HTTPException, Query, Request, Response,
                     WebSocket, WebSocketDisconnect)
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import aprsis, auth, direwolf, wardrive
from .sysinfo import SysInfo
from .config import Config
from .meshlink import MeshError
from .messaging import MessageError
from .service import Core
from .stations import with_distance
from .tiles import TileServer, media_type

WEB_DIR = Path(__file__).parent / "web"
SYMBOL_DIR = Path(__file__).parents[1] / "symbols"  # APRS symbol sprite sheets


class SendMessage(BaseModel):
    to: str
    text: str


class MarkRead(BaseModel):
    peer: str


class SendMesh(BaseModel):
    conv: str
    text: str


class MeshRead(BaseModel):
    conv: str


class Advert(BaseModel):
    flood: bool = False


class Power(BaseModel):
    action: Literal["shutdown", "reboot"]


class WifiNetwork(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    password: str = ""
    priority: int = Field(0, ge=-999, le=999)


class Hotspot(BaseModel):
    enabled: bool
    ssid: str = Field(min_length=1, max_length=32)
    password: str = ""


class Login(BaseModel):
    password: str


def public_config(config: Config) -> dict[str, Any]:
    data = config.model_dump(exclude={"admin_password_hash"})
    data["has_password"] = bool(config.admin_password_hash)
    return data


def merge(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """``base`` updated with ``changes``; nested settings groups merge key by key."""
    out = dict(base)
    for k, v in changes.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def list_devices() -> dict[str, list[dict[str, str]]]:
    """Sound cards (as Direwolf ADEVICE names) and stable serial port paths."""
    audio = []
    try:
        cards = Path("/proc/asound/cards").read_text()
    except OSError:
        cards = ""
    for m in re.finditer(r"^\s*\d+ \[(\S+)\s*\]: \S+ - (.+)$", cards, re.M):
        audio.append({"id": f"plughw:CARD={m.group(1)},DEV=0", "name": m.group(2).strip()})
    serial = [{"id": p, "name": Path(p).name}
              for p in sorted(glob.glob("/dev/serial/by-id/*"))]
    return {"audio": audio, "serial": serial}


def create_app(core: Core, run_core: bool = True, tiles_dir: Path | None = None) -> FastAPI:
    """Build the app. run_core=False leaves starting the radio link to the caller (tests)."""
    sessions = auth.Sessions()
    sysinfo = SysInfo()
    tiles = TileServer(tiles_dir or Path("tiles"), online=lambda: core.config.tiles_online)

    def authenticated(request: Request) -> bool:
        return (not core.config.admin_password_hash
                or auth.is_loopback(request.client.host if request.client else None)
                or sessions.valid(request.cookies.get(auth.COOKIE)))

    def require_auth(request: Request) -> None:
        if not authenticated(request):
            raise HTTPException(401, "log in on the Settings page first")

    def set_session(response: Response) -> None:
        response.set_cookie(auth.COOKIE, sessions.create(), max_age=auth.SESSION_TTL_S,
                            httponly=True, samesite="strict")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if run_core:
            await core.start()
        try:
            yield
        finally:
            if run_core:
                await core.stop()

    app = FastAPI(title="APRS-X", lifespan=lifespan)

    @app.get("/api/status")
    def status():
        return core.status()

    @app.get("/api/packets")
    def packets(limit: int = Query(100, ge=1, le=1000), before: int | None = None):
        return core.store.recent_packets(limit, before)

    @app.get("/api/stations")
    def stations():
        return core.stations()

    @app.get("/api/config")
    def config():
        return public_config(core.config)

    @app.put("/api/config", dependencies=[Depends(require_auth)])
    async def put_config(response: Response, body: dict[str, Any] = Body(...)):
        # Fields left out keep their current values.
        body = merge(core.config.model_dump(), body)
        body.pop("has_password", None)
        new_password = body.pop("admin_password", None)
        body["admin_password_hash"] = core.config.admin_password_hash
        if new_password is not None:
            body["admin_password_hash"] = auth.hash_password(new_password) if new_password else ""
        try:
            new = Config.model_validate(body)
        except ValidationError as e:
            return JSONResponse(status_code=422, content={"detail": [
                {"field": ".".join(str(p) for p in err["loc"]), "msg": err["msg"]}
                for err in e.errors()]})
        result = await core.update_config(new)
        if new_password is not None:
            sessions.clear()
            if new_password:
                set_session(response)  # whoever set it stays logged in
        return {"config": public_config(new),
                "direwolf": None if result is None else vars(result)}

    @app.get("/api/session")
    def session(request: Request):
        return {"password_set": bool(core.config.admin_password_hash),
                "authenticated": authenticated(request)}

    @app.post("/api/login")
    async def login(body: Login, response: Response):
        stored = core.config.admin_password_hash
        if not stored or not auth.check_password(body.password, stored):
            await asyncio.sleep(1)  # slow down guessing
            raise HTTPException(401, "wrong password")
        set_session(response)
        return {"authenticated": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        sessions.revoke(request.cookies.get(auth.COOKIE))
        response.delete_cookie(auth.COOKIE)
        return {"authenticated": False}

    @app.get("/api/aprsis/passcode")
    def aprsis_passcode(callsign: str):
        return {"callsign": callsign.upper(), "passcode": aprsis.passcode(callsign)}

    @app.get("/api/system/devices")
    def devices():
        return list_devices()

    @app.get("/api/system/status")
    async def system_status():
        c = core.config
        return {
            "pi": await sysinfo.collect(),
            "gps": core.gps.detail(),
            "audio": core.audio.status(),
            "links": {
                "radio": c.radio,
                "rf_tx": core.rf_tx,
                "kiss": core.kiss.connected,
                "ftm200": {"device": c.ftm200.device, "baud": c.ftm200.baud,
                           "connected": core.ftm200.connected, "error": core.ftm200.error},
                "direwolf_managed": c.direwolf_managed,
                "direwolf_error": core.direwolf_error,
                "aprsis": {"enabled": c.aprsis.enabled, "connected": core.aprsis.connected,
                           "verified": core.aprsis.verified, "server": core.aprsis.server},
                "mesh": core.mesh.status(),
            },
            "counts": {"rx": core.rx_count, "tx": core.tx_count},
            "uptime_s": round(time.time() - core.started),
        }

    @app.post("/api/system/power", status_code=202, dependencies=[Depends(require_auth)])
    async def power(body: Power):
        core.power(body.action)
        return {"action": body.action}

    # --- Wi-Fi: known networks and the fallback hotspot (NetworkManager) ------------

    async def wifi_change(action) -> dict[str, Any]:
        try:
            await action
        except (RuntimeError, OSError, asyncio.TimeoutError) as e:
            raise HTTPException(400, str(e) or "failed") from e
        try:
            await core.wifi.poll()
        except OSError:
            pass
        return await wifi_settings()

    @app.get("/api/wifi")
    async def wifi_settings():
        try:
            data = await core.wifi_settings.read()
        except (OSError, FileNotFoundError) as e:
            return {"available": False, "error": str(e), "state": core.wifi.state}
        return {"available": True, "state": core.wifi.state, **data}

    @app.get("/api/wifi/scan", dependencies=[Depends(require_auth)])
    async def wifi_scan():
        try:
            return await core.wifi_settings.scan()
        except (RuntimeError, OSError, asyncio.TimeoutError) as e:
            raise HTTPException(400, str(e) or "scan failed") from e

    @app.put("/api/wifi/networks", dependencies=[Depends(require_auth)])
    async def wifi_network_set(body: WifiNetwork):
        return await wifi_change(core.wifi_settings.set_network(
            body.ssid, body.password, body.priority))

    @app.delete("/api/wifi/networks/{name}", dependencies=[Depends(require_auth)])
    async def wifi_network_delete(name: str):
        return await wifi_change(core.wifi_settings.delete_network(name))

    @app.put("/api/wifi/hotspot", dependencies=[Depends(require_auth)])
    async def wifi_hotspot(body: Hotspot):
        return await wifi_change(core.wifi_settings.set_hotspot(
            body.enabled, body.ssid, body.password))

    @app.get("/api/direwolf.conf", response_class=PlainTextResponse)
    def direwolf_conf():
        return direwolf.render(core.config)

    @app.get("/api/tiles")
    def tiles_status():
        return tiles.status()

    @app.get("/tiles/{z}/{x}/{y}.png")
    async def tile(z: int, x: int, y: int):
        data = await tiles.get(z, x, y)
        if data is None:
            raise HTTPException(404, "no tile")
        return Response(data, media_type=media_type(data),
                        headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/messages")
    def messages(
        peer: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        before: int | None = None,
    ):
        return core.store.list_messages(peer.upper() if peer else None, limit, before)

    @app.post("/api/messages", status_code=201, dependencies=[Depends(require_auth)])
    def send_message(body: SendMessage):
        try:
            return core.messenger.send(body.to, body.text)
        except MessageError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/messages/read")
    def mark_read(body: MarkRead):
        peer = body.peer.upper()
        if core.store.mark_read(peer):
            core.bus.publish("read", {"peer": peer, "unread": core.store.unread_count()})
        return {"peer": peer, "unread": core.store.unread_count()}

    @app.post("/api/beacon", dependencies=[Depends(require_auth)])
    def beacon():
        if core.config.callsign == "N0CALL":
            raise HTTPException(409, "set your callsign first")
        if not core.can_beacon():
            raise HTTPException(409, "no position: no GPS fix and no fixed position set")
        if not core.beacon():
            raise HTTPException(503, "not sent: no TNC or APRS-IS connection")
        return {"sent": True, "last_beacon": core.last_beacon}

    @app.get("/api/conversations")
    def conversations():
        return core.store.conversations()

    @app.get("/api/mesh/nodes")
    def mesh_nodes():
        lat, lon = core.my_position()
        return [with_distance(n, lat, lon) for n in core.store.list_mesh_nodes()]

    @app.get("/api/mesh/channels")
    def mesh_channels():
        return core.mesh.channels

    @app.get("/api/mesh/conversations")
    def mesh_conversations():
        return core.store.mesh_conversations()

    @app.get("/api/mesh/messages")
    def mesh_messages(conv: str | None = None, limit: int = Query(100, ge=1, le=1000),
                      before: int | None = None):
        return core.store.list_mesh_messages(conv.lower() if conv else None, limit, before)

    @app.post("/api/mesh/messages", status_code=201, dependencies=[Depends(require_auth)])
    async def send_mesh(body: SendMesh):
        if not core.mesh.link.connected:
            raise HTTPException(503, "MeshCore device not connected")
        try:
            return await core.mesh.send(body.conv, body.text)
        except MeshError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/mesh/messages/read")
    def mesh_read(body: MeshRead):
        conv = body.conv.lower()
        if core.store.mark_mesh_read(conv):
            core.bus.publish("mesh_read", {"conv": conv, "unread": core.store.mesh_unread_count()})
        return {"conv": conv, "unread": core.store.mesh_unread_count()}

    @app.post("/api/mesh/advert", dependencies=[Depends(require_auth)])
    async def mesh_advert(body: Advert = Body(Advert())):
        try:
            await core.mesh.advert(body.flood)
        except MeshError as e:
            raise HTTPException(503, str(e)) from None
        return {"sent": True, "flood": body.flood}

    @app.post("/api/wardrive/start", dependencies=[Depends(require_auth)])
    def wardrive_start():
        if not core.config.meshcore.enabled:
            raise HTTPException(409, "turn MeshCore on in Settings first")
        return core.mesh.wardrive.start()

    @app.post("/api/wardrive/stop", dependencies=[Depends(require_auth)])
    def wardrive_stop():
        return core.mesh.wardrive.stop()

    @app.get("/api/wardrive/sessions")
    def wardrive_sessions():
        return core.store.list_wd_sessions()

    @app.get("/api/wardrive/sessions/{id}")
    def wardrive_session(id: int, rx: bool = True):
        if (s := wardrive.session(core.store, id, rx=rx)) is None:
            raise HTTPException(404, "no such session")
        return s

    @app.get("/api/wardrive/sessions/{id}/export")
    def wardrive_export(id: int, fmt: Literal["geojson", "csv", "gpx"] = "geojson"):
        if (s := wardrive.session(core.store, id)) is None:
            raise HTTPException(404, "no such session")
        name = f"wardrive-{id}.{fmt}"
        headers = {"Content-Disposition": f'attachment; filename="{name}"'}
        if fmt == "csv":
            return Response(wardrive.to_csv(s), media_type="text/csv", headers=headers)
        if fmt == "gpx":
            return Response(wardrive.to_gpx(s), media_type="application/gpx+xml",
                            headers=headers)
        return JSONResponse(wardrive.geojson(s), media_type="application/geo+json",
                            headers=headers)

    @app.get("/api/wardrive/coverage")
    def wardrive_coverage(session: int | None = None):
        return wardrive.coverage(core.store, session)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()

        async def send_events(queue: asyncio.Queue) -> None:
            await websocket.send_json({"type": "status", "data": core.status()})
            while True:
                await websocket.send_json(await queue.get())

        async def wait_disconnect() -> None:
            # Clients don't send anything yet; reading is how we notice they left.
            # Without this, a closed client blocks on queue.get() forever and
            # holds up server shutdown.
            while True:
                await websocket.receive_text()

        with core.bus.subscribe() as queue:
            tasks = [asyncio.create_task(send_events(queue)),
                     asyncio.create_task(wait_disconnect())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in tasks:
                    t.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception) and not isinstance(
                    r, (WebSocketDisconnect, asyncio.CancelledError, RuntimeError)
                ):
                    raise r

    app.mount("/symbols", StaticFiles(directory=SYMBOL_DIR), name="symbols")
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
