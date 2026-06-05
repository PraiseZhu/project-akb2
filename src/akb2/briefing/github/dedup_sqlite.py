"""SQLite 3-day rolling dedup for GitHub URLs seen in curator tweets."""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class DedupStore:
    """3-day rolling dedup. URLs older than 3 days are pruned on insert."""

    def __init__(self, db_path: str | Path = ":memory:"):
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
        """Check if a normalized URL was seen in the last 3 days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        row = self._conn.execute(
            "SELECT 1 FROM seen_urls WHERE normalized_url = ? AND seen_at >= ?",
            (normalized_url, cutoff),
        ).fetchone()
        return row is not None

    def mark_seen(self, normalized_url: str) -> None:
        """Record a URL as seen now."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO seen_urls (normalized_url, seen_at) VALUES (?, ?)",
            (normalized_url, now),
        )
        self._conn.commit()

    def prune(self) -> int:
        """Delete entries older than 3 days. Returns count of removed rows."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
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