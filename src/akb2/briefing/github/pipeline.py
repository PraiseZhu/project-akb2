"""8-step pipeline: intake → aggregate → enrich → filter → score → synthesize → render → vault.

Pipeline output contract (§step-4 PIPELINE_OUTPUT_CONTRACT):
- VAULT_ROOT must be set, absolute path, or write briefing-skipped.json + exit 0
- Only write .md + .json when top_repos == 10 AND schema validator PASS
- Otherwise write briefing-skipped.json (exit 0)
"""

import json
import os
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

from ...common.state_paths import resolve_state_file
from .dedup_sqlite import DedupStore
from .enricher import enrich
from .intake.fallback_judge import decide
from .intake.trending_html_collector import TrendingHTMLCollector
from .intake.twitter_collector import TwitterCollector
from .scoring import engagement_raw, mention_threshold, score_batch
from .star_history import StarHistory
from .synthesis import synthesize
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

    Returns 0 ONLY when the day's briefing .md is actually written to the vault
    (delivered). Any skip / failure path returns non-zero (SC-4 fail-visible) —
    exit code must reflect real delivery, never "0/missing product" 假成功.
    Diagnostic briefing-skipped.json / briefing-failed.json 仍写出供 backstop 诊断。
    """
    vault_root = os.environ.get("VAULT_ROOT", "")
    if not vault_root or not vault_root.startswith("/"):
        _write_skipped("vault_root_missing_or_relative", 0, "skip")
        return 1

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
        _write_skipped("no_candidates_after_intake", len(candidates), fallback_layer)
        return 1

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
                             "after_novelty": len(aggregated)})
        return 1

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
        )
        return 1

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
        _write_skipped("too_few_after_filter", len(top10), fallback_layer)
        return 1

    # Step 7: Synthesize
    try:
        with _StageTimer("synthesize"):
            # mock=mock：synthesis 的占位产出是显式 opt-in（见 synthesize docstring）。
            # 不透传时 `--mock` 自检链路在无 API key 环境下必然 fail-closed，
            # 整条 mock E2E 死掉。
            synthesis_result = synthesize(top10, mock=mock)
            top10_with_summaries = synthesis_result["top_repos"]
    except Exception as e:
        _emit_sc7_metrics(_sc7_metrics)
        _write_failed(f"synthesis_failed: {e}", fallback_layer=fallback_layer)
        return 1

    # Step 8: Render + validate + write vault
    today_str = date.today().isoformat()
    # 文件名 = 抓取日（去掉旧的 +1 偏移，对齐 social briefing）
    now_iso = datetime.now(UTC).isoformat()

    # Inject engagement_raw before _clean_repo（必须在 clean 前，否则 engagement 字段被剥）
    for r in top10_with_summaries:
        r["engagement_raw"] = engagement_raw(r.get("engagement"))

    briefing = {
        "schema_version": "v1",
        "briefing_type": "github-curator",
        "generated_at": now_iso,
        "date": today_str,
        "candidate_pool_size": _pool_size(),
        "active_curators_today": list(set(c.mentioner for c in candidates)),
        "fallback_layer_used": fallback_layer,
        "top_repos": [_clean_repo(r) for r in top10_with_summaries],
    }

    # Validate schema
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        json.dump(briefing, tf)
        tf_path = tf.name
    try:
        errors = validate(tf_path)
    finally:
        Path(tf_path).unlink(missing_ok=True)

    if errors:
        _emit_sc7_metrics(_sc7_metrics)
        _write_skipped(
            f"schema_validation_failed: {errors}",
            len(top10_with_summaries), fallback_layer,
        )
        return 1

    if len(briefing["top_repos"]) < _MIN_REPOS_FOR_BRIEFING:
        _emit_sc7_metrics(_sc7_metrics)
        _write_skipped("top_repos_below_min", len(briefing["top_repos"]), fallback_layer)
        return 1

    # Write vault
    with _StageTimer("write_vault"):
        out_dir = vault / OUTPUT_DIR_TEMPLATE
        if not vault.is_dir():
            if mock:
                vault.mkdir(parents=True, exist_ok=True)
            else:
                _write_failed(f"vault_not_ready: {vault}", fallback_layer=fallback_layer)
                return 1
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
    # 该功能在生产 100% 空转（实证：71.5% 的跨日重复是同一条旧提及被反复触发）。
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
                      fallback_layer=fallback_layer)
        return 1
    _ts_print(f"briefing: WROTE {md_path}")
    return 0


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


def _write_skipped(reason: str, candidates_count: int, fallback_layer: str,
                   diag: dict | None = None) -> None:
    now = datetime.now(UTC)
    skipped = {
        "status": "skipped",
        "reason": reason,
        "candidates_count": candidates_count,
        "fallback_layer_used": fallback_layer,
        "generated_at": now.isoformat(),
    }
    if diag:
        skipped["diag"] = diag
    (_PROJECT_ROOT / "briefing-skipped.json").write_text(json.dumps(skipped, indent=2))
    _ts_print(f"briefing: SKIPPED ({reason})")


def _write_failed(reason: str, fallback_layer: str = "skip") -> None:
    failed = {
        "status": "failed",
        "reason": reason,
        "fallback_layer_used": fallback_layer,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    (_PROJECT_ROOT / "briefing-failed.json").write_text(json.dumps(failed, indent=2))
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
    """
    full_name = (repo.get("repo_full_name") or "").strip()
    if full_name:
        return full_name
    url = repo.get("repo_url", "")
    if not url:
        return "unknown"
    return display_slug(url) or "/".join(url.rstrip("/").split("/")[-2:])


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
