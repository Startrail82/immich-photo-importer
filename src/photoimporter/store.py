"""Lokale datastore (SQLite): runs, logregels, 'aandacht nodig' en favorieten voor Immich."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT NOT NULL,
    finished TEXT,
    trigger TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'bezig',
    stats TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    step TEXT NOT NULL,
    message TEXT NOT NULL,
    path TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    detail TEXT,
    resolved INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_review_open ON review(resolved, kind);
CREATE TABLE IF NOT EXISTS pending_favorites (
    path TEXT PRIMARY KEY,
    created TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    done_ts TEXT
);
CREATE TABLE IF NOT EXISTS imported (
    sha256 TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    size INTEGER NOT NULL,
    target TEXT NOT NULL,
    source TEXT,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fav_seen (
    asset_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    ts TEXT NOT NULL
);
"""

_FMT = "%Y-%m-%d %H:%M:%S"


def _now() -> str:
    return datetime.now().strftime(_FMT)


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
        try:  # de database bevat (indien ingesteld) het iCloud-wachtwoord en de Immich-sleutel
            os.chmod(db_path, 0o600)
        except OSError:
            pass  # bv. een bestandssysteem zonder rechten; het volume blijft privé

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- runs -----------------------------------------------------------
    def start_run(self, trigger: str, dry_run: bool) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runs (started, trigger, dry_run) VALUES (?, ?, ?)",
                (_now(), trigger, int(dry_run)),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, stats_json: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET finished = ?, status = ?, stats = ? WHERE id = ?",
                (_now(), status, stats_json, run_id),
            )

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()

    def list_runs(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
            )

    # --- events ---------------------------------------------------------
    def add_event(self, run_id: int, level: str, step: str, message: str, path: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (run_id, ts, level, step, message, path) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, _now(), level, step, message, path),
            )

    def list_events(self, run_id: int, after_id: int = 0, limit: int = 1000) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._conn.execute(
                    "SELECT * FROM events WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
                    (run_id, after_id, limit),
                )
            )

    # --- aandacht nodig -------------------------------------------------
    def add_review(self, run_id: int, kind: str, path: str, detail: str | None) -> None:
        """Voegt een item toe, tenzij er al een openstaand item met dezelfde soort + pad is."""
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM review WHERE resolved = 0 AND kind = ? AND path = ?",
                (kind, path),
            ).fetchone()
            if exists:
                return
            self._conn.execute(
                "INSERT INTO review (run_id, ts, kind, path, detail) VALUES (?, ?, ?, ?, ?)",
                (run_id, _now(), kind, path, detail),
            )

    def list_review(self, only_open: bool = True) -> list[sqlite3.Row]:
        query = "SELECT * FROM review"
        if only_open:
            query += " WHERE resolved = 0"
        query += " ORDER BY id DESC"
        with self._lock:
            return list(self._conn.execute(query))

    def resolve_review(self, review_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE review SET resolved = 1 WHERE id = ?", (review_id,))

    # --- favorieten voor Immich ----------------------------------------
    def add_pending_favorite(self, path: str) -> None:
        """Onthoudt dat dit bestand in Immich als favoriet gemarkeerd moet worden.

        INSERT OR IGNORE: een foto die al eens verwerkt is (done=1) wordt niet opnieuw
        favoriet gemaakt, ook niet als je hem later in Immich zelf van favoriet haalt.
        """
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO pending_favorites (path, created) VALUES (?, ?)",
                (path, _now()),
            )

    def list_pending_favorites(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT path FROM pending_favorites WHERE done = 0 ORDER BY created, path"
            )
            return [r["path"] for r in rows]

    def mark_favorite_done(self, path: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pending_favorites SET done = 1, done_ts = ? WHERE path = ?",
                (_now(), path),
            )

    # --- Immich-assets met rating 5 die al beoordeeld zijn --------------
    def fav_seen_ids(self) -> set[str]:
        with self._lock:
            return {r["asset_id"] for r in self._conn.execute("SELECT asset_id FROM fav_seen")}

    def add_fav_seen(self, ids: list[str]) -> None:
        now = _now()
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO fav_seen (asset_id, ts) VALUES (?, ?)", [(i, now) for i in ids]
            )

    # --- losse toestandswaarden (bv. iCloud wacht op een code) ----------
    # --- administratie van binnengehaalde bestanden ----------------------------
    def get_imported(self, sha256: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM imported WHERE sha256 = ?", (sha256,)).fetchone()

    def add_imported(self, sha256: str, name: str, size: int, target: str, source: str | None) -> None:
        """Legt vast dat een bestand met deze hash (zoals het binnenkwam) is verwerkt."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO imported (sha256, name, size, target, source, created) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(sha256) DO UPDATE SET target = excluded.target",
                (sha256, name, size, target, source, _now()),
            )

    def count_imported(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM imported").fetchone()[0]

    def has_successful_run(self) -> bool:
        """Is er al eens een echte run gelukt? (Dan is een eerste volledige synchronisatie niet meer nodig.)"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM runs WHERE dry_run = 0 AND status IN ('ok', 'ok_met_fouten') LIMIT 1"
            ).fetchone()
        return row is not None

    def set_state(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO state(key, value, ts) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, ts = excluded.ts",
                (key, value, _now()),
            )

    def get_state(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    # --- opruimen -------------------------------------------------------
    def prune(self, retention_days: int) -> dict[str, int]:
        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime(_FMT)
        with self._lock:
            events = self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,)).rowcount
            review = self._conn.execute(
                "DELETE FROM review WHERE resolved = 1 AND ts < ?", (cutoff,)
            ).rowcount
            runs = self._conn.execute(
                "DELETE FROM runs WHERE started < ? AND id NOT IN (SELECT DISTINCT run_id FROM review WHERE resolved = 0)",
                (cutoff,),
            ).rowcount
            self._conn.execute("DELETE FROM pending_favorites WHERE done = 1 AND done_ts < ?", (cutoff,))
        return {"events": events, "review": review, "runs": runs}
