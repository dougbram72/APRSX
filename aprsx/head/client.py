"""Client for the aprsx-core API: REST for snapshots and actions, /ws for live events.

Runs on Qt's event loop (QNetworkAccessManager + QWebSocket), so the head unit
needs nothing but PySide6. All state lives in the core; this only mirrors it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Property, QByteArray, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from .grouping import group_messages
from .models import RowModel, card_model, message_model, station_model

log = logging.getLogger(__name__)

RECONNECT_MS = 3000
MESSAGE_HISTORY = 200

Callback = Callable[[int | None, Any], None]


class CoreClient(QObject):
    connectedChanged = Signal()
    statusChanged = Signal()
    configChanged = Signal()
    unreadChanged = Signal()
    peersChanged = Signal()
    symbolsChanged = Signal()
    toast = Signal(str)
    messageArrived = Signal(int)  # id of a new incoming message
    rxActivity = Signal()
    txActivity = Signal()

    def __init__(self, base_url: str = "http://127.0.0.1:8080", parent=None) -> None:
        super().__init__(parent)
        self.base_url = base_url.rstrip("/")
        self._connected = False
        self._status: dict = {}
        self._config: dict = {}
        self._unread = 0
        self._raw = message_model(self)     # rows as the core has them
        self._messages = card_model(self)   # what the carousel shows
        self._stations = station_model(self)
        self._symbols: dict[str, str] = {}  # station -> symbol table + code
        self._nam = QNetworkAccessManager(self)
        self._ws = QWebSocket(parent=self)
        self._ws.connected.connect(self._on_connected)
        self._ws.disconnected.connect(self._on_disconnected)
        self._ws.textMessageReceived.connect(self._on_text)
        self._reconnect = QTimer(self, singleShot=True, interval=RECONNECT_MS)
        self._reconnect.timeout.connect(self._open)

    # --- properties for QML ------------------------------------------------

    @Property(bool, notify=connectedChanged)
    def connected(self) -> bool:
        return self._connected

    @Property("QVariantMap", notify=statusChanged)
    def status(self) -> dict:
        return self._status

    @Property("QVariantMap", notify=configChanged)
    def config(self) -> dict:
        return self._config

    @Property(int, notify=unreadChanged)
    def unread(self) -> int:
        return self._unread

    @Property(QObject, constant=True)
    def messages(self) -> RowModel:
        """Carousel cards: messages with multi-part replies joined."""
        return self._messages

    @Property(QObject, constant=True)
    def stations(self) -> RowModel:
        return self._stations

    @Property("QVariantMap", notify=symbolsChanged)
    def symbols(self) -> dict[str, str]:
        """Station name -> two characters, symbol table then symbol code."""
        return self._symbols

    @Property("QVariantList", notify=peersChanged)
    def quickPeers(self) -> list[str]:
        """Recipients for Quick Msg: favorites, then recent conversation peers."""
        peers = list(self._config.get("favorites", []))
        for m in self._raw.rows():
            if m["peer"] not in peers:
                peers.append(m["peer"])
        return peers

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._open()

    def stop(self) -> None:
        self._reconnect.stop()
        self._ws.close()

    def _open(self) -> None:
        ws_url = QUrl(self.base_url + "/ws")
        ws_url.setScheme("wss" if ws_url.scheme() == "https" else "ws")
        self._ws.open(ws_url)

    def _on_connected(self) -> None:
        self._set_connected(True)
        self.refresh()

    def _on_disconnected(self) -> None:
        self._set_connected(False)
        self._reconnect.start()

    def _set_connected(self, value: bool) -> None:
        if value != self._connected:
            self._connected = value
            self.connectedChanged.emit()

    # --- REST --------------------------------------------------------------

    @Slot()
    def refresh(self) -> None:
        """Reload everything; runs after every (re)connect so nothing is missed."""
        self._request("GET", "/api/status", callback=lambda s, d: s == 200 and self._set_status(d))
        self._request("GET", "/api/config", callback=lambda s, d: s == 200 and self._set_config(d))
        self._request("GET", f"/api/messages?limit={MESSAGE_HISTORY}",
                      callback=lambda s, d: s == 200 and self._reset_messages(d))
        self._request("GET", "/api/stations",
                      callback=lambda s, d: s == 200 and self._reset_stations(d))

    @Slot(str, str)
    def sendMessage(self, to: str, text: str) -> None:
        to, text = to.strip().upper(), text.strip()

        def done(status: int | None, data: Any) -> None:
            if status == 201:
                self._upsert_message(data)
                self.toast.emit(f"Sending to {to}")
            elif status is None:
                self.toast.emit("Core not reachable")
            else:
                detail = data.get("detail") if isinstance(data, dict) else None
                self.toast.emit(f"Not sent: {detail or status}")

        self._request("POST", "/api/messages", {"to": to, "text": text}, done)

    @Slot()
    def beacon(self) -> None:
        def done(status: int | None, data: Any) -> None:
            if status == 200:
                self.toast.emit("Beacon sent")
            elif status is None:
                self.toast.emit("Core not reachable")
            else:
                detail = data.get("detail") if isinstance(data, dict) else None
                self.toast.emit(f"No beacon: {detail or status}")

        self._request("POST", "/api/beacon", {}, done)

    @Slot(str)
    def markRead(self, peer: str) -> None:
        self._mark_read_locally(peer)
        self._request("POST", "/api/messages/read", {"peer": peer},
                      lambda s, d: s == 200 and self._set_unread(d["unread"]))

    def _request(self, method: str, path: str, body: Any = None,
                 callback: Callback | None = None) -> None:
        req = QNetworkRequest(QUrl(self.base_url + path))
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        if method == "GET":
            reply = self._nam.get(req)
        else:
            reply = self._nam.post(req, QByteArray(json.dumps(body).encode()))
        reply.finished.connect(lambda: self._finished(reply, callback))

    def _finished(self, reply: QNetworkReply, callback: Callback | None) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll().data())
        reply.deleteLater()
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = None
        if status is None:
            log.warning("request failed: %s", reply.errorString())
        if callback is not None:
            callback(status, data)

    # --- live events -------------------------------------------------------

    def _on_text(self, text: str) -> None:
        try:
            event = json.loads(text)
        except ValueError:
            return
        self.handle_event(event.get("type"), event.get("data"))

    def handle_event(self, type: str, data: Any) -> None:
        if type == "status":
            self._set_status(data)
        elif type == "packet":
            (self.txActivity if data.get("direction") == "tx" else self.rxActivity).emit()
        elif type == "station":
            self._stations.upsert(data)
            self._note_symbol(data)
        elif type in ("message", "ack"):
            self._upsert_message(data)
            if type == "message" and data["direction"] == "in" and not data["read"]:
                self._set_unread(self._unread + 1)
                self.messageArrived.emit(data["id"])
        elif type == "read":
            self._mark_read_locally(data["peer"])
            self._set_unread(data["unread"])

    # --- stations ----------------------------------------------------------

    def _reset_stations(self, rows: list[dict]) -> None:
        self._stations.reset(rows)
        self._symbols = {s["name"]: s["symbol_table"] + s["symbol"]
                         for s in rows if s.get("symbol_table") and s.get("symbol")}
        self.symbolsChanged.emit()

    def _note_symbol(self, station: dict) -> None:
        sym = (station.get("symbol_table") or "") + (station.get("symbol") or "")
        if len(sym) == 2 and self._symbols.get(station["name"]) != sym:
            self._symbols = {**self._symbols, station["name"]: sym}
            self.symbolsChanged.emit()

    # --- messages ----------------------------------------------------------

    def _reset_messages(self, rows: list[dict]) -> None:
        self._raw.reset(rows)
        self._regroup()

    def _upsert_message(self, row: dict) -> None:
        self._raw.upsert(row)
        self._regroup()

    def _mark_read_locally(self, peer: str) -> None:
        self._raw.update_where(lambda m: m["peer"] == peer and m["direction"] == "in", read=1)
        self._regroup()

    def _regroup(self) -> None:
        self._messages.sync(group_messages(self._raw.rows()))
        self.peersChanged.emit()

    def _set_status(self, status: dict) -> None:
        self._status = status
        self.statusChanged.emit()
        self._set_unread(status.get("unread", 0))

    def _set_config(self, config: dict) -> None:
        self._config = config
        self.configChanged.emit()
        self.peersChanged.emit()

    def _set_unread(self, n: int) -> None:
        if n != self._unread:
            self._unread = n
            self.unreadChanged.emit()
