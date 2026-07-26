"""SQLite rolling dedup for GitHub repos already pushed in a briefing.

两条不变式（缺陷 #3，2026-07-26 实证修复）：

1. **去重窗口 ≥ 采集窗口**。旧默认窗口 3 天 < `RECENCY_WINDOW_DAYS`=7：条目出了去重
   窗还留在采集窗内 → 第 4 天必然复活。实测 2026-06-10→07-26 跨日重复
   179/470 = 38.1%，其中间隔恰好 4 天占 124/179 = 69.3%（`virattt/ai-hedge-fund`
   严格 4 天节拍复推 11 次）。窗口提到 7 天可消掉 163/179 = 91.1%。
   关系由 `_floor_window()` 单点表达——两处各写一个魔数就是这个 bug 的成因，
   故任何来源（env / 构造参数）的窗口都过同一 floor。

2. **复推必须有新提及**。剩余间隔 >7 天的重复中，128/179 = 71.5% 的
   `first_mention_at` 与上次推送逐字相同（同一条旧推文被反复当新事件触发）。
   因此账本记住上次推送时的 `first_mention_at`：即使出了去重窗，若提及时间没变
   仍算重复。记忆期 `MENTION_MEMORY_DAYS` 必须长于去重窗口，否则 prune 会把判据
   连同窗口一起裁掉，不变式 2 静默失效。
"""

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .intake.twitter_collector import RECENCY_WINDOW_DAYS

# 请求窗口（env 可调大，不可调到采集窗口以下——见 _floor_window）
_REQUESTED_WINDOW_DAYS = int(os.environ.get("AKB2_NOVELTY_WINDOW_DAYS", "7"))

# first_mention_at 记忆期：跨越去重窗口，支撑「无新提及不复推」判定
MENTION_MEMORY_DAYS = 90


def _floor_window(days: int) -> int:
    """去重窗口下限 = 采集窗口。小于采集窗口的窗口在物理上不可能去重成功。"""
    return max(int(days), RECENCY_WINDOW_DAYS)


DEDUP_WINDOW_DAYS = _floor_window(_REQUESTED_WINDOW_DAYS)


class DedupStore:
    """Rolling dedup + 提及记忆。窗口过 `_floor_window`，不接受小于采集窗口的值。"""

    def __init__(self, db_path: str | Path = ":memory:",
                 window_days: int | None = None):
        requested = window_days if window_days is not None else DEDUP_WINDOW_DAYS
        self._window_days = _floor_window(requested)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS seen_urls ("
            "  normalized_url TEXT PRIMARY KEY,"
            "  seen_at TEXT NOT NULL,"
            "  first_mention_at TEXT"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_at ON seen_urls(seen_at)"
        )
        self._migrate_first_mention_column()
        self._conn.commit()

    @property
    def window_days(self) -> int:
        """生效的去重窗口天数（已过 `_floor_window`）。"""
        return self._window_days

    def _migrate_first_mention_column(self) -> None:
        """生产库是无 first_mention_at 列的旧 schema — 加列续用，不重建、不丢账本。"""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(seen_urls)")}
        if "first_mention_at" not in cols:
            self._conn.execute("ALTER TABLE seen_urls ADD COLUMN first_mention_at TEXT")

    def is_duplicate(self, normalized_url: str,
                     first_mention_at: str | None = None) -> bool:
        """已推过则为 True。

        判定顺序：
        1. 窗口内推过 → 重复（不变式 1）
        2. 窗口外，但账本记的 first_mention_at 与本次逐字相同 → 同一条旧提及，
           不是新事件 → 重复（不变式 2）
        3. 其余 → 不重复
        """
        cutoff = (datetime.now(UTC) - timedelta(days=self._window_days)).isoformat()
        row = self._conn.execute(
            "SELECT seen_at, first_mention_at FROM seen_urls WHERE normalized_url = ?",
            (normalized_url,),
        ).fetchone()
        if row is None:
            return False
        seen_at, remembered_mention = row
        if seen_at >= cutoff:
            return True
        if first_mention_at and remembered_mention and remembered_mention == first_mention_at:
            return True
        return False

    def mark_seen(self, normalized_url: str,
                  first_mention_at: str | None = None) -> None:
        """记录一次推送：推送时刻 + 本次推送依据的 first_mention_at。"""
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO seen_urls "
            "(normalized_url, seen_at, first_mention_at) VALUES (?, ?, ?)",
            (normalized_url, now, first_mention_at),
        )
        self._conn.commit()

    def prune(self) -> int:
        """裁掉超出记忆期的条目。返回删除行数。

        cutoff 取 max(去重窗口, 记忆期)：窗口决定「时间去重」，记忆期决定
        「无新提及不复推」。只按窗口裁会让不变式 2 失效（判据先被删掉）。
        """
        retention_days = max(self._window_days, MENTION_MEMORY_DAYS)
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
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
