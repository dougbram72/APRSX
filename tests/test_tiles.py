import sqlite3

import pytest
from fastapi.testclient import TestClient

from aprsx.core.api import create_app
from aprsx.core.service import Core
from aprsx.core.store import Store
from aprsx.core.tiles import CACHE_FRESH_S, OFFLINE_BACKOFF_S, TileServer

PNG = b"\x89PNG\r\n\x1a\n" + b"tile"
JPG = b"\xff\xd8\xff" + b"pack"


class Net:
    def __init__(self):
        self.up = True
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if not self.up:
            raise OSError("network unreachable")
        return PNG


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def net():
    return Net()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def server(tmp_path, net, clock):
    return TileServer(tmp_path, fetch=net, clock=clock)


def make_pack(path, fmt="jpg", tiles=((5, 7, 11),)):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    conn.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB)")
    conn.execute("INSERT INTO metadata VALUES ('format', ?)", (fmt,))
    for z, x, y in tiles:
        conn.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", (z, x, 2**z - 1 - y, JPG))
    conn.commit()
    conn.close()


async def test_fetches_once_then_serves_cache(server, net, tmp_path):
    assert await server.get(3, 2, 1) == PNG
    assert net.urls == ["https://tile.openstreetmap.org/3/2/1.png"]
    assert (tmp_path / "cache/3/2/1.png").read_bytes() == PNG
    assert await server.get(3, 2, 1) == PNG
    assert len(net.urls) == 1


async def test_offline_serves_stale_cache_and_backs_off(server, net, clock, tmp_path):
    await server.get(3, 2, 1)
    clock.t += CACHE_FRESH_S + 1
    net.up = False
    assert await server.get(3, 2, 1) == PNG      # stale, but better than nothing
    assert await server.get(3, 2, 2) is None     # never cached
    assert len(net.urls) == 2                    # second miss didn't hit the network
    clock.t += OFFLINE_BACKOFF_S
    net.up = True
    assert await server.get(3, 2, 2) == PNG


async def test_mbtiles_pack_first_with_tms_rows(server, net, tmp_path):
    make_pack(tmp_path / "kansas.mbtiles")
    assert await server.get(5, 7, 11) == JPG
    assert net.urls == []
    assert server.status()["packs"] == ["kansas.mbtiles"]


async def test_vector_packs_are_skipped(server, tmp_path):
    make_pack(tmp_path / "vector.mbtiles", fmt="pbf")
    assert server.status()["packs"] == []


async def test_online_disabled_never_fetches(tmp_path, net):
    s = TileServer(tmp_path, online=lambda: False, fetch=net)
    assert await s.get(3, 2, 1) is None
    assert net.urls == []


async def test_invalid_coordinates(server, net):
    for zxy in [(20, 0, 0), (2, 4, 0), (2, 0, -1)]:
        assert await server.get(*zxy) is None
    assert net.urls == []


def test_tile_route(tmp_path):
    make_pack(tmp_path / "t.mbtiles")
    core = Core(Store(tmp_path / "a.db"))
    core.config.tiles_online = False
    with TestClient(create_app(core, run_core=False, tiles_dir=tmp_path)) as c:
        r = c.get("/tiles/5/7/11.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert c.get("/tiles/5/7/12.png").status_code == 404
