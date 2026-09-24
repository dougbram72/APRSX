"""Map tiles for the web map, usable offline.

A tile comes from, in order:
  1. an .mbtiles file in the tiles directory (raster PNG/JPEG/WebP; region
     packs the user provides),
  2. the on-disk cache, if younger than CACHE_FRESH_S,
  3. OpenStreetMap, when online and allowed (the result is cached),
  4. the cache even if stale.
We never bulk-download from OSM (their tile usage policy forbids it): only
tiles someone actually views get fetched, at most FETCH_CONCURRENCY at a time,
with a real User-Agent.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "APRS-X/0.1 (+https://github.com/dougbram72/APRSX)"
MAX_ZOOM = 19
CACHE_FRESH_S = 30 * 86400
FETCH_CONCURRENCY = 2       # OSM policy: keep parallel downloads low
FETCH_TIMEOUT_S = 10
OFFLINE_BACKOFF_S = 60      # after a failed fetch, don't try again for this long

Fetcher = Callable[[str], bytes | None]  # None = no such tile (404); raises OSError when offline


def media_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def osm_fetch(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise OSError(f"HTTP {e.code}") from e


class TileServer:
    def __init__(self, tiles_dir: Path, online: Callable[[], bool] = lambda: True,
                 fetch: Fetcher = osm_fetch, clock: Callable[[], float] = time.time) -> None:
        self.dir = tiles_dir
        self.cache_dir = tiles_dir / "cache"
        self.online = online
        self.fetch = fetch
        self.clock = clock
        self._packs: dict[Path, sqlite3.Connection] = {}
        self._packs_mtime: float | None = None
        self._sem = asyncio.Semaphore(FETCH_CONCURRENCY)
        self._offline_until = 0.0

    @staticmethod
    def valid(z: int, x: int, y: int) -> bool:
        return 0 <= z <= MAX_ZOOM and 0 <= x < 2**z and 0 <= y < 2**z

    async def get(self, z: int, x: int, y: int) -> bytes | None:
        if not self.valid(z, x, y):
            return None
        if (data := self._from_packs(z, x, y)) is not None:
            return data
        path = self.cache_dir / str(z) / str(x) / f"{y}.png"
        stale = None
        if path.exists():
            data = path.read_bytes()
            if self.clock() - path.stat().st_mtime < CACHE_FRESH_S:
                return data
            stale = data
        if self.online() and self.clock() >= self._offline_until:
            try:
                async with self._sem:
                    data = await asyncio.to_thread(self.fetch, OSM_URL.format(z=z, x=x, y=y))
            except OSError as e:
                log.info("tile fetch failed, offline for %ss: %s", OFFLINE_BACKOFF_S, e)
                self._offline_until = self.clock() + OFFLINE_BACKOFF_S
            else:
                if data is not None and media_type(data):
                    self._save(path, data)
                    return data
        return stale

    def status(self) -> dict:
        self._scan_packs()
        return {"packs": sorted(p.name for p in self._packs),
                "online": self.online(), "offline_backoff": self.clock() < self._offline_until}

    # --- cache -------------------------------------------------------------

    def _save(self, path: Path, data: bytes) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError as e:
            log.warning("can't cache tile %s: %s", path, e)

    # --- MBTiles packs -----------------------------------------------------

    def _scan_packs(self) -> None:
        """(Re)open *.mbtiles when the directory changes, so packs can be dropped in live."""
        try:
            mtime = self.dir.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        if mtime == self._packs_mtime:
            return
        self._packs_mtime = mtime
        for conn in self._packs.values():
            conn.close()
        self._packs = {}
        for path in sorted(self.dir.glob("*.mbtiles")) if mtime is not None else []:
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
                row = conn.execute("SELECT value FROM metadata WHERE name = 'format'").fetchone()
                if row and row[0] not in ("png", "jpg", "jpeg", "webp"):
                    log.warning("skipping %s: %s tiles aren't supported (raster only)",
                                path.name, row[0])
                    conn.close()
                    continue
                self._packs[path] = conn
            except sqlite3.Error as e:
                log.warning("can't open %s: %s", path.name, e)

    def _from_packs(self, z: int, x: int, y: int) -> bytes | None:
        self._scan_packs()
        tms_y = 2**z - 1 - y  # MBTiles rows count from the bottom (TMS)
        for path, conn in self._packs.items():
            try:
                row = conn.execute(
                    "SELECT tile_data FROM tiles WHERE zoom_level = ? AND tile_column = ? "
                    "AND tile_row = ?", (z, x, tms_y)).fetchone()
            except sqlite3.Error as e:
                log.warning("reading %s: %s", path.name, e)
                continue
            if row and media_type(row[0]):
                return row[0]
        return None
