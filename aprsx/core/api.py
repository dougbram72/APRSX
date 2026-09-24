"""HTTP/WebSocket API shared by the web UI and the touch-screen head unit.

REST:  GET /api/status, /api/packets?limit=&before=, /api/stations
WS:    /ws  -> {"type": "status"|"packet"|"station", "data": {...}}
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .service import Core

WEB_DIR = Path(__file__).parent / "web"


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
