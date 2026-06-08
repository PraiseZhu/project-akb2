"""SQLite rolling dedup for GitHub URLs seen in curator tweets.

Default window is 3 days; override via AKB2_NOVELTY_WINDOW_DAYS env var
or by passing window_days to the constructor (SC-4).
"""

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

_DEFAULT_WINDOW_DAYS = int(os.environ.get("AKB2_NOVELTY_WINDOW_DAYS", "3"))


class DedupStore:
    """Rolling dedup. URLs older than window_days are pruned on insert."""

    def __init__(self, db_path: str | Path = ":memory:",
                 window_days: int | None = None):
        self._window_days = window_days if window_days is not None else _DEFAULT_WINDOW_DAYS
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS seen_urls ("
            "  normalized_url TEXT PRIMARY KEY,"
            "  seen_at TEXT NOT NULL"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_at ON seen_urls(seen_at)"
        )
        self._conn.commit()

    def is_duplicate(self, normalized_url: str) -> bool:
        """Check if a normalized URL was seen within the rolling window."""
        cutoff = (datetime.now(UTC) - timedelta(days=self._window_days)).isoformat()
        row = self._conn.execute(
            "SELECT 1 FROM seen_urls WHERE normalized_url = ? AND seen_at >= ?",
            (normalized_url, cutoff),
        ).fetchone()
        return row is not None

    def mark_seen(self, normalized_url: str) -> None:
        """Record a URL as seen now."""
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO seen_urls (normalized_url, seen_at) VALUES (?, ?)",
            (normalized_url, now),
        )
        self._conn.commit()

    def prune(self) -> int:
        """Delete entries older than the rolling window. Returns count of removed rows."""
        cutoff = (datetime.now(UTC) - timedelta(days=self._window_days)).isoformat()
        cur = self._conn.execute(
            "DELETE FROM seen_urls WHERE seen_at < ?", (cutoff,)
        )
        self._conn.commit()
        return cur.rowcount

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM seen_urls").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        self._conn.close()
