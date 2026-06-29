"""Tests for SQLite 3-day rolling dedup."""



from akb2.briefing.github.dedup_sqlite import DedupStore


class TestDedupStore:
    def test_not_duplicate_on_first_see(self):
        store = DedupStore()
        assert not store.is_duplicate("https://github.com/owner/repo")
        store.close()

    def test_duplicate_after_mark_seen(self):
        store = DedupStore()
        store.mark_seen("https://github.com/owner/repo")
        assert store.is_duplicate("https://github.com/owner/repo")
        store.close()

    def test_different_urls_not_duplicate(self):
        store = DedupStore()
        store.mark_seen("https://github.com/owner/repo1")
        assert not store.is_duplicate("https://github.com/owner/repo2")
        store.close()

    def test_count_reflects_inserts(self):
        store = DedupStore()
        assert store.count() == 0
        store.mark_seen("https://github.com/a/b")
        store.mark_seen("https://github.com/c/d")
        assert store.count() == 2
        store.close()

    def test_mark_seen_updates_timestamp(self):
        store = DedupStore()
        store.mark_seen("https://github.com/owner/repo")
        # Mark again — should replace
        store.mark_seen("https://github.com/owner/repo")
        assert store.count() == 1
        store.close()

    def test_prune_removes_old_entries(self):
        """Prune removes entries older than 3 days by manipulating seen_at."""
        store = DedupStore()
        # Use raw SQL to backdate an entry
        store.mark_seen("https://github.com/owner/old")
        store._conn.execute(
            "UPDATE seen_urls SET seen_at = ? WHERE normalized_url = ?",
            ("2020-01-01T00:00:00+00:00", "https://github.com/owner/old"),
        )
        store._conn.commit()
        removed = store.prune()
        assert removed >= 1
        assert store.count() == 0
        store.close()

    def test_persistence_across_connections(self, tmp_path):
        """Data survives close + reopen."""
        db_path = tmp_path / "dedup.db"
        store1 = DedupStore(db_path)
        store1.mark_seen("https://github.com/owner/repo")
        store1.close()

        store2 = DedupStore(db_path)
        assert store2.is_duplicate("https://github.com/owner/repo")
        assert store2.count() == 1
        store2.close()

    def test_case_sensitive_dedup(self):
        """Normalized URLs are case-sensitive — caller must lowercase first."""
        store = DedupStore()
        store.mark_seen("https://github.com/owner/repo")
        # Uppercase variant is different key
        assert not store.is_duplicate("https://github.com/Owner/Repo")
        store.close()
