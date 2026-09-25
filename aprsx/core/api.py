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
       GET  /api/aprsis/passcode?callsign=   -> APRS-IS passcode
       GET  /api/direwolf.conf               -> the config Direwolf gets
       GET  /tiles/{z}/{x}/{y}.png           -> map tiles (see tiles.py)
[auth]: needs a session when a settings password is set, except from loopback.
WS:    /ws  -> {"type": ..., "data": {...}} where type is
       status | packet | station
       message  a new message row (in or out)
       ack      an outgoing message changed (tries, or acked/rejected/failed)
       read     {peer, unread}: a conversation was marked read
Message events carry the full row; clients upsert by id.
"""

from __future__ import annotations

import asyncio
import glob
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import (Body, Depends, FastAPI, HTTPException, Query, Request, Response,
                     WebSocket, WebSocketDisconnect)
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import aprsis, auth, direwolf
from .config import Config
from .messaging import MessageError
from .service import Core
from .tiles import TileServer, media_type

WEB_DIR = Path(__file__).parent / "web"
SYMBOL_DIR = Path(__file__).parents[1] / "symbols"  # APRS symbol sprite sheets


class SendMessage(BaseModel):
    to: str
    text: str


class MarkRead(BaseModel):
    peer: str


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
