"""SQLite store of view-count snapshots.

The public YouTube API has no view history. Every analysis of a real video
records a snapshot here; after a few readings the live-tracking model can fit
the video's own trajectory. ``scripts/poll_snapshots.py`` adds readings on a
schedule so history builds up without anyone opening the app.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from typing import List, Optional, Tuple

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    video_id TEXT NOT NULL,
    ts       REAL NOT NULL,          -- unix seconds
    views    INTEGER NOT NULL,
    likes    INTEGER,
    comments INTEGER,
    PRIMARY KEY (video_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots (ts);
"""


class SnapshotStore:
    def __init__(self, path: str) -> None:
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def record(
        self,
        video_id: str,
        ts: float,
        views: int,
        likes: Optional[int],
        comments: Optional[int],
        min_gap_s: float = 600.0,
    ) -> bool:
        """Store a reading unless one was taken less than ``min_gap_s`` earlier."""
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT MAX(ts) FROM snapshots WHERE video_id = ?", (video_id,)
            ).fetchone()
            if row and row[0] is not None and abs(ts - row[0]) < min_gap_s:
                return False
            conn.execute(
                "INSERT OR IGNORE INTO snapshots (video_id, ts, views, likes, comments) VALUES (?, ?, ?, ?, ?)",
                (video_id, ts, int(views), likes, comments),
            )
        return True

    def history(self, video_id: str, limit: int = 500) -> List[Tuple[float, int, Optional[int], Optional[int]]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT ts, views, likes, comments FROM snapshots WHERE video_id = ? ORDER BY ts DESC LIMIT ?",
                (video_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def tracked_ids(self, within_days: float, now: float) -> List[str]:
        """Videos first recorded within the last ``within_days``: what the poller keeps refreshing."""
        cutoff = now - within_days * 86400.0
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT video_id FROM snapshots GROUP BY video_id HAVING MIN(ts) >= ? ORDER BY video_id",
                (cutoff,),
            ).fetchall()
        return [r[0] for r in rows]

    def prune(self, older_than_days: float, now: float) -> int:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute("DELETE FROM snapshots WHERE ts < ?", (now - older_than_days * 86400.0,))
            return cur.rowcount
