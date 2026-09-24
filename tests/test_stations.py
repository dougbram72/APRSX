import pytest

from aprsx.core import aprs, ax25
from aprsx.core.stations import distance_bearing, station_record, with_distance
from aprsx.core.store import Store


def record(line, ts=1000.0):
    frame = ax25.Frame.from_tnc2(line)
    try:
        pkt = aprs.parse(line)
    except aprs.ParseError:
        pkt = None
    path = [str(d) + ("*" if d.repeated else "") for d in frame.path]
    return station_record(str(frame.source), path, pkt, ts)


def test_distance_bearing_known_values():
    # One degree of latitude north is ~111.2 km, bearing 0.
    d, b = distance_bearing(39.0, -95.0, 40.0, -95.0)
    assert d == pytest.approx(111.2, abs=0.2)
    assert b == pytest.approx(0, abs=0.01)
    # Due east along the equator.
    _, b = distance_bearing(0, 0, 0, 1)
    assert b == pytest.approx(90, abs=0.01)


def test_record_position_and_direct():
    r = record("KF0KBP-1>APGRWO,WIDE1-1:!3946.  N/09533.  W-house")
    assert r["name"] == "KF0KBP-1"
    assert r["heard_direct"] is True
    assert r["path"] == "WIDE1-1"
    assert r["lat"] == pytest.approx(39.775)
    assert r["symbol"] == "-"


def test_record_digipeated():
    r = record("K1ABC>APRS,DIGI1,WIDE2*:>status")
    assert r["heard_direct"] is False
    assert r["path"] == "DIGI1*,WIDE2*"
    assert "lat" not in r


def test_record_object_uses_object_name():
    r = record("K1ABC>APRS:;LEADER   *092345z4903.50N/07201.75W>obj")
    assert r["name"] == "LEADER"
    assert r["is_object"] is True


def test_record_unparseable_packet():
    r = record("K1ABC>APRS:!garbage")
    assert r["name"] == "K1ABC"
    assert r["last_format"] is None


def test_upsert_keeps_last_position(tmp_path):
    s = Store(tmp_path / "a.db")
    s.upsert_station(record("K1ABC>APRS:!4903.50N/07201.75W>first", ts=1))
    st = s.upsert_station(record("K1ABC>APRS,DIGI*:>just a status", ts=2))
    assert st["packet_count"] == 2
    assert st["last_heard"] == 2
    assert st["pos_ts"] == 1
    assert st["lat"] == pytest.approx(49.058, abs=1e-3)
    assert st["comment"] == "first"
    assert st["heard_direct"] == 0
    assert st["first_heard"] == 1


def test_with_distance_unknown_position():
    out = with_distance({"lat": 1.0, "lon": 1.0}, None, None)
    assert out["distance_km"] is None and out["bearing"] is None


def test_packet_prune(tmp_path):
    s = Store(tmp_path / "a.db")
    for i in range(10):
        s.add_packet(float(i), f"A>B:{i}")
    assert s.prune_packets(keep=3) == 7
    assert [p["raw"] for p in s.recent_packets()] == ["A>B:9", "A>B:8", "A>B:7"]
    assert s.prune_packets(keep=3) == 0
