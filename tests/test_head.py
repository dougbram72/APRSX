"""Head unit: list models, the core client (against a real server) and QML loading."""

import asyncio
import os
import socket
import threading
import time

import pytest

pytest.importorskip("PySide6.QtQuick")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

import uvicorn  # noqa: E402
from PySide6.QtCore import QEventLoop  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from aprsx.core import ax25  # noqa: E402
from aprsx.core.api import create_app  # noqa: E402
from aprsx.core.config import Config  # noqa: E402
from aprsx.core.service import Core  # noqa: E402
from aprsx.core.store import Store  # noqa: E402
from aprsx.head.client import CoreClient  # noqa: E402
from aprsx.head.models import message_model, station_model  # noqa: E402

ME = "KF0KBP-7"


@pytest.fixture(scope="module")
def qapp():
    return QGuiApplication.instance() or QGuiApplication([])


def wait_until(cond, timeout=5.0) -> bool:
    app = QGuiApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if cond():
            return True
        time.sleep(0.01)
    return False


def roles(model, row):
    names = {bytes(v).decode(): k for k, v in model.roleNames().items()}
    return {n: model.data(model.index(row), r) for n, r in names.items()}


# --- models -------------------------------------------------------------------


def test_message_model_newest_first_and_update_in_place(qapp):
    m = message_model()
    m.reset([{"id": 1, "peer": "A"}, {"id": 3, "peer": "C"}, {"id": 2, "peer": "B"}])
    assert [r["id"] for r in m.rows()] == [3, 2, 1]
    assert m.upsert({"id": 4, "peer": "D"}) == 0
    assert m.upsert({"id": 2, "peer": "B", "state": "acked"}) == 2
    assert roles(m, 2)["state"] == "acked"
    assert m.count == 4
    assert m.get(0)["peer"] == "D"
    assert m.get(99) == {}


def test_station_model_moves_heard_station_to_top(qapp):
    s = station_model()
    s.reset([{"name": "A", "last_heard": 1}, {"name": "B", "last_heard": 2},
              {"name": "C", "last_heard": 3}])
    moved = []
    s.rowsMoved.connect(lambda *a: moved.append((a[1], a[4])))
    assert s.upsert({"name": "A", "last_heard": 10}) == 0
    assert [r["name"] for r in s.rows()] == ["A", "C", "B"]
    assert moved == [(2, 0)]
    assert s.upsert({"name": "B", "last_heard": 11}) == 0
    assert [r["name"] for r in s.rows()] == ["B", "A", "C"]


def test_update_where(qapp):
    m = message_model()
    m.reset([{"id": 1, "peer": "A", "direction": "in", "read": 0},
             {"id": 2, "peer": "B", "direction": "in", "read": 0}])
    m.update_where(lambda r: r["peer"] == "A", read=1)
    assert [r["read"] for r in m.rows()] == [0, 1]


# --- client event handling ----------------------------------------------------


def test_client_events_without_network(qapp):
    c = CoreClient("http://127.0.0.1:9")
    arrived, rx, tx = [], [], []
    c.messageArrived.connect(arrived.append)
    c.rxActivity.connect(lambda: rx.append(1))
    c.txActivity.connect(lambda: tx.append(1))

    c.handle_event("status", {"station": ME, "unread": 2})
    assert c.unread == 2
    msg = {"id": 5, "direction": "in", "peer": "K1ABC", "text": "hi", "read": 0, "ts": 1}
    c.handle_event("message", msg)
    assert arrived == [5] and c.unread == 3
    c.handle_event("ack", {**msg, "id": 6, "direction": "out", "state": "acked"})
    assert arrived == [5]
    assert c.quickPeers == ["K1ABC"]
    c.handle_event("read", {"peer": "K1ABC", "unread": 0})
    assert c.unread == 0 and c.messages.get(1)["read"] == 1
    c.handle_event("packet", {"direction": "rx"})
    c.handle_event("packet", {"direction": "tx"})
    assert (rx, tx) == ([1], [1])
    c.handle_event("station", {"name": "K1ABC", "last_heard": 1})
    assert c.stations.count == 1


# --- client against a real core -----------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served_core(tmp_path):
    """A real aprsx-core (no TNC) served by uvicorn on its own thread and loop."""
    store = Store(tmp_path / "a.db")
    store.save_config(Config(callsign="KF0KBP", ssid=7, favorites=["WXBOT"]))
    core = Core(store)
    port = free_port()
    loop = asyncio.new_event_loop()
    server = uvicorn.Server(uvicorn.Config(create_app(core, run_core=False), host="127.0.0.1",
                                           port=port, log_level="warning"))
    thread = threading.Thread(target=loop.run_until_complete, args=(server.serve(),))
    thread.start()
    assert wait_until(lambda: server.started)
    yield core, loop, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def test_client_against_real_core(qapp, served_core):
    core, loop, url = served_core
    c = CoreClient(url)
    toasts, arrived = [], []
    c.toast.connect(toasts.append)
    c.messageArrived.connect(arrived.append)
    c.start()
    assert wait_until(lambda: c.connected and c.status.get("station") == ME)
    assert wait_until(lambda: c.config.get("favorites") == ["WXBOT"])

    line = f"K1ABC>APRS,WIDE1-1*::{ME:9}:hello head{{7"
    loop.call_soon_threadsafe(core.handle_frame, 0, ax25.encode(ax25.Frame.from_tnc2(line)))
    assert wait_until(lambda: arrived)
    assert c.messages.get(0)["text"] == "hello head"
    assert c.unread == 1
    assert wait_until(lambda: c.stations.count == 1)

    c.markRead("K1ABC")
    assert wait_until(lambda: c.unread == 0)
    assert core.store.unread_count() == 0

    c.sendMessage("wxbot", "66534")
    assert wait_until(lambda: toasts)
    assert toasts[-1] == "Sending to WXBOT"
    assert wait_until(lambda: c.messages.get(0).get("peer") == "WXBOT")
    c.sendMessage("K1ABC", "bad{text")
    assert wait_until(lambda: len(toasts) == 2)
    assert toasts[-1].startswith("Not sent:")
    c.stop()


def test_client_reconnects(qapp, tmp_path):
    c = CoreClient(f"http://127.0.0.1:{free_port()}")
    c._reconnect.setInterval(50)
    c.start()
    assert wait_until(lambda: c._reconnect.isActive())  # failed, retry scheduled
    assert not c.connected
    c.stop()


# --- QML ----------------------------------------------------------------------


def test_qml_loads_without_warnings(qapp):
    from aprsx.head.__main__ import load

    warnings: list[str] = []
    engine, client = load(f"http://127.0.0.1:{free_port()}", keyboard=False, warnings=warnings)
    window = engine.rootObjects()[0]
    window.resize(800, 480)
    window.show()
    wait_until(lambda: False, timeout=0.5)  # let bindings and layout settle
    assert warnings == []
    window.close()


# --- multi-part replies -------------------------------------------------------

from aprsx.head.grouping import group_messages  # noqa: E402

PART1 = "3 Miles NE Powhattan KS. Today,Mostly Cloudy then Slight Chance Sho"


def msg(id, ts, text, peer="WXBOT", direction="in", read=0):
    return {"id": id, "ts": ts, "peer": peer, "direction": direction, "text": text, "read": read}


def test_group_joins_split_reply_mid_word():
    assert len(PART1) == 67
    [card] = group_messages([msg(8, 100, PART1), msg(9, 105, "wers 20% High 72")])
    assert card["text"] == PART1 + "wers 20% High 72"
    assert (card["id"], card["last_id"], card["parts"], card["ts"]) == (8, 9, 2, 100)


def test_group_joins_word_boundary_split_with_space():
    first = "Tonight: Partly cloudy with a chance of thunderstorms after"  # 59 chars
    [card] = group_messages([msg(1, 0, first), msg(2, 3, "midnight. Low 58.")])
    assert card["text"] == first + " midnight. Low 58."


def test_group_read_only_when_all_parts_read():
    cards = group_messages([msg(1, 0, PART1, read=1), msg(2, 3, "wers", read=0)])
    assert cards[0]["read"] == 0


@pytest.mark.parametrize("rows", [
    [msg(1, 0, "short reply"), msg(2, 3, "another")],                 # first not near full
    [msg(1, 0, PART1), msg(2, 61 + 0, "wers")],                       # too late
    [msg(1, 0, PART1), msg(2, 3, "wers", peer="K1ABC")],              # other station
    [msg(1, 0, PART1, direction="out"), msg(2, 3, "wers")],           # ours, not theirs
    [msg(1, 0, PART1), msg(2, 2, "ok?", direction="out"), msg(3, 4, "wers")],  # we replied between
])
def test_group_keeps_separate_messages_apart(rows):
    assert len(group_messages(rows)) == len(rows)


def test_group_other_station_between_parts_does_not_split():
    cards = group_messages([msg(1, 0, PART1), msg(2, 2, "hi", peer="K1ABC"),
                            msg(3, 5, "wers 20% High 72")])
    assert [(c["peer"], c["parts"]) for c in cards] == [("WXBOT", 2), ("K1ABC", 1)]


def test_client_joins_parts_live_without_moving_carousel(qapp):
    c = CoreClient("http://127.0.0.1:9")
    c.handle_event("message", msg(1, 0, "earlier", peer="K1ABC"))
    c.handle_event("message", msg(8, 100, PART1))
    removed = []
    c.messages.rowsRemoved.connect(lambda *a: removed.append(a))
    c.handle_event("message", msg(9, 105, "wers 20% High 72"))
    assert c.messages.count == 2
    assert c.messages.get(0)["text"].endswith("Showers 20% High 72")
    assert c.messages.get(0)["parts"] == 2
    assert removed == []  # the card was updated in place, not rebuilt
    assert c.unread == 3
    c.handle_event("read", {"peer": "WXBOT", "unread": 1})
    assert c.messages.get(0)["read"] == 1


def test_client_symbols_map(qapp):
    c = CoreClient("http://127.0.0.1:9")
    changed = []
    c.symbolsChanged.connect(lambda: changed.append(1))
    c._reset_stations([{"name": "KF0KBP-1", "last_heard": 1, "symbol_table": "/", "symbol": "-"},
                       {"name": "NOPOS", "last_heard": 2, "symbol_table": None, "symbol": None}])
    assert c.symbols == {"KF0KBP-1": "/-"}
    c.handle_event("station", {"name": "N0DIG-2", "last_heard": 3,
                               "symbol_table": "1", "symbol": "#"})
    assert c.symbols["N0DIG-2"] == "1#"
    c.handle_event("station", {"name": "N0DIG-2", "last_heard": 4,
                               "symbol_table": "1", "symbol": "#"})
    assert len(changed) == 2  # unchanged symbol doesn't re-notify
