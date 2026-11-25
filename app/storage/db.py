"""SQLite storage. One file: data/copyright-radar.db. Schema: SPEC section 5."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS secret (name TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS media (
  id TEXT PRIMARY KEY,                -- Instagram media id, or "export:<file>" for export-only items
  shortcode TEXT, permalink TEXT, kind TEXT NOT NULL DEFAULT 'video',   -- video | photo
  media_product_type TEXT, timestamp TEXT, caption_excerpt TEXT,
  in_api INTEGER NOT NULL DEFAULT 0,  -- seen in the Graph API media list at least once
  thumb_path TEXT, thumb_url TEXT, media_url TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presence (
  media_id TEXT PRIMARY KEY REFERENCES media(id),
  state TEXT NOT NULL,                -- in_feed | missing | archived_manual | archived_export
  since TEXT NOT NULL, sync_run_id INTEGER
);
CREATE TABLE IF NOT EXISTS sync_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
  fetched_count INTEGER, complete INTEGER, outcome TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS meta_check (
  media_id TEXT NOT NULL, status TEXT, matches_json TEXT, raw_json TEXT, fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audio_source (
  media_id TEXT PRIMARY KEY, kind TEXT NOT NULL, file_path TEXT NOT NULL,
  duration_s REAL, has_audio_stream INTEGER
);
CREATE TABLE IF NOT EXISTS recognition (
  media_id TEXT NOT NULL, provider TEXT NOT NULL, segment_start_s REAL, status TEXT NOT NULL,
  title TEXT, artists TEXT, label TEXT, isrc TEXT, score INTEGER, raw_json TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assessment (
  media_id TEXT NOT NULL, level TEXT NOT NULL, automatic_level TEXT NOT NULL,
  reasons_json TEXT NOT NULL, rules_version TEXT, assessed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_review (
  media_id TEXT PRIMARY KEY, verdict TEXT NOT NULL, note TEXT, reviewed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (at TEXT NOT NULL, action TEXT NOT NULL, details TEXT);
CREATE INDEX IF NOT EXISTS ix_meta_media ON meta_check(media_id);
CREATE INDEX IF NOT EXISTS ix_rec_media ON recognition(media_id);
CREATE INDEX IF NOT EXISTS ix_assess_media ON assessment(media_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DB:
    """Thin wrapper; one connection guarded by a lock (the app is single-user)."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL") if str(path) != ":memory:" else None
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    def tx(self):
        """Context manager for a transaction: `with db.tx() as c: c.execute(...)`."""
        db = self

        class _Tx:
            def __enter__(self_inner):
                db.lock.acquire()
                return db.conn

            def __exit__(self_inner, exc_type, *_):
                try:
                    if exc_type:
                        db.conn.rollback()
                    else:
                        db.conn.commit()
                finally:
                    db.lock.release()

        return _Tx()

    # key-value settings
    def get(self, key: str, default=None):
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value) -> None:
        self.x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value)))

    def audit(self, action: str, details: dict | None = None) -> None:
        self.x("INSERT INTO audit_log(at,action,details) VALUES(?,?,?)", (now(), action, json.dumps(details or {})))
