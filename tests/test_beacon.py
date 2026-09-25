import asyncio

import pytest
from fastapi.testclient import TestClient

from aprsx.core import aprs, ax25
from aprsx.core.api import create_app
from aprsx.core.beacon import Scheduler, heading_change, smart_rate, turn_threshold
from aprsx.core.config import Config, SmartBeacon
from aprsx.core.gps import Fix
from aprsx.core.service import Core
from aprsx.core.store import Store

SB = SmartBeacon()  # fast 90 km/h @ 180 s, slow 5 km/h @ 1800 s, turn 15° + 255/mph, 15 s


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# --- SmartBeaconing math -------------------------------------------------------


@pytest.mark.parametrize("speed, rate", [
    (0, 1800), (4.9, 1800),   # below slow speed: slow rate
    (5, 180 * 90 / 5),        # at slow speed the scaled rate starts
    (45, 360), (90, 180),     # rate = fast_rate * fast_speed / speed
    (130, 180),               # above fast speed: fast rate
])
def test_smart_rate(speed, rate):
    assert smart_rate(SB, speed) == pytest.approx(rate)


def test_turn_threshold_falls_with_speed():
    mph60 = 60 * 1.609344
    assert turn_threshold(SB, mph60) == pytest.approx(15 + 255 / 60)
    assert turn_threshold(SB, 16.09344) == pytest.approx(15 + 25.5)  # 10 mph
    assert turn_threshold(SB, 1) == 180  # capped
    assert turn_threshold(SB, 0) == 180


@pytest.mark.parametrize("a, b, d", [(10, 350, 20), (350, 10, 20), (0, 180, 180),
                                     (90, 90, 0), (270, 45, 135)])
def test_heading_change(a, b, d):
    assert heading_change(a, b) == d


def test_first_beacon_due_at_once():
    s = Scheduler()
    assert s.due_smart(SB, 0, 0, None)
    assert s.due_fixed(1800, 0)
    assert not s.due_fixed(0, 0)  # 0 = manual only


def test_smart_interval_by_speed():
    s = Scheduler()
    s.sent(0, 90)
    assert not s.due_smart(SB, 359, 45, 90)
    assert s.due_smart(SB, 360, 45, 90)
    assert not s.due_smart(SB, 1799, 0, None)  # parked
    assert s.due_smart(SB, 1800, 0, None)


def test_corner_pegging():
    s = Scheduler()
    s.sent(0, 90)
    mph30 = 30 * 1.609344  # threshold 15 + 8.5 = 23.5°
    assert not s.due_smart(SB, 20, mph30, 110)  # 20° is not a corner
    assert s.due_smart(SB, 20, mph30, 120)      # 30° is
    assert not s.due_smart(SB, 14, mph30, 180)  # but not within turn_time
    assert not s.due_smart(SB, 20, 3, 180)      # nor below slow speed


def test_fixed_interval():
    s = Scheduler()
    s.sent(100)
    assert not s.due_fixed(1800, 1899)
    assert s.due_fixed(1800, 1900)


# --- Core ------------------------------------------------------------------------


@pytest.fixture
def clock():
    return Clock()


def make_core(tmp_path, clock, **cfg) -> Core:
    store = Store(tmp_path / "a.db")
    store.save_config(Config(**{"callsign": "KF0KBP", "ssid": 7, "path": ["WIDE1-1"], **cfg}))
    core = Core(store, clock=clock)
    core.sent = []
    core.kiss.write = lambda frame, port=0: core.sent.append(ax25.decode(frame).to_tnc2())
    return core


def set_fix(core, clock, **kw) -> None:
    core.gps.fix = Fix(**{"lat": 39.5, "lon": -95.25, "mode": 3, "ts": clock(), **kw})


def info(tnc2: str) -> str:
    return tnc2.partition(":")[2]


def test_no_position_no_beacon(tmp_path, clock):
    core = make_core(tmp_path, clock)
    assert not core.can_beacon()
    core.beacon_tick()
    assert not core.beacon() and core.sent == []


def test_n0call_never_beacons(tmp_path, clock):
    core = make_core(tmp_path, clock, callsign="N0CALL", fixed_lat=39, fixed_lon=-95)
    assert not core.can_beacon()
    core.beacon_tick()
    assert core.sent == []


def test_fixed_position_timed_beacons(tmp_path, clock):
    core = make_core(tmp_path, clock, fixed_lat=39.0, fixed_lon=-95.5, beacon_comment="hi")
    core.beacon_tick()
    assert [info(p) for p in core.sent] == ["=3900.00N/09530.00W>hi"]
    assert core.last_beacon == clock.t
    clock.t += 1799
    core.beacon_tick()
    assert len(core.sent) == 1
    clock.t += 1
    core.beacon_tick()
    assert len(core.sent) == 2


def test_manual_only_when_interval_zero(tmp_path, clock):
    core = make_core(tmp_path, clock, fixed_lat=39.0, fixed_lon=-95.5, beacon_interval_s=0)
    core.beacon_tick()
    assert core.sent == []
    assert core.beacon()
    assert len(core.sent) == 1


def test_gps_beacon_has_course_speed_altitude(tmp_path, clock):
    core = make_core(tmp_path, clock, fixed_lat=10.0, fixed_lon=10.0)
    set_fix(core, clock, alt_m=300.0, speed_ms=20.0, course=270.4)
    assert core.my_position() == (39.5, -95.25)
    assert core.beacon()
    pkt = aprs.parse(core.sent[0])
    assert (pkt["latitude"], pkt["longitude"]) == pytest.approx((39.5, -95.25), abs=1e-3)
    assert pkt["course"] == 270
    assert pkt["speed"] == pytest.approx(20 * 3.6, abs=1.9)  # sent in whole knots
    assert pkt["altitude"] == pytest.approx(300, abs=0.5)


def test_2d_fix_without_motion_is_plain_position(tmp_path, clock):
    core = make_core(tmp_path, clock)
    set_fix(core, clock, mode=2)
    assert core.beacon()
    assert info(core.sent[0]) == "=3930.00N/09515.00W>APRS-X"


def test_smartbeacon_follows_speed(tmp_path, clock):
    core = make_core(tmp_path, clock)
    set_fix(core, clock, speed_ms=12.5, course=0.0)  # 45 km/h -> every 360 s
    core.beacon_tick()
    assert len(core.sent) == 1
    for _ in range(359):
        clock.t += 1
        set_fix(core, clock, speed_ms=12.5, course=0.0)
        core.beacon_tick()
    assert len(core.sent) == 1
    clock.t += 1
    set_fix(core, clock, speed_ms=12.5, course=0.0)
    core.beacon_tick()
    assert len(core.sent) == 2


def test_smartbeacon_off_uses_interval_with_gps(tmp_path, clock):
    core = make_core(tmp_path, clock, smartbeacon=SmartBeacon(enabled=False),
                     beacon_interval_s=600)
    set_fix(core, clock, speed_ms=30.0, course=0.0)
    core.beacon_tick()
    clock.t += 300
    set_fix(core, clock, speed_ms=30.0, course=90.0)  # fast turn: ignored
    core.beacon_tick()
    assert len(core.sent) == 1


def test_manual_beacon_restarts_timer(tmp_path, clock):
    core = make_core(tmp_path, clock, fixed_lat=39.0, fixed_lon=-95.5)
    core.beacon_tick()
    clock.t += 1000
    core.beacon()
    clock.t += 1000
    core.beacon_tick()
    assert len(core.sent) == 2


def test_stale_fix_falls_back_to_fixed_position(tmp_path, clock):
    core = make_core(tmp_path, clock, fixed_lat=39.0, fixed_lon=-95.5)
    set_fix(core, clock)
    clock.t += 11
    assert core.gps.current() is None
    assert core.my_position() == (39.0, -95.5)


def test_status_reports_gps_and_beacon(tmp_path, clock):
    core = make_core(tmp_path, clock)
    s = core.status()
    assert (s["gps_fix"], s["gps"], s["can_beacon"], s["last_beacon"]) == (
        False, None, False, None)
    set_fix(core, clock, speed_ms=10.0, course=45.0)
    core.gps.sats_used = 7
    core.beacon()
    s = core.status()
    assert s["gps_fix"] and s["can_beacon"] and s["last_beacon"] == clock.t
    assert s["gps"]["sats"] == 7 and s["gps"]["speed_kmh"] == pytest.approx(36)
    assert s["position"] == {"lat": 39.5, "lon": -95.25}


def test_status_published_when_fix_comes_and_goes(tmp_path, clock):
    core = make_core(tmp_path, clock)
    got = []
    core.bus.publish = lambda type, data: got.append((type, data))
    core.gps.connected = True
    core._gps_update()
    set_fix(core, clock)
    core._gps_update()
    core._gps_update()  # unchanged: not again within GPS_STATUS_S
    clock.t += 11
    core.beacon_tick()  # the fix went stale without a report
    assert [d["gps_fix"] for t, d in got if t == "status"] == [False, True, False]


# --- API ---------------------------------------------------------------------------


def test_beacon_endpoint(tmp_path, clock):
    core = make_core(tmp_path, clock, fixed_lat=39.0, fixed_lon=-95.5)
    with TestClient(create_app(core, run_core=False)) as c:
        r = c.post("/api/beacon")
        assert r.status_code == 200 and r.json()["sent"]
        assert len(core.sent) == 1
        core.kiss.write = lambda frame, port=0: (_ for _ in ()).throw(
            ConnectionError("KISS not connected"))
        assert c.post("/api/beacon").status_code == 503


def test_beacon_endpoint_needs_position(tmp_path, clock):
    core = make_core(tmp_path, clock)
    with TestClient(create_app(core, run_core=False)) as c:
        r = c.post("/api/beacon")
        assert r.status_code == 409 and "position" in r.json()["detail"]


def test_beacon_interval_validation():
    with pytest.raises(ValueError):
        Config(beacon_interval_s=30)
    assert Config(beacon_interval_s=0).beacon_interval_s == 0


def test_failed_beacon_retries_soon(tmp_path, clock):
    core = make_core(tmp_path, clock, fixed_lat=39.0, fixed_lon=-95.5)
    ok = core.kiss.write
    core.kiss.write = lambda frame, port=0: (_ for _ in ()).throw(
        ConnectionError("KISS not connected"))
    core.beacon_tick()
    assert core.last_beacon is None
    core.kiss.write = ok
    clock.t += 29
    core.beacon_tick()
    assert core.sent == []
    clock.t += 1
    core.beacon_tick()
    assert len(core.sent) == 1


async def test_first_automatic_beacon_waits_after_start(tmp_path, clock):
    core = make_core(tmp_path, clock, fixed_lat=39.0, fixed_lon=-95.5)
    core.gps.run = core.kiss.run = _idle  # no gpsd or TNC in tests
    await core.start()
    try:
        core.beacon_tick()
        assert core.sent == []
        clock.t += 60
        core.beacon_tick()
        assert len(core.sent) == 1
    finally:
        await core.stop()


async def _idle():
    await asyncio.Event().wait()


def test_parked_gps_drift_sends_no_course_speed(tmp_path, clock):
    core = make_core(tmp_path, clock)
    set_fix(core, clock, mode=2, speed_ms=1.0, course=343.0)  # 3.6 km/h of jitter
    assert core.beacon()
    assert info(core.sent[0]) == "=3930.00N/09515.00W>APRS-X"
