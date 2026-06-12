"""全量端到端校准跑 — 逐条跑 corpus → 统计 JSON 报告 + 出厂判定。

公开接口:
    main(argv) -> int  — CLI 入口
    run_calibration(corpus_path, report_out, config) -> dict  — 校准核心

用法:
    python3 -m akb2.quality.calibration \\
        --corpus evals/main-pipeline-quality/corpus.yaml \\
        --report-out reports/calibration/factory.json

    python3 -m akb2.quality.calibration \\
        --live-sample 3 \\
        --report-out reports/calibration/live-sample.json

    python3 -m akb2.quality.calibration --help

报告字段:
    - positive_pass_rate / negative_reject_rate: 出厂判定指标
    - promotion_mode: proposal | active
    - amendment_status: not_applied | applied
    - per_segment_failures: 各段失败分布
    - rounds_distribution: 修复轮数分布
    - entries: 逐条详细结果
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 具名常量
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_PER_ENTRY_SEC: int = 300
MAX_LIVE_SAMPLE_DEFAULT: int = 3

# ---------------------------------------------------------------------------
# 报告结构
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """当前 UTC ISO-8601 时间戳。"""
    return datetime.now(UTC).isoformat()


def _init_report(total_samples: int, negative_count: int) -> dict[str, Any]:
    """初始化校准报告骨架。fail-closed: auto_promote 开启但修宪未应用时拒绝。"""
    auto_promote = os.environ.get("AKB2_AUTO_PROMOTE", "0") == "1"
    amendment_applied = os.environ.get("AKB2_AMENDMENT_APPLIED", "0") == "1"

    # fail-closed: auto_promote 开启但修宪未应用 → 强制退回 proposal 模式
    if auto_promote and not amendment_applied:
        promotion = "proposal"
        amendment = "not_applied"
    elif auto_promote and amendment_applied:
        promotion = "active"
        amendment = "applied"
    else:
        promotion = "proposal"
        amendment = "not_applied"

    return {
        "calibration_ts": _now_iso(),
        "total_samples": total_samples,
        "negative_count": negative_count,
        "positive_count": total_samples - negative_count,
        "positive_pass_rate": 0.0,
        "negative_reject_rate": 0.0,
        "promotion_mode": promotion,
        "amendment_status": amendment,
        "per_segment_failures": {},
        "rounds_distribution": {},
        "entries": [],
    }


# ---------------------------------------------------------------------------
# 单条处理
# ---------------------------------------------------------------------------


def _process_corpus_entry(
    entry: dict[str, Any],
    index: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    """处理单条语料。

    Args:
        entry: corpus.yaml 中的一条 entry
        index: 条目序号 (0-based)
        config: 运行配置 {"vault_root": str, "staging_dir": str, ...}

    Returns:
        单条结果 dict
    """
    url = entry.get("url", "")
    domain = entry.get("domain", "unknown")
    expected = entry.get("expected", "ingest")
    notes = entry.get("notes", "")
    replaced_reason = entry.get("replaced_reason", "")

    result: dict[str, Any] = {
        "index": index,
        "url": url,
        "domain": domain,
        "expected": expected,
        "notes": notes,
        "final_status": "unknown",
        "repair_rounds": 0,
        "passed": False,
        "failure_segment": None,
        "failure_reason": None,
        "failure_attribution": None,  # "pipeline_defect" | "corpus_inappropriate" | None
    }

    if replaced_reason:
        result["replaced_reason"] = replaced_reason

    # 获取 runner 模块
    try:
        from src.akb2.quality.runner import (
            RunnerConfig,
            _run_pipeline_segments,
        )
    except ImportError as exc:
        result["final_status"] = "error"
        result["failure_reason"] = f"模块导入失败: {exc}"
        result["failure_attribution"] = "pipeline_defect"
        return result

    runner_config = RunnerConfig(
        raw_dir=config.get("raw_dir", ""),
        ledger_path=config.get("ledger_path", ""),
        staging_dir=config.get("staging_dir", ""),
        vault_root=config.get("vault_root", ""),
        failures_path=config.get("failures_path", ""),
        grader_result_dir=config.get("grader_result_dir", ""),
        poll_interval_seconds=1,
        auto_promote=config.get("auto_promote", False),
    )

    # 创建临时的 raw 文件表示
    slug = url.replace("https://", "").replace("/", "-")[:80]
    raw_path = os.path.join(config["raw_dir"], f"{slug}--raw--calibration.md")

    # 尝试抓取内容
    raw_content = _fetch_url_content(url, config)

    if raw_content is None:
        result["final_status"] = "fetch_failed"
        result["failure_segment"] = "intake_raw"
        result["failure_reason"] = f"无法抓取 URL: {url}"
        # fetch_fail 可能是语料不当 (死链) 或管线缺陷 (抓取工具问题)
        result["failure_attribution"] = "corpus_inappropriate"
        return result

    # 运行七段管道
    pipeline_result = _run_pipeline_segments(
        raw_path=raw_path,
        raw_content=raw_content,
        config=runner_config,
    )

    result["repair_rounds"] = pipeline_result.get("rounds", 0)
    result["converged"] = pipeline_result.get("converged", False)

    final_status = pipeline_result.get("final_status", "unknown")
    result["final_status"] = final_status

    if expected == "ingest":
        # 正样本：期望通过
        if final_status in ("promoted", "proposal_passed"):
            result["passed"] = True
        else:
            result["passed"] = False
            result["failure_segment"] = _classify_failure_segment(final_status)
            result["failure_reason"] = pipeline_result.get("error", "未知原因")
            result["failure_attribution"] = _classify_attribution(
                final_status, pipeline_result
            )
    else:
        # 负样本：期望拒收
        if final_status == "rejected":
            result["passed"] = True  # 拒收 = 正样本场景中的 "pass"
            result["final_status"] = "rejected_correctly"
        else:
            result["passed"] = False
            result["failure_segment"] = "admission"
            result["failure_reason"] = (
                f"负样本未被拒收: 终态={final_status}"
            )
            result["failure_attribution"] = "pipeline_defect"

    return result


def _fetch_url_content(url: str, config: dict[str, Any]) -> str | None:
    """抓取 URL 内容。

    对于 fixture: 前缀的 URL，从本地 fixture 读取。
    对于真实 URL，尝试 defuddle。

    Returns:
        内容字符串，或 None 表示抓取失败。
    """
    # Fixture URL
    if url.startswith("fixture:"):
        fixture_name = url.replace("fixture:", "")
        return _load_fixture_content(fixture_name)

    # 尝试 defuddle
    try:
        import subprocess

        result = subprocess.run(
            ["defuddle", url],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # 尝试直接 HTTP 请求（低权限 fallback）
    try:
        import urllib.request

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AKB2-Calibration/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            if len(content) > 100:
                return _html_to_markdown_fallback(content, url)
    except Exception:
        pass

    return None


def _load_fixture_content(fixture_name: str) -> str | None:
    """从本地 fixture 加载内容。"""
    if "google-io" in fixture_name.lower():
        # 返回新闻型负样本 fixture
        return """---
artifact_type: raw
source_slug: google-io-2026-recap
source_url: fixture:google-io-2026-news
source_title: Google I/O 2026 Recap
source_authors: Tech Reporter
source_publisher: Tech News Daily
source_published_at: 2026-06-10
fetched_at: 2026-06-13T00:00:00Z
fetched_via: fixture
---

# Google I/O 2026 Recap

Google announced several new products at I/O 2026. The keynote included
updates to Gemini, Android, and Web platform. The new Gemini 3.0 model
offers improved reasoning capabilities. Android 17 will ship with new
AI features. Chrome got a performance boost of 15% in the latest benchmark.

The event was held at Shoreline Amphitheatre with 5000 attendees.
Sundar Pichai opened the keynote with a focus on AI responsibility.

This is pure news reporting with no executable steps, code examples,
or concrete how-to knowledge.
"""
    return None


def _html_to_markdown_fallback(html: str, url: str) -> str | None:
    """简单的 HTML 转 markdown fallback。"""
    import re

    # 去掉 script/style 标签
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # 提取标题
    title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else url

    # 提取正文（简单方式：取 <body> 内容）
    body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    text = body_match.group(1) if body_match else html

    # 去掉 HTML 标签
    text = re.sub(r"<[^>]+>", " ", text)
    # 压缩空白
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) < 100:
        return None

    return f"""---
artifact_type: raw
source_slug: calibration-fallback
source_url: {url}
source_title: {title}
source_authors: unknown
source_publisher: unknown
source_published_at: unknown
fetched_at: {_now_iso()}
fetched_via: urllib-fallback
---

# {title}

{text[:10000]}
"""


def _classify_failure_segment(final_status: str) -> str:
    """将终态映射到失败段。"""
    mapping = {
        "rejected": "admission",
        "failed": "distill_compose",
        "quarantined": "repair_loop",
    }
    return mapping.get(final_status, final_status)


def _classify_attribution(
    final_status: str,
    pipeline_result: dict[str, Any],
) -> str:
    """归因失败: 管线缺陷 vs 语料不当。

    简单启发式:
        - fetch_fail → 语料不当（死链）
        - rejected (admission fail) → 语料不当（不符合入库标准）
        - 其他终态 → 管线缺陷
    """
    if final_status == "rejected":
        error = pipeline_result.get("error", "")
        if "how_tendency" in str(error) or "topic_relevance" in str(error):
            return "corpus_inappropriate"
    if final_status == "fetch_failed":
        return "corpus_inappropriate"
    return "pipeline_defect"


# ---------------------------------------------------------------------------
# 校准核心
# ---------------------------------------------------------------------------


def _load_corpus(corpus_path: str) -> list[dict[str, Any]]:
    """加载语料 YAML。"""
    import yaml

    path = Path(corpus_path)
    if not path.exists():
        print(f"ERROR: corpus file not found: {corpus_path}", file=sys.stderr)
        sys.exit(2)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("entries", [])


def _compute_statistics(report: dict[str, Any]) -> dict[str, Any]:
    """从逐条结果计算汇总统计。"""
    entries: list[dict] = report["entries"]

    # 正样本通过率
    positive_entries = [e for e in entries if e["expected"] == "ingest"]
    positive_passed = sum(1 for e in positive_entries if e["passed"])
    if positive_entries:
        report["positive_pass_rate"] = positive_passed / len(positive_entries)
    else:
        report["positive_pass_rate"] = 0.0

    # 负样本拒收率
    negative_entries = [e for e in entries if e["expected"] == "reject"]
    negative_rejected = sum(1 for e in negative_entries if e["passed"])
    if negative_entries:
        report["negative_reject_rate"] = negative_rejected / len(negative_entries)
    else:
        report["negative_reject_rate"] = 0.0

    # 各段失败分布
    per_segment: dict[str, int] = {}
    for e in entries:
        seg = e.get("failure_segment")
        if seg:
            per_segment[seg] = per_segment.get(seg, 0) + 1
    report["per_segment_failures"] = per_segment

    # 修复轮数分布
    rounds_dist: dict[str, int] = {}
    for e in entries:
        r = e.get("repair_rounds", 0)
        key = str(r)
        rounds_dist[key] = rounds_dist.get(key, 0) + 1
    report["rounds_distribution"] = rounds_dist

    return report


def run_calibration(
    corpus_path: str,
    report_out: str,
    config: dict[str, Any] | None = None,
    live_sample: int = 0,
) -> dict[str, Any]:
    """校准核心：逐条跑语料 → 统计报告 → 出厂判定。

    Args:
        corpus_path: corpus.yaml 路径。
        report_out: 输出报告 JSON 路径。
        config: 运行配置。
        live_sample: >0 时从 sources-ledger 取样实抓。

    Returns:
        完整报告 dict。
    """
    if config is None:
        config = _default_config()

    entries: list[dict[str, Any]] = []

    if live_sample > 0:
        entries = _sample_live_sources(live_sample, config)
    else:
        entries = _load_corpus(corpus_path)

    negative_count = sum(1 for e in entries if e.get("expected") == "reject")
    report = _init_report(len(entries), negative_count)

    print(f"校准开始: {len(entries)} 条语料 ({negative_count} 负样本)", file=sys.stderr)
    print(f"promotion_mode: {report['promotion_mode']}", file=sys.stderr)
    print(f"amendment_status: {report['amendment_status']}", file=sys.stderr)

    for i, entry in enumerate(entries):
        url_short = entry.get('url', '?')[:80]
        print(
            f"  [{i + 1}/{len(entries)}] {entry.get('domain', '?')}: {url_short}...",
            file=sys.stderr,
        )
        result = _process_corpus_entry(entry, i, config)
        report["entries"].append(result)

        status = result["final_status"]
        passed = "PASS" if result["passed"] else "FAIL"
        print(f"    -> {status} ({passed}) rounds={result['repair_rounds']}", file=sys.stderr)

    # 计算统计
    report = _compute_statistics(report)

    # 出厂判定
    report["factory_acceptance"] = (
        report["positive_pass_rate"] >= 0.9
        and report["negative_reject_rate"] == 1.0
    )

    # 写报告
    out_path = Path(report_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n校准报告: {report_out}", file=sys.stderr)
    pp_rate = report['positive_pass_rate']
    nr_rate = report['negative_reject_rate']
    print(
        f"  正样本通过率: {pp_rate:.2%} ({pp_rate >= 0.9})",
        file=sys.stderr,
    )
    print(
        f"  负样本拒收率: {nr_rate:.2%} ({nr_rate == 1.0})",
        file=sys.stderr,
    )
    print(f"  出厂判定: {'PASS' if report['factory_acceptance'] else 'FAIL'}", file=sys.stderr)

    return report


def _sample_live_sources(
    count: int,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """从 sources-ledger 取 reachable 源实抓 N 篇跑全链。"""
    import random

    # 尝试多个路径
    alt_paths = [
        Path(config.get("vault_root", "")) / ".." / "reports"
        / "source-validation" / "sources-ledger.jsonl",
        Path(os.getcwd()) / "reports" / "source-validation"
        / "sources-ledger.jsonl",
    ]

    entries: list[dict[str, Any]] = []
    for alt_path in alt_paths:
        if alt_path.exists():
            try:
                with open(alt_path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if row.get("reachable") and row.get("url"):
                            entries.append({
                                "url": row["url"],
                                "domain": row.get("feed", "live-sample"),
                                "expected": "ingest",
                                "notes": f"live-sample from sources-ledger: {row.get('feed', '?')}",
                            })
            except OSError:
                continue
            break

    if len(entries) > count:
        random.shuffle(entries)
        entries = entries[:count]

    if not entries:
        print("WARNING: 无法从 sources-ledger 获取 reachable 源", file=sys.stderr)

    return entries


def _default_config() -> dict[str, Any]:
    """获取默认配置。"""
    vault_root = os.environ.get("AKB2_VAULT_ROOT", "")
    staging_dir = ""
    raw_dir = ""

    if not vault_root:
        try:
            from src.akb2.loops.paths import get_akb2_root, get_staging_root

            vault_root = str(get_akb2_root())
            staging_dir = str(get_staging_root())
        except Exception:
            vault_root = os.getcwd()
            staging_dir = os.path.join(vault_root, "staging")
    else:
        staging_dir = os.path.join(vault_root, "staging")

    if not raw_dir:
        raw_dir = os.path.join(vault_root, "raw")

    auto_promote = os.environ.get("AKB2_AUTO_PROMOTE", "0") == "1"

    return {
        "vault_root": vault_root,
        "staging_dir": staging_dir,
        "raw_dir": raw_dir,
        "ledger_path": os.path.join(staging_dir, "quality-ledger.jsonl"),
        "failures_path": os.path.join(staging_dir, "failures.jsonl"),
        "grader_result_dir": os.path.join(staging_dir, "grader-results"),
        "auto_promote": auto_promote,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """校准 CLI 入口。

    Usage:
        python3 -m akb2.quality.calibration --corpus <path> --report-out <path>
        python3 -m akb2.quality.calibration --live-sample 3 --report-out <path>
        python3 -m akb2.quality.calibration --help
    """
    parser = argparse.ArgumentParser(
        description="AKB2 Quality Calibration — 全量端到端校准跑"
    )
    parser.add_argument(
        "--corpus",
        default="evals/main-pipeline-quality/corpus.yaml",
        help="语料 YAML 路径 (默认: evals/main-pipeline-quality/corpus.yaml)",
    )
    parser.add_argument(
        "--report-out",
        required=True,
        help="输出报告 JSON 路径",
    )
    parser.add_argument(
        "--live-sample",
        type=int,
        default=0,
        help="从 sources-ledger 取 N 个 reachable 真实源实抓跑全链",
    )
    parser.add_argument(
        "--vault-root",
        help="vault 根目录 (默认从 vault.toml 解析)",
    )
    parser.add_argument(
        "--timeout-per-entry",
        type=int,
        default=DEFAULT_TIMEOUT_PER_ENTRY_SEC,
        help=f"每条超时秒数 (默认 {DEFAULT_TIMEOUT_PER_ENTRY_SEC})",
    )

    args = parser.parse_args(argv)

    config = _default_config()
    if args.vault_root:
        config["vault_root"] = args.vault_root
        config["staging_dir"] = os.path.join(args.vault_root, "staging")
        config["raw_dir"] = os.path.join(args.vault_root, "raw")

    try:
        report = run_calibration(
            corpus_path=args.corpus,
            report_out=args.report_out,
            config=config,
            live_sample=args.live_sample,
        )
    except Exception as exc:
        print(f"ERROR: 校准失败: {exc}", file=sys.stderr)
        return 1

    return 0 if report.get("factory_acceptance", False) else 1


if __name__ == "__main__":
    sys.exit(main())
