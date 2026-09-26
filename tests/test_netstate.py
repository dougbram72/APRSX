import asyncio

import pytest
from fastapi.testclient import TestClient

from aprsx.core.api import create_app
from aprsx.core.netstate import WifiSettings, WifiState, parse_devices, parse_scan, split_terse
from aprsx.core.service import Core
from aprsx.core.store import Store

DEV_CLIENT = """wlan0:wifi:connected:netplan-wlan0-Xena
lo:loopback:connected (externally):lo
p2p-dev-wlan0:wifi-p2p:disconnected:
eth0:ethernet:unavailable:
"""
DEV_HOTSPOT = "eth0:ethernet:unavailable:\nwlan0:wifi:connected:aprsx-hotspot\n"
DEV_OFF = "wlan0:wifi:disconnected:\n"
DEV_NO_WIFI = "eth0:ethernet:connected:Wired connection 1\n"


def fake_nmcli(dev: str, con: dict[str, str], ip: str = "192.168.50.232/24\n"):
    calls = []

    async def run(*args: str) -> str:
        calls.append(args)
        if args[-1] == "dev":
            return dev
        if args[-3:-1] == ("con", "show"):
            return con[args[-1]]
        return ip
    return run, calls


def test_split_terse_escapes():
    assert split_terse(r"wlan0:wifi:connected:My\:Net") == ["wlan0", "wifi", "connected", "My:Net"]


def test_parse_devices_first_wifi():
    assert parse_devices(DEV_CLIENT) == ("wlan0", "connected", "netplan-wlan0-Xena")
    assert parse_devices(DEV_NO_WIFI) is None


def test_client_mode():
    run, _ = fake_nmcli(DEV_CLIENT, {"netplan-wlan0-Xena": "Xena\ninfrastructure\n"})
    assert asyncio.run(WifiState(run=run).read()) == {
        "mode": "client", "ssid": "Xena", "ip": "192.168.50.232"}


def test_hotspot_mode():
    run, _ = fake_nmcli(DEV_HOTSPOT, {"aprsx-hotspot": "APRSX-KF0KBP\nap\n"}, "10.42.0.1/24\n")
    assert asyncio.run(WifiState(run=run).read()) == {
        "mode": "hotspot", "ssid": "APRSX-KF0KBP", "ip": "10.42.0.1"}


def test_off_and_no_wifi():
    run, calls = fake_nmcli(DEV_OFF, {})
    assert asyncio.run(WifiState(run=run).read()) == {"mode": "off", "ssid": None, "ip": None}
    assert len(calls) == 1
    run, _ = fake_nmcli(DEV_NO_WIFI, {})
    assert asyncio.run(WifiState(run=run).read()) is None


def test_on_change_only_when_changed():
    changes = []
    run, _ = fake_nmcli(DEV_OFF, {})
    w = WifiState(on_change=lambda: changes.append(w.state), run=run)

    async def go():
        await w.poll()
        await w.poll()
    asyncio.run(go())
    assert changes == [{"mode": "off", "ssid": None, "ip": None}]


def test_no_nmcli_stops_quietly():
    async def missing(*args):
        raise FileNotFoundError("nmcli")
    w = WifiState(run=missing, poll_s=0)
    asyncio.run(asyncio.wait_for(w.run_forever(), 1))
    assert w.state is None


def test_core_status_has_wifi(tmp_path):
    core = Core(Store(tmp_path / "t.db"))
    assert core.status()["wifi"] is None
    core.wifi.state = {"mode": "hotspot", "ssid": "APRSX-N0CALL", "ip": "10.42.0.1"}
    assert core.status()["wifi"]["mode"] == "hotspot"


# --- settings ---------------------------------------------------------------------

CON_SHOW = """netplan-wlan0-Xena:802-11-wireless:0:yes
lo:loopback:0:yes
aprsx-hotspot:802-11-wireless:0:no
Phone:802-11-wireless:10:no
netplan-eth0:802-3-ethernet:0:no
"""
PROFILES = {"netplan-wlan0-Xena": "Xena\ninfrastructure\n",
            "aprsx-hotspot": "APRSX-KF0KBP\nap\n",
            "Phone": "Pixel\ninfrastructure\n"}


class FakeNM:
    def __init__(self, enabled="enabled"):
        self.helper_calls = []
        self.enabled = enabled

    async def run(self, *args):
        if args[-2:] == ("con", "show"):
            return CON_SHOW
        return PROFILES[args[-1]]

    async def helper(self, *args, stdin=""):
        self.helper_calls.append((args, stdin))
        if args[0] == "scan":
            return "Xena:70:WPA2\nPixel:40:WPA2\nXena:30:WPA2\n:20:\nMy\\:Net:55:\n"
        if args[:2] == ("network-set", "bad"):
            raise RuntimeError("SSID: no control characters")
        return ""

    async def systemctl(self, *args):
        return self.enabled

    def settings(self):
        return WifiSettings(run=self.run, helper=self.helper, systemctl=self.systemctl)


def test_settings_read_networks_and_hotspot():
    data = asyncio.run(FakeNM().settings().read())
    assert data["networks"] == [
        {"name": "Phone", "ssid": "Pixel", "active": False, "priority": 10},
        {"name": "netplan-wlan0-Xena", "ssid": "Xena", "active": True, "priority": 0}]
    assert data["hotspot"] == {"configured": True, "enabled": True, "ssid": "APRSX-KF0KBP",
                               "active": False}
    off = asyncio.run(FakeNM("disabled").settings().read())
    assert off["hotspot"]["enabled"] is False


def test_parse_scan_strongest_first_deduped():
    assert parse_scan("Xena:70:WPA2\nPixel:40:WPA2\nXena:30:WPA2\n:20:\nMy\\:Net:55:\n") == [
        {"ssid": "Xena", "signal": 70, "security": "WPA2"},
        {"ssid": "My:Net", "signal": 55, "security": ""},
        {"ssid": "Pixel", "signal": 40, "security": "WPA2"}]


@pytest.fixture
def wifi_client(tmp_path):
    core = Core(Store(tmp_path / "w.db"))
    nm = FakeNM()
    core.wifi_settings = nm.settings()
    core.wifi = WifiState(run=fake_nmcli(DEV_CLIENT, PROFILES)[0])
    with TestClient(create_app(core, run_core=False)) as c:
        yield core, nm, c


def test_api_wifi_read_and_change(wifi_client):
    core, nm, c = wifi_client
    r = c.get("/api/wifi").json()
    assert r["available"] and r["hotspot"]["ssid"] == "APRSX-KF0KBP"
    assert [n["ssid"] for n in c.get("/api/wifi/scan").json()] == ["Xena", "My:Net", "Pixel"]

    r = c.put("/api/wifi/networks", json={"ssid": "Pixel", "password": "secret123", "priority": 5})
    assert r.status_code == 200 and r.json()["state"]["mode"] == "client"
    assert nm.helper_calls[-1] == (("network-set", "Pixel", "5"), "secret123")

    assert c.delete("/api/wifi/networks/Phone").status_code == 200
    assert nm.helper_calls[-1] == (("network-delete", "Phone"), "")

    assert c.put("/api/wifi/hotspot", json={"enabled": False, "ssid": "APRSX-X"}).status_code == 200
    assert nm.helper_calls[-1] == (("hotspot-set", "off", "APRSX-X"), "")


def test_api_wifi_errors(wifi_client):
    core, nm, c = wifi_client
    r = c.put("/api/wifi/networks", json={"ssid": "bad"})
    assert r.status_code == 400 and "control" in r.json()["detail"]
    assert c.put("/api/wifi/networks", json={"ssid": ""}).status_code == 422
    assert c.put("/api/wifi/networks", json={"ssid": "x", "priority": 5000}).status_code == 422


def test_api_wifi_changes_need_auth(wifi_client):
    from aprsx.core import auth
    core, nm, c = wifi_client
    core.config = core.config.model_copy(update={"admin_password_hash": auth.hash_password("pw")})
    assert c.get("/api/wifi").status_code == 200
    assert c.get("/api/wifi/scan").status_code == 401
    assert c.put("/api/wifi/hotspot", json={"enabled": True, "ssid": "A"}).status_code == 401
    assert c.delete("/api/wifi/networks/Phone").status_code == 401
    assert nm.helper_calls == []


def test_api_wifi_without_networkmanager(tmp_path):
    core = Core(Store(tmp_path / "w.db"))

    async def missing(*args):
        raise FileNotFoundError("nmcli")
    core.wifi_settings = WifiSettings(run=missing)
    with TestClient(create_app(core, run_core=False)) as c:
        assert c.get("/api/wifi").json()["available"] is False
