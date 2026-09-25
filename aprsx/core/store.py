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
    """
    -- MeshCore (see mesh.py). Nodes are keyed by their full public key (hex).
    CREATE TABLE mesh_nodes (
        pubkey      TEXT PRIMARY KEY,
        name        TEXT,
        type        INTEGER,                    -- 1 companion, 2 repeater, 3 room, 4 sensor
        lat         REAL,
        lon         REAL,
        pos_ts      REAL,
        snr         REAL,                       -- of the last advert heard
        rssi        INTEGER,
        hops        INTEGER,
        first_heard REAL NOT NULL,
        last_heard  REAL NOT NULL,
        is_contact  INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE mesh_messages (
        id           INTEGER PRIMARY KEY,
        ts           REAL NOT NULL,
        direction    TEXT NOT NULL,             -- in | out
        conv         TEXT NOT NULL,             -- 'dm:<pubkey prefix>' | 'ch:<channel index>'
        sender       TEXT,                      -- channel messages: the sender's name
        text         TEXT NOT NULL,
        state        TEXT NOT NULL,             -- in: received; out: pending|sent|acked|failed
        attempts     INTEGER NOT NULL DEFAULT 0,
        next_try     REAL,                      -- out DMs, pending only
        expected_ack TEXT,
        snr          REAL,
        hops         INTEGER,
        read         INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX mesh_messages_conv ON mesh_messages (conv, id);
    CREATE TABLE wd_sessions (
        id      INTEGER PRIMARY KEY,
        started REAL NOT NULL,
        ended   REAL
    );
    CREATE TABLE wd_pings (
        id         INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES wd_sessions (id),
        ts         REAL NOT NULL,
        kind       TEXT NOT NULL,               -- discover | chan | advert
        lat        REAL NOT NULL,
        lon        REAL NOT NULL,
        tag        TEXT,                        -- discover tag / channel message timestamp
        heard      INTEGER NOT NULL DEFAULT 0,  -- responses (discover) or echoes (chan)
        best_snr   REAL
    );
    CREATE INDEX wd_pings_session ON wd_pings (session_id, id);
    CREATE TABLE wd_obs (
        id         INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES wd_sessions (id),
        ping_id    INTEGER REFERENCES wd_pings (id),
        ts         REAL NOT NULL,
        kind       TEXT NOT NULL,               -- discover | echo | rx
        node       TEXT,                        -- public key or its prefix (hex), if known
        node_type  INTEGER,
        snr        REAL,                        -- how we heard it
        rssi       INTEGER,
        remote_snr REAL,                        -- discover: how the node heard us
        hops       INTEGER,
        my_lat     REAL NOT NULL,
        my_lon     REAL NOT NULL
    );
    CREATE INDEX wd_obs_session ON wd_obs (session_id, id);
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

    # --- MeshCore nodes ----------------------------------------------------

    _NODE_FIELDS = ("name", "type", "lat", "lon", "snr", "rssi", "hops", "is_contact")

    @_locked
    def upsert_mesh_node(self, pubkey: str, ts: float, **fields) -> dict:
        """Record a node heard (an advert) or synced from the device's contacts.
        Fields left out, or None, keep their stored values."""
        fields = {k: v for k, v in fields.items() if k in self._NODE_FIELDS and v is not None}
        has_pos = "lat" in fields and "lon" in fields
        row = {**dict.fromkeys(self._NODE_FIELDS), "is_contact": 0, **fields,
               "pubkey": pubkey, "ts": ts, "pos_ts": ts if has_pos else None}
        sets = ", ".join(f"{k} = excluded.{k}" for k in fields)
        with self.conn:
            self.conn.execute(
                "INSERT INTO mesh_nodes (pubkey, name, type, lat, lon, pos_ts, snr, rssi, hops, "
                "first_heard, last_heard, is_contact) VALUES (:pubkey, :name, :type, :lat, :lon, "
                ":pos_ts, :snr, :rssi, :hops, :ts, :ts, :is_contact) "
                "ON CONFLICT(pubkey) DO UPDATE SET last_heard = MAX(last_heard, excluded.last_heard)"
                + (", pos_ts = excluded.pos_ts" if has_pos else "") + (", " + sets if sets else ""),
                row,
            )
        return self.get_mesh_node(pubkey)

    @_locked
    def get_mesh_node(self, pubkey: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM mesh_nodes WHERE pubkey = ?", (pubkey,)).fetchone()
        return dict(row) if row else None

    @_locked
    def find_mesh_node(self, prefix: str) -> dict | None:
        """The most recently heard node whose key starts with ``prefix`` (hex).
        Short prefixes (a 1-byte path hash) can match several nodes."""
        if not prefix:
            return None
        row = self.conn.execute(
            "SELECT * FROM mesh_nodes WHERE substr(pubkey, 1, ?) = ? "
            "ORDER BY type = 2 DESC, last_heard DESC LIMIT 1",
            (len(prefix), prefix.lower()),
        ).fetchone()
        return dict(row) if row else None

    @_locked
    def list_mesh_nodes(self, limit: int = 500) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM mesh_nodes ORDER BY last_heard DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # --- MeshCore messages -------------------------------------------------

    _MESH_MESSAGE_FIELDS = ("ts", "direction", "conv", "sender", "text", "state", "attempts",
                            "next_try", "expected_ack", "snr", "hops", "read")

    @_locked
    def add_mesh_message(self, **fields) -> dict:
        cols = [k for k in fields if k in self._MESH_MESSAGE_FIELDS]
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO mesh_messages ({', '.join(cols)}) "
                f"VALUES ({', '.join(':' + c for c in cols)})",
                fields,
            )
        return self.get_mesh_message(cur.lastrowid)

    @_locked
    def update_mesh_message(self, id: int, **fields) -> dict:
        cols = [k for k in fields if k in self._MESH_MESSAGE_FIELDS]
        with self.conn:
            self.conn.execute(
                f"UPDATE mesh_messages SET {', '.join(f'{c} = :{c}' for c in cols)} WHERE id = :id",
                {**fields, "id": id},
            )
        return self.get_mesh_message(id)

    @_locked
    def get_mesh_message(self, id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM mesh_messages WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None

    @_locked
    def list_mesh_messages(self, conv: str | None = None, limit: int = 100,
                           before_id: int | None = None) -> list[dict]:
        """Newest first, optionally for one conversation. Page backwards with before_id."""
        rows = self.conn.execute(
            "SELECT * FROM mesh_messages WHERE id < ? AND (? IS NULL OR conv = ?) "
            "ORDER BY id DESC LIMIT ?",
            (before_id if before_id is not None else 2**63 - 1, conv, conv, limit),
        )
        return [dict(r) for r in rows]

    @_locked
    def due_mesh_messages(self, now: float) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM mesh_messages WHERE state = 'pending' AND next_try <= ? ORDER BY id",
            (now,),
        )
        return [dict(r) for r in rows]

    @_locked
    def find_mesh_by_ack(self, code: str) -> dict | None:
        row = self.conn.execute(
            # expected_ack holds the code of every try, space-separated.
            "SELECT * FROM mesh_messages WHERE state = 'pending' "
            "AND ' ' || expected_ack || ' ' LIKE '% ' || ? || ' %' "
            "ORDER BY id DESC LIMIT 1", (code,),
        ).fetchone()
        return dict(row) if row else None

    @_locked
    def mesh_conversations(self) -> list[dict]:
        """One row per conversation: last message and unread count, most recent first."""
        rows = self.conn.execute(
            """
            SELECT m.conv, m.ts, m.text, m.sender, m.direction, m.state,
                   (SELECT COUNT(*) FROM mesh_messages u
                     WHERE u.conv = m.conv AND u.direction = 'in' AND u.read = 0) AS unread
            FROM mesh_messages m
            WHERE m.id = (SELECT MAX(id) FROM mesh_messages WHERE conv = m.conv)
            ORDER BY m.id DESC
            """
        )
        return [dict(r) for r in rows]

    @_locked
    def mesh_unread_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM mesh_messages WHERE direction = 'in' AND read = 0"
        ).fetchone()[0]

    @_locked
    def mark_mesh_read(self, conv: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE mesh_messages SET read = 1 WHERE conv = ? AND direction = 'in' AND read = 0",
                (conv,),
            )
        return cur.rowcount

    # --- war-driving -------------------------------------------------------

    @_locked
    def start_wd_session(self, ts: float) -> int:
        with self.conn:
            return self.conn.execute(
                "INSERT INTO wd_sessions (started) VALUES (?)", (ts,)).lastrowid

    @_locked
    def end_wd_session(self, id: int, ts: float) -> None:
        with self.conn:
            self.conn.execute("UPDATE wd_sessions SET ended = ? WHERE id = ?", (ts, id))

    @_locked
    def end_open_wd_sessions(self, ts: float) -> None:
        """Close sessions left open by a restart."""
        with self.conn:
            self.conn.execute(
                "UPDATE wd_sessions SET ended = COALESCE("
                "(SELECT MAX(ts) FROM wd_pings WHERE session_id = wd_sessions.id), started) "
                "WHERE ended IS NULL")

    @_locked
    def add_wd_ping(self, session_id: int, ts: float, kind: str, lat: float, lon: float,
                    tag: str | None = None) -> dict:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO wd_pings (session_id, ts, kind, lat, lon, tag) "
                "VALUES (?, ?, ?, ?, ?, ?)", (session_id, ts, kind, lat, lon, tag))
        return self._wd_ping(cur.lastrowid)

    @_locked
    def count_wd_response(self, ping_id: int, snr: float | None) -> dict:
        """One more response to a ping; keeps the best SNR."""
        with self.conn:
            self.conn.execute(
                "UPDATE wd_pings SET heard = heard + 1, best_snr = CASE "
                "WHEN ? IS NULL THEN best_snr WHEN best_snr IS NULL OR ? > best_snr THEN ? "
                "ELSE best_snr END WHERE id = ?", (snr, snr, snr, ping_id))
        return self._wd_ping(ping_id)

    @_locked
    def get_wd_ping(self, id: int) -> dict:
        return self._wd_ping(id)

    def _wd_ping(self, id: int) -> dict:
        return dict(self.conn.execute("SELECT * FROM wd_pings WHERE id = ?", (id,)).fetchone())

    _WD_OBS_FIELDS = ("session_id", "ping_id", "ts", "kind", "node", "node_type", "snr", "rssi",
                      "remote_snr", "hops", "my_lat", "my_lon")

    @_locked
    def add_wd_obs(self, **fields) -> dict:
        cols = [k for k in fields if k in self._WD_OBS_FIELDS]
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO wd_obs ({', '.join(cols)}) "
                f"VALUES ({', '.join(':' + c for c in cols)})",
                fields,
            )
        return dict(self.conn.execute(
            "SELECT * FROM wd_obs WHERE id = ?", (cur.lastrowid,)).fetchone())

    @_locked
    def list_wd_sessions(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT s.*,
                   (SELECT COUNT(*) FROM wd_pings p WHERE p.session_id = s.id) AS pings,
                   (SELECT COUNT(*) FROM wd_pings p WHERE p.session_id = s.id AND p.heard > 0)
                       AS pings_heard,
                   -- Echoes name a repeater by its first key byte only, so count by that.
                   (SELECT COUNT(DISTINCT substr(node, 1, 2)) FROM wd_obs o
                     WHERE o.session_id = s.id AND o.kind != 'rx' AND o.node IS NOT NULL)
                       AS nodes
            FROM wd_sessions s ORDER BY s.id DESC LIMIT ?
            """, (limit,))
        return [dict(r) for r in rows]

    @_locked
    def wd_session(self, id: int) -> dict | None:
        """A session with all its pings and observations, oldest first."""
        row = self.conn.execute("SELECT * FROM wd_sessions WHERE id = ?", (id,)).fetchone()
        if row is None:
            return None
        pings = self.conn.execute(
            "SELECT * FROM wd_pings WHERE session_id = ? ORDER BY id", (id,))
        obs = self.conn.execute(
            "SELECT * FROM wd_obs WHERE session_id = ? ORDER BY id", (id,))
        return {**dict(row), "pings": [dict(r) for r in pings], "obs": [dict(r) for r in obs]}

    @_locked
    def wd_coverage(self, session_id: int | None = None, limit: int = 20000) -> dict:
        """The newest pings (all sessions, or one) and the answers to them."""
        pings = [dict(r) for r in self.conn.execute(
            "SELECT * FROM wd_pings WHERE (? IS NULL OR session_id = ?) AND kind != 'advert' "
            "ORDER BY id DESC LIMIT ?", (session_id, session_id, limit))]
        obs = [dict(r) for r in self.conn.execute(
            "SELECT * FROM wd_obs WHERE ping_id >= ? AND (? IS NULL OR session_id = ?) "
            "AND kind != 'rx' ORDER BY id",
            (pings[-1]["id"] if pings else 0, session_id, session_id))]
        return {"pings": pings[::-1], "obs": obs}
