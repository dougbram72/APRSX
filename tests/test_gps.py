import asyncio
import json

import pytest

from aprsx.core.gps import WATCH, GpsdClient, parse_tpv

TPV_3D = {"class": "TPV", "device": "/dev/serial0", "mode": 3, "lat": 39.1, "lon": -95.2,
          "altHAE": 280.0, "altMSL": 305.5, "speed": 13.9, "track": 181.2}


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_parse_tpv_3d():
    fix = parse_tpv(TPV_3D, 5.0)
    assert (fix.lat, fix.lon, fix.mode, fix.alt_m, fix.course, fix.ts) == (
        39.1, -95.2, 3, 305.5, 181.2, 5.0)
    assert fix.speed_kmh == pytest.approx(50.04)


def test_parse_tpv_2d_has_no_altitude():
    fix = parse_tpv({**TPV_3D, "mode": 2}, 0)
    assert fix.alt_m is None


def test_parse_tpv_old_gpsd_alt():
    msg = {k: v for k, v in TPV_3D.items() if k not in ("altMSL", "altHAE")}
    assert parse_tpv({**msg, "alt": 100.0}, 0).alt_m == 100.0


@pytest.mark.parametrize("msg", [
    {"class": "TPV", "mode": 1, "time": "2026-09-25T00:15:06.000Z"},  # no fix yet
    {"class": "TPV", "mode": 0},
    {"class": "TPV", "mode": 2, "lat": 39.1},  # incomplete
])
def test_parse_tpv_no_fix(msg):
    assert parse_tpv(msg, 0) is None


def test_client_tracks_fix_and_satellites():
    clock = Clock()
    updates = []
    c = GpsdClient("h", 1, on_update=lambda: updates.append(1), clock=clock)
    c.handle_line(json.dumps(TPV_3D))
    assert c.current().lat == 39.1
    c.handle_line(json.dumps({"class": "SKY", "satellites": [
        {"PRN": 1, "used": True}, {"PRN": 2, "used": False}, {"PRN": 3, "used": True}]}))
    assert (c.sats_used, c.sats_seen) == (2, 3)
    c.handle_line(json.dumps({"class": "SKY", "nSat": 9, "uSat": 5}))
    assert (c.sats_used, c.sats_seen) == (5, 9)
    c.handle_line(b'{"class":"VERSION","release":"3.25"}')  # ignored
    c.handle_line(b"not json")
    assert len(updates) == 3
    c.handle_line(json.dumps({"class": "TPV", "mode": 1}))  # fix lost
    assert c.current() is None


def test_fix_goes_stale():
    clock = Clock()
    c = GpsdClient("h", 1, clock=clock)
    c.handle_line(json.dumps(TPV_3D))
    clock.t += 10
    assert c.current() is not None
    clock.t += 0.5
    assert c.current() is None


async def test_client_against_fake_gpsd():
    watched = asyncio.Event()

    async def gpsd(reader, writer):
        assert await reader.readline() == WATCH
        watched.set()
        writer.write(b'{"class":"VERSION","release":"3.25"}\n')
        writer.write(json.dumps(TPV_3D).encode() + b"\n")
        await writer.drain()
        await asyncio.sleep(0.2)
        writer.close()

    server = await asyncio.start_server(gpsd, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    states = []
    c = GpsdClient("127.0.0.1", port, reconnect_delay=10)
    c.on_update = lambda: states.append((c.connected, c.fix is not None))
    task = asyncio.create_task(c.run())
    try:
        await asyncio.wait_for(watched.wait(), 2)
        for _ in range(50):
            if states and not states[-1][0]:
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        server.close()
        await asyncio.gather(task, return_exceptions=True)
    # connected, got a fix, then the peer closed: the fix is dropped with the link
    assert states[0] == (True, False)
    assert (True, True) in states
    assert states[-1] == (False, False)


def test_untrusted_fixes_are_ignored():
    clock = Clock()
    c = GpsdClient("h", 1, clock=clock)
    c.handle_line(json.dumps(TPV_3D))
    assert c.current() is not None  # satellite count not known yet
    c.handle_line(json.dumps({"class": "SKY", "nSat": 15, "uSat": 3}))
    assert c.current() is None  # a 3-satellite solution
    c.handle_line(json.dumps({"class": "SKY", "nSat": 15, "uSat": 7}))
    assert c.current() is not None
    c.handle_line(json.dumps({**TPV_3D, "speed": 103.0}))  # ~200 knots
    assert c.current() is None
    c.handle_line(json.dumps({**TPV_3D, "speed": 30.0}))  # 108 km/h is fine
    assert c.current() is not None
