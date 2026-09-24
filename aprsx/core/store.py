"""SQLite persistence. Schema is versioned with PRAGMA user_version."""

from __future__ import annotations

import functools
import sqlite3
import threading
from pathlib import Path

from .config import Config

# A station heard on RF within this long stays shown as an RF station even when
# APRS-IS copies of its packets arrive.
RF_STICKY_S = 30 * 60

# Each entry upgrades the schema by one version. Append; never edit old entries.
_MIGRATIONS: list[str] = [
    """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE packets (
        id        INTEGER PRIMARY KEY,
        ts        REAL NOT NULL,
        source    TEXT,
        raw       TEXT NOT NULL,
        format    TEXT,
        direction TEXT NOT NULL DEFAULT 'rx',  -- rx | tx
        channel   TEXT NOT NULL DEFAULT 'rf'   -- rf | is
    );
    CREATE TABLE stations (
        name         TEXT PRIMARY KEY,         -- callsign-ssid, or object/item name
        is_object    INTEGER NOT NULL DEFAULT 0,
        first_heard  REAL NOT NULL,
        last_heard   REAL NOT NULL,
        packet_count INTEGER NOT NULL DEFAULT 0,
        last_format  TEXT,
        heard_direct INTEGER NOT NULL DEFAULT 0,  -- last packet not digipeated
        path         TEXT,
        lat          REAL,
        lon          REAL,
        pos_ts       REAL,
        symbol_table TEXT,
        symbol       TEXT,
        comment      TEXT,
        speed_kmh    REAL,
        course       INTEGER
    );
    """,
    """
    CREATE TABLE messages (
        id        INTEGER PRIMARY KEY,
        ts        REAL NOT NULL,
        direction TEXT NOT NULL,                -- in | out
        peer      TEXT NOT NULL,                -- the other station
        text      TEXT NOT NULL,
        msgno     TEXT,
        state     TEXT NOT NULL,                -- in: received; out: pending|acked|rejected|failed
        tries     INTEGER NOT NULL DEFAULT 0,
        next_try  REAL,                         -- out, pending only
        acked_ts  REAL,
        read      INTEGER NOT NULL DEFAULT 0,   -- in only
        channel   TEXT NOT NULL DEFAULT 'rf'    -- rf | is
    );
    CREATE INDEX messages_peer ON messages (peer, id);
    CREATE INDEX messages_due ON messages (next_try) WHERE state = 'pending';
    """,
    """
    -- Stations can be heard over APRS-IS too. rf_heard/rf_direct keep the RF
    -- sightings separate, so an internet packet never makes a station look local.
    ALTER TABLE stations ADD COLUMN channel TEXT NOT NULL DEFAULT 'rf';  -- of the last packet
    ALTER TABLE stations ADD COLUMN rf_heard REAL;    -- last heard on RF
    ALTER TABLE stations ADD COLUMN rf_direct REAL;   -- last heard directly (no digi) on RF
    UPDATE stations SET rf_heard = last_heard,
                        rf_direct = CASE WHEN heard_direct THEN last_heard END;
    """,
]


def _locked(method):
    """One connection is shared by the event loop and FastAPI's worker threads
    (plain ``def`` endpoints); interleaved use corrupts results, so serialize."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Store:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        # Safe with WAL (no corruption on power loss, at most the last commits
        # are lost) and far fewer fsyncs, which spares the Pi's SD card.
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    @_locked
    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(_MIGRATIONS[version:], start=version + 1):
            with self.conn:
                self.conn.executescript(sql)
                self.conn.execute(f"PRAGMA user_version = {i}")

    @_locked
    def close(self) -> None:
        self.conn.close()

    @_locked
    def load_config(self) -> Config:
        row = self.conn.execute("SELECT value FROM settings WHERE key = 'config'").fetchone()
        return Config.model_validate_json(row["value"]) if row else Config()

    @_locked
    def save_config(self, config: Config) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES ('config', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (config.model_dump_json(),),
            )

    @_locked
    def next_seq(self, key: str) -> int:
        """Increment and return a persistent counter kept in the settings table."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, '1') "
                "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
                (key,),
            )
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return int(row["value"])

    # --- packets -----------------------------------------------------------

    @_locked
    def add_packet(
        self,
        ts: float,
        raw: str,
        source: str | None = None,
        format: str | None = None,
        direction: str = "rx",
        channel: str = "rf",
    ) -> dict:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO packets (ts, source, raw, format, direction, channel) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, source, raw, format, direction, channel),
            )
        return {"id": cur.lastrowid, "ts": ts, "source": source, "raw": raw,
                "format": format, "direction": direction, "channel": channel}

    @_locked
    def recent_packets(self, limit: int = 100, before_id: int | None = None) -> list[dict]:
        """Newest first. Page backwards with before_id."""
        rows = self.conn.execute(
            "SELECT * FROM packets WHERE id < ? ORDER BY id DESC LIMIT ?",
            (before_id if before_id is not None else 2**63 - 1, limit),
        )
        return [dict(r) for r in rows]

    @_locked
    def prune_packets(self, keep: int) -> int:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM packets WHERE id <= "
                "(SELECT id FROM packets ORDER BY id DESC LIMIT 1 OFFSET ?)",
                (keep,),
            )
        return cur.rowcount

    # --- stations ----------------------------------------------------------

    @_locked
    def upsert_station(self, st: dict) -> dict:
        """Record a packet heard from a station.

        ``st`` needs name, ts, is_object, last_format, heard_direct, path and
        channel ('rf' or 'is'); the position fields (lat, lon, symbol_table,
        symbol, comment, speed_kmh, course) are optional. A packet without a
        position keeps the last one.
        """
        channel = st.get("channel", "rf")
        pos = {k: st.get(k) for k in
               ("lat", "lon", "symbol_table", "symbol", "comment", "speed_kmh", "course")}
        has_pos = pos["lat"] is not None and pos["lon"] is not None
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO stations (name, is_object, first_heard, last_heard, packet_count,
                    last_format, heard_direct, path, lat, lon, pos_ts, symbol_table, symbol,
                    comment, speed_kmh, course, channel, rf_heard, rf_direct)
                VALUES (:name, :is_object, :ts, :ts, 1, :last_format, :heard_direct, :path,
                    :lat, :lon, :pos_ts, :symbol_table, :symbol, :comment, :speed_kmh, :course,
                    :channel, :rf_heard, :rf_direct)
                ON CONFLICT(name) DO UPDATE SET
                    -- An APRS-IS copy of a station we hear on RF (another iGate
                    -- gated it) mustn't make it look like an internet station.
                    channel      = CASE WHEN :keep_rf THEN channel ELSE excluded.channel END,
                    heard_direct = CASE WHEN :keep_rf THEN heard_direct ELSE excluded.heard_direct END,
                    path         = CASE WHEN :keep_rf THEN path ELSE excluded.path END,
                    rf_heard     = COALESCE(excluded.rf_heard, rf_heard),
                    rf_direct    = COALESCE(excluded.rf_direct, rf_direct),
                    last_heard   = excluded.last_heard,
                    packet_count = packet_count + 1,
                    last_format  = excluded.last_format,
                    lat          = CASE WHEN :has_pos THEN excluded.lat ELSE lat END,
                    lon          = CASE WHEN :has_pos THEN excluded.lon ELSE lon END,
                    pos_ts       = CASE WHEN :has_pos THEN excluded.pos_ts ELSE pos_ts END,
                    symbol_table = COALESCE(excluded.symbol_table, symbol_table),
                    symbol       = COALESCE(excluded.symbol, symbol),
                    comment      = CASE WHEN :has_pos THEN excluded.comment ELSE comment END,
                    speed_kmh    = CASE WHEN :has_pos THEN excluded.speed_kmh ELSE speed_kmh END,
                    course       = CASE WHEN :has_pos THEN excluded.course ELSE course END
                """,
                {
                    "name": st["name"], "is_object": int(st.get("is_object", False)),
                    "ts": st["ts"], "last_format": st.get("last_format"),
                    "heard_direct": int(st.get("heard_direct", False)),
                    "path": st.get("path"), "pos_ts": st["ts"] if has_pos else None,
                    "has_pos": has_pos, **pos,
                    "channel": channel,
                    "keep_rf": channel == "is" and self._heard_on_rf(st["name"], st["ts"] - RF_STICKY_S),
                    "rf_heard": st["ts"] if channel == "rf" else None,
                    "rf_direct": st["ts"] if channel == "rf" and st.get("heard_direct") else None,
                },
            )
        return self.get_station(st["name"])

    @_locked
    def get_station(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM stations WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    @_locked
    def heard_on_rf(self, name: str, since: float, direct: bool = False) -> bool:
        """Was ``name`` heard on RF (directly, if ``direct``) at or after ``since``?"""
        return self._heard_on_rf(name, since, direct)

    def _heard_on_rf(self, name: str, since: float, direct: bool = False) -> bool:
        col = "rf_direct" if direct else "rf_heard"
        row = self.conn.execute(
            f"SELECT 1 FROM stations WHERE name = ? AND {col} >= ?", (name, since)).fetchone()
        return row is not None

    @_locked
    def list_stations(self, limit: int = 500) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM stations ORDER BY last_heard DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    # --- messages ----------------------------------------------------------

    _MESSAGE_FIELDS = ("ts", "direction", "peer", "text", "msgno", "state", "tries",
                       "next_try", "acked_ts", "read", "channel")

    @_locked
    def add_message(self, **fields) -> dict:
        cols = [k for k in fields if k in self._MESSAGE_FIELDS]
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO messages ({', '.join(cols)}) "
                f"VALUES ({', '.join(':' + c for c in cols)})",
                fields,
            )
        return self.get_message(cur.lastrowid)

    @_locked
    def update_message(self, id: int, **fields) -> dict:
        cols = [k for k in fields if k in self._MESSAGE_FIELDS]
        with self.conn:
            self.conn.execute(
                f"UPDATE messages SET {', '.join(f'{c} = :{c}' for c in cols)} WHERE id = :id",
                {**fields, "id": id},
            )
        return self.get_message(id)

    @_locked
    def get_message(self, id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM messages WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None

    @_locked
    def list_messages(
        self, peer: str | None = None, limit: int = 100, before_id: int | None = None
    ) -> list[dict]:
        """Newest first, optionally for one peer. Page backwards with before_id."""
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE id < ? AND (? IS NULL OR peer = ?) "
            "ORDER BY id DESC LIMIT ?",
            (before_id if before_id is not None else 2**63 - 1, peer, peer, limit),
        )
        return [dict(r) for r in rows]

    @_locked
    def due_messages(self, now: float) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE state = 'pending' AND next_try <= ? ORDER BY id",
            (now,),
        )
        return [dict(r) for r in rows]

    @_locked
    def find_pending(self, peer: str, msgno: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM messages WHERE direction = 'out' AND state = 'pending' "
            "AND peer = ? AND msgno = ? ORDER BY id DESC LIMIT 1",
            (peer, msgno),
        ).fetchone()
        return dict(row) if row else None

    @_locked
    def find_duplicate(self, peer: str, msgno: str | None, text: str, since: float) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM messages WHERE direction = 'in' AND peer = ? AND msgno IS ? "
            "AND text = ? AND ts >= ? LIMIT 1",
            (peer, msgno, text, since),
        ).fetchone()
        return row is not None

    @_locked
    def conversations(self) -> list[dict]:
        """One row per peer: last message and unread count, most recent first."""
        rows = self.conn.execute(
            """
            SELECT m.peer, m.ts, m.text, m.direction, m.state,
                   (SELECT COUNT(*) FROM messages u
                     WHERE u.peer = m.peer AND u.direction = 'in' AND u.read = 0) AS unread
            FROM messages m
            WHERE m.id = (SELECT MAX(id) FROM messages WHERE peer = m.peer)
            ORDER BY m.id DESC
            """
        )
        return [dict(r) for r in rows]

    @_locked
    def unread_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE direction = 'in' AND read = 0"
        ).fetchone()[0]

    @_locked
    def mark_read(self, peer: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE messages SET read = 1 WHERE peer = ? AND direction = 'in' AND read = 0",
                (peer,),
            )
        return cur.rowcount
