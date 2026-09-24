import asyncio

import pytest

from aprsx.core import aprsis, ax25
from aprsx.core.config import Config
from aprsx.core.service import Core
from aprsx.core.store import Store

ME = "KF0KBP-7"


# --- pure helpers -------------------------------------------------------------


def test_passcode():
    assert aprsis.passcode("N0CALL") == 13023
    assert aprsis.passcode("n0call-9") == 13023  # SSID ignored


def test_effective_filter():
    assert aprsis.effective_filter("m/50", None, None) == "m/50"
    assert aprsis.effective_filter("m/50 g/WXBOT", 39.77, -95.55) == "r/39.770/-95.550/50 g/WXBOT"
    assert aprsis.effective_filter("r/1/2/3", 39.77, -95.55) == "r/1/2/3"


@pytest.mark.parametrize("tnc2,expected", [
    ("K1ABC>APRS,WIDE1-1:!3946.00N/09533.00W-", f"K1ABC>APRS,WIDE1-1,qAR,{ME}:!3946.00N/09533.00W-"),
    ("K1ABC>APRS,W0NH-1*,WIDE2-1::KF0KBP-7 :hi{1", f"K1ABC>APRS,W0NH-1*,WIDE2-1,qAR,{ME}::KF0KBP-7 :hi{{1"),
    ("K1ABC>APRS,TCPIP*:>from the internet", None),
    ("K1ABC>APRS,NOGATE:>private", None),
    ("K1ABC>APRS,RFONLY,WIDE1-1:>rf only", None),
    ("K1ABC>APRS,TCPXX*:>x", None),
    ("KF0KBP-1>APGWLF:}WXBOT>APRS,TCPIP,KF0KBP-1*::KF0KBP-7 :ack4", None),  # third party
    ("K1ABC>APRS:?APRS?", None),  # query
    ("K1ABC>APRS:>two\rlines", f"K1ABC>APRS,qAR,{ME}:>two"),
])
def test_rf_to_is_line(tnc2, expected):
    assert aprsis.rf_to_is_line(tnc2, ME) == expected


def test_path_rules_for_is_to_rf():
    assert not aprsis.path_forbids_gating(["TCPIP*", "qAC", "T2USA"], to_rf=True)
    assert aprsis.path_forbids_gating(["TCPIP*", "qAC", "T2USA"])
    assert aprsis.path_forbids_gating(["TCPXX*", "qAX"], to_rf=True)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_deduper_and_rate_limiter():
    clock = Clock()
    d = aprsis.Deduper(30, clock)
    assert not d.seen("a") and d.seen("a")
    clock.t += 30
    assert not d.seen("a")

    r = aprsis.RateLimiter(clock=clock)
    assert all(r.allow() for _ in range(6))
    assert not r.allow()          # 6 per minute
    clock.t += 60
    assert all(r.allow() for _ in range(4))
    assert not r.allow()          # 10 per 5 minutes
    clock.t += 300
    assert r.allow()


# --- a fake APRS-IS server ----------------------------------------------------


class FakeServer:
    def __init__(self, verified=True):
        self.verified = verified
        self.logins: list[str] = []
        self.received: list[str] = []
        self.writers: list[asyncio.StreamWriter] = []

    async def start(self):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _client(self, reader, writer):
        writer.write(b"# aprsc 2.1.19 fake\r\n")
        login = (await reader.readline()).decode().strip()
        self.logins.append(login)
        call = login.split()[1]
        state = "verified" if self.verified else "unverified"
        writer.write(f"# logresp {call} {state}, server T2TEST\r\n".encode())
        await writer.drain()
        self.writers.append(writer)
        while line := await reader.readline():
            self.received.append(line.decode().rstrip("\r\n"))

    def push(self, line: str):
        for w in self.writers:
            w.write(line.encode() + b"\r\n")

    async def stop(self):
        for w in self.writers:
            w.close()
        self.server.close()


async def until(cond, timeout=3.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


@pytest.fixture
async def server():
    s = FakeServer()
    await s.start()
    yield s
    await s.stop()


def make_core(tmp_path, port, **aprsis_cfg):
    store = Store(tmp_path / "a.db")
    store.save_config(Config(
        callsign="KF0KBP", ssid=7, fixed_lat=39.77, fixed_lon=-95.55, path=["WIDE1-1"],
        aprsis={"enabled": True, "server": "127.0.0.1", "port": port,
                "passcode": aprsis.passcode("KF0KBP"), "filter": "m/50", **aprsis_cfg}))
    core = Core(store)
    core.rf_sent = []
    core.kiss.write = lambda frame, port=0: core.rf_sent.append(ax25.decode(frame).to_tnc2())
    return core


def rx(core, line):
    core.handle_frame(0, ax25.encode(ax25.Frame.from_tnc2(line)))


async def test_client_logs_in_and_reports_state(tmp_path, server):
    core = make_core(tmp_path, server.port)
    await core.start()
    try:
        await until(lambda: core.aprsis.verified)
        code = aprsis.passcode("KF0KBP")
        assert server.logins == [
            f"user {ME} pass {code} vers APRS-X {aprsis.VERSION} filter r/39.770/-95.550/50"]
        s = core.status()
        assert s["aprsis_connected"] and s["aprsis_verified"] and s["aprsis_server"] == "T2TEST"
    finally:
        await core.stop()
    assert not core.aprsis.connected


async def test_unverified_login_never_sends(tmp_path):
    s = FakeServer(verified=False)
    await s.start()
    core = make_core(tmp_path, s.port, igate=True)
    await core.start()
    try:
        await until(lambda: core.aprsis.connected)
        await asyncio.sleep(0.1)
        assert not core.aprsis.verified
        rx(core, "K1ABC>APRS,WIDE1-1:>hello")
        assert not core.aprsis.send(f"{ME}>APRS,TCPIP*:>x")
        await asyncio.sleep(0.1)
        assert s.received == []
    finally:
        await core.stop()
        await s.stop()


async def test_is_packets_are_stored_as_is(tmp_path, server):
    core = make_core(tmp_path, server.port)
    await core.start()
    try:
        await until(lambda: core.aprsis.verified)
        server.push("W1AW>APRS,TCPIP*,qAC,T2USA:!4145.00N/07245.00W-Newington")
        await until(lambda: core.store.get_station("W1AW"))
        st = core.store.get_station("W1AW")
        assert st["channel"] == "is" and st["heard_direct"] == 0
        assert st["rf_heard"] is None
        assert core.store.recent_packets(1)[0]["channel"] == "is"
        # Our own packets echoed back are ignored.
        server.push(f"{ME}>APZAPX,TCPIP*,qAC,T2USA:>me")
        await asyncio.sleep(0.1)
        assert core.store.get_station(ME) is None
    finally:
        await core.stop()


async def test_rf_to_is_gating_once_per_packet(tmp_path, server):
    core = make_core(tmp_path, server.port, igate=True)
    await core.start()
    try:
        await until(lambda: core.aprsis.verified)
        rx(core, "K1ABC>APRS,WIDE1-1:!3946.00N/09533.00W-")
        rx(core, "K1ABC>APRS,W0NH-1*,WIDE1*:!3946.00N/09533.00W-")  # digipeated copy
        rx(core, "K2DEF>APRS,TCPIP*:>no")
        await until(lambda: server.received)
        await asyncio.sleep(0.1)
        assert server.received == [f"K1ABC>APRS,WIDE1-1,qAR,{ME}:!3946.00N/09533.00W-"]
        assert core.status()["gated"]["rf_to_is"] == 1
    finally:
        await core.stop()


async def test_igate_off_gates_nothing(tmp_path, server):
    core = make_core(tmp_path, server.port, igate=False)
    await core.start()
    try:
        await until(lambda: core.aprsis.verified)
        rx(core, "K1ABC>APRS,WIDE1-1:>hello")
        await asyncio.sleep(0.1)
        assert server.received == []
    finally:
        await core.stop()


async def test_messages_go_to_rf_and_is_and_is_acks_work(tmp_path, server):
    core = make_core(tmp_path, server.port)
    await core.start()
    try:
        await until(lambda: core.aprsis.verified)
        msg = core.messenger.send("WXBOT", "66534")
        assert core.rf_sent == [f"{ME}>APZAPX,WIDE1-1::WXBOT    :66534{{01}}"]
        await until(lambda: server.received)
        assert server.received == [f"{ME}>APZAPX,TCPIP*::WXBOT    :66534{{01}}"]
        assert {p["channel"] for p in core.store.recent_packets() if p["direction"] == "tx"} == {
            "rf", "is"}
        # The ack and reply arrive over APRS-IS.
        server.push(f"WXBOT>APRS,TCPIP*,qAC,T2USA::{ME:9}:ack01")
        server.push(f"WXBOT>APRS,TCPIP*,qAC,T2USA::{ME:9}:Sabetha KS. Showers{{AB}}")
        await until(lambda: core.store.get_message(msg["id"])["state"] == "acked")
        await until(lambda: len(server.received) == 2)
        assert server.received[1] == f"{ME}>APZAPX,TCPIP*::WXBOT    :ackAB"
    finally:
        await core.stop()


async def test_message_over_is_only_when_tnc_down(tmp_path, server):
    core = make_core(tmp_path, server.port)
    core.kiss.write = lambda *a: (_ for _ in ()).throw(ConnectionError("KISS not connected"))
    await core.start()
    try:
        await until(lambda: core.aprsis.verified)
        msg = core.messenger.send("WXBOT", "hi")
        assert msg["tries"] == 1  # went out over APRS-IS
        await until(lambda: server.received)
    finally:
        await core.stop()


async def test_is_to_rf_gates_messages_for_local_stations_only(tmp_path, server):
    core = make_core(tmp_path, server.port, is_to_rf=True)
    await core.start()
    try:
        await until(lambda: core.aprsis.verified)
        rx(core, "K0LOC-9>APRS,WIDE1-1:>I am local")          # heard directly
        rx(core, "K0FAR-9>APRS,W0NH-1*,WIDE2-1:>far away")     # only via a digi
        rx(core, "K0RF>APRS,WIDE1-1:>sender on RF")

        server.push("N0NET>APRS,TCPIP*,qAC,T2USA::K0LOC-9  :hello local{5")
        await until(lambda: core.rf_sent)
        assert core.rf_sent == ["KF0KBP-7>APZAPX:}N0NET>APRS,TCPIP,KF0KBP-7*::K0LOC-9  :hello local{5"]

        for line in [
            "N0NET>APRS,TCPIP*,qAC,T2USA::K0LOC-9  :hello local{5",  # duplicate
            "N0NET>APRS,TCPIP*,qAC,T2USA::K0FAR-9  :not direct{6",   # not heard directly
            "N0NET>APRS,TCPIP*,qAC,T2USA::NOBODY   :unknown{7",      # never heard
            "K0RF>APRS,TCPIP*,qAC,T2USA::K0LOC-9  :sender is local{8",
            "N0NET>APRS,TCPXX*,qAX,T2USA::K0LOC-9  :tcpxx{9",
            "N0NET>APRS,TCPIP*,qAC,T2USA:>not a message",
        ]:
            server.push(line)
        await asyncio.sleep(0.3)
        assert len(core.rf_sent) == 1
        assert core.status()["gated"]["is_to_rf"] == 1
        assert server.received == []  # gated packets aren't echoed back to APRS-IS
    finally:
        await core.stop()


async def test_is_to_rf_off_by_default(tmp_path, server):
    core = make_core(tmp_path, server.port)
    await core.start()
    try:
        await until(lambda: core.aprsis.verified)
        rx(core, "K0LOC-9>APRS,WIDE1-1:>I am local")
        server.push("N0NET>APRS,TCPIP*,qAC,T2USA::K0LOC-9  :hello{5")
        await asyncio.sleep(0.3)
        assert core.rf_sent == []
    finally:
        await core.stop()


async def test_settings_change_relogs_and_disable_stops(tmp_path, server):
    core = make_core(tmp_path, server.port)
    await core.start()
    try:
        await until(lambda: core.aprsis.verified)
        cfg = core.config.model_copy(deep=True)
        cfg.aprsis.filter = "r/40/-95/100"
        await core.update_config(cfg)
        await until(lambda: len(server.logins) == 2)
        assert server.logins[1].endswith("filter r/40/-95/100")
        cfg = core.config.model_copy(deep=True)
        cfg.aprsis.enabled = False
        await core.update_config(cfg)
        await until(lambda: not core.aprsis.connected)
    finally:
        await core.stop()


def test_stations_migration_backfills_rf_times(tmp_path):
    import sqlite3

    from aprsx.core import store as store_mod

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    for sql in store_mod._MIGRATIONS[:3]:
        conn.executescript(sql)
    conn.execute("PRAGMA user_version = 3")
    conn.execute("INSERT INTO stations (name, first_heard, last_heard, heard_direct) "
                 "VALUES ('A', 1, 100, 1), ('B', 1, 200, 0)")
    conn.commit()
    conn.close()
    s = Store(db)
    assert s.heard_on_rf("A", 100, direct=True)
    assert s.heard_on_rf("B", 200) and not s.heard_on_rf("B", 0, direct=True)
    assert s.get_station("A")["channel"] == "rf"


def test_is_copy_of_rf_station_keeps_it_rf(tmp_path):
    from aprsx.core.stations import station_record

    s = Store(tmp_path / "s.db")
    s.upsert_station(station_record("KF0KBP-1", ["WIDE1-1"], {}, 1000.0, "rf"))
    st = s.upsert_station(station_record("KF0KBP-1", ["WIDE1-1", "qAO", "KI0AU-2"], {}, 1000.2, "is"))
    assert (st["channel"], st["heard_direct"], st["path"]) == ("rf", 1, "WIDE1-1")
    assert st["packet_count"] == 2 and st["last_heard"] == 1000.2
    # After 30 minutes without RF, internet sightings take over.
    st = s.upsert_station(station_record("KF0KBP-1", ["TCPIP*"], {}, 1000.0 + 1801, "is"))
    assert (st["channel"], st["heard_direct"]) == ("is", 0)
    assert s.heard_on_rf("KF0KBP-1", 1000.0, direct=True)  # RF history is kept
