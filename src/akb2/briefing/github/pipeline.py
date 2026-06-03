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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .intake import CollectorResult
from .intake.fallback_judge import decide
from .intake.trending_html_collector import TrendingHTMLCollector
from .intake.twitter_collector import TwitterCollector
from .enricher import enrich
from .scoring import score_batch, mention_threshold
from .star_history import StarHistory
from .synthesis import synthesize
from .validator import validate

_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "")) if os.environ.get("PROJECT_ROOT") else Path(__file__).resolve().parents[4]  # src/akb2/briefing/github/pipeline.py → project root (parents[4]=<root>; 旧值 [5] 越到 <root> 上一级，导致 POOL_CONFIG/skip 文件落错目录)
POOL_CONFIG = _PROJECT_ROOT / "config" / "github_curator_pool.yaml"
OUTPUT_DIR_TEMPLATE = "AKB2.0/AI TRENDS/GitHub"

# Schema-approved repo fields (strip enrichment internals before vault write)
_SCHEMA_REPO_FIELDS = {
    "repo_url", "stars", "language", "topic_tags", "mention_count",
    "mentioners", "first_mention_at", "score", "summary", "use_case",
}

def _clean_repo(repo: dict) -> dict:
    """Keep only schema-approved repo fields, stripping enrichment internals."""
    return {k: v for k, v in repo.items() if k in _SCHEMA_REPO_FIELDS}


def run_pipeline(mock: bool = False, force_layer: int | None = None) -> int:
    """Run the full GitHub briefing pipeline. Returns 0 on success (including skip)."""
    vault_root = os.environ.get("VAULT_ROOT", "")
    if not vault_root or not vault_root.startswith("/"):
        _write_skipped("vault_root_missing_or_relative", 0, "skip")
        return 0

    vault = Path(vault_root)

    # Step 1: Intake via three-layer fallback
    if mock:
        candidates, fallback_layer = _mock_intake(force_layer)
    else:
        candidates, fallback_layer = _real_intake(force_layer)

    if not candidates:
        _write_skipped("no_candidates_after_intake", len(candidates), fallback_layer)
        return 0

    # Step 2-3: Aggregate (dedup by repo_url)
    aggregated = _aggregate(candidates)
    if len(aggregated) < 10:
        _write_skipped("too_few_aggregated_repos", len(aggregated), fallback_layer)
        return 0

    # Build tier_map from pool config
    tier_map = _load_tier_map()

    # Initialize star_history (ephemeral per-run; day 1 velocity will be None)
    star_db = _PROJECT_ROOT / "data" / "star_history.sqlite"
    star_db.parent.mkdir(parents=True, exist_ok=True)
    star_history = StarHistory(star_db)

    # Step 4: Enrich via GitHub API
    enriched = []
    enrich_attempted = 0
    enrich_failed = 0
    for repo_url in list(aggregated.keys())[:30]:  # Cap at 30 for rate limits
        # mock 模式必须 hermetic：不打真实 GitHub API（否则受限额影响 flaky，
        # 且会被 enrich_degraded gate 误杀）。真实模式才走 enrich()。
        data = _mock_enrich(repo_url) if mock else enrich(repo_url)
        enrich_attempted += 1
        # fail-safe 默认：缺 enrich_ok 字段 → 视为失败（保守计入降级），
        # 避免未来新增 enrich 变体忘记打标记时静默绕过 enrich_degraded 门控。
        if not data.get("enrich_ok", False):
            enrich_failed += 1
        # Merge aggregate data into enriched
        agg = aggregated[repo_url]
        data["mention_count"] = agg["mention_count"]
        data["mentioners"] = agg["mentioners"]
        data["first_mention_at"] = agg["first_mention_at"]

        # Record star snapshot (for future velocity computation)
        if data.get("stars", 0) > 0:
            star_history.record(repo_url, data["stars"])
        # Inject velocity_7d (None until ≥7d of snapshots accumulate)
        data["velocity_7d"] = star_history.velocity_7d(repo_url)

        enriched.append(data)

    star_history.close()

    # Enrich 降级红线：过半 enrich 失败（API 失败/限流）→ 可见 skip，不静默产全 0 简报。
    # GitHub API 限流或网络故障会让 enrich 返回 stars=0/lang=None，若放行会产出
    # schema 合法但内容全空的简报；这里 fail-visibly 而非 fail-silently。
    if enrich_attempted > 0 and enrich_failed * 2 > enrich_attempted:
        _write_skipped(
            f"enrich_degraded: {enrich_failed}/{enrich_attempted} enrich calls failed (rate-limit/network)",
            enrich_attempted, fallback_layer,
        )
        return 0

    # Step 5: Filter by mention threshold.
    # mention 共识过滤只对 twitter 源(layer1)有意义；layer2(trending)/layer3(manual)
    # 已是预排序源，每个 repo 仅 @github_trending 单次 mention(count=1)，会被 min=2
    # 阈值全杀 → 兜底永远 skip。故 fallback 层旁路 mention 过滤。
    if fallback_layer in ("layer2", "layer3"):
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

    if len(top10) < 10:
        _write_skipped("too_few_after_filter", len(top10), fallback_layer)
        return 0

    # Step 7: Synthesize
    try:
        synthesis_result = synthesize(top10)
        top10_with_summaries = synthesis_result["top_repos"]
    except Exception as e:
        _write_failed(f"synthesis_failed: {e}", fallback_layer=fallback_layer)
        return 0

    # Step 8: Render + validate + write vault
    next_day = (date.today() + timedelta(days=1)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    briefing = {
        "schema_version": "v1",
        "briefing_type": "github-curator",
        "generated_at": now_iso,
        "date": next_day,
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
        _write_skipped(f"schema_validation_failed: {errors}", len(top10_with_summaries), fallback_layer)
        return 0

    if len(briefing["top_repos"]) != 10:
        _write_skipped("top_repos_not_10", len(briefing["top_repos"]), fallback_layer)
        return 0

    # Write vault
    out_dir = vault / OUTPUT_DIR_TEMPLATE
    out_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"{next_day} GitHub Briefing"
    md_path = out_dir / f"{base_name}.md"
    json_path = out_dir / f"{base_name}.json"

    md_path.write_text(_render_markdown(briefing))
    json_path.write_text(json.dumps(briefing, ensure_ascii=False, indent=2))

    print(f"briefing: WROTE {md_path}")
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
            }
        entry = result[c.repo_url]
        entry["mention_count"] += 1
        if c.mentioner not in entry["mentioners"]:
            entry["mentioners"].append(c.mentioner)
        if c.mentioned_at < entry["first_mention_at"]:
            entry["first_mention_at"] = c.mentioned_at
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
    now = datetime.now(timezone.utc).isoformat()

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
        for m in mentioners:
            candidates.append(Candidate(
                repo_url=f"https://github.com/{repo}",
                mentioner=m,
                tweet_id="mock",
                mentioned_at=now,
                tweet_text="mock tweet",
            ))

    layer = "layer1" if force_layer is None else f"layer{force_layer}"
    return candidates, layer


def _real_intake(force_layer: int | None = None) -> tuple[list, str]:
    """Run real three-layer intake."""
    if force_layer == 1:
        l1 = TwitterCollector().collect(POOL_CONFIG)
        return l1.candidates, "layer1"
    if force_layer == 2:
        l2 = TrendingHTMLCollector().collect()
        return l2.candidates, "layer2"
    if force_layer == 3:
        # 强制 layer3：与 mock 模式对称（_mock_intake 也处理 force_layer 3）。
        # 无 manual JSON 时返回空 → pipeline 走 too_few/skip，行为可判定。
        from .intake.manual_json_collector import ManualJSONCollector
        l3 = ManualJSONCollector().collect()
        return l3.candidates, "layer3"

    # 无强制：完整三层 fallback judge（decide 内部含 l1→l2→l3 链）。
    l1 = TwitterCollector().collect(POOL_CONFIG)
    l2_runner = lambda: TrendingHTMLCollector().collect()
    decision = decide(l1, l2_runner)
    return decision.candidates, decision.selected_layer


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


def _write_skipped(reason: str, candidates_count: int, fallback_layer: str) -> None:
    skipped = {
        "status": "skipped",
        "reason": reason,
        "candidates_count": candidates_count,
        "fallback_layer_used": fallback_layer,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (_PROJECT_ROOT / "briefing-skipped.json").write_text(json.dumps(skipped, indent=2))
    print(f"briefing: SKIPPED ({reason})")


def _write_failed(reason: str, fallback_layer: str = "skip") -> None:
    failed = {
        "status": "failed",
        "reason": reason,
        "fallback_layer_used": fallback_layer,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (_PROJECT_ROOT / "briefing-failed.json").write_text(json.dumps(failed, indent=2))
    print(f"briefing: FAILED ({reason})")


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

    # 正常链路不输出诊断行；仅兜底链路给读者一句人话警告
    # （layer 编号 + 活跃博主数留在 frontmatter / 日志里给运维诊断）
    layer_warnings = {
        "layer2": "> ⚠️ 今日博主数据不足，推荐来自 GitHub 热榜，质量需人工核查。",
        "layer3": "> ⚠️ 今日自动抓取失败，为手动补充列表。",
    }
    warning = layer_warnings.get(layer)
    if warning:
        lines.append(warning)
        lines.append("")

    for i, repo in enumerate(briefing["top_repos"], 1):
        url = repo.get("repo_url", "")
        name = "/".join(url.rstrip("/").split("/")[-2:]) if url else "unknown"
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

        lines.extend([
            f"## {i}. [{name}]({url}) :star: {stars_str} · {language}",
            "",
            f"{tags} · **{mention_count} mentions**（{mentioners}）",
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