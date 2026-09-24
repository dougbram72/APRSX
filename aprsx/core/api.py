"""HTTP/WebSocket API shared by the web UI and the touch-screen head unit.

REST:  GET  /api/status, /api/config, /api/packets?limit=&before=, /api/stations
       GET  /api/messages?peer=&limit=&before=, /api/conversations
       POST /api/messages {to, text}         -> queued outgoing message
       POST /api/messages/read {peer}        -> mark a conversation read
WS:    /ws  -> {"type": ..., "data": {...}} where type is
       status | packet | station
       message  a new message row (in or out)
       ack      an outgoing message changed (tries, or acked/rejected/failed)
       read     {peer, unread}: a conversation was marked read
Message events carry the full row; clients upsert by id.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .messaging import MessageError
from .service import Core

WEB_DIR = Path(__file__).parent / "web"


class SendMessage(BaseModel):
    to: str
    text: str


class MarkRead(BaseModel):
    peer: str


def create_app(core: Core, run_core: bool = True) -> FastAPI:
    """Build the app. run_core=False leaves starting the radio link to the caller (tests)."""

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
        return core.config.model_dump()

    @app.get("/api/messages")
    def messages(
        peer: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        before: int | None = None,
    ):
        return core.store.list_messages(peer.upper() if peer else None, limit, before)

    @app.post("/api/messages", status_code=201)
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

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
