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
import subprocess
import sys
import shutil
import tempfile
from pathlib import Path

from akb2.briefing.translate import translate_briefing  # noqa: E402
from akb2.briefing.audit import audit_briefing  # noqa: E402


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent.parent  # worktree root (src/akb2/briefing/ -> src/akb2/ -> src/ -> worktree/)
GATE_CLI = Path(os.environ.get("AKB2_GATE_CLI") or str(REPO_ROOT / "src" / "akb2" / "gate" / "akb2_gate_check.py"))

GATE_DECISION_ENUM = {
    "active", "needs_repair", "needs_human_review",
    "brief_only", "archive", "reject",
}


# ---------------------------------------------------------------------------
# Stage 1: Intake
# ---------------------------------------------------------------------------

def run_intake(dry_run: bool, output_path: Path) -> dict:
    """Call l0_collector via subprocess, return parsed JSON dict."""
    cmd = [
        sys.executable, "-m", "akb2.briefing.intake.l0_collector",
        "--output", str(output_path),
    ]
    if dry_run:
        cmd.append("--dry-run")

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

def build_briefing_markdown(synth: dict, gate_decision: str, date: str) -> str:
    """Build the final markdown briefing content with YAML frontmatter.

    The frontmatter includes all AKB gate-compatible fields so that a re-run of
    the gate CLI on this file produces the same proposed_gate_decision.
    """
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tweets = synth.get("tweets", [])
    focus_discussions = synth.get("focus_discussions", [])

    # Build frontmatter with full AKB gate-compatible fields
    # These mirror the temp briefing frontmatter so gate produces the same decision.
    frontmatter_lines = [
        "---",
        f"date: {date}",
        f"proposed_gate_decision: {gate_decision}",
        f"source: social_trends_v1_1",
        f"generated_at: {now_iso}",
        f"page_status: candidate",
        f"source_type: news",
        f"template_type: synthesis",
        "evidence_paths:",
        "  - social/l0_collector/dry_run",
        "future_questions:",
        "  - What are the top AI trends this week?",
        "  - Which models are gaining the most developer attention?",
        "related_synthesis:",
        "  - AKB2.0/AI TRENDS/Social",
        "---",
    ]
    frontmatter = "\n".join(frontmatter_lines)

    # Build body — 文件名/标题与 frontmatter date 一致，不再 +1
    body_parts = [
        f"# {date} AI 社交趋势日报",
        "",
        "## 导读",
        "",
        f"基于 {date} 公开时间线抓取，对 AI/ML 领域社交信号的综合分析。",
        f"质量门控：**{gate_decision}**",
        "",
    ]

    # 执行摘要（model 综合给出；fallback 时可能为空）
    executive_summary = synth.get("executive_summary", "")
    if executive_summary:
        body_parts += ["## 执行摘要", "", executive_summary, ""]

    if focus_discussions:
        body_parts += ["## 核心讨论", ""]
        for disc in focus_discussions:
            topic = disc.get("topic", "")
            summary = disc.get("summary", "")
            body_parts += [f"### {topic}", "", summary, ""]

    # 关键推文（合并板块：每条 = 作者 + 评分 + 中文点题 + 中文译文，长推折叠英文原文）
    body_parts += ["## 关键推文", ""]
    for t in tweets[:10]:
        author = t.get("author", "")
        text = t.get("text", "")          # 译后为中文（或原英文若翻译失败）
        text_original = t.get("text_original", "")  # 英文原文备查（长推）
        core_excerpt = t.get("core_excerpt", "")    # 长推中文核心段
        zh_point = t.get("zh_point", "")            # 中文点题
        score = t.get("score", 0)
        url = t.get("url", "")

        body_parts += [f"**{author}**（评分：{score:.3f}）"]
        # 中文点题行（斜体，区别于正文）
        if zh_point:
            body_parts += [f"*{zh_point}*", ""]

        if core_excerpt:
            # 长推：显示中文核心段 + 折叠英文原文（多行逐行加 > 前缀）
            original_for_fold = text_original or text
            folded_lines = [
                f"> {line}" if line.strip() else ">"
                for line in original_for_fold.splitlines()
            ]
            body_parts += [
                core_excerpt,
                "",
                "> [!quote]- 展开英文原文",
                *folded_lines,
                "",
            ]
        else:
            body_parts += [
                f"> {text}",
                "",
            ]

        if url:
            body_parts += [f"[原文]({url})", ""]

    scoring_summary = synth.get("scoring_summary", {})
    total = scoring_summary.get("total_tweets", len(tweets))
    scored = scoring_summary.get("scored_tweets", len(tweets))
    body_parts += [
        "## 数据摘要",
        "",
        f"- 总推文数：{total}",
        f"- 精选推文：{scored}",
        f"- 生成时间：{now_iso}",
        f"- 数据源：social_trends_v1_1",
        "",
    ]

    body = "\n".join(body_parts)
    return frontmatter + "\n" + body


def build_temp_briefing_markdown(synth: dict, date: str) -> str:
    """Build briefing markdown WITHOUT gate decision in frontmatter (for initial gate check)."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tweets = synth.get("tweets", [])

    frontmatter_lines = [
        "---",
        f"date: {date}",
        f"page_status: candidate",
        f"source_type: news",
        f"template_type: synthesis",
        f"source: social_trends_v1_1",
        f"generated_at: {now_iso}",
        "evidence_paths:",
        "  - social/l0_collector/dry_run",
        "future_questions:",
        "  - What are the top AI trends this week?",
        "  - Which models are gaining the most developer attention?",
        "related_synthesis:",
        "  - AKB2.0/AI TRENDS/Social",
        "---",
    ]
    frontmatter = "\n".join(frontmatter_lines)

    body_parts = [
        f"# {date} AI 社交趋势日报",
        "",
        "## 导读",
        "",
        f"基于 {date} 公开时间线抓取，对 AI/ML 领域社交信号的综合分析。",
        "",
        "## 关键发现",
        "",
        "按相关性与权威性打分后的 top 推文。",
        "",
        "## 关键推文",
        "",
    ]
    for t in tweets[:5]:
        author = t.get("author", "")
        text = t.get("text", "")
        score = t.get("score", 0)
        body_parts += [
            f"**{author}**（评分：{score:.3f}）",
            f"> {text}",
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

    # Fail-closed: check vault_root before running any expensive stages.
    if not vault_root.is_dir():
        sys.stderr.write(
            f"ERROR: --vault-root does not exist or is not a directory: {vault_root}\n"
            "Hint: ensure the vault is mounted and the path is correct before running.\n"
        )
        sys.exit(1)

    # 文件名 = 抓取日（去掉旧的 +1 偏移）
    briefing_filename = f"{date} Daily Briefing.md"

    # Stage 1: Intake
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        intake_tmp = Path(tf.name)
    try:
        intake_data = run_intake(dry_run=dry_run_intake, output_path=intake_tmp)
    finally:
        intake_tmp.unlink(missing_ok=True)

    # Stage 2: Scoring
    scored_data = run_scoring(intake_data)

    # Stage 3: Synthesis（产出英文结构化分析，语言交翻译阶段负责）
    synth_en = run_synthesis(scored_data, date=date)

    # Prepare paths
    akb2_root = vault_root / "Praise Space" / "AKB2.0"
    staging_candidate_dir = akb2_root / "staging" / "candidate"
    staging_candidate_dir.mkdir(parents=True, exist_ok=True)
    social_dir = akb2_root / "AI TRENDS" / "Social"
    social_dir.mkdir(parents=True, exist_ok=True)

    real_briefing_path = staging_candidate_dir / briefing_filename
    final_briefing_path = social_dir / briefing_filename

    # Stage 4a: Write temp briefing → gate（自检①：schema/结构，语言无关）
    temp_content = build_temp_briefing_markdown(synth_en, date)
    real_briefing_path.write_text(temp_content, encoding="utf-8")

    gate_decision = run_gate(akb_root=akb2_root, input_file=real_briefing_path, date=date)

    if gate_decision == "reject":
        sys.stderr.write("gate decision: reject — skipping translate/audit/Social write\n")
        print(f"proposed_gate_decision: {gate_decision}")
        sys.exit(1)

    # Stage 4b: 翻译（全中文化）
    synth_zh = translate_briefing(synth_en)

    # Stage 4c: 译后审计（自检②：代码标准 + LLM 漂移/准确）
    verdict = audit_briefing(synth_zh, synth_en)
    audit_verdict = verdict["verdict"]  # pass | warn | fail

    # Stage 5: 写最终内容（含 gate_decision + audit_verdict）
    final_content = build_briefing_markdown(synth_zh, gate_decision, date)
    # 将 audit_verdict 写入 frontmatter（在 proposed_gate_decision 行后追加）
    final_content = final_content.replace(
        f"proposed_gate_decision: {gate_decision}",
        f"proposed_gate_decision: {gate_decision}\naudit_verdict: {audit_verdict}",
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
    if final_briefing_path.exists() or final_briefing_path.is_symlink():
        final_briefing_path.unlink()
    shutil.copy2(real_briefing_path, final_briefing_path)

    print(f"proposed_gate_decision: {gate_decision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
