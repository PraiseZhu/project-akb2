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
from src.akb2.video.external_credentials import (
    CREDENTIAL_NLM_AUTH,
    CREDENTIAL_YTDLP_COOKIES,
    STATUS_DEAD,
    CredentialHealth,
)

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


def _credential_root_cause(
    stats: dict, health: CredentialHealth | None
) -> tuple[str, str] | None:
    """凭证根因归因：**探测结论 + 对应的运行时症状同时成立**才定论。

    只看探测会把"cookies 没显式配但今天确实没新视频"误报成凭证故障；只看症状就是
    2026-07-30 当天的现状 —— 三处凭证症状被拆成三条分散 WARNING，落盘 reason 只剩
    最末一层的 `date_unresolvable`。两者取交集才既不误报也不漏报。

    两档置信度，机读可分：
      `credential_expired`  —— 至少一项凭证被探测**判死**（二进制缺失 / cookie 文件
                               不存在 / NLM auth 过期）且症状吻合。确证。
      `credential_suspect`  —— 只有兜底配置（无显式 cookies，靠 `--cookies-from-browser`）
                               且症状吻合。推断，不谎称确证。

    NLM 只认 `dead`：它的 `degraded` 是网络 transient（researcher 会转 pending 重试），
    不是凭证失效，算进来就是把网络抖动误报成"登录态没了"。

    Returns: `(reason, detail)`，或 None 表示凭证不构成本轮真因。
    """
    if health is None:
        return None

    raw = int(stats.get("raw_listed", 0))
    sources = int(stats.get("sources_enabled", 0))
    backfill_failed = int(stats.get("date_backfill_failed", 0))
    date_unknown = int(stats.get("rejected_date_unknown", 0))
    candidates = int(stats.get("candidates_after_filters", 0))

    findings: list[tuple[str, str, str]] = []  # (status, 归因说明, 修复命令)

    cookies = health.get(CREDENTIAL_YTDLP_COOKIES)
    if cookies is not None and not cookies.is_ok:
        # cookies 的症状：列表整体拿不到东西，或日期补取成批失败（bot-check 的形态）
        if raw == 0:
            # `creator_errors` 必须带上：本分支抢在 `list_stage_empty` 之前返回，
            # 不带就等于把那条分支原有的异常线索整段吞掉（诊断能力反而变差）。
            errors = list(stats.get("creator_errors", []) or [])
            symptom = (
                f"{sources} 个源的列表阶段全部返回 0 条；creator 异常: {errors[:5] or '无'}"
            )
        elif backfill_failed > 0:
            symptom = (
                f"日期补取失败 {backfill_failed} 次 → 按日期未知拒收 {date_unknown} 个候选"
            )
        else:
            symptom = ""
        if symptom:
            findings.append(
                (cookies.status, f"yt-dlp cookies（{cookies.detail}）→ {symptom}",
                 str(cookies.fix_command))
            )

    nlm = health.get(CREDENTIAL_NLM_AUTH)
    if nlm is not None and nlm.status == STATUS_DEAD and candidates > 0:
        findings.append(
            (nlm.status, f"NLM 登录态（{nlm.detail}）→ {candidates} 个候选进入研究阶段但 0 产出",
             str(nlm.fix_command))
        )

    if not findings:
        return None

    confirmed = any(status == STATUS_DEAD for status, _, _ in findings)
    reason = "credential_expired" if confirmed else "credential_suspect"
    head = (
        "外部凭证失效（探测判死）"
        if confirmed
        else "外部凭证高度疑似失效（只有兜底配置 + 症状吻合）"
    )
    body = "；".join(desc for _, desc, _ in findings)
    fixes = " / ".join(dict.fromkeys(fix for _, _, fix in findings if fix))
    detail = f"{head}：{body}。修复：{fixes}"
    return reason, detail


def classify(
    stats: dict,
    published: int,
    previous_zero_runs: int = 0,
    credential_health: CredentialHealth | None = None,
) -> ZeroYieldVerdict:
    """把分层漏斗计数归因成一句真因。

    归因顺序 = 按"能力失效"的确定性从高到低排，第一条命中即返回：先看采集能力本身
    有没有坏（无源 / 凭证失效 / 列表全空 / 补取预算撞顶 / 补取全失败 / 上游元数据坏），
    再退到"确实没有符合条件的新视频"。顺序倒过来会把真故障归成"没新视频"。

    **凭证判定排在其余能力故障之前**：`list_stage_empty` / `date_backfill_all_failed` /
    `date_unresolvable` 都只是"取数坏了"的症状，凭证失效是它们共同的上游根因。先报
    症状就等于让运维自己去猜该跑 `nlm login` 还是重导 cookies。

    Args:
        stats: `CollectStats.as_dict()`
        published: 本轮实际发布的产出数
        previous_zero_runs: 上一份诊断记录的连续 0 产出轮数
        credential_health: 启动凭证预检结论；None = 未探测（归因退回改动前行为）
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
    # 凭证归因是附加能力，不得把归因主体拖下水：它异常时退回原有的漏斗归因，
    # 而不是让 classify 抛出去、连诊断文件都写不成（那就又静默一轮）。
    try:
        credential = _credential_root_cause(stats, credential_health)
    except Exception as exc:  # noqa: BLE001
        logger.error("凭证根因归因异常，退回漏斗归因: %s", exc)
        credential = None
    if credential is not None:
        return ZeroYieldVerdict(STATUS_BLOCKED, credential[0], credential[1], zero_runs)
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


#: 由凭证归因产生的 reason（读取侧可据此判断"该跑修复命令"）
CREDENTIAL_REASONS = frozenset({"credential_expired", "credential_suspect"})


def _should_record_credential_health(
    health: CredentialHealth | None, verdict: ZeroYieldVerdict
) -> bool:
    """`credential_health` 块的落盘条件 —— 只在它是信号的时候写，其余时候不制造噪音。

    - 凭证健康 → 不写（每轮贴一遍"一切正常"没有信息量）；
    - 探测判死（`dead`）→ 一律写，即便本轮还有产出：凭证死了就是硬故障，提前告警
      才有意义；
    - 只是 `degraded`（无显式 cookies 的浏览器兜底 —— 本机的**默认**形态）→ 只在归因
      真的落到凭证上时才写。否则"今天窗口内确实没有新视频"这种完全正常的一天也会挂
      一个凭证告警，运维会被引去查一个不存在的故障；预算撞顶同理（那不是 cookies 的错）。
    """
    if health is None or health.is_healthy:
        return False
    if health.overall == STATUS_DEAD:
        return True
    return verdict.reason in CREDENTIAL_REASONS


def write_zero_yield_diagnostic(
    stats: dict,
    published: int,
    repo_root: Path | None = None,
    credential_health: CredentialHealth | None = None,
) -> ZeroYieldVerdict:
    """归因 + 落盘 `logs/video-blocked.json`，返回结论供调用方决定退出码。

    覆盖写单份文件（与社媒链路一致）：`date` 字段让读取侧能拒绝拿昨天的原因贴到
    今天的告警上。落盘失败只记 ERROR，绝不让诊断本身把主链路搞挂。
    """
    path = _diag_path(repo_root)
    verdict = classify(stats, published, _previous_zero_runs(path), credential_health)
    now = datetime.now(UTC)
    payload: dict = {
        "status": verdict.status,
        "date": now.astimezone().strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(),
        "published_count": published,
        "consecutive_zero_runs": verdict.consecutive_zero_runs,
        "funnel": stats,
    }
    # 凭证块紧跟 funnel：读取侧拿到 reason 之后紧接着就能拿到"跑哪条命令能修"。
    # 附加块绝不能连坐主体：它序列化失败时只丢自己，漏斗诊断照常落盘（否则又变成
    # "整份诊断没写出来"的静默失败）。
    if _should_record_credential_health(credential_health, verdict):
        try:
            payload["credential_health"] = credential_health.as_dict()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.error("凭证健康块序列化失败，已跳过该块（漏斗诊断不受影响）: %s", exc)
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
