import pytest
from fastapi.testclient import TestClient

from aprsx.core import auth, direwolf
from aprsx.core.api import create_app
from aprsx.core.config import Config
from aprsx.core.direwolf import DirewolfManager
from aprsx.core.service import Core
from aprsx.core.store import Store


class FakeDirewolf(DirewolfManager):
    def __init__(self, path, fail=False):
        super().__init__(path, "test.service")
        self.restarts = 0
        self.fail = fail

    async def restart(self):
        if self.fail:
            raise RuntimeError("Unit test.service not found.")
        self.restarts += 1


@pytest.fixture
def dw(tmp_path):
    return FakeDirewolf(tmp_path / "dw" / "direwolf.conf")


@pytest.fixture
def core(tmp_path, dw):
    store = Store(tmp_path / "a.db")
    store.save_config(Config(callsign="KF0KBP", ssid=7))
    return Core(store, direwolf=dw)


@pytest.fixture
def client(core, tmp_path):
    with TestClient(create_app(core, run_core=False, tiles_dir=tmp_path / "tiles")) as c:
        yield c


def settings(client, **changes):
    cfg = client.get("/api/config").json()
    cfg.update(changes)
    return cfg


# --- config API ---------------------------------------------------------------


def test_get_config_hides_password_hash(client):
    cfg = client.get("/api/config").json()
    assert "admin_password_hash" not in cfg
    assert cfg["has_password"] is False


def test_put_config_saves_and_applies(core, client):
    r = client.put("/api/config", json=settings(client, callsign="k1abc", ssid=9, units="metric",
                                                path=["wide1-1"]))
    assert r.status_code == 200
    assert r.json()["config"]["callsign"] == "K1ABC"
    assert core.config.station == "K1ABC-9"
    assert core.config.path == ["WIDE1-1"]
    assert core.store.load_config().units == "metric"
    assert client.get("/api/status").json()["station"] == "K1ABC-9"


def test_put_config_is_a_merge(core, client):
    assert client.put("/api/config", json={}).status_code == 200
    assert core.config.station == "KF0KBP-7"  # nothing reset to defaults
    client.put("/api/config", json={"aprsis": {"filter": "r/39.8/-95.6/100"}, "units": "metric"})
    assert core.config.aprsis.filter == "r/39.8/-95.6/100"
    assert core.config.aprsis.server == "rotate.aprs2.net"  # sibling kept
    assert core.config.units == "metric"


def test_loopback_detection():
    assert auth.is_loopback("127.0.0.1") and auth.is_loopback("127.0.0.2")
    assert auth.is_loopback("::1") and auth.is_loopback("localhost")
    assert not auth.is_loopback("192.168.1.5") and not auth.is_loopback("testclient")
    assert not auth.is_loopback(None)


def test_put_config_validation_errors_name_the_field(core, client):
    r = client.put("/api/config", json=settings(client, callsign="TOOLONGCALL", ptt="x\nKISSPORT 1",
                                                aprsis={"port": 0}))
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["detail"]}
    assert {"callsign", "ptt", "aprsis.port"} <= fields
    assert core.config.callsign == "KF0KBP"  # nothing saved


def test_kiss_address_change_reconnects(core, client):
    client.put("/api/config", json=settings(client, direwolf_host="10.0.0.5", direwolf_kiss_port=8101))
    assert (core.kiss.host, core.kiss.port) == ("10.0.0.5", 8101)


def test_devices_endpoint(client):
    d = client.get("/api/system/devices").json()
    assert set(d) == {"audio", "serial"}


# --- Direwolf -----------------------------------------------------------------


def test_render_direwolf_conf():
    conf = direwolf.render(Config(callsign="KF0KBP", ssid=7, ptt="/dev/ttyUSB0 RTS"))
    assert "MYCALL KF0KBP-7\n" in conf
    assert "ADEVICE plughw:CARD=Device,DEV=0\n" in conf
    assert "PTT /dev/ttyUSB0 RTS\n" in conf
    assert "KISSPORT 8001\n" in conf
    assert "DIGIPEAT" not in conf
    assert "PTT" not in direwolf.render(Config())
    digi = direwolf.render(Config(digipeater=True))
    assert "DIGIPEAT 0 0 ^WIDE1-1$ ^WIDE1-1$\n" in digi


def test_unmanaged_direwolf_is_left_alone(core, client, dw):
    r = client.put("/api/config", json=settings(client, digipeater=True))
    assert r.json()["direwolf"] is None
    assert not dw.conf_path.exists() and dw.restarts == 0


def test_managed_direwolf_written_and_restarted_only_when_needed(core, client, dw):
    r = client.put("/api/config", json=settings(client, direwolf_managed=True))
    assert r.json()["direwolf"] == {"written": True, "restarted": True, "error": None}
    assert "MYCALL KF0KBP-7" in dw.conf_path.read_text()

    client.put("/api/config", json=settings(client, beacon_comment="no restart needed"))
    assert dw.restarts == 1
    client.put("/api/config", json=settings(client, digipeater=True))
    assert dw.restarts == 2
    assert "DIGIPEAT" in dw.conf_path.read_text()
    assert client.get("/api/direwolf.conf").text == dw.conf_path.read_text()


def test_direwolf_restart_failure_is_reported(tmp_path, client, core):
    core.direwolf = FakeDirewolf(tmp_path / "x.conf", fail=True)
    r = client.put("/api/config", json=settings(client, direwolf_managed=True))
    assert r.status_code == 200  # settings still saved
    assert "not found" in r.json()["direwolf"]["error"]
    assert client.get("/api/status").json()["direwolf_error"]


# --- password -----------------------------------------------------------------


def test_password_hashing():
    h = auth.hash_password("s3cret")
    assert auth.check_password("s3cret", h)
    assert not auth.check_password("wrong", h)
    assert not auth.check_password("s3cret", "garbage")


def test_password_protects_settings_and_sending(core, client):
    assert client.get("/api/session").json() == {"password_set": False, "authenticated": True}
    r = client.put("/api/config", json=settings(client, admin_password="s3cret"))
    assert r.status_code == 200 and r.json()["config"]["has_password"]
    assert client.get("/api/session").json()["authenticated"]  # setter stays logged in

    client.post("/api/logout")
    assert client.get("/api/session").json() == {"password_set": True, "authenticated": False}
    assert client.put("/api/config", json=settings(client, units="metric")).status_code == 401
    assert client.post("/api/messages", json={"to": "K1ABC", "text": "hi"}).status_code == 401
    assert client.get("/api/config").status_code == 200  # reading is open

    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    assert client.put("/api/config", json=settings(client, units="metric")).status_code == 200
    assert core.store.load_config().admin_password_hash  # kept when not in the request

    client.put("/api/config", json=settings(client, admin_password=""))
    assert client.get("/api/session").json() == {"password_set": False, "authenticated": True}


def test_loopback_is_trusted(core, tmp_path):
    core.config = Config(callsign="KF0KBP", admin_password_hash=auth.hash_password("x"))
    app = create_app(core, run_core=False, tiles_dir=tmp_path)
    with TestClient(app, client=("127.0.0.1", 5555)) as local:
        assert local.get("/api/session").json()["authenticated"]
        assert local.post("/api/messages", json={"to": "K1ABC", "text": "hi"}).status_code == 201
