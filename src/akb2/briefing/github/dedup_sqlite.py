"""SQLite rolling dedup for GitHub repos already pushed in a briefing.

两条不变式（缺陷 #3，2026-07-26）。**性质不同，别混为一谈**：不变式 1 是实证修复
（回放数据支撑，独力消掉 91.1% 的历史重复）；不变式 2 是防御性设计（历史独占拦截
量 = 0，防的是尚未出现的场景）。

回放口径（下列数字均出自 2026-07-26 只读回放 `AI TRENDS/GitHub/*.json` 全部 48 份
已发布简报 = 480 条 top_repos，跨日重复对 179 = 每条 repo 的第 2 次及之后出现与其
上一次出现配对）：

1. **去重窗口 ≥ 采集窗口**。旧默认窗口 3 天 < `RECENCY_WINDOW_DAYS`=7：条目出了去重
   窗还留在采集窗内 → 第 4 天必然复活。实测跨日重复 179/480 = 37.3%，其中间隔恰好
   4 天占 124/179 = 69.3%（`virattt/ai-hedge-fund` 严格 4 天节拍复推 11 次）。
   窗口提到 7 天可消掉 163/179 = 91.1% —— **这一处修复独力解释了几乎全部收益**。
   关系由 `_floor_window()` 单点表达——两处各写一个魔数就是这个 bug 的成因，
   故任何来源（env / 构造参数）的窗口都过同一 floor。

2. **复推必须有新提及**（防御性，历史收益为 0，不要据此宣称已消掉重复）。
   账本记住上次推送时的 `first_mention_at`：即使出了去重窗，若提及时间没变仍算重复。
   回放核实的实际情况：
     - 179 对里 128 对（71.5%）`first_mention_at` 逐字相同 —— 但这 128 对间隔**全部
       ≤6 天**，落在 7 天窗口内，`is_duplicate` 第 1 步 `seen_at >= cutoff` 就已返回
       True，第 2 判据永远读不到。
     - 唯一需要第 2 判据的场景（间隔 >7 天）共 16 对，其中 `first_mention_at` 相同的
       是 **0 对**。
     - 故不变式 2 对历史重复的**独占拦截量 = 0/179**。它防的是"出了去重窗、但仍是同
       一条旧提及"这一类，历史样本中尚未出现；保留是因为该场景一旦出现就会静默复推，
       而不是因为它已经拦下了什么。
   记忆期 `MENTION_MEMORY_DAYS` 必须长于去重窗口，否则 prune 会把判据
   连同窗口一起裁掉，不变式 2 静默失效。

下限的归属（2026-07-26 补）：下限值 = **调用方链路自己的采集窗口**，不是本模块的
常量。本模块曾用 GitHub 的 `RECENCY_WINDOW_DAYS` 给所有调用方（含复用同一
`DedupStore` 的社媒链路）设下限——等于 GitHub 的常量在暗中决定社媒的去重窗口，
社媒侧既无声明也无测试。故下限改为构造参数 `min_window_days`：每条链路显式声明
自己的采集窗口，缺省保持 GitHub 的 `RECENCY_WINDOW_DAYS`（不改既有行为）。
抬升窗口时向 stderr 打一行（constitution 原则 4：静默抬升 = 失败不可见）。
"""

import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .intake.twitter_collector import RECENCY_WINDOW_DAYS

# 请求窗口（env 可调大，不可调到采集窗口以下——见 _floor_window）
_REQUESTED_WINDOW_DAYS = int(os.environ.get("AKB2_NOVELTY_WINDOW_DAYS", "7"))

# first_mention_at 记忆期：跨越去重窗口，支撑「无新提及不复推」判定
MENTION_MEMORY_DAYS = 90

# 缺省下限 = GitHub 采集窗口（历史行为）。其他链路应显式传 min_window_days。
DEFAULT_MIN_WINDOW_DAYS = RECENCY_WINDOW_DAYS


def _floor_window(days: int, min_days: int | None = None, *,
                  source: str = "去重窗口") -> int:
    """去重窗口下限 = 调用方链路的采集窗口。小于采集窗口的窗口物理上不可能去重成功。

    抬升时必须可见：静默把显式传入的 1 天改成 7 天，等于配置被无声吞掉
    （constitution 原则 4）。故抬升走 stderr 一行，不抬升时不打噪音。
    """
    floor = DEFAULT_MIN_WINDOW_DAYS if min_days is None else int(min_days)
    requested = int(days)
    if requested < floor:
        sys.stderr.write(
            f"WARNING [dedup_sqlite]: {source} 显式传入 {requested} 天，被下限抬到 "
            f"{floor} 天 — 原因：下限 = 本链路采集窗口 {floor} 天，"
            "小于采集窗口的去重窗口在物理上不可能去重成功"
            "（条目出了去重窗仍在采集窗内，必然复活）\n"
        )
        return floor
    return requested


DEDUP_WINDOW_DAYS = _floor_window(
    _REQUESTED_WINDOW_DAYS, source="AKB2_NOVELTY_WINDOW_DAYS"
)


class DedupStore:
    """Rolling dedup + 提及记忆。窗口过 `_floor_window`，不接受小于采集窗口的值。

    Args:
        db_path: 账本 sqlite 路径。
        window_days: 请求的去重窗口；None → 模块级 `DEDUP_WINDOW_DAYS`。
        min_window_days: **本链路的采集窗口**（窗口下限）。None → GitHub 的
            `DEFAULT_MIN_WINDOW_DAYS`（历史行为）。社媒等其他链路应传自己的值，
            否则 GitHub 的常量会暗中决定该链路的去重窗口。
    """

    def __init__(self, db_path: str | Path = ":memory:",
                 window_days: int | None = None,
                 min_window_days: int | None = None):
        requested = window_days if window_days is not None else DEDUP_WINDOW_DAYS
        self._min_window_days = (
            DEFAULT_MIN_WINDOW_DAYS if min_window_days is None else int(min_window_days)
        )
        self._window_days = _floor_window(
            requested, self._min_window_days, source="DedupStore(window_days=)"
        )
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

    @property
    def min_window_days(self) -> int:
        """本实例生效的窗口下限（= 调用方声明的采集窗口）。"""
        return self._min_window_days

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

    def in_dedup_window(self, normalized_url: str) -> bool:
        """只判不变式 1：窗口内推过。窗口外的旧提及仍由 is_duplicate 拦。"""
        cutoff = (datetime.now(UTC) - timedelta(days=self._window_days)).isoformat()
        row = self._conn.execute(
            "SELECT seen_at FROM seen_urls WHERE normalized_url = ?",
            (normalized_url,),
        ).fetchone()
        return bool(row) and row[0] >= cutoff

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
