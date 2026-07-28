"""视频链路 0 产出诊断落盘 —— 让"采集能力整体失效"停止伪装成"今天没有新视频"。

事故实况（2026-07-28）：视频链路当天产出 0 个视频。日志里有 109 行
"[时间闸] 拒收（无法确定发布日期，fail-closed）"，但：

- launchd 退出码是 **0**（`_report_results([])` 对空结果返回 0）；
- 没有任何告警出口（社媒链路有 `logs/briefing-social-blocked.json`，视频链路没有）；
- 于是"日期补取预算 40 撞顶导致排序靠后的源整批消失"这件事，在运维视角与
  "今天这些博主都没更新"完全无法区分。

宪法 P4（预算显式 / 失败可见）要求部分失败必须明示。本模块把 `CollectStats` 的
分层漏斗归因成一句机读真因，写到 `logs/video-blocked.json`，schema 与社媒链路的
`briefing-social-blocked.json` 对齐（`feishu_sync.blocked` 的读取侧直接可用）。

**不注册任何新的自动化触发器**（项目红线）：只做代码层检测 + 诊断落盘 + 退出码。
连续 0 产出的计数复用诊断文件自身（读上一份的 `consecutive_zero_runs` + 1），
不引入第二个状态文件。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.akb2.video.constants import VIDEO_MAX_CONSECUTIVE_ZERO_RUNS

logger = logging.getLogger("akb2.video.blocked")

# 仓库根：src/akb2/video/blocked.py → parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]

VIDEO_BLOCKED_REL_PATH = "logs/video-blocked.json"

# status 取值（机读枚举，不本地化）：
#   ok          —— 本轮有产出
#   zero_yield  —— 0 产出但归因为"窗口内确实没有新视频"（信息性，退出码仍为 0）
#   blocked     —— 0 产出且归因为采集能力失效（退出码非 0，运维必须看）
STATUS_OK = "ok"
STATUS_ZERO_YIELD = "zero_yield"
STATUS_BLOCKED = "blocked"


@dataclass(frozen=True)
class ZeroYieldVerdict:
    """0 产出归因结论。"""

    status: str
    reason: str
    detail: str
    consecutive_zero_runs: int

    @property
    def is_blocked(self) -> bool:
        return self.status == STATUS_BLOCKED


def _diag_path(repo_root: Path | None = None) -> Path:
    return (repo_root or REPO_ROOT) / VIDEO_BLOCKED_REL_PATH


def _previous_zero_runs(path: Path) -> int:
    """读上一份诊断的连续 0 产出计数；读不到就当 0（诊断本身绝不失败）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    try:
        return max(0, int(data.get("consecutive_zero_runs", 0)))
    except (TypeError, ValueError):
        return 0


def classify(stats: dict, published: int, previous_zero_runs: int = 0) -> ZeroYieldVerdict:
    """把分层漏斗计数归因成一句真因。

    归因顺序 = 按"能力失效"的确定性从高到低排，第一条命中即返回：先看采集能力本身
    有没有坏（无源 / 列表全空 / 补取预算撞顶 / 补取全失败 / 上游元数据坏），再退到
    "确实没有符合条件的新视频"。顺序倒过来会把真故障归成"没新视频"。

    Args:
        stats: `CollectStats.as_dict()`
        published: 本轮实际发布的产出数
        previous_zero_runs: 上一份诊断记录的连续 0 产出轮数
    """
    if published > 0:
        return ZeroYieldVerdict(STATUS_OK, "", "", 0)

    zero_runs = previous_zero_runs + 1
    sources = int(stats.get("sources_enabled", 0))
    raw = int(stats.get("raw_listed", 0))
    backfill_used = int(stats.get("date_backfill_used", 0))
    backfill_ok = int(stats.get("date_backfill_ok", 0))
    backfill_failed = int(stats.get("date_backfill_failed", 0))
    date_unknown = int(stats.get("rejected_date_unknown", 0))
    no_title = int(stats.get("dropped_no_title", 0))
    exhausted = bool(stats.get("date_backfill_budget_exhausted", False))
    budget = int(stats.get("date_backfill_budget", 0))
    errors = list(stats.get("creator_errors", []) or [])

    if sources == 0:
        return ZeroYieldVerdict(
            STATUS_BLOCKED, "no_enabled_sources",
            "video_sources.json 里没有 enabled 的 creator，采集不可能有产出",
            zero_runs,
        )
    if no_title > 0 and no_title >= raw:
        # `raw_listed` 只统计带 title 活下来的条目，所以"丢弃数 >= 存活数"意味着
        # 至少一半列表条目连标题都拿不到 —— 坏的不只是日期，是整个元数据获取。
        # 必须排在 list_stage_empty 之前：raw=0 且 no_title>0 的真因是元数据坏，
        # 不是"列表返回空"。
        return ZeroYieldVerdict(
            STATUS_BLOCKED, "upstream_metadata_broken",
            f"{no_title} 条列表条目 2 阶段补全后仍无 title（存活 {raw} 条）—— "
            f"上游元数据整体获取失败，不只是日期",
            zero_runs,
        )
    if raw == 0:
        return ZeroYieldVerdict(
            STATUS_BLOCKED, "list_stage_empty",
            f"{sources} 个源的列表阶段全部返回 0 条 —— yt-dlp 不可用 / cookies 失效 / "
            f"网络被拦；creator 异常: {errors[:5] or '无'}",
            zero_runs,
        )
    if exhausted:
        return ZeroYieldVerdict(
            STATUS_BLOCKED, "date_backfill_budget_exhausted",
            f"发布日期补取预算 {budget} 撞顶（已用 {backfill_used}），撞顶后的候选一律按"
            f"日期未知拒收 {date_unknown} 个 —— 排序靠后的源整批消失，不是"
            f"这些源没更新",
            zero_runs,
        )
    if backfill_used > 0 and backfill_ok == 0:
        return ZeroYieldVerdict(
            STATUS_BLOCKED, "date_backfill_all_failed",
            f"发布日期补取 {backfill_used} 次全部失败（失败 {backfill_failed}），"
            f"日期未知拒收 {date_unknown} 个 —— 常见真因：YouTube 判机器人限频"
            f"（\"Sign in to confirm you're not a bot\"）或 cookies 失效",
            zero_runs,
        )
    if date_unknown > 0:
        # 0 产出 + 存在"过了全部本地过滤、只差一个日期就能入库"的候选被拒
        # → 时间闸本身是对的（宪法 P3 不放宽），坏的是取数能力，必须可见。
        return ZeroYieldVerdict(
            STATUS_BLOCKED, "date_unresolvable",
            f"{date_unknown} 个候选取不到精确发布日期被 fail-closed 拒收（补取成功 "
            f"{backfill_ok} / 失败 {backfill_failed}）—— 时间闸没错，取数坏了",
            zero_runs,
        )
    if errors:
        return ZeroYieldVerdict(
            STATUS_BLOCKED, "creator_detection_errors",
            f"{len(errors)} 个源检测抛异常: {errors[:5]}",
            zero_runs,
        )
    if zero_runs >= VIDEO_MAX_CONSECUTIVE_ZERO_RUNS:
        # 有界升级：单轮真零是正常的，连续 N 轮真零在 21 源 / 14 天窗口下不正常。
        return ZeroYieldVerdict(
            STATUS_BLOCKED, "consecutive_zero_yield",
            f"连续 {zero_runs} 轮 0 产出（阈值 {VIDEO_MAX_CONSECUTIVE_ZERO_RUNS}）—— "
            f"逐层计数看不出硬故障，但连续真零概率极低，请人工核查采集链路",
            zero_runs,
        )
    out_of_window = (
        int(stats.get("dropped_out_of_window", 0))
        + int(stats.get("rejected_date_prefilter", 0))
    )
    return ZeroYieldVerdict(
        STATUS_ZERO_YIELD, "no_candidates_in_window",
        f"列表 {raw} 条，去重 -{stats.get('dropped_dedup', 0)}、"
        f"相关性 -{stats.get('dropped_relevance', 0)}、"
        f"超窗 -{out_of_window} 后无候选；各层计数正常，判定为窗口内确实没有新视频",
        zero_runs,
    )


def write_zero_yield_diagnostic(
    stats: dict,
    published: int,
    repo_root: Path | None = None,
) -> ZeroYieldVerdict:
    """归因 + 落盘 `logs/video-blocked.json`，返回结论供调用方决定退出码。

    覆盖写单份文件（与社媒链路一致）：`date` 字段让读取侧能拒绝拿昨天的原因贴到
    今天的告警上。落盘失败只记 ERROR，绝不让诊断本身把主链路搞挂。
    """
    path = _diag_path(repo_root)
    verdict = classify(stats, published, _previous_zero_runs(path))
    now = datetime.now(UTC)
    payload: dict = {
        "status": verdict.status,
        "date": now.astimezone().strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(),
        "published_count": published,
        "consecutive_zero_runs": verdict.consecutive_zero_runs,
        "funnel": stats,
    }
    # status=ok 不写 reason/detail：读取侧靠 `reason` 非空判断"有真因可播报"，
    # 留着上一轮的 reason 会让恢复之后的告警继续引用旧故障。
    if verdict.reason:
        payload["reason"] = verdict.reason
    if verdict.detail:
        payload["detail"] = verdict.detail

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        logger.error("视频链路 0 产出诊断落盘失败 %s: %s", path, exc)
        return verdict

    if verdict.is_blocked:
        logger.error(
            "[0 产出] blocked: %s —— %s（连续 %d 轮；诊断: %s）",
            verdict.reason, verdict.detail, verdict.consecutive_zero_runs, path,
        )
    elif verdict.status == STATUS_ZERO_YIELD:
        logger.warning(
            "[0 产出] %s: %s（连续 %d 轮；诊断: %s）",
            verdict.reason, verdict.detail, verdict.consecutive_zero_runs, path,
        )
    return verdict
