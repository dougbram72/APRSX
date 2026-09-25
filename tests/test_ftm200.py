"""The FTM-200D backend (phase 9), driven by output recorded from the radio."""

import asyncio
import os
from pathlib import Path

import pytest

from aprsx.core.config import Config
from aprsx.core.direwolf import DirewolfManager
from aprsx.core.ftm200 import Ftm200Reader, LineParser
from aprsx.core.service import Core
from aprsx.core.store import Store

# Recorded from the FTM-200D's DATA jack with tools/ftm200_capture.py (docs/FTM200.md).
CAPTURE = Path(__file__).parent / "data" / "ftm200" / "capture-20260925.raw"


# In the capture, KB1EMU-6's info line stopped after "4P" with no CR/LF, and the
# next header came 36 s later. The reader flushes the parser in such gaps.
CUT = b"<UI R>:\r\n4P"


def headers(data: bytes) -> int:
    return sum(b"] <UI" in line or b"]:" in line for line in data.split(b"\n"))


def parse_capture(parser: LineParser | None = None) -> list[str]:
    """The capture's packets, as the reader sees them: flushed at the gap."""
    parser = parser or LineParser()
    data = CAPTURE.read_bytes()
    gap = data.index(CUT) + len(CUT)
    out = parser.feed(data[:gap])
    parser.flush()
    return out + parser.feed(data[gap:])


def test_capture_parses_to_tnc2():
    data = CAPTURE.read_bytes()
    out = parse_capture()
    assert len(out) == headers(data) - 1 > 30  # all but the cut-off packet
    assert out[0] == "KF0KBP-1>APGRWO,WIDE1-1:!3946.  N/09533.  W-"
    # Digipeated: only the last used hop carries the "*" (TNC2 convention).
    assert ("ENSOR>APN383,TNGNXI,W0NH-1,WIDE2*:"
            "!3847.74NS09448.34W#PHG7330 W2, KSn-N Ensor Museum Digi") in out
    # Mic-E comes through byte for byte, control characters and backslashes included.
    assert "K0RAR>SXRU8U,TNGNXI,WIDE1,W0NH-1,WIDE2*:`{){l \x1c-/`_4" in out
    assert all("\r" not in p and "\n" not in p and " [" not in p.partition(":")[0]
               for p in out)
    # The packet after the gap is whole; the cut-off one is gone.
    assert ("KF0WGF-9>APN000,K0ATT-10,WIDE1,CARROL,W0NH-1,WIDE2*:"
            "=3845.62N/09025.65Wk060/067/A=000498passing thru") in out
    assert not any(p.startswith(("KB1EMU-6>SX5W7W", "4P")) for p in out)


def test_without_the_flush_the_cut_off_line_corrupts_the_next_packet():
    # Why the reader flushes: the fragment glues onto the next header.
    assert any(p.startswith("4PKF0WGF-9>") for p in LineParser().feed(CAPTURE.read_bytes()))


def test_split_reads_give_the_same_packets():
    data = CAPTURE.read_bytes()
    parser = LineParser()
    out = [p for i in range(len(data)) for p in parser.feed(data[i:i + 1])]
    assert out == LineParser().feed(data)
    assert len(out) == len(parse_capture())


def test_noise_and_orphan_headers_are_skipped():
    out = LineParser().feed(
        b"3.74NS09448.34W# partial line at connect\r\n"
        b"A1AA>APRS,WIDE1-1 [09/25/26 12:00:00] <UI C>:\r\n"  # no info line follows
        b"B1BB-9>APRS [09/25/26 12:00:05] <UI>:\r\n"
        b">status text \r\n"
        b"\r\n"
        b"stray line\r\n")
    assert out == ["B1BB-9>APRS:>status text "]


# --- core ---------------------------------------------------------------------


def make_core(tmp_path, **cfg) -> Core:
    store = Store(tmp_path / "a.db")
    store.save_config(Config(callsign="KF0KBP", ssid=7, fixed_lat=39.77, fixed_lon=-95.55,
                             radio="ftm200", **cfg))
    core = Core(store)
    core.kiss.write = lambda *a: pytest.fail("wrote to KISS with the FTM-200 selected")
    return core


def test_capture_through_core(tmp_path):
    core = make_core(tmp_path)
    for line in parse_capture():
        core.handle_tnc2(line)
    stations = {s["name"]: s for s in core.stations()}
    home = stations["KF0KBP-1"]
    assert home["heard_direct"] and home["channel"] == "rf" and home["lat"] is not None
    assert not stations["ENSOR"]["heard_direct"]
    assert stations["K0RAR"]["lat"] is not None  # Mic-E decoded
    assert "146.850-R" in stations  # objects under their own name
    assert core.store.heard_on_rf("KF0KBP-1", 0, direct=True)
    assert not core.store.heard_on_rf("ENSOR", 0, direct=True)
    assert core.rx_count == len(core.store.recent_packets(1000))


def test_unusable_line_is_dropped(tmp_path):
    core = make_core(tmp_path)
    core.handle_tnc2("NOT A CALL>APRS:>x")
    assert core.store.recent_packets() == []


def test_receive_only(tmp_path):
    core = make_core(tmp_path)
    assert core.radio is core.ftm200
    assert core.transmit(">hello") is False
    s = core.status()
    assert s["radio"] == "ftm200" and s["rf_tx"] is False
    assert s["can_transmit"] is False and s["can_beacon"] is False
    assert core.beacon() is False
    assert core.store.recent_packets() == []


def test_receive_only_still_sends_to_aprsis(tmp_path):
    core = make_core(tmp_path)
    sent = []
    core.aprsis.send = lambda line: sent.append(line) or True
    core.aprsis.verified = True
    assert core.status()["can_beacon"] is True
    assert core.beacon() is True
    assert len(sent) == 1 and sent[0].startswith("KF0KBP-7>APZAPX,TCPIP*:")
    assert [p["channel"] for p in core.store.recent_packets()] == ["is"]


def test_direwolf_backend_is_unchanged(tmp_path):
    core = make_core(tmp_path)
    core.config = core.config.model_copy(update={"radio": "direwolf"})
    assert core.radio is core.kiss and core.status()["rf_tx"] is True


# --- switching backends ---------------------------------------------------------


class FakeDirewolf(DirewolfManager):
    def __init__(self, path):
        super().__init__(path, "test.service")
        self.calls = []

    async def _systemctl(self, action):
        self.calls.append(action)


async def _idle():
    await asyncio.Event().wait()


async def test_switching_backends_stops_and_restarts_direwolf(tmp_path):
    store = Store(tmp_path / "a.db")
    store.save_config(Config(callsign="KF0KBP", ssid=7, direwolf_managed=True))
    dw = FakeDirewolf(tmp_path / "direwolf.conf")
    core = Core(store, direwolf=dw)
    core.gps.run = core.kiss.run = core.ftm200.run = _idle
    await core.start()
    try:
        assert core._radio_task.get_name() == "radio-direwolf"
        await core.update_config(core.config.model_copy(update={"radio": "ftm200"}))
        assert dw.calls == ["stop"]
        assert core._radio_task.get_name() == "radio-ftm200"
        await core.update_config(core.config.model_copy(update={"radio": "direwolf"}))
        assert dw.calls == ["stop", "restart"]
        assert core._radio_task.get_name() == "radio-direwolf"
    finally:
        await core.stop()


async def test_start_with_ftm200_stops_direwolf(tmp_path):
    store = Store(tmp_path / "a.db")
    store.save_config(Config(callsign="KF0KBP", ssid=7, direwolf_managed=True, radio="ftm200"))
    dw = FakeDirewolf(tmp_path / "direwolf.conf")
    core = Core(store, direwolf=dw)
    core.gps.run = core.ftm200.run = _idle
    await core.start()
    try:
        assert dw.calls == ["stop"] and not dw.conf_path.exists()
    finally:
        await core.stop()


# --- the serial reader, on a pty -------------------------------------------------


async def until(cond, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.01)


async def test_reader_on_a_pty():
    master, slave = os.openpty()
    got, states = [], []
    reader = Ftm200Reader(os.ttyname(slave), 9600, got.append,
                          reconnect_delay=0.05, on_state=states.append, idle_flush=0.2)
    task = asyncio.create_task(reader.run())
    try:
        await until(lambda: reader.connected)
        data = CAPTURE.read_bytes()
        gap = data.index(CUT) + len(CUT)
        os.write(master, data[:500])
        os.write(master, data[500:gap])
        await asyncio.sleep(0.4)  # the radio went quiet mid-line
        os.write(master, data[gap:])
        expected = parse_capture()
        await until(lambda: len(got) == len(expected))
        assert got == expected
        with pytest.raises(ConnectionError):
            reader.write(b"x")

        reader.set_device("/dev/nonexistent-ftm200", 9600)
        await until(lambda: not reader.connected and reader.error)
        assert states == [True, False]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        os.close(master)
        os.close(slave)


async def test_reader_reports_a_missing_device():
    reader = Ftm200Reader("", 9600, lambda line: None, reconnect_delay=0.05)
    task = asyncio.create_task(reader.run())
    try:
        await until(lambda: reader.error is not None)
        assert not reader.connected and "no serial device" in reader.error
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# --- API ------------------------------------------------------------------------


def test_select_ftm200_through_the_api(tmp_path):
    from fastapi.testclient import TestClient

    from aprsx.core.api import create_app

    store = Store(tmp_path / "a.db")
    store.save_config(Config(callsign="KF0KBP", ssid=7))
    core = Core(store)
    with TestClient(create_app(core, run_core=False, tiles_dir=tmp_path / "tiles")) as client:
        cfg = client.get("/api/config").json()
        cfg.update(radio="ftm200", ftm200={"device": "/dev/ttyUSB9", "baud": 9600})
        assert client.put("/api/config", json=cfg).status_code == 200
        assert core.config.radio == "ftm200" and core.ftm200.device == "/dev/ttyUSB9"
        s = client.get("/api/status").json()
        assert (s["radio"], s["rf_tx"], s["radio_connected"]) == ("ftm200", False, False)
        links = client.get("/api/system/status").json()["links"]
        assert links["radio"] == "ftm200" and links["ftm200"]["device"] == "/dev/ttyUSB9"
        cfg["ftm200"]["baud"] = 1200
        assert client.put("/api/config", json=cfg).status_code == 422


async def test_reader_notices_the_port_going_away():
    master, slave = os.openpty()
    reader = Ftm200Reader(os.ttyname(slave), 9600, lambda line: None, reconnect_delay=10)
    task = asyncio.create_task(reader.run())
    try:
        await until(lambda: reader.connected)
        os.close(master)  # like pulling the cable: the port hangs up
        await until(lambda: not reader.connected)
        assert reader.error
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        os.close(slave)


async def test_reader_keeps_off_a_port_another_program_has():
    import fcntl

    master, slave = os.openpty()
    other = os.open(os.ttyname(slave), os.O_RDWR | os.O_NOCTTY)
    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # as pyserial does
    reader = Ftm200Reader(os.ttyname(slave), 9600, lambda line: None, reconnect_delay=0.05)
    task = asyncio.create_task(reader.run())
    try:
        await until(lambda: reader.error is not None)
        assert "in use" in reader.error and not reader.connected
        os.close(other)
        await until(lambda: reader.connected)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        os.close(master)
        os.close(slave)


# --- beacons ----------------------------------------------------------------------


def logged_in(core) -> list[str]:
    sent = []
    core.aprsis.send = lambda line: sent.append(line) or True
    core.aprsis.verified = True
    return sent


def test_no_automatic_beacons_with_the_ftm200_by_default(tmp_path):
    core = make_core(tmp_path, beacon_interval_s=60)
    sent = logged_in(core)
    for _ in range(3):
        core.beacon_tick()
    assert sent == [] and core.last_beacon is None
    assert core.beacon() is True and len(sent) == 1  # the Beacon button still works


def test_automatic_beacons_with_the_ftm200_when_asked(tmp_path):
    core = make_core(tmp_path, beacon_interval_s=60, ftm200={"auto_beacon": True})
    sent = logged_in(core)
    core.beacon_tick()
    assert len(sent) == 1
