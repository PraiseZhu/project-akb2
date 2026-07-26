"""缺陷 #3 回归：去重窗口 ≥ 采集窗口 + 无新提及不复推。

实证（2026-06-10 → 07-26 简报）：跨日重复 179/470 = 38.1%，间隔恰好 4 天占
124/179 = 69.3%——旧去重窗 3 天 < 采集窗 7 天，条目出去重窗仍在采集窗内，
第 4 天必然复活。剩余 >7 天的重复中 128/179 = 71.5% 的 first_mention_at 与
上次逐字相同（同一条旧推文被反复当新事件触发）。
"""

from datetime import UTC, datetime, timedelta

from akb2.briefing.github.dedup_sqlite import (
    DEDUP_WINDOW_DAYS,
    MENTION_MEMORY_DAYS,
    DedupStore,
    _floor_window,
)
from akb2.briefing.github.intake.twitter_collector import RECENCY_WINDOW_DAYS


def _backdate(store: DedupStore, url: str, days: float) -> None:
    """把某条记录的 seen_at 真实回拨 days 天（驱动真实时间间隔，不是 mock 形状）。"""
    seen_at = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    store._conn.execute(
        "UPDATE seen_urls SET seen_at = ? WHERE normalized_url = ?", (seen_at, url)
    )
    store._conn.commit()


class TestWindowInvariant:
    """不变式 1：去重窗口不得小于采集窗口——关系在代码里显式表达，不是两个魔数。"""

    def test_default_window_not_smaller_than_recency_window(self):
        assert DEDUP_WINDOW_DAYS >= RECENCY_WINDOW_DAYS

    def test_constructor_window_floored_to_recency_window(self):
        """显式传入更小窗口也会被抬到采集窗口（否则第 4 天必复活）。"""
        store = DedupStore(window_days=1)
        assert store.window_days >= RECENCY_WINDOW_DAYS
        store.close()

    def test_floor_window_is_the_single_source_of_the_relation(self):
        assert _floor_window(1) == RECENCY_WINDOW_DAYS
        assert _floor_window(RECENCY_WINDOW_DAYS + 7) == RECENCY_WINDOW_DAYS + 7

    def test_mention_memory_outlives_window(self):
        """first_mention_at 记忆期必须长于去重窗口，否则 >窗口 的复推无从判断。"""
        assert MENTION_MEMORY_DAYS > DEDUP_WINDOW_DAYS


class TestFourDayBeatIsBlocked:
    """实证的 4 天节拍复推（virattt/ai-hedge-fund 11 次）必须被拦。"""

    def test_duplicate_at_four_day_interval(self):
        url = "https://github.com/virattt/ai-hedge-fund"
        store = DedupStore()
        store.mark_seen(url, first_mention_at="2026-06-10T08:00:00+00:00")
        _backdate(store, url, 4)
        # 旧行为（窗口 3 天）：第 4 天 is_duplicate=False → 复活并再次入库
        assert store.is_duplicate(url, first_mention_at="2026-06-14T08:00:00+00:00")
        store.close()

    def test_duplicate_at_six_day_interval(self):
        url = "https://github.com/virattt/ai-hedge-fund"
        store = DedupStore()
        store.mark_seen(url, first_mention_at="2026-06-10T08:00:00+00:00")
        _backdate(store, url, 6)
        assert store.is_duplicate(url, first_mention_at="2026-06-16T08:00:00+00:00")
        store.close()


class TestNewMentionRequiredToRepush:
    """不变式 2：出了窗口也必须有新提及才可复推。"""

    def test_same_first_mention_at_still_duplicate_beyond_window(self):
        """间隔 >窗口，但 first_mention_at 逐字相同 → 同一条旧推文，不复推。"""
        url = "https://github.com/emcf/dexter"
        first_mention = "2026-06-01T10:00:00+00:00"
        store = DedupStore()
        store.mark_seen(url, first_mention_at=first_mention)
        _backdate(store, url, DEDUP_WINDOW_DAYS + 3)
        assert store.is_duplicate(url, first_mention_at=first_mention)
        store.close()

    def test_new_first_mention_at_allows_repush_beyond_window(self):
        """间隔 >窗口 且有更新的提及 → 允许复推（真新事件不被误杀）。"""
        url = "https://github.com/emcf/dexter"
        store = DedupStore()
        store.mark_seen(url, first_mention_at="2026-06-01T10:00:00+00:00")
        _backdate(store, url, DEDUP_WINDOW_DAYS + 3)
        assert not store.is_duplicate(url, first_mention_at="2026-06-20T09:00:00+00:00")
        store.close()

    def test_same_first_mention_at_inside_window_is_duplicate(self):
        url = "https://github.com/emcf/dexter"
        store = DedupStore()
        store.mark_seen(url, first_mention_at="2026-06-01T10:00:00+00:00")
        assert store.is_duplicate(url, first_mention_at="2026-06-01T10:00:00+00:00")
        store.close()

    def test_mark_seen_updates_remembered_first_mention(self):
        url = "https://github.com/emcf/dexter"
        store = DedupStore()
        store.mark_seen(url, first_mention_at="2026-06-01T10:00:00+00:00")
        store.mark_seen(url, first_mention_at="2026-06-20T09:00:00+00:00")
        _backdate(store, url, DEDUP_WINDOW_DAYS + 3)
        # 记忆已更新为第二次的提及时间 → 再拿第二次的时间来不得复推
        assert store.is_duplicate(url, first_mention_at="2026-06-20T09:00:00+00:00")
        store.close()

    def test_prune_keeps_mention_memory_beyond_window(self):
        """prune 不得把 first_mention_at 记忆连窗口一起裁掉，否则不变式 2 失效。"""
        url = "https://github.com/emcf/dexter"
        first_mention = "2026-06-01T10:00:00+00:00"
        store = DedupStore()
        store.mark_seen(url, first_mention_at=first_mention)
        _backdate(store, url, DEDUP_WINDOW_DAYS + 3)
        store.prune()
        assert store.count() == 1
        assert store.is_duplicate(url, first_mention_at=first_mention)
        store.close()

    def test_prune_removes_beyond_mention_memory(self):
        url = "https://github.com/emcf/dexter"
        store = DedupStore()
        store.mark_seen(url, first_mention_at="2026-01-01T00:00:00+00:00")
        _backdate(store, url, MENTION_MEMORY_DAYS + 1)
        assert store.prune() == 1
        assert store.count() == 0
        store.close()


class TestLegacySchemaMigration:
    """生产库是无 first_mention_at 列的旧 schema，必须可加列续用（不丢账本）。"""

    def test_opens_legacy_table_without_first_mention_column(self, tmp_path):
        import sqlite3

        db = tmp_path / "legacy.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE seen_urls (normalized_url TEXT PRIMARY KEY, seen_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO seen_urls VALUES (?, ?)",
            ("https://github.com/a/b", datetime.now(UTC).isoformat()),
        )
        conn.commit()
        conn.close()

        store = DedupStore(db)
        assert store.is_duplicate("https://github.com/a/b")
        # 旧行无 first_mention_at → 无记忆，窗口外允许复推（不静默永久封杀）
        _backdate(store, "https://github.com/a/b", DEDUP_WINDOW_DAYS + 3)
        assert not store.is_duplicate(
            "https://github.com/a/b", first_mention_at="2026-06-01T00:00:00+00:00"
        )
        store.close()
