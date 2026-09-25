import json

import pytest
from fastapi.testclient import TestClient

from aprsx.core.api import create_app
from aprsx.core.audiomon import AudioMonitor, parse_line
from aprsx.core.config import Config
from aprsx.core.gps import GpsdClient
from aprsx.core.service import Core
from aprsx.core.store import Store
from aprsx.core.sysinfo import decode_throttled, parse_cpu_times, parse_meminfo

# --- Direwolf log lines ---------------------------------------------------------


@pytest.mark.parametrize("line, kind, data", [
    ("KF0KBP-1 audio level = 48(24/12)   [NONE]   __|||||__", "packet",
     {"station": "KF0KBP-1", "via": None, "level": 48, "mark": 24, "space": 12}),
    ("Digipeater WIDE2 (probably W0NH-1) audio level = 1(0/0)    __|||||__", "packet",
     {"station": "W0NH-1", "via": "Digipeater WIDE2", "level": 1, "mark": 0, "space": 0}),
    ("ADEVICE0: Sample rate approx. 44.1 k, 0 errors, receive audio level CH0 9", "stats",
     {"rate_k": 44.1, "errors": 0, "level": 9}),
    ("Audio input level is too low.  Increase so most stations are around 50.", "advice",
     {"too": "low"}),
])
def test_parse_direwolf_lines(line, kind, data):
    assert parse_line(line) == (kind, data)


def test_parse_ignores_other_lines():
    assert parse_line("[0.4] BASHOR>APMI01,W0NH-1,WIDE2*:;444.25+KC*111111z") is None
    assert parse_line("Channel 0: 1200 baud, AFSK 1200 & 2200 Hz, A+, 44100 sample rate.") is None


def test_audio_monitor_keeps_newest_first_and_median():
    a = AudioMonitor(clock=lambda: 0)
    for i, lvl in enumerate((10, 50, 60)):
        a.handle(f"K{i}ABC audio level = {lvl}(1/1)   [NONE]", ts=i)
    s = a.status()
    assert [p["station"] for p in s["packets"]] == ["K2ABC", "K1ABC", "K0ABC"]
    assert s["median_level"] == 50 and s["stats"] is None


# --- Pi -------------------------------------------------------------------------


def test_decode_throttled():
    assert decode_throttled(0x50005) == {"now": ["undervoltage", "throttled"],
                                         "since_boot": ["undervoltage", "throttled"]}
    assert decode_throttled(0x50000) == {"now": [], "since_boot": ["undervoltage", "throttled"]}
    assert decode_throttled(0) == {"now": [], "since_boot": []}


def test_parse_meminfo_and_cpu():
    m = parse_meminfo("MemTotal:  948304 kB\nMemAvailable: 512000 kB\nHugePages_Total: 0\n")
    assert m == {"MemTotal": 948304 * 1024, "MemAvailable": 512000 * 1024, "HugePages_Total": 0}
    assert parse_cpu_times("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3\n") == (150, 1000)


# --- GPS detail -------------------------------------------------------------------


def test_gps_detail_keeps_satellites_and_rejection():
    t = [1000.0]
    g = GpsdClient("h", 1, clock=lambda: t[0])
    g.handle_line(json.dumps({"class": "TPV", "mode": 2, "lat": 41.9, "lon": -91.9,
                              "speed": 103.0, "time": "2026-09-25T10:56:00.000Z", "epx": 30.1}))
    g.handle_line(json.dumps({"class": "SKY", "hdop": 2.6, "satellites": [
        {"PRN": 18, "el": 42, "az": 10, "ss": 29, "used": True},
        {"PRN": 5, "el": 59, "az": 80, "ss": 25, "used": True},
        {"PRN": 29, "el": 72, "az": 200, "ss": 16, "used": True},
        {"PRN": 11, "el": 12, "az": 300, "ss": 0, "used": False}]}))
    d = g.detail()
    assert d["used"] is False and d["rejected"] == "only 3 satellites used (need 4)"
    assert (d["sats_used"], d["sats_seen"], d["dop"], d["errors"]) == (3, 4, {"hdop": 2.6}, {"epx": 30.1})
    assert d["satellites"][0] == {"PRN": 18, "gnssid": None, "el": 42, "az": 10, "ss": 29, "used": True}
    assert d["fix"]["lat"] == 41.9 and d["time"] == "2026-09-25T10:56:00.000Z"
    t[0] += 60
    assert g.detail()["rejected"] == "stale"


# --- API ----------------------------------------------------------------------------


def test_system_status_endpoint(tmp_path):
    store = Store(tmp_path / "a.db")
    store.save_config(Config(callsign="KF0KBP", ssid=7))
    core = Core(store)
    core.audio.handle("KF0KBP-1 audio level = 48(24/12)   [NONE]", ts=5.0)
    with TestClient(create_app(core, run_core=False)) as c:
        s = c.get("/api/system/status").json()
    assert set(s) == {"pi", "gps", "audio", "links", "counts", "uptime_s"}
    assert s["pi"]["cpus"] >= 1 and "services" in s["pi"]
    assert s["audio"]["packets"][0]["level"] == 48
    assert s["gps"]["connected"] is False and s["links"]["kiss"] is False
    assert s["links"]["mesh"]["enabled"] is False


def test_status_page_served(tmp_path):
    store = Store(tmp_path / "a.db")
    with TestClient(create_app(Core(store), run_core=False)) as c:
        r = c.get("/status.html")
    assert r.status_code == 200 and "status.js" in r.text
