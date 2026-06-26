"""End-to-end pipeline orchestrator for AKB2.0 social-trends daily briefing.

CLI: python3 -m akb2.briefing.pipeline --date YYYY-MM-DD --vault-root <path> [--dry-run-intake]

Pipeline stages:
  1. Intake  — subprocess l0_collector (--dry-run)
  2. Scoring — ScoreEngine.score_tweets() (direct import)
  3. Synthesis — synthesize() (direct import)
  4. Gate   — subprocess akb2_gate_check.py check (--dry-run)
  5. Output  — write briefing markdown to vault

Triple-assertion guarantee:
  proposed_gate_decision printed to stdout
  == value in frontmatter of final output file
  == gate CLI decision on final output file (gate is deterministic)

Design:
  - Write temp briefing to <vault-root>/staging/candidate/<date>-briefing.md
  - Run gate on temp file (guard_input_path accepts staging/candidate/)
  - Write final file to <vault-root>/AKB2.0/AI TRENDS/Social/<date+1> Daily Briefing.md
  - Copy physical file to Social/<date+1> Daily Briefing.md via shutil.copy2
    (no symlink — ensures Obsidian/iCloud compatibility; gate decision reject skips this step)
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zoneinfo
from pathlib import Path

from akb2.briefing.audit_fix import run_audit_fix_loop  # noqa: E402
from akb2.briefing.translate import translate_briefing  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent.parent
# worktree root (src/akb2/briefing/ -> src/akb2/ -> src/ -> worktree/)
GATE_CLI = Path(
    os.environ.get("AKB2_GATE_CLI")
    or str(REPO_ROOT / "src" / "akb2" / "gate" / "akb2_gate_check.py")
)

GATE_DECISION_ENUM = {
    "active", "needs_repair", "needs_human_review",
    "brief_only", "archive", "reject",
}

_TZ_SH = zoneinfo.ZoneInfo("Asia/Shanghai")
# 默认 3 天：19 个活跃账号在 2 天窗口下安静日仅 ~10 条原创、只够 3 主题；
# 放宽到 3 天提升素材量（够 4-5 主题），新鲜度损失可接受。可 AKB2_INTAKE_WINDOW_DAYS 覆盖。
_INTAKE_WINDOW_DAYS_DEFAULT = 3


def _stage_log(stage: str, event: str, elapsed: float | None = None) -> None:
    now = datetime.datetime.now(_TZ_SH).isoformat(timespec="seconds")
    suffix = f" elapsed={elapsed:.1f}s" if elapsed is not None else ""
    sys.stderr.write(f"{now} [briefing-stage] {stage} {event}{suffix}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# 产出层日期兜底（SC-7）
# ---------------------------------------------------------------------------

def _filter_recent(tweets: list, date: str, days: int = _INTAKE_WINDOW_DAYS_DEFAULT) -> list:
    """保留 created_at 在 [date-(days-1), date] 范围内的推文（时区 Asia/Shanghai）。

    - 缺 created_at 或格式异常 → 保守保留，stderr 告警
    - days=3 表示今天+昨天+前天（近3天）
    """
    try:
        end_day = datetime.datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        sys.stderr.write(f"WARNING [filter_recent]: invalid date '{date}', skipping filter\n")
        return tweets

    start_day = end_day - datetime.timedelta(days=days - 1)
    # Asia/Shanghai 时区边界
    start_dt = datetime.datetime(
        start_day.year, start_day.month, start_day.day, 0, 0, 0, tzinfo=_TZ_SH
    )
    end_dt = datetime.datetime(
        end_day.year, end_day.month, end_day.day, 23, 59, 59, 999999, tzinfo=_TZ_SH
    )

    kept = []
    for t in tweets:
        raw_ts = t.get("created_at", "")
        if not raw_ts:
            sys.stderr.write(
                f"WARNING [filter_recent]: tweet {t.get('id','?')} missing created_at, keeping\n"
            )
            kept.append(t)
            continue
        try:
            dt = datetime.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            dt_sh = dt.astimezone(_TZ_SH)
            if start_dt <= dt_sh <= end_dt:
                kept.append(t)
        except (ValueError, TypeError):
            sys.stderr.write(
                f"WARNING [filter_recent]: tweet {t.get('id','?')} "
                f"bad created_at '{raw_ts}', keeping\n"
            )
            kept.append(t)

    if len(kept) < len(tweets):
        sys.stderr.write(
            f"INFO [filter_recent]: dropped {len(tweets)-len(kept)} "
            f"out-of-window tweets, kept {len(kept)}\n"
        )

    return kept


# ---------------------------------------------------------------------------
# Stage 1: Intake
# ---------------------------------------------------------------------------

def run_intake(
    dry_run: bool,
    output_path: Path,
    since_date: str | None = None,
    until_date: str | None = None,
) -> dict:
    """Call the intake collector via subprocess, return parsed JSON dict.

    Collector 由 env AKB2_INTAKE_COLLECTOR 选择：
      - "account"（默认）：curator 逐账号抓取（account_collector），内容为策展 AI 账号
      - "keyword"：旧泛关键词搜索（l0_collector），噪音高，作回退
    两者 CLI 同构（--output/--dry-run/--since/--until），下游处理不变。
    """
    collector_mode = os.environ.get("AKB2_INTAKE_COLLECTOR", "account")
    collector_module = (
        "akb2.briefing.intake.l0_collector"
        if collector_mode == "keyword"
        else "akb2.briefing.intake.account_collector"
    )
    cmd = [
        sys.executable, "-m", collector_module,
        "--output", str(output_path),
    ]
    # P2-1: account collector 默认抓 en+zh 双 tier 合并（打 lang 标）；keyword 模式不支持 --merge
    if collector_mode != "keyword" and os.environ.get("AKB2_FETCH_MERGE", "1") == "1":
        cmd.append("--merge")
    if dry_run:
        cmd.append("--dry-run")
    if since_date:
        cmd += ["--since", since_date]
    if until_date:
        cmd += ["--until", until_date]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + (
        ":" + env["PYTHONPATH"] if "PYTHONPATH" in env else ""
    )
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
    if result.returncode != 0:
        sys.stderr.write(f"ERROR: intake failed (exit {result.returncode}):\n")
        sys.stderr.write(result.stderr)
        sys.exit(1)

    with open(output_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Stage 2: Scoring
# ---------------------------------------------------------------------------

def run_scoring(intake_data: dict) -> dict:
    """Call ScoreEngine directly, return scored data dict."""
    from akb2.briefing.scoring import ScoreEngine  # noqa: PLC0415

    engine = ScoreEngine()
    tweets = intake_data.get("tweets", [])
    scored_tweets = [engine.score_tweet(t) for t in tweets]
    return {"tweets": scored_tweets}


# ---------------------------------------------------------------------------
# Stage 3: Synthesis
# ---------------------------------------------------------------------------

def run_synthesis(scored_data: dict, date: str) -> dict:
    """Call synthesize() directly, return synth payload dict."""
    from akb2.briefing.synthesis import synthesize  # noqa: PLC0415

    return synthesize(scored_data, date=date)


# ---------------------------------------------------------------------------
# Stage 4: Gate check
# ---------------------------------------------------------------------------

def run_gate(akb_root: Path, input_file: Path, date: str) -> str:
    """Call gate CLI, parse proposed_gate_decision from stdout. Returns decision string."""
    if not GATE_CLI.exists():
        sys.stderr.write(f"ERROR: gate CLI not found at {GATE_CLI}\n")
        sys.exit(1)

    cmd = [
        sys.executable, str(GATE_CLI), "check",
        "--akb-root", str(akb_root),
        "--input", str(input_file),
        "--date", date,
        "--dry-run",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + (
        ":" + env["PYTHONPATH"] if "PYTHONPATH" in env else ""
    )
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
    if result.returncode != 0:
        sys.stderr.write(f"ERROR: gate CLI failed (exit {result.returncode}):\n")
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        sys.exit(1)

    # Parse proposed_gate_decision from stdout
    output = result.stdout
    import re
    m = re.search(
        r"proposed_gate_decision:\s*(active|needs_repair|needs_human_review|brief_only|archive|reject)",
        output,
    )
    if not m:
        sys.stderr.write("ERROR: gate CLI output did not contain proposed_gate_decision:\n")
        sys.stderr.write(output[:2000])
        sys.exit(1)

    decision = m.group(1)
    if decision not in GATE_DECISION_ENUM:
        sys.stderr.write(f"ERROR: gate decision '{decision}' not in valid enum\n")
        sys.exit(1)
    return decision


# ---------------------------------------------------------------------------
# Stage 5: Build markdown briefing
# ---------------------------------------------------------------------------

_ORPHAN_TOP_K = int(os.environ.get("AKB2_ORPHAN_TOP_K", "5"))
# 展示层质量过滤（2026-06-02 试跑发现的 3 个缺陷修复）
_MIN_DISPLAY_SCORE = float(os.environ.get("AKB2_MIN_DISPLAY_SCORE", "0.35"))
# 低于此分不入正文（去低分噪音）
_PER_AUTHOR_CAP = int(os.environ.get("AKB2_PER_AUTHOR_CAP", "2"))
# 单作者每主题/孤儿区上限（防霸屏）


def _apply_display_filters(
    tweets: list[dict],
    *,
    min_score: float = _MIN_DISPLAY_SCORE,
    per_author_cap: int = _PER_AUTHOR_CAP,
) -> list[dict]:
    """简报展示过滤（保持入参顺序）：

    1. 剔除近重复：`dimensions.novelty == 0`（语义/精确去重已判定为重复的后出现者）——
       修复"novelty 软扣分 0.05 不足以剔重，重复内容仍进简报"（试跑发现 @lmsysorg 两条
       cosine=0.8776>0.85 公告都入选）。
    2. 最低分门槛：score < min_score 不入正文（去 0.27 这类低分噪音）。
    3. per-author 上限：同作者超过 per_author_cap 条丢弃（防单作者霸占整个主题）。
    """
    out: list[dict] = []
    author_count: dict[str, int] = {}
    for t in tweets:
        dims = t.get("dimensions") or {}
        # 1) 剔重：novelty==0 表示已有近重复在前
        if "novelty" in dims and dims.get("novelty") == 0:
            continue
        # 2) 最低分
        if float(t.get("score", 0) or 0) < min_score:
            continue
        # 3) per-author 上限
        author = (t.get("author") or "").lower().lstrip("@")
        if author and author_count.get(author, 0) >= per_author_cap:
            continue
        author_count[author] = author_count.get(author, 0) + 1
        out.append(t)
    return out


def _collect_display_tweets(
    synth: dict,
    orphan_top_k: int = _ORPHAN_TOP_K,
) -> tuple[list[list[dict]], list[dict]]:
    """将推文按主题分组，返回 (per_topic_tweets, orphan_tweets)。

    per_topic_tweets[i] = focus_discussions[i] 对应的支撑推文列表
    （应用展示过滤：剔重/最低分/per-author）
    orphan_tweets = 未被任何主题引用的高分推文，按 score 倒序取 top-k（同样过滤）
    """
    tweets = synth.get("tweets", [])
    id_to_tweet: dict[str, dict] = {str(t.get("id", "")): t for t in tweets}

    # 收集所有主题引用的推文 id（用于孤儿判定）；per-topic 各自应用过滤（per-author 上限按主题独立）
    all_referenced: set[str] = set()
    per_topic: list[list[dict]] = []
    for fd in synth.get("focus_discussions", []):
        topic_tweets = []
        for tid in fd.get("supporting_tweet_ids", []):
            sid = str(tid)
            if sid in id_to_tweet:
                topic_tweets.append(id_to_tweet[sid])
                all_referenced.add(sid)
        per_topic.append(_apply_display_filters(topic_tweets))

    # 孤儿高分：未被任何主题引用，按 score 倒序，过滤后取 top-k
    orphans_sorted = sorted(
        [t for t in tweets if str(t.get("id", "")) not in all_referenced],
        key=lambda t: t.get("score", 0),
        reverse=True,
    )
    orphans = _apply_display_filters(orphans_sorted)[:orphan_top_k]

    return per_topic, orphans


_INLINE_LINK_RE = re.compile(r"https?://\S+")
_PIC_TWITTER_RE = re.compile(r"\bpic\.twitter\.com/\S+")


def _strip_inline_links(text: str) -> str:
    """剥离正文内联链接（http(s) URL / pic.twitter 短链）。

    用户决策：每条推文只保留底部唯一 [原文] 链接，正文不再出现内联短链
    （避免与 [原文] 重复）。剥离后折叠多余空白。
    """
    if not text:
        return text
    cleaned = _INLINE_LINK_RE.sub("", text)
    cleaned = _PIC_TWITTER_RE.sub("", cleaned)
    # P0-2 SC-2：剥链接后清除孤立的 🔗 链接标记（推文里 🔗 只是"链接在此"标记，
    # 链接已剥 + 底部已有唯一 [原文]，正文残留 🔗 是无意义噪音）
    cleaned = cleaned.replace("🔗", "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _render_tweet(t: dict) -> list[str]:
    """渲染一条推文为 markdown 行列表（作者 + 评分 + 点题 + 中文正文 + 唯一 [原文] 链接）。

    用户决策：简报不保留英文原文（移除英文折叠块）；正文内联链接剥离，
    每条推文只在底部保留一个 [原文] 链接。长推优先展示中文核心段。
    """
    author = t.get("author", "")
    text = _strip_inline_links(t.get("text", ""))
    core_excerpt = _strip_inline_links(t.get("core_excerpt", ""))
    zh_point = t.get("zh_point", "")
    score = t.get("score", 0)
    url = t.get("url", "")

    parts: list[str] = [f"**{author}**（评分：{score:.3f}）"]
    if zh_point:
        parts += [f"*{zh_point}*", ""]

    body = core_excerpt or text
    parts += [f"> {body}", ""]

    if url:
        parts += [f"[原文]({url})", ""]

    return parts


def build_briefing_markdown(
    synth: dict,
    gate_decision: str,
    date: str,
    collector_name: str = "account_collector",
) -> str:
    """Build the final markdown briefing content with YAML frontmatter.

    The frontmatter includes all AKB gate-compatible fields so that a re-run of
    the gate CLI on this file produces the same proposed_gate_decision.
    collector_name: 实际 collector 名（SC-P3b-1）；dry-run 时传 "dry_run"。
    """
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    tweets = synth.get("tweets", [])
    focus_discussions = synth.get("focus_discussions", [])

    frontmatter_lines = [
        "---",
        f"date: {date}",
        f"proposed_gate_decision: {gate_decision}",
        "source: social_trends_v1_1",
        f"generated_at: {now_iso}",
        "page_status: candidate",
        "source_type: news",
        "template_type: synthesis",
        "evidence_paths:",
        f"  - social/{collector_name}",
        "future_questions:",
        "  - What are the top AI trends this week?",
        "  - Which models are gaining the most developer attention?",
        "related_synthesis:",
        "  - AKB2.0/AI TRENDS/Social",
        "---",
    ]
    frontmatter = "\n".join(frontmatter_lines)

    # Build body — v2 结构：执行摘要 → 核心讨论(嵌套推文) → 其他高分推文 → 数据摘要
    body_parts = [
        f"# {date} AI 社交趋势日报",
        "",
        "## 总览",
        # P0-2 SC-14 修复：用 gate HC-08 白名单内的标题（导读→总览），消除恒 needs_repair
        "",
        f"基于 {date} 公开时间线抓取，对 AI/ML 领域社交信号的综合分析。",
        f"质量门控：**{gate_decision}**",
        "",
    ]

    # 执行摘要（model 综合给出；fallback 时可能为空）
    executive_summary = synth.get("executive_summary", "")
    if executive_summary:
        body_parts += ["## 执行摘要", "", executive_summary, ""]

    # 核心讨论骨架：每主题 summary + 嵌套支撑推文
    per_topic, orphans = _collect_display_tweets(synth, _ORPHAN_TOP_K)
    if focus_discussions:
        body_parts += ["## 核心讨论", ""]
        for i, disc in enumerate(focus_discussions):
            topic = disc.get("topic", "")
            summary = disc.get("summary", "")
            body_parts += [f"### {topic}", "", summary, ""]
            for t in per_topic[i]:
                body_parts += _render_tweet(t)

    # 其他高分推文（孤儿：未被任何主题引用）
    if orphans:
        body_parts += ["## 其他高分推文", ""]
        for t in orphans:
            body_parts += _render_tweet(t)

    scoring_summary = synth.get("scoring_summary", {})
    total = scoring_summary.get("total_tweets", len(tweets))
    scored = scoring_summary.get("scored_tweets", len(tweets))
    body_parts += [
        "## 数据摘要",
        "",
        f"- 总推文数：{total}",
        f"- 精选推文：{scored}",
        f"- 生成时间：{now_iso}",
        "- 数据源：social_trends_v1_1",
        "",
    ]

    body = "\n".join(body_parts)
    return frontmatter + "\n" + body


def build_temp_briefing_markdown(
    synth: dict,
    date: str,
    collector_name: str = "account_collector",
) -> str:
    """Build briefing markdown WITHOUT gate decision in frontmatter (for initial gate check).
    collector_name: 实际 collector 名（SC-P3b-1）；dry-run 时传 "dry_run"。
    """
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    tweets = synth.get("tweets", [])

    frontmatter_lines = [
        "---",
        f"date: {date}",
        "page_status: candidate",
        "source_type: news",
        "template_type: synthesis",
        "source: social_trends_v1_1",
        f"generated_at: {now_iso}",
        "evidence_paths:",
        f"  - social/{collector_name}",
        "future_questions:",
        "  - What are the top AI trends this week?",
        "  - Which models are gaining the most developer attention?",
        "related_synthesis:",
        "  - AKB2.0/AI TRENDS/Social",
        "---",
    ]
    frontmatter = "\n".join(frontmatter_lines)

    # v2 骨架（与 build_briefing_markdown 保持一致，Step5）：
    # 执行摘要 → 核心讨论(嵌套推文) → 其他高分推文 → 数据摘要。
    # temp 在翻译前用 synth_en 跑 gate，推文为英文（无 zh_point/core_excerpt），
    # _render_tweet 会自然降级为 `> text` + 原文链接。
    body_parts = [
        f"# {date} AI 社交趋势日报",
        "",
        "## 总览",
        # P0-2 SC-14 修复：用 gate HC-08 白名单内的标题（导读→总览），消除恒 needs_repair
        "",
        f"基于 {date} 公开时间线抓取，对 AI/ML 领域社交信号的综合分析。",
        "",
    ]

    executive_summary = synth.get("executive_summary", "")
    if executive_summary:
        body_parts += ["## 执行摘要", "", executive_summary, ""]

    focus_discussions = synth.get("focus_discussions", [])
    per_topic, orphans = _collect_display_tweets(synth, _ORPHAN_TOP_K)
    if focus_discussions:
        body_parts += ["## 核心讨论", ""]
        for i, disc in enumerate(focus_discussions):
            body_parts += [f"### {disc.get('topic', '')}", "", disc.get("summary", ""), ""]
            for t in per_topic[i]:
                body_parts += _render_tweet(t)

    if orphans:
        body_parts += ["## 其他高分推文", ""]
        for t in orphans:
            body_parts += _render_tweet(t)

    scoring_summary = synth.get("scoring_summary", {})
    total = scoring_summary.get("total_tweets", len(tweets))
    scored = scoring_summary.get("scored_tweets", len(tweets))
    body_parts += [
        "## 数据摘要",
        "",
        f"- 总推文数：{total}",
        f"- 精选推文：{scored}",
        f"- 生成时间：{now_iso}",
        "",
    ]

    body = "\n".join(body_parts)
    return frontmatter + "\n" + body


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AKB2.0 social-trends daily briefing pipeline")
    parser.add_argument("--date", required=True, help="Briefing date (YYYY-MM-DD)")
    parser.add_argument("--vault-root", required=True, help="Vault root directory path")
    parser.add_argument("--dry-run-intake", action="store_true", help="Use fixture data for intake")
    args = parser.parse_args(argv)

    date = args.date
    vault_root = Path(args.vault_root).resolve()
    dry_run_intake = args.dry_run_intake
    window_days = int(os.environ.get("AKB2_INTAKE_WINDOW_DAYS", str(_INTAKE_WINDOW_DAYS_DEFAULT)))

    # Fail-closed: check vault_root before running any expensive stages.
    if not vault_root.is_dir():
        sys.stderr.write(
            f"ERROR: --vault-root does not exist or is not a directory: {vault_root}\n"
            "Hint: ensure the vault is mounted and the path is correct before running.\n"
        )
        sys.exit(1)

    # 文件名 = 抓取日（去掉旧的 +1 偏移）
    briefing_filename = f"{date} Daily Briefing.md"

    # 采集层时间窗（SC-6）
    # twitter 的 --until 是排他上界（"早于该日"），要包含抓取当天必须 +1 天
    date_dt = datetime.datetime.strptime(date, "%Y-%m-%d")
    since_dt = date_dt - datetime.timedelta(days=window_days - 1)
    since_date_str = since_dt.strftime("%Y-%m-%d")
    until_date_str = (date_dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    # Stage 1: Intake
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        intake_tmp = Path(tf.name)
    stage_start = time.monotonic()
    _stage_log("intake", "start")
    try:
        intake_data = run_intake(
            dry_run=dry_run_intake,
            output_path=intake_tmp,
            since_date=since_date_str if not dry_run_intake else None,
            until_date=until_date_str if not dry_run_intake else None,
        )
    finally:
        intake_tmp.unlink(missing_ok=True)
    _stage_log("intake", "end", time.monotonic() - stage_start)

    # Stage 2: Scoring
    stage_start = time.monotonic()
    _stage_log("scoring", "start")
    scored_data = run_scoring(intake_data)
    _stage_log("scoring", "end", time.monotonic() - stage_start)

    # 产出层日期兜底（SC-7）：去掉超出时间窗的旧推文
    # 跳过条件：dry-run 模式（fixture 日期与今日不同）或 intake 标记了 fallback（降级到旧 fixture）
    intake_is_fallback = bool(intake_data.get("_fallback_used", False))
    if not dry_run_intake and not intake_is_fallback:
        scored_data["tweets"] = _filter_recent(
            scored_data.get("tweets", []), date, days=window_days
        )

    # Stage 3: Synthesis（产出英文结构化分析，语言交翻译阶段负责）
    stage_start = time.monotonic()
    _stage_log("synthesis", "start")
    synth_en = run_synthesis(scored_data, date=date)
    _stage_log("synthesis", "end", time.monotonic() - stage_start)

    # Prepare paths
    akb2_root = vault_root / "Praise Space" / "AKB2.0"
    staging_candidate_dir = akb2_root / "staging" / "candidate"
    staging_candidate_dir.mkdir(parents=True, exist_ok=True)
    social_dir = akb2_root / "AI TRENDS" / "Social"
    social_dir.mkdir(parents=True, exist_ok=True)

    real_briefing_path = staging_candidate_dir / briefing_filename
    final_briefing_path = social_dir / briefing_filename

    # SC-P3b-1: collector_name 从实际模式推导，干 run 标 dry_run
    collector_mode = os.environ.get("AKB2_INTAKE_COLLECTOR", "account")
    effective_collector = (
        "dry_run"
        if dry_run_intake
        else ("l0_collector" if collector_mode == "keyword" else "account_collector")
    )

    # Stage 4a: Write temp briefing → gate（自检①：schema/结构，语言无关）
    stage_start = time.monotonic()
    _stage_log("gate", "start")
    temp_content = build_temp_briefing_markdown(synth_en, date, collector_name=effective_collector)
    real_briefing_path.write_text(temp_content, encoding="utf-8")

    gate_decision = run_gate(akb_root=akb2_root, input_file=real_briefing_path, date=date)
    _stage_log("gate", "end", time.monotonic() - stage_start)

    if gate_decision == "reject":
        sys.stderr.write("gate decision: reject — skipping translate/audit/Social write\n")
        print(f"proposed_gate_decision: {gate_decision}")
        sys.exit(1)

    # Stage 4b: 翻译（全中文化）+ 译后审计定点修复收敛循环
    stage_start = time.monotonic()
    _stage_log("translate", "start")
    synth_zh = translate_briefing(synth_en)
    _stage_log("translate", "end", time.monotonic() - stage_start)
    stage_start = time.monotonic()
    _stage_log("audit_fix", "start")
    synth_zh, verdict = run_audit_fix_loop(synth_zh, synth_en)
    _stage_log("audit_fix", "end", time.monotonic() - stage_start)
    audit_verdict = verdict["verdict"]  # pass | warn | fail
    fix_loop_reason = verdict.get("fix_loop_reason")

    # Stage 5: 写最终内容（含 gate_decision + audit_verdict）
    final_content = build_briefing_markdown(synth_zh, gate_decision, date,
                                            collector_name=effective_collector)
    # 将 audit_verdict 写入 frontmatter（在 proposed_gate_decision 行后追加）
    final_content = final_content.replace(
        f"proposed_gate_decision: {gate_decision}",
        f"proposed_gate_decision: {gate_decision}\naudit_verdict: {audit_verdict}",
        1,
    )
    if fix_loop_reason == "max_rounds_reached":
        final_content = final_content.replace(
            f"audit_verdict: {audit_verdict}",
            f"audit_verdict: {audit_verdict}\nfix_loop: max_rounds_reached",
            1,
        )
    real_briefing_path.write_text(final_content, encoding="utf-8")

    # Provisional enforcement matrix:
    #   fail  → 不入库（文件留 staging，不 copy 到 Social）
    #   warn  → 入库但 frontmatter 已标 audit_verdict: warn
    #   pass  → 正常入库
    if audit_verdict == "fail":
        sys.stderr.write(
            f"audit verdict: fail — briefing留在 staging/candidate，不写入 Social。\n"
            f"standard_issues: {verdict.get('standard_issues')}\n"
        )
        print(f"proposed_gate_decision: {gate_decision}")
        print(f"audit_verdict: {audit_verdict}")
        sys.exit(2)  # 2 = audit fail，不入库；调度器可据此区分成功(0)与审计拒绝(2)

    # Copy to Social
    stage_start = time.monotonic()
    _stage_log("write_social", "start")
    if final_briefing_path.exists() or final_briefing_path.is_symlink():
        final_briefing_path.unlink()
    shutil.copy2(real_briefing_path, final_briefing_path)
    real_briefing_path.unlink(missing_ok=True)
    sys.stderr.write("staging 中转已清除\n")
    _stage_log("write_social", "end", time.monotonic() - stage_start)

    print(f"proposed_gate_decision: {gate_decision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
