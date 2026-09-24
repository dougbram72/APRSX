import anyio


def close_ws(client, ws) -> None:
    """Close a TestClient WebSocket and let the app's /ws handler finish.

    Starlette's TestClient sends the disconnect and then cancels the app at
    once on leaving ``websocket_connect()``; if the handler is still cleaning
    up, the cancellation escapes as CancelledError (flaky, ~50%). Real servers
    wait for the handler (covered by test_server.py).
    """
    ws.close()
    client.portal.call(anyio.sleep, 0.1)
