"""8-step pipeline: intake → aggregate → enrich → filter → score → synthesize → render → vault.

Pipeline output contract (§step-4 PIPELINE_OUTPUT_CONTRACT):
- VAULT_ROOT must be set and absolute; otherwise write briefing-skipped.json + exit≠0
- Only write .md + .json when top_repos ≥ _MIN_REPOS_FOR_BRIEFING AND schema validator PASS
- Otherwise write briefing-skipped.json / briefing-failed.json + exit≠0

退出码不变式：**未交付即非零**（见 EXIT_* 常量注释里的 2026-06-26~29 事故）。
本 docstring 早先写的 "exit 0" 是失效描述 —— 那正是 4 天零交付被监控读成成功的说法。
诊断落盘不变式：每个未交付分支都写出当日诊断文件，含 `date`（本地日，与 vault 文件名
同源）/ `stage` / `failure_class` / `reason` / `detail`，供飞书告警直接引用真因
（见 FAILURE_* 常量注释里的 2026-08-03 告警事故）。
"""

import json
import os
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

from ...common.state_paths import resolve_state_file
from . import synthesis as synthesis_module
from .dedup_sqlite import DedupStore
from .enricher import enrich
from .intake.fallback_judge import decide
from .intake.trending_html_collector import TrendingHTMLCollector
from .intake.twitter_collector import TwitterCollector
from .scoring import engagement_raw, mention_threshold, score_batch
from .star_history import StarHistory
from .synthesis import reset_family_latch, synthesize
from .url_normalizer import display_slug
from .validator import validate

_PROJECT_ROOT = (
    Path(os.environ.get("PROJECT_ROOT", ""))
    if os.environ.get("PROJECT_ROOT")
    else Path(__file__).resolve().parents[4]
)
# src/akb2/briefing/github/pipeline.py -> project root (parents[4]=<root>).
# 旧值 [5] 越到 <root> 上一级，导致 POOL_CONFIG/skip 文件落错目录。
POOL_CONFIG = _PROJECT_ROOT / "config" / "github_curator_pool.yaml"
OUTPUT_DIR_TEMPLATE = "Praise Space/AKB2.0/AI TRENDS/GitHub"

# Schema-approved repo fields (strip enrichment internals before vault write)
_SCHEMA_REPO_FIELDS = {
    "repo_url", "stars", "language", "topic_tags", "mention_count",
    "mentioners", "first_mention_at", "score", "summary", "use_case",
    "engagement_raw",
    # 显示名（301 后的权威大小写）+ grounding 审计字段：产出必须留下 LLM 依据的原文，
    # 否则事后无法核对幻觉率（旧 _clean_repo 把 readme_excerpt 整个剥掉）。
    "repo_full_name", "grounding_source", "grounding_excerpt",
}

# Minimum repos required to produce a briefing (SC-3: partial output allowed above this floor)
_MIN_REPOS_FOR_BRIEFING = 6
# Ideal target for supplementation (SC-1: L2 supplement tries to reach this before falling back)
_IDEAL_REPOS_FOR_BRIEFING = 10

# ── 退出码（宪法 P4「失败可见」）───────────────────────────────────────────────
# 不变式：**未交付即非零**。0 只在当日 briefing .md 真正落盘并复查存在后才返回。
#
# 事故背景（这条不变式为什么要具名固化）：2026-06-26~06-29 连续 4 天
# logs/github-briefing-stderr.log 记 `exit=0`，同期 stdout 是
# `SKIPPED (enrich_degraded: 18/18 enrich calls failed)`，而 vault
# `AI TRENDS/GitHub/` 这 4 天零文件 —— 任何看退出码的监控（含 launchctl 的
# LastExitStatus）都把 4 天零交付读成成功。同为 SKIPPED 类的
# schema_validation_failed 当时却是 exit=1，同类分支退出码不一致。
#
# 保留码位（本模块**不得**占用，即使将来要细分 skip 原因）：
#   124 = scripts/run_with_deadline.py 的 deadline 超时码
#   125 = backstop 脚本的主动放弃码
#
# 为什么所有未交付分支共用同一个 1 而不各给一个数值：backstop
# （`scripts/github_briefing_backstop.sh` 的 recovered 判据，搜
# `SC-5: recovered 必须复查文件` 定位 —— 不写行号，那个文件本轮从 144 行长到 274 行，
# 行号引用已经过期过一次）的判据是
# `rc == 0 && 当日文件存在 → recovered`，否则 failed 并把 rc **原样 exit 出去**。
# 给每个分支分配不同数值只会让这些数字漏进 backstop / launchd 的 LastExitStatus，
# 而分支语义已由 briefing-skipped.json / briefing-failed.json 的 reason 字段承载。
EXIT_DELIVERED = 0
EXIT_NOT_DELIVERED = 1
# 静态自证用（tests/briefing/github/test_pipeline_exit_codes.py 断言与保留码位无交集）
EXIT_CODES = frozenset({EXIT_DELIVERED, EXIT_NOT_DELIVERED})
RESERVED_EXIT_CODES = frozenset({124, 125})

# ── synthesis 输出质量重生成预算（宪法 P1「有界自动化」+ P4「预算显式」）─────────
# 归因（24 天内 6 次 exit=1 逐条查证）：5 次是 LLM **输出质量**问题 —— 3 次 JSON 写坏
# （07-22/25/28 `Unterminated string starting at: line 44 column 16 …`）+ 2 次 schema
# 校验不过（07-03/10 `'…' is too short`）；只有 1 次是 429 预算耗尽。而
# synthesis.MAX_RETRIES=3 只覆盖 **HTTP 层**，这两类此前**零自动重试**：模型一次把
# JSON 写坏 = 当天零交付。
#
# 预算取 2 次重生成（总计 ≤3 次 synthesize）。为什么不更多：每次重生成都重新烧一整轮
# LLM 额度。这是**显式代价不是隐藏成本**：配额正是这条链路的稀缺资源
# （08-02 即因 UTC 日配额打穿而零交付）。
MAX_SYNTHESIS_REGENERATIONS = 2

# ── 整条重生成阶段的真实最坏 HTTP 请求数（宪法 P4：声明值 == 真实最坏值）──────────
# 此前这里写「最坏 6 次 LLM 调用」，R1 审查实测最坏 36 —— 差 6 倍。与本文件刚修掉的
# 「RETRY_BACKOFF 声明 65s 实际 20s」是同一类缺陷：预算声明没跟着实现推导。
#
# 重新逐层推导（每一项都可在 synthesis.py 里核对）：
#   调用点数 = (重生成轮数 + 1) × synthesize 内调用点数 = 3 × 2 = 6
#   族闩（synthesis.reset_family_latch 划界，跨轮保持）保证换族最多发生一次，
#   于是每个调用点只打一族，唯独发生族切换的那个调用点打两族：
#       总请求数 = (调用点数 + 1) × MAX_RETRIES = 7 × 4 = 28
#   预算耗尽路径远低于此：首击即抛、不退避、不换族（每调用点 1 次请求）。
# 断言在 tests/briefing/github/test_pipeline_exit_codes.py 里绑实测计数，
# 改任一常量而不同步推导会立刻打红。
SYNTHESIS_CALL_POINTS_PER_RUN = (
    MAX_SYNTHESIS_REGENERATIONS + 1
) * synthesis_module.SYNTHESIZE_CALL_POINTS
MAX_LLM_REQUESTS_PER_RUN = (
    SYNTHESIS_CALL_POINTS_PER_RUN + 1
) * synthesis_module.MAX_RETRIES
REGEN_REASON_JSON_TRUNCATED = "json_truncated"
REGEN_REASON_SCHEMA_INVALID = "schema_invalid"

# ── 诊断落盘：告警归因的唯一素材来源 ──────────────────────────────────────────
# 事故（2026-08-03 早上）：飞书告警只说「github 缺失（期望路径: …）」，看不出真因是
# 预算耗尽。排查一度以为「pipeline 失败时没留下诊断文件」——**不是**。
# `briefing-failed.json` 确实写出来了，且含 429 原文：
#   {"status":"failed","reason":"synthesis_failed: DeepSeek API failed after 3 retries:
#     HTTP Error 429: Too Many Requests","generated_at":"2026-08-02T23:30:51+00:00"}
# 卡住的是**日期键**：`feishu_sync/blocked.py:blocking_reason()` 要求诊断的日期 ==
# 告警的日期，而 github 诊断此前**没有 `date` 字段**，读取侧只能退回
# `generated_at[:10]` —— 那是 **UTC** 日。07:30 CST = 前一天 23:30 UTC，于是
# UTC 日(08-02) ≠ 告警用的本地日(08-03) → 日期校验不过 → 返回 None → 告警退回
# "文件缺失"。与 08-02 那次事故同源的 UTC/本地日偏移，只是这次咬在告警链路上。
#
# 修法：显式写 `date` = **本地抓取日**，与 vault 文件名同源（都用 `date.today()`），
# 也就是告警拿去算 expected_path 的那把同一把尺子 —— 两侧同源才不会再错开。
# 另外补 `stage` / `failure_class` / `detail`（可补救提示）与契约 2 的 provider 诊断，
# 让告警能直接说"预算耗尽，等 UTC 日重置后重试"而不是"文件缺失"。
FAILURE_STAGE_UNKNOWN = "unknown"
FAILURE_STAGE_PREFLIGHT = "preflight"
FAILURE_STAGE_INTAKE = "intake"
FAILURE_STAGE_ENRICH = "enrich"
FAILURE_STAGE_SYNTHESIZE = "synthesize"
FAILURE_STAGE_VALIDATE = "validate"
FAILURE_STAGE_WRITE_VAULT = "write_vault"

FAILURE_CLASS_CONFIG = "config"                        # 部署/配置错，需人工
FAILURE_CLASS_UPSTREAM_INTAKE = "upstream_intake"      # 上游素材不足，等下轮
FAILURE_CLASS_ENRICH_DEGRADED = "enrich_degraded"      # GitHub API 限流/故障
FAILURE_CLASS_LLM_PROVIDER = "llm_provider"            # 429 / 预算 / HTTP
FAILURE_CLASS_LLM_OUTPUT_QUALITY = "llm_output_quality"  # JSON 写坏 / schema 不过
FAILURE_CLASS_OUTPUT_BUDGET = "output_budget"          # finish_reason=length
FAILURE_CLASS_VAULT_IO = "vault_io"                    # vault 未挂载/写盘失败
FAILURE_CLASS_INTERNAL = "internal"


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text via fsync + same-dir replace so partial files never appear."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class _StageTimer:
    def __init__(self, name: str):
        self.name = name
        self.started = 0.0

    def __enter__(self):
        self.started = time.monotonic()
        _ts_print(f"STAGE_START {self.name}")
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.monotonic() - self.started
        status = "error" if exc_type else "ok"
        _ts_print(f"STAGE_END {self.name} status={status} elapsed={elapsed:.1f}s")
        return False


def _clean_repo(repo: dict) -> dict:
    """Keep only schema-approved repo fields, stripping enrichment internals."""
    return {k: v for k, v in repo.items() if k in _SCHEMA_REPO_FIELDS}


def _resolve_star_db(mock: bool) -> Path:
    """star_history db 路径：显式覆盖 > mock 一次性临时 db > 机器本地状态根。

    实证（缺陷 #11）：`data/star_history.sqlite` 落在 Syncthing 同步范围内，两台机器
    各自 read-modify-write → live 库丢了 690 行写入，冲突副本另有 1378 条独有记录。
    """
    override = os.environ.get("AKB2_STAR_HISTORY_DB")
    if override:
        return Path(override)
    if mock:
        import tempfile
        return Path(tempfile.mkdtemp(prefix="akb2-star-")) / "star_history.sqlite"
    return resolve_state_file(
        "star_history.sqlite",
        legacy_path=_PROJECT_ROOT / "data" / "star_history.sqlite",
    )


def _merge_canonical(existing: dict, incoming: dict) -> None:
    """两个输入 URL 归一到同一 canonical repo（301 改名）时合并提及信号。

    直接丢弃后来者会丢 mention 与 engagement 证据，故合并而非覆盖；聚合语义与
    `_aggregate` 保持一致（mention 累加、mentioner 去重、first_mention 取最早、
    engagement 取 MAX）。
    """
    existing["mention_count"] = existing.get("mention_count", 0) + incoming.get("mention_count", 0)
    for m in incoming.get("mentioners", []):
        if m not in existing["mentioners"]:
            existing["mentioners"].append(m)
    inc_first = incoming.get("first_mention_at")
    cur_first = existing.get("first_mention_at")
    if inc_first and (not cur_first or inc_first < cur_first):
        existing["first_mention_at"] = inc_first
    if engagement_raw(incoming.get("engagement")) > engagement_raw(existing.get("engagement")):
        existing["engagement"] = incoming.get("engagement")


def run_pipeline(mock: bool = False, force_layer: int | None = None) -> int:
    """Run the full GitHub briefing pipeline.

    Returns EXIT_DELIVERED (0) ONLY when the day's briefing .md is actually written
    to the vault. Every skip / block / failure path returns EXIT_NOT_DELIVERED —
    退出码必须反映真实交付，禁止 "0/无产物" 假成功（见 EXIT_* 常量注释里的
    2026-06-26~29 四天零交付事故）。诊断 briefing-skipped.json / briefing-failed.json
    仍写出供 backstop 归因。
    """
    vault_root = os.environ.get("VAULT_ROOT", "")
    if not vault_root or not vault_root.startswith("/"):
        _write_skipped("vault_root_missing_or_relative", 0, "skip",
                       stage=FAILURE_STAGE_PREFLIGHT,
                       failure_class=FAILURE_CLASS_CONFIG)
        return EXIT_NOT_DELIVERED

    vault = Path(vault_root)

    # Step 1: Intake via three-layer fallback
    with _StageTimer("intake"):
        if mock:
            candidates, fallback_layer = _mock_intake(force_layer)
            l1_recency_stats = {}  # mock 无真实 L1
        else:
            candidates, fallback_layer, l1_recency_stats = _real_intake(force_layer)

    if not candidates:
        sys.stderr.write(
            f"WARNING [github-pipeline]: 0 candidates after intake "
            f"(fallback_layer={fallback_layer}) — possible transient fetch failure "
            "or genuinely no trending repos. Backstop should retry.\n"
        )
        _emit_sc7_metrics(_make_zero_sc7_metrics())
        _write_skipped("no_candidates_after_intake", len(candidates), fallback_layer,
                       stage=FAILURE_STAGE_INTAKE,
                       failure_class=FAILURE_CLASS_UPSTREAM_INTAKE)
        return EXIT_NOT_DELIVERED

    # Step 2-3: Aggregate by repo_url（mention_count 在此正确累加：collector 现按
    # per-handle 去重，跨博主同 repo 会保留多条 candidate）。
    with _StageTimer("aggregate"):
        aggregated = _aggregate(candidates)

    # 跨天 novelty 去重：过滤去重窗口内已推过的 repo（窗口 ≥ 采集窗口，见
    # dedup_sqlite 不变式 1；同一 first_mention_at 不复推，见不变式 2）。这是真正该做
    # 去重的地方——在聚合算完热度之后，而非抓取时（抓取去重会抹掉 mention 信号）。
    # novelty db 路径：AKB2_NOVELTY_DB 显式覆盖（测试/特殊部署用）> mock 一次性临时 db
    # （hermetic/可重复）> 机器本地状态根（state_paths：状态文件不能待在 Syncthing
    # 同步范围内，data/star_history.sqlite 已实证跨机分叉丢 690 行写入）。
    _novelty_override = os.environ.get("AKB2_NOVELTY_DB")
    if _novelty_override:
        _novelty_db = Path(_novelty_override)
        _novelty_db.parent.mkdir(parents=True, exist_ok=True)
    elif mock:
        import tempfile
        _novelty_db = Path(tempfile.mkdtemp(prefix="akb2-novelty-")) / "novelty.sqlite"
    else:
        _novelty_db = resolve_state_file(
            "github_recommended.sqlite",
            legacy_path=_PROJECT_ROOT / "data" / "github_recommended.sqlite",
        )
        _novelty_db.parent.mkdir(parents=True, exist_ok=True)
    pre_novelty_count = len(aggregated)  # SC-6: capture before dedup for diagnostics
    _novelty = DedupStore(_novelty_db)
    try:
        aggregated = {
            u: d for u, d in aggregated.items()
            if not _novelty.is_duplicate(u, d.get("first_mention_at"))
        }
        # SC-1 fix: post-novelty L2 supplement for layer1 dead-zone.
        # Pre-novelty ≥10 but novelty dedup may reduce below 10; try layer2 before giving up.
        if len(aggregated) < _IDEAL_REPOS_FOR_BRIEFING and fallback_layer == "layer1":
            if mock:
                l2_cands, _ = _mock_intake(force_layer=2)
            else:
                _l2 = TrendingHTMLCollector().collect()
                l2_cands = _l2.candidates if _l2.ok else []
            if l2_cands:
                l2_agg = _aggregate(l2_cands)
                l2_new = {
                    url: data for url, data in l2_agg.items()
                    if url not in aggregated
                    and not _novelty.is_duplicate(url, data.get("first_mention_at"))
                }
                if l2_new:
                    aggregated.update(l2_new)
                    fallback_layer = "layer1+2"
    finally:
        _novelty.close()

    if len(aggregated) < _MIN_REPOS_FOR_BRIEFING:
        _emit_sc7_metrics(_make_zero_sc7_metrics())
        _write_skipped("too_few_aggregated_repos", len(aggregated), fallback_layer,
                       diag={"before_novelty": pre_novelty_count,
                             "novelty_dropped": pre_novelty_count - len(aggregated),
                             "after_novelty": len(aggregated)},
                       stage=FAILURE_STAGE_INTAKE,
                       failure_class=FAILURE_CLASS_UPSTREAM_INTAKE)
        return EXIT_NOT_DELIVERED

    # Build tier_map from pool config
    tier_map = _load_tier_map()

    # Step 4: Enrich via GitHub API
    # enrich 失败（含 404 营销页、空 grounding）的条目**逐条剔除**，不再只看批量比例：
    # 旧实现仅 >50% 失败才 skip 整批，个别 404 照常进 LLM（实测 6 条 stars=0 落库，
    # 其中 features/ai、features/copilot 是 GitHub 营销页）。
    enriched_by_url: dict[str, dict] = {}
    enrich_attempted = 0
    enrich_failed = 0
    with _StageTimer("enrich"):
        for repo_url in list(aggregated.keys())[:30]:  # Cap at 30 for rate limits
            # mock 模式必须 hermetic：不打真实 GitHub API（否则受限额影响 flaky，
            # 且会被 enrich_degraded gate 误杀）。真实模式才走 enrich()。
            data = _mock_enrich(repo_url) if mock else enrich(repo_url)
            enrich_attempted += 1
            # fail-safe 默认：缺 enrich_ok 字段 → 视为失败（保守计入降级），
            # 避免未来新增 enrich 变体忘记打标记时静默绕过 enrich_degraded 门控。
            if not data.get("enrich_ok", False):
                enrich_failed += 1
                sys.stderr.write(
                    f"WARNING [github-pipeline]: enrich 失败，剔除该条 {repo_url} "
                    "(API 失败/404/无 grounding) — 不进 LLM\n"
                )
                continue

            # Merge aggregate data into enriched
            agg = aggregated[repo_url]
            # 301 回填后的规范身份（enricher 已把 repo_url 换成 full_name 派生的去重键）。
            # 旧名与新名必须收敛到同一条目，否则同仓双份入库、去重键分裂。
            canonical_url = data.get("repo_url") or repo_url
            data["repo_url"] = canonical_url
            data["mention_count"] = agg["mention_count"]
            data["mentioners"] = list(agg["mentioners"])
            data["first_mention_at"] = agg["first_mention_at"]
            data["engagement"] = agg.get("engagement")  # MAX tweet metrics（engagement 打分维）

            existing = enriched_by_url.get(canonical_url)
            if existing is not None:
                _merge_canonical(existing, data)
                sys.stderr.write(
                    f"WARNING [github-pipeline]: {repo_url} 与 "
                    f"{existing.get('requested_repo_url', canonical_url)} 归一到同一仓 "
                    f"{canonical_url}（301 改名）— 已合并提及，不双份入库\n"
                )
                continue
            enriched_by_url[canonical_url] = data

    enriched = list(enriched_by_url.values())

    # Enrich 降级红线：过半 enrich 失败（API 失败/限流）→ 可见 skip，不静默产全 0 简报。
    # GitHub API 限流或网络故障会让 enrich 返回 stars=0/lang=None，若放行会产出
    # schema 合法但内容全空的简报；这里 fail-visibly 而非 fail-silently。
    if enrich_attempted > 0 and enrich_failed * 2 > enrich_attempted:
        _emit_sc7_metrics(_make_zero_sc7_metrics())
        _write_skipped(
            f"enrich_degraded: {enrich_failed}/{enrich_attempted} "
            "enrich calls failed (rate-limit/network)",
            enrich_attempted, fallback_layer,
            stage=FAILURE_STAGE_ENRICH,
            failure_class=FAILURE_CLASS_ENRICH_DEGRADED,
        )
        return EXIT_NOT_DELIVERED

    # 回填后的身份必须重新过 novelty：301 改名（codedrobe/codex-skill →
    # CodeDrobe/skills）时旧名不在账本里、新名可能已推过，只查旧名等于漏判。
    if enriched:
        _novelty = DedupStore(_novelty_db)
        try:
            kept = []
            for data in enriched:
                if _novelty.is_duplicate(data["repo_url"], data.get("first_mention_at")):
                    sys.stderr.write(
                        f"WARNING [github-pipeline]: 回填后身份 {data['repo_url']} "
                        f"已推过（原始 {data.get('requested_repo_url')}）— 剔除\n"
                    )
                    continue
                kept.append(data)
            enriched = kept
        finally:
            _novelty.close()

    # star_history 快照（ephemeral per-run；day 1 velocity 为 None）。
    # 路径同 novelty：AKB2_STAR_HISTORY_DB 覆盖 > mock 临时 db > 机器本地状态根。
    star_db = _resolve_star_db(mock)
    star_db.parent.mkdir(parents=True, exist_ok=True)
    star_history = StarHistory(star_db)
    try:
        for data in enriched:
            # Record star snapshot (for future velocity computation)
            if data.get("stars", 0) > 0:
                star_history.record(data["repo_url"], data["stars"])
            # Inject velocity_7d (None until ≥7d of snapshots accumulate)
            data["velocity_7d"] = star_history.velocity_7d(data["repo_url"])
    finally:
        star_history.close()

    # Step 5: Filter by mention threshold.
    # mention 共识过滤只对 twitter 源(layer1)有意义；layer2(trending)/layer3(manual)
    # 已是预排序源，每个 repo 仅 @github_trending 单次 mention(count=1)，会被 min=2
    # 阈值全杀 → 兜底永远 skip。故 fallback 层旁路 mention 过滤。
    if fallback_layer in ("layer2", "layer3", "layer1+2"):
        filtered = list(enriched)
    else:
        active_curators = len(set(c.mentioner for c in candidates))
        threshold = mention_threshold(active_curators)
        filtered = [r for r in enriched if r.get("mention_count", 0) >= threshold]

    # Inject tier_map for scoring
    for r in filtered:
        r["_tier_map"] = tier_map

    # Step 6: Score
    scored = score_batch(filtered)
    top10 = scored[:10]

    # --- SC-7 metrics (computed once, emitted on all exit paths) ---
    candidates_after_safety = sum(1 for r in scored if r.get("score", 0) > 0)
    fallback_triggered = (fallback_layer != "layer1")
    top10_all_layer1 = (fallback_layer == "layer1" and len(top10) >= 10)
    _sc7_metrics = {
        "candidates_before_recency": l1_recency_stats.get("candidates_before_recency", 0),
        "candidates_after_recency": l1_recency_stats.get("candidates_after_recency", 0),
        "recency_dropped": l1_recency_stats.get("recency_dropped", 0),
        "timestamp_missing": l1_recency_stats.get("timestamp_missing", 0),
        "timestamp_parse_failed": l1_recency_stats.get("timestamp_parse_failed", 0),
        "candidates_after_safety": candidates_after_safety,
        "fallback_triggered": fallback_triggered,
        "top10_all_layer1": top10_all_layer1,
    }

    if len(top10) < _MIN_REPOS_FOR_BRIEFING:
        _emit_sc7_metrics(_sc7_metrics)
        _write_skipped("too_few_after_filter", len(top10), fallback_layer,
                       stage=FAILURE_STAGE_INTAKE,
                       failure_class=FAILURE_CLASS_UPSTREAM_INTAKE)
        return EXIT_NOT_DELIVERED

    # 文件名 = 抓取日（去掉旧的 +1 偏移，对齐 social briefing）
    today_str = date.today().isoformat()

    # Step 7 + 8a: synthesize → 组装 → schema 校验，**输出质量类失败有界重生成**。
    # 见 MAX_SYNTHESIS_REGENERATIONS 常量注释：JSON 写坏 + schema 不过合计占历史失败
    # 5/6，而此前这两类零自动重试（MAX_RETRIES=3 只管 HTTP 层）。
    # 重生成必须可见（宪法 P4）：每轮在 stdout 打 REGENERATE attempt=n/N reason=...；
    # 预算耗尽后仍 fail-closed 不写 vault（宪法 P3），不为"有产出"而降级写入。
    briefing: dict | None = None
    regen_reason = ""
    # 族可用性闩的生命周期 = 整个重生成阶段。在**循环外**划界：判死的族跨轮保持判死，
    # 否则每轮重生成都把它复活、原样再打一遍（宪法 P2 收敛优先于重跑）。
    reset_family_latch()
    for regen in range(MAX_SYNTHESIS_REGENERATIONS + 1):
        if regen > 0:
            _ts_print(
                f"REGENERATE attempt={regen}/{MAX_SYNTHESIS_REGENERATIONS} "
                f"reason={regen_reason}"
            )
        try:
            with _StageTimer("synthesize"):
                # mock=mock：synthesis 的占位产出是显式 opt-in（见 synthesize docstring）。
                # 不透传时 `--mock` 自检链路在无 API key 环境下必然 fail-closed，
                # 整条 mock E2E 死掉。
                synthesis_result = synthesize(top10, mock=mock)
                top10_with_summaries = synthesis_result["top_repos"]
        except Exception as e:
            if _is_regenerable_synthesis_error(e) and regen < MAX_SYNTHESIS_REGENERATIONS:
                regen_reason = REGEN_REASON_JSON_TRUNCATED
                sys.stderr.write(
                    f"WARNING [github-pipeline]: synthesis 输出不是合法 JSON（{e}）—— "
                    "判为可重生成，触发下一轮\n"
                )
                continue
            _emit_sc7_metrics(_sc7_metrics)
            # 告警素材：failure_class 决定"能不能补跑"，provider_diagnosis 决定
            # "是不是预算耗尽、要等到什么时候"（契约 2 字段，由 synthesis 层产出）
            _write_failed(f"synthesis_failed: {e}", fallback_layer=fallback_layer,
                          stage=FAILURE_STAGE_SYNTHESIZE,
                          failure_class=_classify_synthesis_failure(e),
                          provider_diagnosis=_provider_diagnosis(e))
            return EXIT_NOT_DELIVERED

        # Step 8a: 组装 + schema 校验（每轮重新组装：产出换了，engagement_raw /
        # 显示名降级告警都要按新产出重算）
        candidate = _assemble_briefing(
            top10_with_summaries, candidates, fallback_layer, today_str,
            synthesis_model=synthesis_result.get("synthesis_model", ""),
        )
        errors = _schema_errors(candidate)
        if errors:
            if regen < MAX_SYNTHESIS_REGENERATIONS:
                regen_reason = REGEN_REASON_SCHEMA_INVALID
                sys.stderr.write(
                    f"WARNING [github-pipeline]: schema 校验不过（{errors}）—— "
                    "判为可重生成，触发下一轮\n"
                )
                continue
            _emit_sc7_metrics(_sc7_metrics)
            _write_skipped(
                f"schema_validation_failed: {errors}",
                len(top10_with_summaries), fallback_layer,
                stage=FAILURE_STAGE_VALIDATE,
                failure_class=FAILURE_CLASS_LLM_OUTPUT_QUALITY,
            )
            return EXIT_NOT_DELIVERED

        briefing = candidate
        break

    if briefing is None:
        # 不可达：循环内每条失败路径都已 return。留一条 fail-closed 兜底，
        # 而不是让 None 往下走成 TypeError（宁可可判读地拒绝，不要栈回溯）。
        _emit_sc7_metrics(_sc7_metrics)
        _write_failed("synthesis_regeneration_no_result",
                      fallback_layer=fallback_layer,
                      stage=FAILURE_STAGE_SYNTHESIZE,
                      failure_class=FAILURE_CLASS_INTERNAL)
        return EXIT_NOT_DELIVERED

    # Step 8b: write vault
    if len(briefing["top_repos"]) < _MIN_REPOS_FOR_BRIEFING:
        _emit_sc7_metrics(_sc7_metrics)
        _write_skipped("top_repos_below_min", len(briefing["top_repos"]), fallback_layer,
                       stage=FAILURE_STAGE_VALIDATE,
                       failure_class=FAILURE_CLASS_UPSTREAM_INTAKE)
        return EXIT_NOT_DELIVERED

    # Write vault
    with _StageTimer("write_vault"):
        out_dir = vault / OUTPUT_DIR_TEMPLATE
        if not vault.is_dir():
            if mock:
                vault.mkdir(parents=True, exist_ok=True)
            else:
                _write_failed(f"vault_not_ready: {vault}",
                              fallback_layer=fallback_layer,
                              stage=FAILURE_STAGE_WRITE_VAULT,
                              failure_class=FAILURE_CLASS_VAULT_IO)
                return EXIT_NOT_DELIVERED
        out_dir.mkdir(parents=True, exist_ok=True)
        base_name = f"{today_str} GitHub Briefing"
        md_path = out_dir / f"{base_name}.md"
        json_path = out_dir / f"{base_name}.json"

        _ts_print(f"briefing: WRITE_START {md_path}")
        _atomic_write_text(md_path, _render_markdown(briefing))
        _atomic_write_text(json_path, json.dumps(briefing, ensure_ascii=False, indent=2))

    # 仅在简报真正写盘后，把这批 repo 标记为"已推荐"（去重窗口内不再重复推）。
    # 标记发生在写成功之后，避免被 skip 的候选误占用 novelty 名额。
    # first_mention_at 必须一起落库：读端 is_duplicate 的第二判据（无新提及不复推，
    # dedup_sqlite 不变式 2）就靠这一列。不传 → 列恒为 NULL → 第二判定恒 False，
    # 该防御在生产 100% 空转。
    #
    # 收益归属（2026-07-26 独立回放 48 份已发布 GitHub 简报 / 480 条 top_repos 核实）：
    # 不变式 2 对**历史**重复的独占拦截量 = 0/179。179 对跨日重复里确有 128 对
    # （71.5%）first_mention_at 逐字相同，但这 128 对间隔全部 ≤6 天，落在 7 天去重窗
    # 内 —— is_duplicate 第 1 步 `seen_at >= cutoff` 已返回 True，第 2 判据根本读不到。
    # 唯一需要第 2 判据的场景（间隔 >7 天）共 16 对，其中 first_mention_at 相同的是
    # 0 对。真正消掉这批重复的是把默认去重窗口从 3 天抬到 ≥采集窗口（163/179 = 91.1%）。
    # 故本判据的价值是**防御未来场景**（"出了去重窗、但仍是同一条旧提及"，历史样本里
    # 尚未出现），不是已实现的收益 —— 不得据此宣称已消掉 71.5% 的重复。
    # 保留它仍是对的：一旦该场景出现，缺这一列就会静默复推，故写端必须传。
    _novelty = DedupStore(_novelty_db)
    try:
        for r in briefing["top_repos"]:
            _novelty.mark_seen(r["repo_url"], r.get("first_mention_at"))
        _novelty.prune()
    finally:
        _novelty.close()

    _emit_sc7_metrics(_sc7_metrics)
    # SC-4: CLI 层最终交付断言 — 写盘后复查当日 md 真实存在，否则 fail-visible
    if not md_path.exists():
        _write_failed(f"vault_write_verified_missing: {md_path}",
                      fallback_layer=fallback_layer,
                      stage=FAILURE_STAGE_WRITE_VAULT,
                      failure_class=FAILURE_CLASS_VAULT_IO)
        return EXIT_NOT_DELIVERED
    _ts_print(f"briefing: WROTE {md_path}")
    return EXIT_DELIVERED


def _is_regenerable_synthesis_error(exc: BaseException) -> bool:
    """该 synthesis 异常是否属于「换一次生成就可能好」的**输出质量**类失败。

    只认 `json.JSONDecodeError`：这正是 07-22/25/28 三次生产失败逃出 `synthesize()`
    的异常类型 —— synthesis.py 内部那次 JSON 重试之后的第二次 `json.loads` 不再被捕获，
    于是原样冒泡，文本形如 `Unterminated string starting at: line 44 column 16 (char 2490)`。
    模型这一轮把 JSON 写坏了，换一轮生成有实质胜率。

    明确**排除**（重试无胜率，只白烧额度）：
    - `SynthesisTruncatedError`（finish_reason=length，输出预算撞顶）—— 同上限重跑必然
      截在同一处，该类型的 docstring 已论证过；它继承 RuntimeError 而非 JSONDecodeError，
      靠类型即可分开，不需要匹配文本。
    - 429 / 预算耗尽（`RuntimeError: DeepSeek API failed after 3 retries: HTTP Error 429`）
      —— 重跑只会继续撞同一个已耗尽的配额；HTTP 层重试与模型族降级是 synthesis 层的
      职责，不在 pipeline 层兜。
    - 确定性参数错（空 grounding / prompt 过大 / repo 数超上限 / 条数不匹配）—— 输入
      不变则必然复现，重试是纯浪费。
    """
    return isinstance(exc, json.JSONDecodeError)


def _classify_synthesis_failure(exc: BaseException) -> str:
    """synthesize 抛出的异常归到哪个 failure_class（告警据此说"能不能补跑"）。

    分类依据是**类型 + 契约 2 的 error_type**，不匹配错误文本：
    - `SynthesisTruncatedError` → 输出预算撞顶，补跑无用（`output_budget`）
    - 带 `diagnosis` 的调用层错（429/HTTP）→ `llm_provider`，可补救性由预算 scope 决定
    - `json.JSONDecodeError` → 产出质量（重生成已耗尽才会走到这）
    - 其余 → internal（含确定性参数错，补跑无用）
    """
    from .synthesis import SynthesisTruncatedError  # 局部导入：避免加重模块导入图

    if isinstance(exc, SynthesisTruncatedError):
        return FAILURE_CLASS_OUTPUT_BUDGET
    if isinstance(exc, json.JSONDecodeError):
        return FAILURE_CLASS_LLM_OUTPUT_QUALITY
    if _provider_diagnosis(exc) is not None:
        return FAILURE_CLASS_LLM_PROVIDER
    return FAILURE_CLASS_INTERNAL


def _assemble_briefing(top10_with_summaries: list[dict], candidates: list,
                       fallback_layer: str, today_str: str,
                       synthesis_model: str = "") -> dict:
    """把 synthesis 产出组装成待校验的 briefing dict（每轮重生成后重新调用）。

    `synthesis_model` 记录实际产出的模型。主族预算耗尽后族降级时，这里落的是**备用族**模型
    —— 宪法 P4「失败可见」：降级必须留在产物里。此前它只出现在 stdout 的
    `SYNTHESIS_FALLBACK` 行，读简报的人（以及事后审计的人）看不出这份是谁产的。
    """
    # Inject engagement_raw before _clean_repo（必须在 clean 前，否则 engagement 字段被剥）
    for r in top10_with_summaries:
        r["engagement_raw"] = engagement_raw(r.get("engagement"))

    _warn_display_name_degraded(top10_with_summaries)

    briefing = {
        "schema_version": "v1",
        "briefing_type": "github-curator",
        "generated_at": datetime.now(UTC).isoformat(),
        "date": today_str,
        "candidate_pool_size": _pool_size(),
        "active_curators_today": list(set(c.mentioner for c in candidates)),
        "fallback_layer_used": fallback_layer,
        "top_repos": [_clean_repo(r) for r in top10_with_summaries],
    }
    # schema 里该字段是 minLength:1 的 optional —— 空值不写入，避免既有调用方
    # （未传该参数者）产出一份 schema 不合法的 briefing。生产路径必然带真值。
    if synthesis_model:
        briefing["synthesis_model"] = synthesis_model
    return briefing


def _schema_errors(briefing: dict) -> list:
    """对组装好的 briefing 跑 schema 校验（validator 只吃文件路径，故走临时文件）。"""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        json.dump(briefing, tf)
        tf_path = tf.name
    try:
        return validate(tf_path)
    finally:
        Path(tf_path).unlink(missing_ok=True)


def _aggregate(candidates: list) -> dict[str, dict]:
    """Aggregate candidates by normalized repo_url."""
    result: dict[str, dict] = {}
    for c in candidates:
        if c.repo_url not in result:
            result[c.repo_url] = {
                "repo_url": c.repo_url,
                "mention_count": 0,
                "mentioners": [],
                "first_mention_at": c.mentioned_at,
                "engagement": None,
            }
        entry = result[c.repo_url]
        entry["mention_count"] += 1
        if c.mentioner not in entry["mentioners"]:
            entry["mentioners"].append(c.mentioner)
        if c.mentioned_at < entry["first_mention_at"]:
            entry["first_mention_at"] = c.mentioned_at
        # engagement 取 MAX（最火一次背书），不取 sum：sum 会随被提次数累加 →
        # 重新引入 mention_count 偏置，与 v3 去共识主轴的目标相悖。
        # 严格 > ：并列保留首见（含 None 起步，0 > 0 为 False → 后续真 metrics 正确接管）。
        if engagement_raw(c.metrics) > engagement_raw(entry["engagement"]):
            entry["engagement"] = c.metrics
    return result


def _mock_enrich(repo_url: str) -> dict:
    """Deterministic mock enrichment（hermetic，不打 GitHub API）。
    stars 由 repo_url hash 派生，保证 scoring 有稳定排序。"""
    h = sum(ord(ch) for ch in repo_url)
    return {
        "repo_url": repo_url,
        "stars": 1000 + (h % 9000),
        "language": ["Python", "TypeScript", "Rust", "Go"][h % 4],
        "topic_tags": ["ai", "agent", "llm"][: (h % 3) + 1],
        "created_at": "2024-01-01T00:00:00Z",
        "readme_excerpt": f"Mock README excerpt for {repo_url}. " * 5,
        "commits_7d": h % 50,
        "enrich_ok": True,
    }


def _mock_intake(force_layer: int | None = None) -> tuple[list, str]:
    """Generate mock candidates for testing."""
    from .intake import Candidate
    now = datetime.now(UTC).isoformat()

    mock_repos = [
        ("microsoft/qlib", ["@simonw", "@karpathy", "@GitTrend0x"]),
        ("vercel/next.js", ["@rauchg", "@leeerob", "@cassidoo"]),
        ("shadcn-ui/ui", ["@shadcn", "@cassidoo", "@rauchg"]),
        ("langchain-ai/langgraph", ["@hwchase17", "@simonw", "@karpathy"]),
        ("jerryjliu/llama_index", ["@jerryjliu0", "@simonw", "@hwchase17"]),
        ("anthropics/anthropic-sdk-python", ["@alexalbert__", "@simonw", "@karpathy"]),
        ("openai/openai-python", ["@OfficialLoganK", "@simonw", "@hwchase17"]),
        ("microsoft/autogen", ["@matthewberman", "@karpathy", "@GitTrend0x"]),
        ("openinterpreter/open-interpreter", ["@LiorOnAI", "@matthewberman", "@simonw"]),
        ("exo-explore/exo", ["@levelsio", "@rauchg", "@cassidoo"]),
        ("t3-oss/create-t3-app", ["@t3dotgg", "@rauchg", "@shadcn"]),
        ("trpc/trpc", ["@t3dotgg", "@simonw", "@karpathy"]),
    ]

    # layer2/3 兜底必须复刻真实形态：trending/manual 每个 repo 仅 @github_trending 单次
    # mention(count=1)。早期 mock 对所有层都用多 mentioner 数据，掩盖了 layer2 被
    # mention 阈值(min=2)全杀的真 bug（真实链路验收 FIX-SC-2）。
    if force_layer in (2, 3):
        layer = f"layer{force_layer}"
        candidates = [
            Candidate(
                repo_url=f"https://github.com/{repo}",
                mentioner="@github_trending",
                tweet_id=None,
                mentioned_at=now,
                tweet_text=None,
            )
            for repo, _ in mock_repos
        ]
        return candidates, layer

    candidates = []
    for repo, mentioners in mock_repos:
        # 确定性 mock metrics（按 repo hash 派生），让 mock 链路也走 engagement 维
        h = sum(ord(ch) for ch in repo)
        metrics = {
            "likes": 100 + (h % 5000),
            "retweets": 10 + (h % 500),
            "bookmarks": 20 + (h % 800),
            "views": 10000 + (h % 90000),
        }
        for m in mentioners:
            candidates.append(Candidate(
                repo_url=f"https://github.com/{repo}",
                mentioner=m,
                tweet_id="mock",
                mentioned_at=now,
                tweet_text="mock tweet",
                metrics=metrics,
            ))

    layer = "layer1" if force_layer is None else f"layer{force_layer}"
    return candidates, layer


def _real_intake(force_layer: int | None = None) -> tuple[list, str, dict]:
    """Run real three-layer intake. Returns (candidates, selected_layer, l1_recency_stats)."""
    from .intake import _ZERO_RECENCY_STATS

    if force_layer == 1:
        l1 = TwitterCollector().collect(POOL_CONFIG)
        return l1.candidates, "layer1", l1.recency_stats
    if force_layer == 2:
        l2 = TrendingHTMLCollector().collect()
        return l2.candidates, "layer2", _ZERO_RECENCY_STATS
    if force_layer == 3:
        # 强制 layer3：与 mock 模式对称（_mock_intake 也处理 force_layer 3）。
        # 无 manual JSON 时返回空 → pipeline 走 too_few/skip，行为可判定。
        from .intake.manual_json_collector import ManualJSONCollector
        l3 = ManualJSONCollector().collect()
        return l3.candidates, "layer3", _ZERO_RECENCY_STATS

    # 无强制：完整三层 fallback judge（decide 内部含 l1→l2→l3 链）。
    l1 = TwitterCollector().collect(POOL_CONFIG)
    def l2_runner():
        return TrendingHTMLCollector().collect()

    decision = decide(l1, l2_runner)

    # 即使 decide 选了 L2，也带 L1 的 recency_stats（SC-7 验收需要）
    # 注：pre-novelty 死区补位已移至 run_pipeline novelty 去重后执行（SC-1 fix）
    return decision.candidates, decision.selected_layer, l1.recency_stats


def _pool_size() -> int:
    try:
        import yaml
        with open(POOL_CONFIG) as f:
            data = yaml.safe_load(f)
        return len(data.get("pool", []))
    except Exception:
        return 0


def _load_tier_map() -> dict[str, int]:
    """Load {handle (without @): tier} mapping from pool config."""
    try:
        import yaml
        with open(POOL_CONFIG) as f:
            data = yaml.safe_load(f)
        tier_map = {}
        for curator in data.get("pool", []):
            handle = curator.get("handle", "").lstrip("@")
            tier = curator.get("tier", 0)
            if tier > 0:
                tier_map[handle] = tier
        return tier_map
    except Exception:
        return {}


def _provider_diagnosis(exc: BaseException) -> dict | None:
    """从异常里取契约 2 的 provider 诊断字段（`error_type` / `budget_scope` / `spend` /
    `cap` / `budget_date` / `provider_code`）。

    鸭子类型消费 `synthesis.SynthesisAPIError.diagnosis.as_dict()`（字段名由
    `common/http_retry.ProviderErrorDiagnosis` 定义，即契约 2 的权威形态）——
    **不在本模块重新解析 429 文本**（宪法「约定优于新奇」：解析器已经有了，
    再写一个就会有两套对不上的归因）。拿不到就返回 None，绝不让诊断落盘本身失败。
    """
    diagnosis = getattr(exc, "diagnosis", None)
    as_dict = getattr(diagnosis, "as_dict", None)
    if not callable(as_dict):
        return None
    try:
        payload = as_dict()
    except Exception:  # noqa: BLE001 — 诊断写盘绝不能因为归因取不到而失败
        return None
    return payload if isinstance(payload, dict) else None


def _remediation(failure_class: str, provider_diagnosis: dict | None) -> tuple[bool, str]:
    """(是否可补救, 给告警看的一句话处置建议)。

    这句话会被 `feishu_sync/blocked.py:blocking_reason()` 当作 `detail` 拼进告警文案
    （形态 `status: reason（detail）`），所以必须是**可执行的下一步**，
    不是"请检查日志"这类空话。
    """
    if failure_class == FAILURE_CLASS_LLM_PROVIDER and provider_diagnosis:
        if provider_diagnosis.get("error_type") == "budget_exceeded":
            scope = provider_diagnosis.get("budget_scope") or "unknown"
            spend, cap = provider_diagnosis.get("spend"), provider_diagnosis.get("cap")
            amount = f"（spend={spend} / cap={cap}）" if cap is not None else ""
            if scope == "utc_daily":
                day = provider_diagnosis.get("budget_date") or "当日"
                # 不再建议换模型族：额度是**账户级**的，主备族同 Gateway 同 key 同 user，
                # 换族拿不回额度（R1 审查 F13/CADV-A10）。唯一有效的处置是等边界。
                return True, (
                    f"共享 Gateway 账户的 UTC 日预算已打穿{amount}，"
                    f"UTC {day} 内重跑必然再撞同一上限（换模型族也无效："
                    "主备族共用同一账户额度）—— 等 UTC 日预算重置后（11:00 CST 补跑窗口）重试"
                )
            return True, (
                f"共享 Gateway 账户的总预算已打穿{amount} —— "
                "需 owner 调整额度后才能重跑，单纯补跑不会成功"
            )
        if provider_diagnosis.get("error_type") == "rate_limit":
            return True, "provider 限流（非预算耗尽）—— 退避后重跑可能成功"
    return {
        FAILURE_CLASS_CONFIG: (False, "部署/配置错误，补跑不会成功，需人工修配置"),
        FAILURE_CLASS_UPSTREAM_INTAKE: (True, "上游素材不足 —— 等下一轮采集或 backstop 补跑"),
        FAILURE_CLASS_ENRICH_DEGRADED: (
            True, "GitHub API 限流/网络故障导致 enrich 过半失败 —— 稍后补跑可能成功",
        ),
        FAILURE_CLASS_LLM_PROVIDER: (True, "LLM 调用层失败 —— 稍后补跑可能成功"),
        FAILURE_CLASS_LLM_OUTPUT_QUALITY: (
            True,
            f"LLM 产出质量不过关，已在 pipeline 层重生成 "
            f"{MAX_SYNTHESIS_REGENERATIONS} 次仍未收敛 —— 补跑可能成功",
        ),
        FAILURE_CLASS_OUTPUT_BUDGET: (
            False,
            "输出预算撞顶（finish_reason=length）—— 同上限重跑必然截在同一处，"
            "需加大 MAX_OUTPUT_TOKENS 或减少单次 repo 数",
        ),
        FAILURE_CLASS_VAULT_IO: (False, "vault 未挂载或不可写 —— 先修挂载，补跑才有意义"),
    }.get(failure_class, (False, "未分类失败 —— 需人工查 stdout/stderr 定位"))


def _diag_payload(status: str, reason: str, fallback_layer: str,
                  stage: str, failure_class: str,
                  provider_diagnosis: dict | None) -> dict:
    """两个诊断文件的共同骨架。

    `date` 是这里最关键的一个字段（见模块 FAILURE_* 常量注释里的 2026-08-03 事故）：
    用 `date.today()` 取**本地日**，与 vault 文件名 `f"{today_str} GitHub Briefing.md"`
    同源，也就是告警算 expected_path 用的同一把尺子。不要改成 UTC 日 —— 那正是
    告警此前对不上、只能报"文件缺失"的原因。
    """
    remediable, detail = _remediation(failure_class, provider_diagnosis)
    payload = {
        "status": status,
        "date": date.today().isoformat(),
        "stage": stage,
        "failure_class": failure_class,
        "reason": reason,
        "detail": detail,
        "remediable": remediable,
        "fallback_layer_used": fallback_layer,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    if provider_diagnosis:
        payload["provider_diagnosis"] = provider_diagnosis
    return payload


def _write_skipped(reason: str, candidates_count: int, fallback_layer: str,
                   diag: dict | None = None, *,
                   stage: str = FAILURE_STAGE_UNKNOWN,
                   failure_class: str = FAILURE_CLASS_INTERNAL,
                   provider_diagnosis: dict | None = None) -> None:
    """写当日 skip 诊断。

    stage / failure_class 有默认值（而非必填）是刻意的：这个函数只在**失败路径**上被
    调用，忘传参数不该在最糟的时刻抛 TypeError 把诊断整份丢掉 —— 退化成
    `unknown/internal` 仍然留下 reason 供归因。"每个调用点都显式传参"由测试静态守。
    """
    skipped = _diag_payload("skipped", reason, fallback_layer, stage, failure_class,
                            provider_diagnosis)
    skipped["candidates_count"] = candidates_count
    if diag:
        skipped["diag"] = diag
    (_PROJECT_ROOT / "briefing-skipped.json").write_text(
        json.dumps(skipped, indent=2, ensure_ascii=False)
    )
    _ts_print(f"briefing: SKIPPED ({reason})")


def _write_failed(reason: str, fallback_layer: str = "skip", *,
                  stage: str = FAILURE_STAGE_UNKNOWN,
                  failure_class: str = FAILURE_CLASS_INTERNAL,
                  provider_diagnosis: dict | None = None) -> None:
    """写当日 failed 诊断（见 `_write_skipped` 关于默认值的说明）。"""
    failed = _diag_payload("failed", reason, fallback_layer, stage, failure_class,
                           provider_diagnosis)
    (_PROJECT_ROOT / "briefing-failed.json").write_text(
        json.dumps(failed, indent=2, ensure_ascii=False)
    )
    print(f"briefing: FAILED ({reason})")


def _display_name(repo: dict) -> str:
    """Markdown 标题用的仓库显示名 — 保留 GitHub 侧真实大小写。

    去重键 `repo_url` 是**无条件小写**的（GitHub owner/repo 大小写不敏感，键必须
    小写才稳定），直接从它切名会把 `id-Software/Quake` 渲染成 `id-software/quake`、
    `xianfei/SysMocap` 渲染成 `sysmocap`。故显示层单独取权威大小写：

    1. `repo_full_name` — GitHub API `full_name`（301 改名后为新名），最权威；
    2. `display_slug(url)` — 无 API 数据时的兜底（保留 url 里残存的大小写）；
    3. 末段切分 — 兜底的兜底（非标准 URL）。

    只影响显示；`repo_url` 一字不改，去重键仍全小写。

    **兜底 2 在生产恢复不了大小写**：collector 入库时就写的是 `normalize(url)`
    （`twitter_collector.py` → `repo_url=normalized`，无条件 `.lower()`），到渲染时
    URL 里已经没有大小写可保留 —— 实测 `display_slug(normalize(".../id-Software/Quake"))
    == "id-software/quake"`。所以显示名**完全依赖** `repo_full_name`：API 失败/无
    `full_name` 时结果就是小写降级，不可恢复。这类降级必须可见（constitution 原则 4），
    由 `_warn_display_name_degraded()` 在写盘前逐条点名；本函数保持纯函数不打日志，
    因为 `_render_markdown` 在测试/重渲染里会被反复调用。
    """
    full_name = (repo.get("repo_full_name") or "").strip()
    if full_name:
        return full_name
    url = repo.get("repo_url", "")
    if not url:
        return "unknown"
    return display_slug(url) or "/".join(url.rstrip("/").split("/")[-2:])


def _warn_display_name_degraded(repos: list[dict]) -> int:
    """缺 `repo_full_name` → 显示名降级为小写且不可恢复，逐条告警。返回降级条数。

    为什么必须告警而不是静默兜底：`_display_name` 的兜底链在生产**恢复不了大小写**
    （见该函数 docstring：collector 已把 `repo_url` 小写化）。于是 API 没给
    `full_name` 时，简报标题会安静地写成 `id-software/quake`、`xianfei/sysmocap`，
    读者与后来的 agent 都无从知道这是降级产物还是仓库真名。不阻断（小写名仍可点、
    仍指向正确仓库），但不得静默 —— 静默降级正是 constitution 原则 4 禁止的那类。
    """
    degraded = [r for r in repos if not (r.get("repo_full_name") or "").strip()]
    if not degraded:
        return 0
    names = ", ".join(_display_name(r) for r in degraded)
    sys.stderr.write(
        f"WARNING [github-pipeline]: {len(degraded)}/{len(repos)} 条缺 repo_full_name"
        "（GitHub API 未返回 full_name / 调用失败）— 显示名降级为小写且**不可恢复**"
        f"（repo_url 已被 collector 归一化为小写）: {names}\n"
    )
    return len(degraded)


def _render_markdown(briefing: dict) -> str:
    """Render briefing JSON to Markdown per §6.12 template."""
    date_str = briefing["date"]
    layer = briefing["fallback_layer_used"]
    pool_size = briefing["candidate_pool_size"]
    active = len(briefing["active_curators_today"])

    lines = [
        "---",
        f"date: {date_str}",
        "type: github-briefing",
        "source: github-curator",
        f"fallback_layer: {layer}",
        f"candidate_pool_size: {pool_size}",
        f"active_curators_today: {active}",
        f"generated_at: {briefing['generated_at']}",
        # 降级可见性（宪法 P4）：主族预算耗尽后族降级时，这里显示的是备用族模型。
        # 读简报的人与事后审计的人都能看出这份是谁产的，而不是只在 stdout 一闪而过。
        *(
            [f"synthesis_model: {briefing['synthesis_model']}"]
            if briefing.get("synthesis_model")
            else []
        ),
        "tags:",
        "  - github",
        "  - ai-trends",
        "  - daily",
        "---",
        "",
        f"# GitHub 项目日报 · {date_str}",
        "",
    ]

    # 正常 layer1 博主精选日不输出此行；仅兜底链路(layer2/3)给读者一句中性提示，
    # 让读者从正文就能区分"博主精选" vs "兜底产出"（layer 编号/活跃博主数留 frontmatter
    # 与日志给运维）。措辞保持中性事实陈述，不用"数据不足"等可能误导的因果归因。
    layer_warnings = {
        "layer2": "> ℹ️ 本期为 GitHub 热榜兜底产出(非博主精选)。",
        "layer3": "> ℹ️ 本期为手动补充列表(自动链路未产出)。",
        "layer1+2": "> ℹ️ 本期为博主精选 + GitHub 热榜混合产出。",
    }
    warning = layer_warnings.get(layer)
    if warning:
        lines.append(warning)
        lines.append("")

    for i, repo in enumerate(briefing["top_repos"], 1):
        url = repo.get("repo_url", "")
        name = _display_name(repo)
        stars = repo.get("stars", 0)
        stars_str = f"{stars:,}" if stars >= 1000 else str(stars)
        language = repo.get("language") or "unknown"
        tags = " ".join(f"`{t}`" for t in (repo.get("topic_tags", [])[:5]))
        mentioners = " · ".join(f"@{m.lstrip('@')}" for m in repo.get("mentioners", []))
        mention_count = repo.get("mention_count", 1)
        score = repo.get("score", 0)
        first_mention = repo.get("first_mention_at", "")[:16]
        summary = repo.get("summary", "")
        use_case = repo.get("use_case", "")
        eng_raw = repo.get("engagement_raw", 0)

        mention_line = f"{tags} · **{mention_count} mentions**（{mentioners}）"
        if eng_raw > 0:
            mention_line += f" · 🔥 {eng_raw}"

        lines.extend([
            f"## {i}. [{name}]({url}) :star: {stars_str} · {language}",
            "",
            mention_line,
            f"score {score:.2f} · 推荐于 {first_mention}",
            "",
            f"**做什么**: {summary}",
            "",
            f"**适用场景**: {use_case}",
            "",
            "---",
            "",
        ])

    return "\n".join(lines)


def _ts_print(msg: str) -> None:
    """Print msg prefixed with ISO timestamp for multi-run log disambiguation (SC-8)."""
    print(f"[{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}")


def _emit_sc7_metrics(metrics: dict) -> None:
    """Emit deterministic SC7_METRICS line to stdout (SC-7 contract)."""
    _ts_print(f"SC7_METRICS: {json.dumps(metrics, ensure_ascii=False)}")


def _make_zero_sc7_metrics() -> dict:
    """Zero-placeholder SC7 metrics for paths without L1 recency data."""
    return {
        "candidates_before_recency": 0,
        "candidates_after_recency": 0,
        "recency_dropped": 0,
        "timestamp_missing": 0,
        "timestamp_parse_failed": 0,
        "candidates_after_safety": 0,
        "fallback_triggered": False,
        "top10_all_layer1": False,
    }


if __name__ == "__main__":
    import sys
    mock = "--mock" in sys.argv
    force_layer = None
    for i, arg in enumerate(sys.argv):
        if arg == "--force-layer" and i + 1 < len(sys.argv):
            try:
                force_layer = int(sys.argv[i + 1])
            except ValueError:
                pass
    sys.exit(run_pipeline(mock=mock, force_layer=force_layer))
