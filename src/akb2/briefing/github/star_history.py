"""Star-history SQLite store for computing 7-day star velocity.

Modeled after dedup_sqlite.py — same lightweight sqlite3 pattern.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class StarHistory:
    """Daily star-count snapshots. Supports velocity_7d() for scoring."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS star_history ("
            "  repo_url TEXT NOT NULL,"
            "  stars INTEGER NOT NULL,"
            "  recorded_at TEXT NOT NULL"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_star_repo_time "
            "ON star_history(repo_url, recorded_at)"
        )
        self._conn.commit()

    def record(self, repo_url: str, stars: int) -> None:
        """Record a star-count snapshot at the current time."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO star_history (repo_url, stars, recorded_at) VALUES (?, ?, ?)",
            (repo_url, stars, now),
        )
        self._conn.commit()

    def velocity_7d(self, repo_url: str) -> int | None:
        """Return star delta between latest snapshot and >=7 days ago.

        Returns None when:
        - Fewer than 2 snapshots exist for this repo
        - The oldest snapshot is less than 6 days old (span too short)
        """
        rows = self._conn.execute(
            "SELECT stars, recorded_at FROM star_history "
            "WHERE repo_url = ? "
            "ORDER BY recorded_at DESC",
            (repo_url,),
        ).fetchall()

        if len(rows) < 2:
            return None

        latest = rows[0]
        # Find the most recent snapshot >= 6 days before latest
        latest_time = datetime.fromisoformat(latest[1])
        for row in rows[1:]:
            row_time = datetime.fromisoformat(row[1])
            span_days = (latest_time - row_time).days
            if span_days >= 6:
                return latest[0] - row[0]

        return None  # span too short

    def count(self, repo_url: str | None = None) -> int:
        """Return snapshot count, optionally filtered by repo_url."""
        if repo_url:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM star_history WHERE repo_url = ?",
                (repo_url,),
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM star_history").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        self._conn.close()