import csv
import io
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from aprsx.core import wardrive
from aprsx.core.api import create_app
from tests.meshfake import REPEATER, Clock, events, make_core, set_fix


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def core(tmp_path, clock):
    core = make_core(tmp_path, clock)
    await core.mesh._sync(clock())  # channels, as after connecting
    core.mesh.link.sent.clear()
    return core


async def step(core, clock, dt=1, **fix):
    clock.t += dt
    set_fix(core, clock, **fix)
    await core.mesh.wardrive.tick(clock())


def pings(core):
    return [s for s in core.mesh.link.sent if s[0] in ("discover", "chan", "advert")]


def echo(sender_ts, path="c3", chan="#wardriving", msg="KF0KBP: @wd 39.50000,-95.25000"):
    return {"snr": 5.5, "rssi": -95, "ptype": "GRP_TXT", "path": path, "hash_size": 1,
            "hops": len(path) // 2, "chan_name": chan, "message": msg, "sender_ts": sender_ts}


async def test_nothing_sent_without_a_session(core, clock):
    for _ in range(60):
        await step(core, clock)
    assert pings(core) == []


async def test_pauses_without_gps_or_device(core, clock):
    wd = core.mesh.wardrive
    wd.start()
    clock.t += 1
    await wd.tick(clock())
    assert wd.paused == "waiting for a GPS fix" and pings(core) == []
    core.mesh.link.connected = False
    await step(core, clock)
    assert wd.paused == "MeshCore device not connected" and pings(core) == []
    core.mesh.link.connected = True
    await step(core, clock)
    assert wd.paused is None and core.mesh.link.kinds() == ["discover"]


async def test_pings_alternate_and_need_time_and_distance(core, clock):
    wd = core.mesh.wardrive
    wd.start()
    await step(core, clock)
    assert [p[0] for p in pings(core)] == ["discover"]
    await step(core, clock, dt=40)  # 40 s later but not moved
    assert len(pings(core)) == 1
    await step(core, clock, dt=1, lat=39.5003)  # ~33 m
    assert [p[0] for p in pings(core)] == ["discover", "chan"]
    assert pings(core)[1] == ("chan", 1, "@wd 39.50030,-95.25000", int(clock()))
    await step(core, clock, dt=10, lat=39.5006)  # moved, but only 10 s
    assert len(pings(core)) == 2
    await step(core, clock, dt=20, lat=39.5006)
    assert [p[0] for p in pings(core)] == ["discover", "chan", "discover"]
    assert wd.status()["pings"] == 3


async def test_only_discovery_without_the_ping_channel(core, clock):
    core.mesh.channels = [{"idx": 0, "name": "Public"}]
    core.mesh.wardrive.start()
    for i in range(3):
        await step(core, clock, dt=30, lat=39.5 + i * 0.001)
    assert [p[0] for p in pings(core)] == ["discover"] * 3


async def test_discover_answers_inside_the_window(core, clock):
    wd = core.mesh.wardrive
    wd.start()
    await step(core, clock)
    tag = wd._open["tag"]
    core.mesh.store.upsert_mesh_node(REPEATER, clock(), name="Hilltop", type=2,
                                     lat=39.6, lon=-95.2)
    ans = {"tag": tag, "pubkey": REPEATER[:16], "node_type": 2, "snr": 6.0, "rssi": -88,
           "remote_snr": 4.25}
    core.mesh.on_event("discover_resp", ans)
    core.mesh.on_event("discover_resp", {**ans, "tag": "deadbeef"})  # someone else's
    obs = events(core, "wardrive_obs")
    assert len(obs) == 1
    assert obs[0]["node_name"] == "Hilltop" and obs[0]["node_lat"] == 39.6
    assert (obs[0]["snr"], obs[0]["remote_snr"], obs[0]["my_lat"]) == (6.0, 4.25, 39.5)
    await step(core, clock, dt=7)  # window closes
    core.mesh.on_event("discover_resp", {**ans, "pubkey": "ee" * 8})  # too late
    assert len(events(core, "wardrive_obs")) == 1
    ping = events(core, "wardrive_ping")[-1]
    assert (ping["heard"], ping["best_snr"]) == (1, 6.0)
    assert wd.status()["pings_heard"] == 1 and wd.status()["nodes"] == 1


async def test_channel_echoes_matched(core, clock):
    wd = core.mesh.wardrive
    wd.start()
    await step(core, clock)
    await step(core, clock, dt=7)  # discover window over
    await step(core, clock, dt=30, lat=39.501)
    ts = int(clock())
    assert wd._open["kind"] == "chan"
    core.mesh.on_event("rx_log", echo(ts, path="c3"))
    core.mesh.on_event("rx_log", echo(ts, path="c3a7"))       # a second repeater
    core.mesh.on_event("rx_log", echo(ts - 1, path="b2"))     # another message
    core.mesh.on_event("rx_log", echo(ts, path="d4", chan="Public"))
    core.mesh.on_event("rx_log", echo(ts, path=""))           # not repeated
    got = [(o["kind"], o["node"], o["hops"]) for o in events(core, "wardrive_obs")]
    assert got == [("echo", "c3", 1), ("echo", "a7", 2)]
    s = wardrive.session(core.store, wd.session_id)
    assert [o["kind"] for o in s["obs"]].count("rx") == 2  # other message, other channel


async def test_passive_rx_logged_only_during_a_session(core, clock):
    set_fix(core, clock)
    core.mesh.on_event("rx_log", echo(1, path="c3"))
    wd = core.mesh.wardrive
    wd.start()
    core.mesh.on_event("rx_log", {"snr": 1.0, "rssi": -110, "ptype": "ADVERT", "path": "",
                                  "hash_size": 1, "hops": 0, "adv_key": REPEATER,
                                  "adv_type": 2, "adv_lat": None, "adv_lon": None})
    obs = wardrive.session(core.store, wd.session_id)["obs"]
    assert [(o["kind"], o["node"], o["node_type"]) for o in obs] == [("rx", REPEATER, 2)]
    assert wd.status()["rx"] == 1


async def test_optional_adverts(tmp_path, clock):
    core = make_core(tmp_path, clock, wardrive={"advert_interval_s": 120})
    core.mesh.channels = []
    core.mesh.wardrive.start()
    for _ in range(130):
        await step(core, clock)
    assert [p for p in pings(core) if p[0] == "advert"] == [("advert", False)] * 2


async def test_failed_ping_waits_for_next_slot(core, clock):
    core.mesh.link.fail = True
    wd = core.mesh.wardrive
    wd.start()
    await step(core, clock)
    await step(core, clock)
    assert wd.status()["pings"] == 0 and wd._last_ping is not None


async def test_stop_closes_session(core, clock):
    wd = core.mesh.wardrive
    wd.start()
    sid = wd.session_id
    await step(core, clock)
    wd.stop()
    assert not wd.active and core.store.list_wd_sessions()[0]["ended"] == clock()
    assert core.store.list_wd_sessions()[0]["id"] == sid


# --- exports and API -----------------------------------------------------------------


async def drive(core, clock):
    wd = core.mesh.wardrive
    wd.start()
    core.store.upsert_mesh_node(REPEATER, clock(), name="Hill<top>", type=2,
                                lat=39.6, lon=-95.2)
    await step(core, clock)
    core.mesh.on_event("discover_resp", {"tag": wd._open["tag"], "pubkey": REPEATER[:16],
                                         "node_type": 2, "snr": 6.0, "rssi": -88,
                                         "remote_snr": 4.0})
    await step(core, clock, dt=30, lat=39.51)  # window closed; next ping has no answer
    await step(core, clock, dt=10)
    return wd.session_id


async def test_exports(core, clock):
    sid = await drive(core, clock)
    s = wardrive.session(core.store, sid)
    gj = wardrive.geojson(s)
    kinds = [(f["properties"]["record"], f["geometry"]["type"]) for f in gj["features"]]
    assert kinds == [("ping", "Point"), ("ping", "Point"), ("discover", "LineString")]
    assert gj["features"][2]["geometry"]["coordinates"] == [[-95.25, 39.5], [-95.2, 39.6]]
    rows = list(csv.DictReader(io.StringIO(wardrive.to_csv(s))))
    assert [r["record"] for r in rows] == ["ping", "ping", "obs"]
    assert rows[2]["node_name"] == "Hill<top>" and rows[0]["heard"] == "1"
    gpx = ET.fromstring(wardrive.to_gpx(s))
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    assert len(gpx.findall("g:wpt", ns)) == 2
    assert gpx.find("g:wpt/g:desc", ns).text == "Hill<top> 6.0 dB"


async def test_api(core, clock):
    sid = await drive(core, clock)
    with TestClient(create_app(core, run_core=False)) as c:
        assert c.get("/api/status").json()["mesh"]["wardrive"]["session_id"] == sid
        sessions = c.get("/api/wardrive/sessions").json()
        assert (sessions[0]["pings"], sessions[0]["pings_heard"], sessions[0]["nodes"]) == (2, 1, 1)
        cov = c.get("/api/wardrive/coverage").json()
        assert len(cov["pings"]) == 2 and cov["obs"][0]["node_name"] == "Hill<top>"
        r = c.get(f"/api/wardrive/sessions/{sid}/export?fmt=csv")
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        assert c.get(f"/api/wardrive/sessions/{sid}/export?fmt=kml").status_code == 422
        assert c.get("/api/wardrive/sessions/999").status_code == 404
        assert c.post("/api/wardrive/stop").json()["active"] is False
        assert c.get("/api/mesh/channels").json()[1]["name"] == "#wardriving"
        nodes = c.get("/api/mesh/nodes").json()
        assert nodes[0]["name"] == "Hill<top>" and "distance_km" in nodes[0]


def test_api_messages(tmp_path, clock):
    core = make_core(tmp_path, clock)
    with TestClient(create_app(core, run_core=False)) as c:
        r = c.post("/api/mesh/messages", json={"conv": "ch:0", "text": "hi"})
        assert r.status_code == 201 and r.json()["state"] == "sent"
        assert c.post("/api/mesh/messages", json={"conv": "bad", "text": "hi"}).status_code == 400
        core.mesh.on_event("dm", {"prefix": "f1e2d3c4b5a6", "text": "yo", "sender_ts": 1})
        assert [m["conv"] for m in c.get("/api/mesh/conversations").json()] == [
            "dm:f1e2d3c4b5a6", "ch:0"]
        assert c.get("/api/mesh/messages?conv=dm:F1E2D3C4B5A6").json()[0]["text"] == "yo"
        assert c.post("/api/mesh/messages/read", json={"conv": "dm:f1e2d3c4b5a6"}).json()[
            "unread"] == 0
        assert c.post("/api/mesh/advert", json={"flood": True}).json()["flood"] is True
        core.mesh.link.connected = False
        assert c.post("/api/mesh/messages", json={"conv": "ch:0", "text": "x"}).status_code == 503


def test_wardrive_needs_meshcore_enabled(tmp_path, clock):
    core = make_core(tmp_path, clock, enabled=False)
    with TestClient(create_app(core, run_core=False)) as c:
        assert c.post("/api/wardrive/start").status_code == 409


async def test_core_stop_ends_session_and_restart_closes_leftovers(tmp_path, clock):
    core = make_core(tmp_path, clock)
    core.mesh.wardrive.start()
    core.store.start_wd_session(clock())  # left open by a crash
    await core.stop()
    core.store.end_open_wd_sessions(clock())
    assert all(s["ended"] is not None for s in core.store.list_wd_sessions())
