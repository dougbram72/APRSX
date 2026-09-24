"""Runs the app under real uvicorn (TestClient bypasses the server, so it can't
catch a missing WebSocket implementation or other server-level problems)."""

import asyncio
import json
import socket

import uvicorn
import websockets

from aprsx.core import ax25
from aprsx.core.api import create_app
from aprsx.core.service import Core
from aprsx.core.store import Store


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_websocket_over_real_server(tmp_path):
    core = Core(Store(tmp_path / "a.db"))
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(core, run_core=False), host="127.0.0.1", port=port,
                       log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert hello["type"] == "status"
            core.handle_frame(0, ax25.encode(ax25.Frame.from_tnc2("K1ABC>APRS:>hi")))
            event = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert event["type"] == "packet"
            assert event["data"]["raw"] == "K1ABC>APRS:>hi"
    finally:
        server.should_exit = True
        await task
