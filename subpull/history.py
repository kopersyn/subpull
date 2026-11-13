"""Download and operation history – the program's own SQLite database (never inside frame folders)."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object TEXT NOT NULL,
    session_date TEXT,
    filename TEXT NOT NULL,
    frame_type TEXT,
    remote_path TEXT NOT NULL,
    local_path TEXT NOT NULL,
    size INTEGER,
    remote_mtime INTEGER,
    sha256 TEXT,
    verified TEXT,
    downloaded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_downloads_remote ON downloads(remote_path);
CREATE INDEX IF NOT EXISTS ix_downloads_local ON downloads(local_path);
CREATE TABLE IF NOT EXISTS partials (
    part_path TEXT PRIMARY KEY,
    remote_path TEXT NOT NULL,
    remote_size INTEGER,
    remote_mtime INTEGER,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL
);
"""


class History:
    def __init__(self, path: Path = config.HISTORY_DB):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------------------------------------------------------------- downloads
    def record_download(self, *, object: str, session_date: str, filename: str, frame_type: Optional[str],
                        remote_path: str, local_path: str, size: int, remote_mtime: int,
                        sha256: Optional[str], verified: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO downloads(object, session_date, filename, frame_type, remote_path, local_path, size,"
                " remote_mtime, sha256, verified, downloaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (object, session_date, filename, frame_type, remote_path, local_path, size, remote_mtime,
                 sha256, verified, time.time()))
            self._db.commit()

    def downloaded_remote_paths(self) -> dict[str, sqlite3.Row]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM downloads ORDER BY downloaded_at").fetchall()
        return {r["remote_path"]: r for r in rows}

    def list_downloads(self, limit: int = 2000) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute("SELECT * FROM downloads ORDER BY downloaded_at DESC LIMIT ?", (limit,)).fetchall()

    # ---------------------------------------------------------------- partial files
    def set_partial(self, part_path: str, remote_path: str, size: int, mtime: int) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO partials VALUES (?,?,?,?,?)",
                             (part_path, remote_path, size, mtime, time.time()))
            self._db.commit()

    def get_partial(self, part_path: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._db.execute("SELECT * FROM partials WHERE part_path=?", (part_path,)).fetchone()

    def clear_partial(self, part_path: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM partials WHERE part_path=?", (part_path,))
            self._db.commit()

    # ---------------------------------------------------------------- operations
    def log_operation(self, kind: str, summary: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO operations(ts, kind, summary) VALUES (?,?,?)", (time.time(), kind, summary))
            self._db.commit()

    def list_operations(self, limit: int = 500) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute("SELECT * FROM operations ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()

    def last_operation(self, kind: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._db.execute("SELECT * FROM operations WHERE kind=? ORDER BY ts DESC LIMIT 1", (kind,)).fetchone()
