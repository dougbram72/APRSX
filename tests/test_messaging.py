import pytest
from fastapi.testclient import TestClient

from aprsx.core import ax25
from aprsx.core.api import create_app
from aprsx.core.config import Config
from aprsx.core.messaging import MAX_TRIES, RETRY_DELAYS_S, MessageError, msgno_from_seq
from aprsx.core.service import Core
from aprsx.core.store import Store

ME = "KF0KBP-7"


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def core(tmp_path, clock):
    store = Store(tmp_path / "a.db")
    store.save_config(Config(callsign="KF0KBP", ssid=7, path=["WIDE1-1"]))
    return Core(store, clock=clock)


@pytest.fixture
def sent(core):
    """TNC2 text of every packet the core transmits (stands in for a connected TNC)."""
    out: list[str] = []
    core.kiss.write = lambda frame, port=0: out.append(ax25.decode(frame).to_tnc2())
    return out


def rx(core, line):
    core.handle_frame(0, ax25.encode(ax25.Frame.from_tnc2(line)))


def events(core):
    got = []
    core.bus.publish = lambda type, data: got.append((type, data))
    return got


# --- transmit path ------------------------------------------------------------


def test_transmit_logs_tx_packet(core, sent):
    assert core.transmit(">hello")
    assert sent == [f"{ME}>APZAPX,WIDE1-1:>hello"]
    [pkt] = core.store.recent_packets()
    assert pkt["direction"] == "tx"
    assert pkt["format"] == "status"
    assert core.status()["tx_count"] == 1
    assert core.status()["rx_count"] == 0


def test_transmit_refuses_without_tnc_or_callsign(tmp_path, core):
    assert not core.transmit(">x")  # KISS not connected
    nocall = Core(Store(tmp_path / "b.db"))
    nocall.kiss.write = lambda *a: pytest.fail("transmitted as N0CALL")
    assert not nocall.transmit(">x")
    assert core.store.recent_packets() == []


# --- outgoing -----------------------------------------------------------------


def test_msgno_is_two_chars():
    assert msgno_from_seq(1) == "01"
    assert msgno_from_seq(36) == "10"
    assert msgno_from_seq(36 * 36 - 1) == "ZZ"
    assert msgno_from_seq(36 * 36) == "00"


def test_send_goes_out_immediately(core, sent):
    msg = core.messenger.send("k1abc", "hello")
    assert sent == [f"{ME}>APZAPX,WIDE1-1::K1ABC    :hello{{01}}"]
    assert msg["state"] == "pending"
    assert msg["tries"] == 1
    assert core.messenger.send("K1ABC", "again")["msgno"] == "02"


@pytest.mark.parametrize("to,text", [("", "hi"), ("K1ABC", " "), ("TOOLONGCALL", "hi"),
                                     ("K1ABC", "x" * 68), ("K1ABC", "a{b"), ("K1ABC", "°")])
def test_send_validation(core, sent, to, text):
    with pytest.raises(MessageError):
        core.messenger.send(to, text)
    assert sent == []
    assert core.store.list_messages() == []


def test_retries_with_backoff_then_fail(core, sent, clock):
    msg = core.messenger.send("K1ABC", "hello")
    start = clock.t
    core.messenger.tick()
    assert len(sent) == 1  # nothing due yet

    elapsed = 0
    for i, delay in enumerate(RETRY_DELAYS_S[:-1]):
        clock.t = start + elapsed + delay - 1
        core.messenger.tick()
        assert len(sent) == i + 1
        clock.t = start + elapsed + delay
        core.messenger.tick()
        assert len(sent) == i + 2
        elapsed += delay

    assert len(sent) == MAX_TRIES
    assert len(set(sent)) == 1  # every retry is the same packet
    clock.t = start + sum(RETRY_DELAYS_S)
    core.messenger.tick()
    assert len(sent) == MAX_TRIES
    assert core.store.get_message(msg["id"])["state"] == "failed"


def test_no_tnc_does_not_use_up_tries(core, clock):
    msg = core.messenger.send("K1ABC", "hello")
    assert msg["tries"] == 0
    assert msg["state"] == "pending"
    sent = []
    core.kiss.write = lambda frame, port=0: sent.append(frame)
    clock.t += 10
    core.messenger.tick()
    assert len(sent) == 1
    assert core.store.get_message(msg["id"])["tries"] == 1


def test_ack_stops_retries(core, sent, clock):
    got = events(core)
    msg = core.messenger.send("K1ABC", "hello")
    rx(core, f"K1ABC>APRS,WIDE1-1*::{ME:9}:ack01")
    acked = core.store.get_message(msg["id"])
    assert acked["state"] == "acked"
    assert acked["acked_ts"] == clock.t
    assert ("ack", acked) in got
    clock.t += 1000
    core.messenger.tick()
    assert len(sent) == 1


def test_ack_from_wrong_station_or_for_someone_else_is_ignored(core, sent):
    msg = core.messenger.send("K1ABC", "hello")
    rx(core, f"W2XYZ>APRS::{ME:9}:ack01")
    rx(core, "K1ABC>APRS::N0CALL-1 :ack01")
    assert core.store.get_message(msg["id"])["state"] == "pending"


def test_rej(core, sent):
    msg = core.messenger.send("K1ABC", "hello")
    rx(core, f"K1ABC>APRS::{ME:9}:rej01")
    assert core.store.get_message(msg["id"])["state"] == "rejected"


def test_legacy_ack_with_trailing_brace(core, sent):
    msg = core.messenger.send("K1ABC", "hello")
    rx(core, f"K1ABC>APRS::{ME:9}:ack01}}")
    assert core.store.get_message(msg["id"])["state"] == "acked"


# --- incoming -----------------------------------------------------------------


def test_incoming_is_stored_and_acked(core, sent):
    got = events(core)
    rx(core, f"K1ABC>APRS,WIDE1-1*::{ME:9}:hi there{{42")
    assert sent == [f"{ME}>APZAPX,WIDE1-1::K1ABC    :ack42"]
    [msg] = core.store.list_messages()
    assert (msg["direction"], msg["peer"], msg["text"], msg["msgno"], msg["read"]) == (
        "in", "K1ABC", "hi there", "42", 0)
    assert ("message", msg) in got
    assert core.status()["unread"] == 1


def test_messages_for_others_and_our_own_echo_are_ignored(core, sent):
    rx(core, "K1ABC>APRS::W2XYZ    :not for us{1")
    rx(core, f"{ME}>APZAPX,DIGI*::K1ABC    :hello{{01}}")
    rx(core, "K1ABC>APRS::BLN1     :bulletin")
    assert core.store.list_messages() == []
    assert sent == []


def test_unnumbered_message_is_stored_but_not_acked(core, sent):
    rx(core, f"K1ABC>APRS::{ME:9}:no number")
    assert sent == []
    assert core.store.list_messages()[0]["msgno"] is None


def test_duplicates_are_stored_once_and_acked_again_after_holdoff(core, sent, clock):
    line = f"K1ABC>APRS,WIDE1-1*::{ME:9}:hi{{42"
    rx(core, line)
    clock.t += 2
    rx(core, line.replace("WIDE1-1*", "WIDE1*,WIDE2-1*"))  # digipeated copy
    assert len(sent) == 1  # one ack for both copies
    clock.t += 30
    rx(core, line)  # sender retried: our ack was lost
    assert len(sent) == 2
    assert len(core.store.list_messages()) == 1

    rx(core, f"K1ABC>APRS::{ME:9}:different{{43")
    assert len(core.store.list_messages()) == 2


def test_duplicate_window_expires(core, sent, clock):
    rx(core, f"K1ABC>APRS::{ME:9}:ok")
    clock.t += 10
    rx(core, f"K1ABC>APRS::{ME:9}:ok")
    assert len(core.store.list_messages()) == 1
    clock.t += 120
    rx(core, f"K1ABC>APRS::{ME:9}:ok")  # user really sent "ok" again
    assert len(core.store.list_messages()) == 2


def test_third_party_ack_and_reply_from_igate(core, sent):
    """IS->RF traffic arrives wrapped by the iGate (the real packets from WXBOT)."""
    msg = core.messenger.send("WXBOT", "66534")
    rx(core, "KF0KBP-1>APGWLF:}WXBOT>APRS,TCPIP,KF0KBP-1*::KF0KBP-7 :ack01}")
    assert core.store.get_message(msg["id"])["state"] == "acked"

    rx(core, "KF0KBP-1>APGWLF:}WXBOT>APRS,TCPIP,KF0KBP-1*::KF0KBP-7 :Sabetha KS. Today{AB}")
    [reply] = [m for m in core.store.list_messages() if m["direction"] == "in"]
    assert (reply["peer"], reply["text"], reply["msgno"]) == ("WXBOT", "Sabetha KS. Today", "AB")
    assert sent[-1] == f"{ME}>APZAPX,WIDE1-1::WXBOT    :ackAB"
    # WXBOT used the reply-ack form, so our next message carries its number.
    core.messenger.send("WXBOT", "again")
    assert sent[-1].endswith(":WXBOT    :again{02}AB")


def test_third_party_for_someone_else_is_ignored(core, sent):
    rx(core, "KF0KBP-1>APGWLF:}WXBOT>APRS,TCPIP,KF0KBP-1*::W2XYZ    :not ours{AB}")
    assert core.store.list_messages() == []
    assert sent == []


# --- reply-acks ---------------------------------------------------------------


def test_reply_ack_acks_our_message(core, sent):
    msg = core.messenger.send("K1ABC", "hello")
    rx(core, f"K1ABC>APRS::{ME:9}:hi back{{AB}}01")
    assert core.store.get_message(msg["id"])["state"] == "acked"
    assert sent[-1] == f"{ME}>APZAPX,WIDE1-1::K1ABC    :ackAB"  # still ack theirs


def test_we_send_reply_acks_to_capable_peers(core, sent):
    rx(core, f"K1ABC>APRS::{ME:9}:hello{{AB}}")
    rx(core, f"W2XYZ>APRS::{ME:9}:hello{{12345")  # legacy peer
    core.messenger.send("K1ABC", "reply")
    core.messenger.send("W2XYZ", "reply")
    assert sent[-2].endswith(":K1ABC    :reply{01}AB")
    assert sent[-1].endswith(":W2XYZ    :reply{02}")


# --- API ----------------------------------------------------------------------


@pytest.fixture
def client(core):
    with TestClient(create_app(core, run_core=False)) as c:
        yield c


def test_api_send_list_and_read(core, sent, client):
    r = client.post("/api/messages", json={"to": "k1abc", "text": "hello"})
    assert r.status_code == 201
    assert r.json()["peer"] == "K1ABC"
    assert client.post("/api/messages", json={"to": "K1ABC", "text": ""}).status_code == 400

    rx(core, f"K1ABC>APRS::{ME:9}:hi{{7")
    rx(core, f"W2XYZ>APRS::{ME:9}:yo{{8")
    convs = client.get("/api/conversations").json()
    assert [(c["peer"], c["unread"]) for c in convs] == [("W2XYZ", 1), ("K1ABC", 1)]
    assert [m["text"] for m in client.get("/api/messages", params={"peer": "k1abc"}).json()] == [
        "hi", "hello"]

    assert client.post("/api/messages/read", json={"peer": "k1abc"}).json() == {
        "peer": "K1ABC", "unread": 1}
    assert client.get("/api/status").json()["unread"] == 1


def test_api_websocket_message_events(core, sent, client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status"
        client.post("/api/messages", json={"to": "K1ABC", "text": "hello"})
        types = [ws.receive_json()["type"] for _ in range(3)]
        assert types == ["message", "packet", "ack"]
        client.portal.call(rx, core, f"K1ABC>APRS::{ME:9}:ack01")
        types = [ws.receive_json() for _ in range(3)]
        assert [e["type"] for e in types] == ["packet", "station", "ack"]
        assert types[2]["data"]["state"] == "acked"
