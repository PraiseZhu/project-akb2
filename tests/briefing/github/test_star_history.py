"""Tests for star_history.py — snapshot recording + velocity_7d computation."""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from akb2.briefing.github.star_history import StarHistory


class TestStarHistory:
    def test_record_and_count(self):
        sh = StarHistory(":memory:")
        sh.record("https://github.com/owner/repo", 100)
        sh.record("https://github.com/owner/repo", 110)
        assert sh.count("https://github.com/owner/repo") == 2
        sh.close()

    def test_velocity_7d_none_with_one_snapshot(self):
        sh = StarHistory(":memory:")
        sh.record("https://github.com/owner/repo", 100)
        assert sh.velocity_7d("https://github.com/owner/repo") is None
        sh.close()

    def test_velocity_7d_none_with_short_span(self):
        """Two snapshots too close together (<6 days) → None."""
        sh = StarHistory(":memory:")
        sh.record("https://github.com/owner/repo", 100)
        # Same day or 1 day apart → span < 6 days → None
        sh.record("https://github.com/owner/repo", 105)
        assert sh.velocity_7d("https://github.com/owner/repo") is None
        sh.close()

    def test_velocity_7d_positive_growth(self):
        """≥7 day span → return delta."""
        sh = StarHistory(":memory:")
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(days=8)).isoformat()

        # Insert old snapshot directly to simulate 8-day span
        sh._conn.execute(
            "INSERT INTO star_history (repo_url, stars, recorded_at) VALUES (?, ?, ?)",
            ("https://github.com/owner/repo", 100, old_time),
        )
        sh.record("https://github.com/owner/repo", 150)
        sh._conn.commit()

        velocity = sh.velocity_7d("https://github.com/owner/repo")
        assert velocity == 50  # 150 - 100
        sh.close()

    def test_velocity_7d_zero_growth(self):
        """Stars unchanged over 7+ days → 0 delta."""
        sh = StarHistory(":memory:")
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(days=7)).isoformat()

        sh._conn.execute(
            "INSERT INTO star_history (repo_url, stars, recorded_at) VALUES (?, ?, ?)",
            ("https://github.com/owner/repo", 200, old_time),
        )
        sh.record("https://github.com/owner/repo", 200)
        sh._conn.commit()

        velocity = sh.velocity_7d("https://github.com/owner/repo")
        assert velocity == 0
        sh.close()

    def test_velocity_7d_picks_oldest_sufficient_span(self):
        """With multiple old snapshots, pick the one >=6 days back."""
        sh = StarHistory(":memory:")
        now = datetime.now(timezone.utc)

        # 10 days ago
        t1 = (now - timedelta(days=10)).isoformat()
        # 5 days ago
        t2 = (now - timedelta(days=5)).isoformat()
        # today
        t3 = now.isoformat()

        sh._conn.execute(
            "INSERT INTO star_history (repo_url, stars, recorded_at) VALUES (?, ?, ?)",
            ("https://github.com/owner/repo", 100, t1),
        )
        sh._conn.execute(
            "INSERT INTO star_history (repo_url, stars, recorded_at) VALUES (?, ?, ?)",
            ("https://github.com/owner/repo", 115, t2),
        )
        sh._conn.execute(
            "INSERT INTO star_history (repo_url, stars, recorded_at) VALUES (?, ?, ?)",
            ("https://github.com/owner/repo", 120, t3),
        )
        sh._conn.commit()

        # Should use t1 (10d span ≥ 6d), not t2 (5d span)
        velocity = sh.velocity_7d("https://github.com/owner/repo")
        assert velocity == 20  # 120 - 100
        sh.close()

    def test_count_all_repos(self):
        sh = StarHistory(":memory:")
        sh.record("https://github.com/a/b", 10)
        sh.record("https://github.com/c/d", 20)
        assert sh.count() == 2
        sh.close()

    def test_record_and_close_idempotent(self):
        sh = StarHistory(":memory:")
        sh.record("https://github.com/owner/repo", 100)
        sh.close()
        # Should not raise on double-close
        sh.close()