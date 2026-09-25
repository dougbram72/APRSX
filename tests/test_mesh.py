import pytest

from aprsx.core.meshlink import MeshError, last_hop, translate
from tests.meshfake import FRIEND, ME, REPEATER, Clock, events, make_core, set_fix


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def core(tmp_path, clock):
    return make_core(tmp_path, clock)


# --- translating library events ---------------------------------------------------


def test_translate_contact_message():
    kind, d = translate("contact_message", {
        "type": "PRIV", "SNR": 7.25, "pubkey_prefix": "f1e2d3c4b5a6", "path_len": 2,
        "txt_type": 0, "sender_timestamp": 1700000000, "text": "hi"})
    assert kind == "dm"
    assert d == {"prefix": "f1e2d3c4b5a6", "text": "hi", "sender_ts": 1700000000,
                 "snr": 7.25, "hops": 2}
    assert translate("contact_message", {"pubkey_prefix": "ab" * 6, "path_len": 255})[1][
        "hops"] is None  # sent direct


def test_translate_rx_log_advert_drops_null_position():
    kind, d = translate("rx_log_data", {
        "snr": 9.5, "rssi": -80, "payload_typename": "ADVERT", "route_typename": "FLOOD",
        "path": "c3", "path_hash_size": 1, "path_len": 1, "pkt_hash": 5,
        "adv_key": REPEATER, "adv_name": "Hilltop", "adv_type": 2,
        "adv_lat": 0.0, "adv_lon": 0.0})
    assert kind == "rx_log"
    assert d["adv_key"] == REPEATER and d["adv_type"] == 2
    assert d["adv_lat"] is None and d["adv_lon"] is None
    assert d["hops"] == 1 and d["path"] == "c3"


def test_translate_discover_and_ignored_events():
    kind, d = translate("discover_response", {"node_type": 2, "SNR_in": 4.5, "tag": "01020304",
                                              "pubkey": "c311111111111111", "SNR": 6.0,
                                              "RSSI": -90, "path_len": 0})
    assert kind == "discover_resp"
    assert d == {"tag": "01020304", "pubkey": "c311111111111111", "node_type": 2,
                 "snr": 6.0, "rssi": -90, "remote_snr": 4.5}
    assert translate("battery_info", {"level": 4000}) is None
    assert translate("next_contact", {"public_key": FRIEND}) is None


def test_last_hop():
    assert last_hop("", 1) is None
    assert last_hop("a1b2c3", 1) == "c3"
    assert last_hop("a1b2c3d4", 2) == "c3d4"


# --- sync and nodes -------------------------------------------------------------------


async def test_connect_syncs_time_channels_and_contacts(core, clock):
    core.mesh.link.contacts = [{"pubkey": FRIEND, "name": "Alice", "type": 1,
                                "lat": 39.1, "lon": -95.1, "hops": 2}]
    core.mesh._link_state()  # link came up
    await core.mesh.tick()
    assert ("time", int(clock())) in core.mesh.link.sent
    assert [c["name"] for c in core.mesh.channels] == ["Public", "#wardriving"]
    node = core.store.get_mesh_node(FRIEND)
    assert node["name"] == "Alice" and node["is_contact"] == 1 and node["lat"] == 39.1


async def test_sync_failure_retries_later(core, clock):
    core.mesh.link.fail = True
    core.mesh._link_state()
    await core.mesh.tick()
    assert core.mesh._next_sync == clock() + 30
    core.mesh.link.fail = False
    clock.t += 30
    await core.mesh.tick()
    assert core.mesh._next_sync is None and core.mesh.channels


def test_advert_heard_updates_node_and_keeps_position(core, clock):
    rx = {"snr": 9.5, "rssi": -80, "ptype": "ADVERT", "path": "", "hash_size": 1, "hops": 0,
          "adv_key": REPEATER, "adv_name": "Hilltop", "adv_type": 2,
          "adv_lat": 39.2, "adv_lon": -95.3}
    core.mesh.on_event("rx_log", rx)
    clock.t += 60
    core.mesh.on_event("rx_log", {**rx, "adv_lat": None, "adv_lon": None, "snr": 3.0})
    node = core.store.get_mesh_node(REPEATER)
    assert (node["lat"], node["lon"], node["snr"], node["type"]) == (39.2, -95.3, 3.0, 2)
    assert node["last_heard"] == clock() and node["pos_ts"] == clock() - 60
    assert len(events(core, "mesh_node")) == 2
    assert core.store.find_mesh_node("c3")["pubkey"] == REPEATER


async def test_position_shared_when_moved(core, clock):
    set_fix(core, clock)
    await core.mesh.tick()
    clock.t += 10
    set_fix(core, clock, lat=39.5001)  # ~11 m: not yet
    await core.mesh.tick()
    clock.t += 10
    set_fix(core, clock, lat=39.502)  # ~220 m
    await core.mesh.tick()
    assert [s for s in core.mesh.link.sent if s[0] == "coords"] == [
        ("coords", 39.5, -95.25), ("coords", 39.502, -95.25)]


async def test_position_not_shared_when_off(tmp_path, clock):
    core = make_core(tmp_path, clock, share_position=False)
    set_fix(core, clock)
    await core.mesh.tick()
    assert "coords" not in core.mesh.link.kinds()


async def test_node_name_set_once(tmp_path, clock):
    core = make_core(tmp_path, clock, name="KF0KBP-X")
    await core.mesh.tick()  # no self info yet: wait
    assert "name" not in core.mesh.link.kinds()
    core.mesh.on_event("self_info", {"name": "F5E588DD", "pubkey": ME})
    await core.mesh.tick()
    await core.mesh.tick()
    assert [s for s in core.mesh.link.sent if s[0] == "name"] == [("name", "KF0KBP-X")]
    assert core.mesh.status()["name"] == "KF0KBP-X"
    core.mesh.on_event("self_info", {"name": "KF0KBP-X", "pubkey": ME})
    core.config.meshcore.name = ""  # blank leaves the device's name alone
    await core.mesh.tick()
    assert core.mesh.link.kinds().count("name") == 1


async def test_node_name_refused_not_retried(tmp_path, clock):
    core = make_core(tmp_path, clock, name="KF0KBP-X")
    core.mesh.on_event("self_info", {"name": "F5E588DD", "pubkey": ME})
    core.mesh.link.fail = True
    await core.mesh.tick()
    core.mesh.link.fail = False
    await core.mesh.tick()
    assert "name" not in core.mesh.link.kinds()


# --- messages -------------------------------------------------------------------------


def test_received_dm_and_channel_message(core):
    core.mesh.on_event("dm", {"prefix": "f1e2d3c4b5a6", "text": "hello", "sender_ts": 5,
                              "snr": 6.0, "hops": 1})
    core.mesh.on_event("dm", {"prefix": "f1e2d3c4b5a6", "text": "hello", "sender_ts": 5,
                              "snr": 6.0, "hops": 1})  # a repeat
    core.mesh.on_event("chan_msg", {"channel_idx": 0, "text": "Bob: anyone out there?",
                                    "sender_ts": 9, "snr": 2.0, "hops": 3})
    dm, ch = core.store.list_mesh_messages()[::-1]
    assert len(core.store.list_mesh_messages()) == 2
    assert (dm["conv"], dm["text"], dm["state"]) == ("dm:f1e2d3c4b5a6", "hello", "received")
    assert (ch["conv"], ch["sender"], ch["text"]) == ("ch:0", "Bob", "anyone out there?")
    assert core.store.mesh_unread_count() == 2
    assert core.status()["mesh"]["unread"] == 2
    assert core.store.mark_mesh_read("ch:0") == 1


async def test_dm_acked(core, clock):
    core.store.upsert_mesh_node(FRIEND, clock(), name="Alice", type=1)
    msg = await core.mesh.send("dm:f1e2d3c4b5a6", "hi Alice")
    assert msg["state"] == "pending" and msg["attempts"] == 1
    assert core.mesh.link.sent == [("dm", FRIEND, "hi Alice", 0, int(clock()))]
    core.mesh.on_event("ack", {"code": "00000001"})
    assert core.store.get_mesh_message(msg["id"])["state"] == "acked"
    assert events(core, "mesh_ack")[-1]["state"] == "acked"


async def test_dm_retries_floods_last_try_then_fails(core, clock):
    core.store.upsert_mesh_node(FRIEND, clock(), name="Alice", type=1)
    ts = int(clock())
    msg = await core.mesh.send("dm:f1e2d3c4b5a6", "hi")
    for _ in range(3):
        clock.t += 10
        await core.mesh.tick()
    assert core.mesh.link.sent == [("dm", FRIEND, "hi", 0, ts), ("dm", FRIEND, "hi", 1, ts),
                                   ("reset_path", FRIEND), ("dm", FRIEND, "hi", 2, ts)]
    assert core.store.get_mesh_message(msg["id"])["state"] == "failed"


async def test_late_ack_for_an_earlier_try_counts(core, clock):
    msg = await core.mesh.send("dm:f1e2d3c4b5a6", "hi")  # unknown node: sent by prefix
    assert core.mesh.link.sent[0][1] == "f1e2d3c4b5a6"
    clock.t += 10
    await core.mesh.tick()
    core.mesh.on_event("ack", {"code": "00000001"})
    assert core.store.get_mesh_message(msg["id"])["state"] == "acked"


async def test_refused_dm_waits_without_using_a_try(core, clock):
    core.mesh.link.fail = True
    msg = await core.mesh.send("dm:f1e2d3c4b5a6", "hi")
    assert msg["attempts"] == 0 and msg["next_try"] == clock() + 10


async def test_channel_send(core, clock):
    core.mesh.self_info = {"name": "KF0KBP mobile"}
    msg = await core.mesh.send("ch:0", "hello mesh")
    assert core.mesh.link.sent == [("chan", 0, "hello mesh", int(clock()))]
    assert (msg["state"], msg["sender"], msg["direction"]) == ("sent", "KF0KBP mobile", "out")


@pytest.mark.parametrize("conv, text", [
    ("dm:xyz", "hi"), ("KF0KBP", "hi"), ("ch:0", "  "), ("ch:0", "x" * 141)])
async def test_send_rejects_bad_input(core, conv, text):
    with pytest.raises(MeshError):
        await core.mesh.send(conv, text)
    assert core.mesh.link.sent == []


async def test_send_needs_device(core):
    core.mesh.link.connected = False
    with pytest.raises(MeshError, match="not connected"):
        await core.mesh.send("ch:0", "hi")


async def test_link_without_library_reports_why(monkeypatch):
    import sys

    from aprsx.core.meshlink import MeshLink
    monkeypatch.setitem(sys.modules, "meshcore", None)  # import fails
    states = []
    link = MeshLink(lambda: ("/dev/ttyACM0", 115200), lambda k, d: None,
                    on_state=lambda: states.append(link.error))
    await link.run()  # returns instead of retrying forever
    assert not link.connected and "not installed" in states[0]
