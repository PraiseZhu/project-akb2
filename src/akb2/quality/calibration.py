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
    - positive_pass_rate / negative_reject_rate: 出厂判定指标（混池）
    - positive_live_pass_rate / positive_fixture_pass_rate: 分层通过率（B3-1）
    - live_positive_intake_raw_failures: live 正样本卡 intake_raw 计数
    - promotion_mode: proposal | active
    - amendment_status: not_applied | applied
    - per_segment_failures: 各段失败分布
    - rounds_distribution: 修复轮数分布
    - entries: 逐条详细结果（每条含诊断键 failure_class/extract_method/
      word_count/metadata_complete/missing_metadata/extract_error/source）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.akb2.quality.intake.extract import (
    MIN_CONTENT_LENGTH,
    REQUIRED_METADATA_FIELDS,
    ExtractResult,
    extract_content,
)
from src.akb2.quality.intake.models import ExtractionContext
from src.akb2.quality.intake.raw_archiver import ARTIFACT_TYPE, FRONTMATTER_FIELDS

# ---------------------------------------------------------------------------
# 具名常量
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_PER_ENTRY_SEC: int = 300
MAX_LIVE_SAMPLE_DEFAULT: int = 3
# B3-1: live 层下限——fixture 不补偿 live health
LIVE_POSITIVE_QUOTA: int = 3           # live 正样本最少条数
LIVE_POSITIVE_PASS_FLOOR: float = 0.9  # live 正样本通过率下限

# corpus fixtures 目录（_load_fixture_content 按 fixture:<name> 读取）
_FIXTURES_DIR_DEFAULT = Path("evals/main-pipeline-quality/fixtures")

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
        # B3-1 分层字段（live / fixture 物理分离）
        "positive_live_total": 0,
        "positive_live_passed": 0,
        "positive_live_pass_rate": 0.0,
        "positive_fixture_total": 0,
        "positive_fixture_passed": 0,
        "positive_fixture_pass_rate": 0.0,
        "live_positive_intake_raw_failures": 0,
        "promotion_mode": promotion,
        "amendment_status": amendment,
        "per_segment_failures": {},
        "rounds_distribution": {},
        "entries": [],
    }


# ---------------------------------------------------------------------------
# 单条处理
# ---------------------------------------------------------------------------


def _build_extraction_context(
    entry: dict[str, Any], config: dict[str, Any]
) -> ExtractionContext:
    """从 corpus entry 构造 ExtractionContext（镜像 orchestrator.py:214-227）。

    从 entry 读 feed_title/feed_author/feed_published/source_publisher/source_slug + url，
    缺字段回退空串。供 extract_content 的 _apply_feed_fallback 回填缺失元数据（SC-2 根因）。
    """
    return ExtractionContext(
        source_slug=entry.get("source_slug", "") or "",
        source_publisher=entry.get("source_publisher", "") or "",
        feed_title=entry.get("feed_title", "") or "",
        feed_author=entry.get("feed_author", "") or "",
        feed_published=entry.get("feed_published", "") or "",
        url=entry.get("url", "") or "",
    )


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
        单条结果 dict（含全 7 诊断键 + source，全 6 返回路径携带）
    """
    url = entry.get("url", "")
    domain = entry.get("domain", "unknown")
    expected = entry.get("expected", "ingest")
    notes = entry.get("notes", "")
    replaced_reason = entry.get("replaced_reason", "")

    # base result dict — 全诊断键安全默认值（防 all() KeyError，round-2 P1）
    # 全 6 条返回路径（成功/死链早退/抽取失败/fixture/负样本/ImportError）都携带这些键
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
        # SC-1 诊断键
        "failure_class": None,        # dead_link | metadata_failure | extraction_failure | None
        "extract_method": None,       # defuddle | scrapling | docling | fixture | failed
        "word_count": 0,
        "metadata_complete": False,
        "extraction_success": False,
        "extract_error": None,
        "missing_metadata": [],       # ["title", "author", "published", "url"] 子集
        "source": None,               # fixture | live
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
        # path 1: ImportError 早退 — defaults 携带诊断键
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

    # --- 抓取内容（fixture / live 分流）---
    if url.startswith("fixture:"):
        # fixture 路径（Step.2b/SC-1a）
        result["source"] = "fixture"
        result["extract_method"] = "fixture"
        fixture_name = url.replace("fixture:", "")
        fixture_content = _load_fixture_content(fixture_name, config)
        if fixture_content is None:
            # path 2: fixture 缺失 — defaults 携带诊断键
            result["final_status"] = "fetch_failed"
            result["failure_segment"] = "intake_raw"
            result["failure_reason"] = f"fixture 缺失: {fixture_name}"
            result["failure_attribution"] = "pipeline_defect"
            result["failure_class"] = "extraction_failure"
            return result
        # fixture 正文加载成功
        result["extraction_success"] = True
        raw_content = fixture_content
    else:
        # live 路径（Step.2: 镜像 orchestrator 构造 ExtractionContext）
        result["source"] = "live"
        ctx = _build_extraction_context(entry, config)
        extract_result = _fetch_url_content(url, config, ctx)

        # 填充诊断键（从 ExtractResult 派生，extract.py 零改动）
        result["extract_method"] = (
            extract_result.method.value
            if hasattr(extract_result.method, "value")
            else str(extract_result.method)
        )
        result["word_count"] = extract_result.word_count
        result["metadata_complete"] = extract_result.metadata_complete
        result["extraction_success"] = extract_result.extraction_success
        result["extract_error"] = extract_result.error
        result["missing_metadata"] = _compute_missing_metadata(extract_result)

        if not (extract_result.extraction_success and extract_result.content):
            # path 3: live 抽取失败 — 三分类
            preflight_lookup = config.get("preflight_lookup", {})
            preflight_entry = preflight_lookup.get(url, {})
            http_status = preflight_entry.get("http_status")
            result["failure_class"] = _classify_failure_class(
                extract_result, http_status
            )
            result["final_status"] = "fetch_failed"
            result["failure_segment"] = "intake_raw"
            result["failure_reason"] = extract_result.error or "抽取失败"
            result["failure_attribution"] = (
                "corpus_inappropriate"
                if result["failure_class"] in ("dead_link", "metadata_failure")
                else "pipeline_defect"
            )
            return result

        # path 4: live 抽取成功 → compose raw with 9-field frontmatter (SC-6)
        raw_content = _compose_raw_content(extract_result, ctx)

    # --- 运行七段管道 ---
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
        # path 5: 正样本——期望通过
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
        # path 6: 负样本——期望拒收
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


def _fetch_url_content(
    url: str,
    config: dict[str, Any],
    ctx: ExtractionContext | None = None,
) -> ExtractResult:
    """对 live URL 走生产 extract_content 决策树（镜像 orchestrator 构造 ExtractionContext）。

    返回完整 ExtractResult（含 method/title/author/published/word_count/
    metadata_complete/extraction_success/error/raw_metadata 诊断字段），
    不再只回 result.content（SC-1 诊断 + SC-2 ctx 传递）。
    fixture: 前缀由 _process_corpus_entry 直接调 _load_fixture_content 处理。
    """
    if url.startswith("fixture:"):
        # fixture 应由 _load_fixture_content 处理；防御性返回失败
        result = ExtractResult(url=url)
        result.error = "fixture URL 应由 _load_fixture_content 处理"
        return result

    failures_path = config.get("failures_path", "")
    try:
        return extract_content(
            url,
            failures_path=failures_path or None,
            extraction_config=ctx,
        )
    except Exception as exc:
        # extract_content 内部已捕获大部分异常；此兜底防意外
        result = ExtractResult(url=url)
        result.error = f"_fetch_url_content 异常: {exc}"
        return result


def _load_fixture_content(
    fixture_name: str, config: dict[str, Any] | None = None
) -> str | None:
    """按 fixture:<name> 从 evals/main-pipeline-quality/fixtures/<name>.md 读取。

    SC-1a/cross-review P1#M1：不再硬编码只认 google-io，改为按名读目录。
    文件缺失 return None（→ _process_corpus_entry 标 extraction_failure）。
    """
    # 候选目录：config → 默认相对路径 → __file__ 推导绝对兜底
    candidate_dirs: list[Path] = []
    if config and config.get("fixtures_dir"):
        candidate_dirs.append(Path(config["fixtures_dir"]))
    candidate_dirs.append(_FIXTURES_DIR_DEFAULT)
    # 绝对兜底（防 CWD 非 repo root）
    candidate_dirs.append(
        Path(__file__).resolve().parents[3]
        / "evals"
        / "main-pipeline-quality"
        / "fixtures"
    )

    for directory in candidate_dirs:
        fixture_path = directory / f"{fixture_name}.md"
        if fixture_path.exists():
            return fixture_path.read_text(encoding="utf-8")
    return None


def _compose_raw_content(
    extract_result: ExtractResult, ctx: ExtractionContext | None
) -> str:
    """拼装 9 字段 frontmatter + result.content 成完整 raw markdown（SC-6）。

    字段名集合从 raw_archiver.FRONTMATTER_FIELDS 取（不硬编码，B3-7）。
    source_* 优先取 extract_result、缺失从 ctx 补。
    """
    method_str = (
        extract_result.method.value
        if hasattr(extract_result.method, "value")
        else str(extract_result.method)
    )

    # source_* 优先取 result、缺失从 ctx 补
    source_slug = ""
    source_publisher = extract_result.raw_metadata.get("publisher", "")
    if ctx is not None:
        source_slug = ctx.source_slug or source_slug
        source_publisher = source_publisher or ctx.source_publisher

    fm_values: dict[str, str] = {
        "artifact_type": ARTIFACT_TYPE,
        "source_slug": source_slug,
        "source_url": extract_result.url,
        "source_title": extract_result.title,
        "source_authors": extract_result.author,
        "source_publisher": source_publisher,
        "source_published_at": extract_result.published,
        "fetched_at": _now_iso(),
        "fetched_via": method_str,
    }

    # 按 FRONTMATTER_FIELDS 顺序拼装 frontmatter（等价 raw_archiver._format_frontmatter_yaml）
    lines = ["---"]
    special_chars = (":", "#", "[", "]", "{", "}", "*", "&", "!", "|", ">", "'", '"')
    for fname in FRONTMATTER_FIELDS:
        value = fm_values.get(fname, "")
        if any(c in str(value) for c in special_chars):
            value = f'"{value}"'
        lines.append(f"{fname}: {value}")
    lines.append("---")

    fm_block = "\n".join(lines)
    return f"{fm_block}\n\n{extract_result.content}\n"


def _classify_failure_class(
    extract_result: ExtractResult | None,
    http_status: int | str | None = None,
) -> str | None:
    """三分类失败归因（SC-1）: dead_link / metadata_failure / extraction_failure / None。

    优先级:
        1. extraction_success=True → None（成功）
        2. HTTP 死链（preflight http_status>=400/unreachable，或退化按 error 网络特征）→ dead_link
        3. word_count>=MIN 且 metadata_complete=False → metadata_failure
        4. 其余 → extraction_failure

    preflight 缺失时 http_status=None，dead_link 退化按 ExtractResult.error（B3-3）。
    """
    # 1. 成功
    if extract_result is not None and extract_result.extraction_success:
        return None

    # 2. dead_link
    # 2a. preflight http_status 明确指示
    if http_status is not None:
        if isinstance(http_status, int) and http_status >= 400:
            return "dead_link"
        if http_status in ("unreachable", "error", 0):
            return "dead_link"
    # 2b. 退化: 无 preflight，无正文 + error 含网络特征 → dead_link
    if http_status is None and extract_result is not None:
        if (
            not extract_result.content
            and not extract_result.word_count
            and extract_result.error
        ):
            err_lower = str(extract_result.error).lower()
            network_indicators = (
                "all extraction methods failed",
                "connection",
                "timeout",
                "refused",
                "unreachable",
            )
            if any(ind in err_lower for ind in network_indicators):
                return "dead_link"

    # 3. metadata_failure: 有正文但元数据不全
    if extract_result is not None:
        if (
            extract_result.word_count >= MIN_CONTENT_LENGTH
            and not extract_result.metadata_complete
        ):
            return "metadata_failure"

    # 4. extraction_failure
    return "extraction_failure"


def _compute_missing_metadata(extract_result: ExtractResult) -> list[str]:
    """返回缺失的必需元数据字段列表（title/author/published/url）。

    从 extract.py REQUIRED_METADATA_FIELDS 取字段名（不硬编码）。
    """
    missing: list[str] = []
    for field_name in REQUIRED_METADATA_FIELDS:
        if not getattr(extract_result, field_name, ""):
            missing.append(field_name)
    return missing


def _load_preflight_lookup(
    preflight_report_path: str,
) -> dict[str, dict[str, Any]]:
    """加载 preflight 报告，返回 {url: entry} 查找表。

    用于 dead_link 分类读 http_status。报告缺失时返回空 dict（退化按 error）。
    """
    if not preflight_report_path:
        return {}
    path = Path(preflight_report_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            e["url"]: e for e in data.get("entries", []) if "url" in e
        }
    except (json.JSONDecodeError, OSError):
        return {}


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
    """从逐条结果计算汇总统计（含 B3-1 live/fixture 分层）。"""
    entries: list[dict] = report["entries"]

    # 混池正样本通过率
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

    # B3-1 分层: live / fixture（物理分离，fixture 不补偿 live）
    live_pos = [e for e in positive_entries if e.get("source") == "live"]
    fixture_pos = [e for e in positive_entries if e.get("source") == "fixture"]

    report["positive_live_total"] = len(live_pos)
    report["positive_live_passed"] = sum(1 for e in live_pos if e["passed"])
    report["positive_live_pass_rate"] = (
        report["positive_live_passed"] / len(live_pos) if live_pos else 0.0
    )
    report["positive_fixture_total"] = len(fixture_pos)
    report["positive_fixture_passed"] = sum(1 for e in fixture_pos if e["passed"])
    report["positive_fixture_pass_rate"] = (
        report["positive_fixture_passed"] / len(fixture_pos) if fixture_pos else 0.0
    )
    report["live_positive_intake_raw_failures"] = sum(
        1
        for e in live_pos
        if e.get("failure_segment") == "intake_raw"
    )

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

    # 设置 fixtures_dir（从 corpus 路径推导，供 _load_fixture_content）
    if corpus_path:
        config["fixtures_dir"] = str(Path(corpus_path).parent / "fixtures")

    # 加载 preflight 报告（供 dead_link 分类读 http_status；缺失退化按 error）
    preflight_path = config.get("preflight_report_path", "")
    config["preflight_lookup"] = _load_preflight_lookup(preflight_path)

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

    # 出厂判定（B3-1: 混池 AND live 层下限，fixture 不补偿 live）
    report["factory_acceptance"] = (
        report["positive_pass_rate"] >= 0.9
        and report["negative_reject_rate"] == 1.0
        and report["positive_live_total"] >= LIVE_POSITIVE_QUOTA
        and report["live_positive_intake_raw_failures"] == 0
        and report["positive_live_pass_rate"] >= LIVE_POSITIVE_PASS_FLOOR
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
        f"  正样本通过率(混池): {pp_rate:.2%} ({pp_rate >= 0.9})",
        file=sys.stderr,
    )
    print(
        f"  负样本拒收率: {nr_rate:.2%} ({nr_rate == 1.0})",
        file=sys.stderr,
    )
    lp_rate = report['positive_live_pass_rate']
    print(
        f"  live 正样本通过率: {lp_rate:.2%} "
        f"({report['positive_live_passed']}/{report['positive_live_total']}, "
        f"floor={LIVE_POSITIVE_PASS_FLOOR})",
        file=sys.stderr,
    )
    print(
        f"  live intake_raw 卡死: {report['live_positive_intake_raw_failures']}",
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
    vault_root = ""
    staging_dir = ""
    raw_dir = ""

    try:
        from src.akb2.loops.paths import get_akb2_root, get_staging_root

        vault_root = str(get_akb2_root())
        staging_dir = str(get_staging_root())
    except Exception:
        vault_root = os.getcwd()
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
        "preflight_report_path": "",
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
        "--preflight-report",
        default="",
        help="preflight 报告 JSON 路径（供 dead_link 分类；缺失时退化按 error）",
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
    if args.preflight_report:
        config["preflight_report_path"] = args.preflight_report

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
