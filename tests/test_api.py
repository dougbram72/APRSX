import pytest
from fastapi.testclient import TestClient

from aprsx.core import ax25
from aprsx.core.api import create_app
from aprsx.core.config import Config
from aprsx.core.service import Core
from aprsx.core.store import Store


@pytest.fixture
def core(tmp_path):
    store = Store(tmp_path / "a.db")
    store.save_config(Config(callsign="KF0KBP", ssid=7, fixed_lat=39.0, fixed_lon=-95.5))
    return Core(store)


@pytest.fixture
def client(core):
    with TestClient(create_app(core, run_core=False)) as c:
        yield c


def rx(core, line):
    core.handle_frame(0, ax25.encode(ax25.Frame.from_tnc2(line)))


def test_status(client):
    s = client.get("/api/status").json()
    assert s["station"] == "KF0KBP-7"
    assert s["kiss_connected"] is False
    assert s["units"] == "imperial"


def test_received_packet_shows_in_rest(core, client):
    rx(core, "KF0KBP-1>APGRWO,WIDE1-1:!3946.  N/09533.  W-")
    rx(core, "K1ABC>APRS:!garbage")
    core.handle_frame(0, b"\x00junk")  # undecodable AX.25 is dropped

    packets = client.get("/api/packets").json()
    assert [p["raw"] for p in packets] == [
        "K1ABC>APRS:!garbage",
        "KF0KBP-1>APGRWO,WIDE1-1:!3946.  N/09533.  W-",
    ]
    assert packets[0]["format"] is None
    assert packets[1]["format"] == "uncompressed"
    assert client.get("/api/packets", params={"limit": 1, "before": packets[0]["id"]}).json()[0][
        "raw"
    ].startswith("KF0KBP-1")

    stations = {s["name"]: s for s in client.get("/api/stations").json()}
    home = stations["KF0KBP-1"]
    assert home["heard_direct"] == 1
    assert home["distance_km"] == pytest.approx(86.3, abs=1)
    assert home["bearing"] == pytest.approx(357, abs=2)  # slightly west of due north
    assert stations["K1ABC"]["distance_km"] is None
    assert core.status()["rx_count"] == 2


def test_websocket_pushes_events(core, client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status"
        # TestClient runs the app on its own loop/thread; inject through it.
        client.portal.call(rx, core, "K1ABC>APRS:>hello")
        packet = ws.receive_json()
        assert packet["type"] == "packet"
        assert packet["data"]["raw"] == "K1ABC>APRS:>hello"
        station = ws.receive_json()
        assert station["type"] == "station"
        assert station["data"]["name"] == "K1ABC"


def test_web_page_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "APRS-X" in r.text
    assert client.get("/app.js").status_code == 200
