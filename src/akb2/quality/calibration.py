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
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.akb2.quality.chrome_strip import ANCHOR_DECL_RE, strip_chrome_blocks
from src.akb2.quality.intake.extract import (
    MIN_CONTENT_LENGTH,
    REQUIRED_METADATA_FIELDS,
    ExtractResult,
    extract_content,
)
from src.akb2.quality.intake.models import ExtractionContext
from src.akb2.quality.intake.raw_archiver import (
    ANCHOR_DEFINITIONS,
    ARTIFACT_TYPE,
    FRONTMATTER_FIELDS,
    REQUIRED_ANCHORS,
    _assert_anchors,
    _inject_anchors,
)

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
        # SC-1 live floor 子契约：按 reachable live 正样本数 n 动态调整
        "effective_live_positive_pass_floor": LIVE_POSITIVE_PASS_FLOOR,  # 默认 0.9
        "live_positive_pass_floor_reason": "",  # 下调原因（如 "5<=n<10, 下调到 0.8"）
        "promotion_mode": promotion,
        "amendment_status": amendment,
        "per_segment_failures": {},
        "rounds_distribution": {},
        # WP-0/D6: 去重 funnel（按 chrome-stripped body content hash，排除
        # frontmatter/TOC/锚点行/volatile fetched_at）+ 全链 failure_class taxonomy
        # + 独立 repair_acceptance（与 factory_acceptance 解耦，SC-5 修复门）
        "deduped_funnel": {
            "total_entries": 0,
            "unique_bodies": 0,
            "duplicate_count": 0,
            "duplicate_groups": [],
        },
        "repair_acceptance": {
            "positive_pass_rate": 0.0,  # mechanism_present=true 子集，SC-5 目标 ≥0.60
            "mechanism_present_true_total": 0,
            "mechanism_present_true_passed": 0,
            "negative_reject_rate": 0.0,  # 含 no_mechanism 负样本，目标 ==1.0
            "negative_total": 0,
            "negative_rejected": 0,
            "layers": {},
            "procedural_zero_pass_hard_fail": False,
            "needs_adjudication_count": 0,
            "needs_adjudication_indices": [],
            "repair_acceptance_gate": False,
            "AUTO_PROMOTE": os.environ.get("AKB2_AUTO_PROMOTE", "0"),
        },
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
        # WP-0: corpus 审计块透传（mechanism_present 驱动 repair_acceptance，SC-5）
        # + 去重 hash（chrome-stripped body，排除 fm/TOC/anchor/fetched_at，D6）
        "mechanism_present": (entry.get("audit") or {}).get("mechanism_present"),
        "reject_reason": (entry.get("audit") or {}).get("reject_reason"),
        "review_status": (entry.get("audit") or {}).get("review_status", "confirmed"),
        "raw_content_sha256": None,
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
        # P1: 注入锚点 + 锚点目录（镜像 archive_raw，使 fixture raw 与生产同形）
        raw_content = _inject_anchors_into_raw(fixture_content)
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

    # WP-0/D6: raw_content_sha256 = chrome-stripped body hash（去重 funnel key）。
    # hash 输入排除 frontmatter / TOC / 锚点声明行 / volatile fetched_at / chrome，
    # 故同正文不同 fetched_at 必去重为 1（不复用 runner._compute_raw_hash 的路径 hash）。
    result["raw_content_sha256"] = _compute_raw_content_sha256(raw_content)

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

    # P0-0：携 runner 机读诊断字段（content_lane/failed_node/error_class），
    # 报告据此区分真实失败阶段，不再只靠 final_status 粗映射。
    result["content_lane"] = pipeline_result.get("content_lane", "")
    result["failed_node"] = pipeline_result.get("failed_node")
    result["error_class"] = pipeline_result.get("error_class")
    # §0.2/D9：透传 fallback_quality_basis（component_count/source_causal_edge_count/
    # inferred_edge_count/component_evidence_anchors）——失败路径也须可见，不靠 error
    # string 解析。供 §10 漏斗量化 + D12 counters 归因。
    fbasis = pipeline_result.get("fallback_quality_basis")
    if isinstance(fbasis, dict):
        result["fallback_quality_basis"] = fbasis

    if expected == "ingest":
        # path 5: 正样本——期望通过
        if final_status in ("promoted", "proposal_passed"):
            result["passed"] = True
        else:
            result["passed"] = False
            # 优先用 runner 返回的真实失败节点；缺失时回落 final_status 粗映射。
            result["failure_segment"] = (
                pipeline_result.get("failed_node")
                or _classify_failure_segment(final_status)
            )
            result["failure_reason"] = pipeline_result.get("error", "未知原因")
            result["failure_attribution"] = _classify_attribution(
                final_status, pipeline_result
            )
            # grader 环境不可用（GPT-5.4 down / 双裁判挂）是裁判环境问题，不是代码缺陷：
            # 单列 grader_unavailable / environment_blocked，避免与真实质量缺陷混桶。
            ec = str(pipeline_result.get("error_class") or "")
            if ec.startswith("grader_") and (
                "fallback_not_allowed" in ec or "all_down" in ec
            ):
                result["failure_segment"] = "grader_unavailable"
                result["failure_attribution"] = "environment_blocked"
    else:
        # path 6: 负样本——期望拒收
        if final_status == "rejected":
            result["passed"] = True  # 拒收 = 正样本场景中的 "pass"
            result["final_status"] = "rejected_correctly"
            # P0-0：保留底层真实拒因，避免 rejected_correctly 抹掉"靠哪道门拒"。
            result["underlying_final_status"] = "rejected"
            result["underlying_error"] = pipeline_result.get("error")
            result["underlying_failed_node"] = pipeline_result.get("failed_node")
            result["underlying_error_class"] = pipeline_result.get("error_class")
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
    """拼装 9 字段 frontmatter + 锚点目录 + 锚点化正文（镜像 raw_archiver.archive_raw）。

    P1 修复：复用 raw_archiver._inject_anchors / _assert_anchors /
    ANCHOR_DEFINITIONS，保证 calibration raw 与生产 raw 同形
    （fm_block + 锚点目录 + anchored_content），供下游 distill self-gate
    （checks_structural.A3）校验引用锚点存在性——不再因缺 §A 触发
    "引用的锚点 §A 在 raw 文件中不存在"。

    字段名集合从 raw_archiver.FRONTMATTER_FIELDS 取（不硬编码，B3-7）。
    source_* 优先取 extract_result、缺失从 ctx 补。

    fail-closed: 必需锚点缺失时仍返回带锚点目录的 raw（含已注入锚点），
    由下游 distill self-gate 裁决 anchor_mismatch——镜像 archive_raw
    :367-384 的 fail-closed 流（archive_raw 不写文件 + 落签名；此处无
    落盘环节，走 self-gate 等价路径，不自创逻辑）。
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

    # 注入锚点 + 锚点目录（镜像 raw_archiver.archive_raw :360-384）
    # WP-1: 注入锚点前先剥 chrome（与 intake extract 同 helper，两链同源；
    # extract_result.content 在 extract.py 已剥过，此处 idempotent 再剥保两链一致）
    clean_content, _chrome_diag = strip_chrome_blocks(extract_result.content)
    anchored_content, toc_entries, hit_counts = _inject_anchors(
        clean_content, ANCHOR_DEFINITIONS
    )
    # 断言必需锚点（fail-closed 契约；实际裁决由下游 distill self-gate 执行）
    _assert_anchors(hit_counts, REQUIRED_ANCHORS)

    toc_block = "## 锚点目录\n\n" + "\n".join(toc_entries) + "\n"
    return f"{fm_block}\n\n{toc_block}\n\n{anchored_content}\n"


def _inject_anchors_into_raw(raw_md: str) -> str:
    """对已含 frontmatter 的 raw markdown 注入锚点 + 锚点目录（fixture 路径）。

    P1 修复：fixture 文件有 frontmatter 但无锚点，运行时注入使其与生产
    archive_raw 产出的 raw 同形（fm_block + 锚点目录 + anchored_content），
    供下游 distill self-gate（checks_structural.A3）校验引用锚点存在性。

    分离 frontmatter 与正文后，对正文调用 _inject_anchors，frontmatter
    原样保留（含 fixture_kind/fixture_domain 等额外字段）。

    fail-closed: 必需锚点缺失时仍返回带锚点目录的 raw，由下游 distill
    self-gate 裁决（镜像 _compose_raw_content / archive_raw 语义）。
    """
    if not raw_md.startswith("---"):
        # 无 frontmatter — 直接注入（WP-1: 先剥 chrome）
        body, _ = strip_chrome_blocks(raw_md)
        anchored, toc, hit_counts = _inject_anchors(body, ANCHOR_DEFINITIONS)
        _assert_anchors(hit_counts, REQUIRED_ANCHORS)
        toc_block = "## 锚点目录\n\n" + "\n".join(toc) + "\n"
        return f"{toc_block}\n\n{anchored}\n"

    # 定位 frontmatter 闭合（第二个 --- 行）
    second_delim = raw_md.find("\n---", 3)
    if second_delim < 0:
        # 无闭合 — 当作无 frontmatter（WP-1: 先剥 chrome）
        body, _ = strip_chrome_blocks(raw_md)
        anchored, toc, hit_counts = _inject_anchors(body, ANCHOR_DEFINITIONS)
        _assert_anchors(hit_counts, REQUIRED_ANCHORS)
        toc_block = "## 锚点目录\n\n" + "\n".join(toc) + "\n"
        return f"{toc_block}\n\n{anchored}\n"

    fm_block = raw_md[: second_delim + 4]  # 含闭合 ---（无 trailing newline）
    body = raw_md[second_delim + 4 :].lstrip("\n")
    body, _ = strip_chrome_blocks(body)  # WP-1: 注入锚点前剥 chrome

    anchored, toc, hit_counts = _inject_anchors(body, ANCHOR_DEFINITIONS)
    _assert_anchors(hit_counts, REQUIRED_ANCHORS)
    toc_block = "## 锚点目录\n\n" + "\n".join(toc) + "\n"
    return f"{fm_block}\n\n{toc_block}\n\n{anchored}\n"


def _classify_failure_class(
    extract_result: ExtractResult | None,
    http_status: int | str | None = None,
) -> str | None:
    """三分类失败归因（SC-1）: dead_link / metadata_failure / extraction_failure / None。

    优先级:
        1. extraction_success=True → None（成功）
        2. HTTP 死链（preflight http_status>=400 或 unreachable/error/0）→ dead_link
        3. word_count>=MIN 且 metadata_complete=False → metadata_failure
        4. 其余 → extraction_failure

    P2 修复：dead_link 只在有 HTTP 死链证据（preflight http_status>=400 或
    unreachable/error/0）时才标；无 preflight（http_status=None）时 extraction
    全失败（如 "All extraction methods failed"）保守归 extraction_failure，
    不再退化按 error 网络特征误判 dead_link——避免把抓取器全部失败错分为死链。
    """
    # 1. 成功
    if extract_result is not None and extract_result.extraction_success:
        return None

    # 2. dead_link — 仅认 preflight HTTP 证据（P2: 不再退化按 error 判 dead_link）
    if http_status is not None:
        if isinstance(http_status, int) and http_status >= 400:
            return "dead_link"
        if http_status in ("unreachable", "error", 0):
            return "dead_link"

    # 3. metadata_failure: 有正文但元数据不全
    if extract_result is not None:
        if (
            extract_result.word_count >= MIN_CONTENT_LENGTH
            and not extract_result.metadata_complete
        ):
            return "metadata_failure"

    # 4. extraction_failure（无 preflight 时全失败保守归此类，P2）
    return "extraction_failure"


def _extract_body_for_hash(raw_content: str) -> str:
    """提取去重用 body 文本：去 frontmatter / 锚点目录 / 锚点声明行 / chrome。

    hash 输入明确排除（WP-0/D6 round-2 P2）：
      - frontmatter（含 volatile ``fetched_at``）；
      - 锚点目录块（``## 锚点目录`` + ``- §X: ...`` 条目）；
      - 锚点声明行（raw_archiver 注入的 ``§A_CONTENT_START`` 等）；
      - 站点导航 chrome（经 strip_chrome_blocks 剥离）。
    故同一正文不同 fetched_at / 不同 chrome 残留 → 同 hash（dedup 为 1）。
    """
    if not raw_content:
        return ""
    text = raw_content
    # 1. 去 frontmatter（首尾 --- 之间，含 volatile fetched_at）
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:].lstrip("\n")
    # 2. 逐行过滤锚点目录标题 / TOC 条目 / 锚点声明行
    kept: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == "## 锚点目录":
            continue
        if stripped.startswith("- §") and ":" in stripped:
            continue
        if ANCHOR_DECL_RE.match(line):
            continue
        kept.append(line)
    body = "\n".join(kept)
    # 3. 去 chrome（与 intake extract 共用同一 helper，WP-1 两链同源）
    clean, _ = strip_chrome_blocks(body)
    return clean.strip()


def _compute_raw_content_sha256(raw_content: str | None) -> str | None:
    """计算 raw_content 的 chrome-stripped body sha256（去重 funnel key）。

    返回 None 表示无 raw_content（早退路径：fixture 缺失 / live 抽取失败 /
    ImportError），该 entry 不计入 deduped_funnel.total（缺 hash 不参与去重统计）。
    """
    if not raw_content:
        return None
    body = _extract_body_for_hash(raw_content)
    if not body:
        return None
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _classify_failure_class_chain(result: dict[str, Any]) -> str:
    """全链 failure_class taxonomy（WP-0/D6）。

    pass → 哨兵 ``"none"``；否则按 failed_node / error_class / final_status 映射，
    覆盖 admission / distill_extract / distill_compose / integrity_floor /
    repair_loop / grader / promote 全段（不只 intake_raw 的三分类）。

    intake_raw 段已由 ``_classify_failure_class`` 设具体值（dead_link /
    metadata_failure / extraction_failure），此处保留不覆盖。
    """
    if result.get("passed"):
        return "none"
    # 负样本未被正确拒收（passed=False 且 expected=reject）
    if result.get("expected") == "reject":
        return "negative_not_rejected"
    # intake_raw 段已有具体 failure_class → 保留
    existing = result.get("failure_class")
    if existing and existing != "none":
        return existing
    failed_node = str(result.get("failed_node") or "")
    error_class = str(result.get("error_class") or "")
    final_status = str(result.get("final_status") or "")
    # 具体段优先（按 failed_node / error_class 精确映射，先于 final_status 兜底，
    # 避免 integrity_floor/distill 等的 final_status=rejected 被误归 admission）
    if "poisoned_trace_entailment" in error_class:
        return "poisoned_trace_entailment"
    if "distill_no_converge" in error_class:
        return "repair_no_converge"
    if "integrity_floor" in failed_node or "integrity_floor" in error_class:
        return "integrity_floor_failed"
    if failed_node == "distill_extract":
        return "distill_extract_failed"
    if failed_node == "distill_compose":
        return "distill_compose_failed"
    if failed_node == "grader" or error_class.startswith("grader_"):
        if "fallback_not_allowed" in error_class or "all_down" in error_class:
            return "grader_unavailable"
        return "grader_failed"
    if failed_node == "promote":
        return "promote_failed"
    if failed_node == "admission":
        return "admission_reject"
    # final_status 兜底（failed_node 缺失时，rejected 多为 admission 段拒收）
    if final_status == "rejected":
        return "admission_reject"
    if final_status == "fetch_failed":
        return "extraction_failure"
    if final_status == "error":
        return "module_import_error"
    return "unknown"


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

    SC-7：how_tendency 拒收不再机械归 corpus_inappropriate——how_tendency 门是管线
    判定（concept 车道下豁免），其 fail 暗示 lane 分类可能把概念内容误判 procedural，
    属管线缺陷风险，不能等同于"语料本身 how 向"。旧启发式（rejected+how_tendency
    →corpus_inappropriate）是循环论证：把"分类器误判"包装成"语料不当"，直接导致
    交接文档把 7 条概念样本的 lane 误判错诊为"语料不当"。仅 topic_relevance（离题，
    客观语料问题）与 fetch_failed（死链）才确定性归 corpus_inappropriate。
    """
    if final_status == "rejected":
        error = pipeline_result.get("error", "")
        if "topic_relevance" in str(error):
            return "corpus_inappropriate"
        # how_tendency / novelty / content_lane 等拒收归 pipeline_defect——
        # 这些是管线门判定（含 lane 分类），不机械责怪语料。
        return "pipeline_defect"
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


def _compute_effective_live_floor(n: int) -> tuple[float, str]:
    """SC-1 live floor 子契约：按 reachable live 正样本数 n 计算 effective floor。

    master-plan.md:41 规则：
    - n<5：不下调（0.9，样本太小则补 live 或重探）
    - 5<=n<10：下调到 0.8（最多容忍 1 条公网漂移）
    - n>=10：回到 0.9
    - 任何情况下 floor 永不低于 0.8

    Args:
        n: reachable live 正样本数（= positive_live_total）。

    Returns:
        (effective_floor, reason)：effective_floor ∈ [0.8, 0.9]；
        reason 为非空字符串，说明 floor 取值依据。
    """
    if n < 5:
        floor, reason = 0.9, f"n={n}<5, floor=0.9 样本不足需补 live 或重探"
    elif n < 10:
        floor, reason = 0.8, f"5<=n={n}<10, 下调到 0.8 容忍 1 条公网漂移"
    else:
        floor, reason = 0.9, f"n={n}>=10, floor=0.9 未下调"
    # 任何情况下 floor 永不低于 0.8（防御性 clamp）
    if floor < 0.8:
        floor = 0.8
        reason = f"{reason} (clamp to 0.8)"
    return floor, reason


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

    # SC-1 live floor 子契约：按 reachable live 正样本数 n 动态计算 effective floor
    # n<5 → 0.9；5<=n<10 → 0.8；n>=10 → 0.9；永不低于 0.8（master-plan.md:41）
    live_n = len(live_pos)
    effective_floor, floor_reason = _compute_effective_live_floor(live_n)
    report["effective_live_positive_pass_floor"] = effective_floor
    report["live_positive_pass_floor_reason"] = floor_reason

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

    # WP-0/D6: 全链 failure_class taxonomy — 每 entry failure_class 非 null
    # （pass → "none"；intake_raw 保留三分类；pipeline 失败按 failed_node 映射）
    for e in entries:
        e["failure_class"] = _classify_failure_class_chain(e)

    # WP-0/D6: 去重 funnel — 按 chrome-stripped body hash 聚类（同正文不同 fetched_at → 1）
    hash_groups: dict[str, list[int]] = {}
    for e in entries:
        h = e.get("raw_content_sha256")
        if h:
            hash_groups.setdefault(h, []).append(e.get("index"))
    total_with_hash = sum(len(v) for v in hash_groups.values())
    unique_bodies = len(hash_groups)
    dup_groups = [
        {"hash": h[:16], "indices": v, "count": len(v)}
        for h, v in hash_groups.items()
        if len(v) > 1
    ]
    report["deduped_funnel"] = {
        "total_entries": total_with_hash,
        "unique_bodies": unique_bodies,
        "duplicate_count": total_with_hash - unique_bodies,
        "duplicate_groups": dup_groups,
    }

    # WP-0/SC-5: repair_acceptance — mechanism_present=true 子集修复门
    # （与 factory_acceptance 解耦；procedural 0 通过 = 硬 fail）
    report["repair_acceptance"] = _compute_repair_acceptance(entries)

    return report


def _compute_repair_acceptance(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """SC-5 修复门：mechanism_present=true 子集通过率 + 分层 + procedural 硬 fail。

    与 factory_acceptance 解耦（factory_acceptance / CLI exit 不动）：
      - positive_pass_rate: mechanism_present=true 且 expected=ingest 子集通过率，
        SC-5 concept 起步线 ≥0.60；
      - negative_reject_rate: 负样本（含 no_mechanism）拒收率，目标 ==1.0；
      - layers: live/fixture/concept/procedural 分层通过率（concept/procedural
        取运行时 content_lane；空 lane 不计入 concept/procedural 层）；
      - procedural_zero_pass_hard_fail: procedural 子集 >0 且 0 通过 → True（SC-5 硬 fail）；
      - needs_adjudication: 双审不一致条目，不计入 mechanism_present=true 分母（不移 reject）。
    """
    def _layer(subset: list[dict[str, Any]]) -> dict[str, Any]:
        passed = sum(1 for e in subset if e.get("passed"))
        total = len(subset)
        return {
            "passed": passed,
            "total": total,
            "pass_rate": (passed / total) if total else 0.0,
        }

    mp_true = [
        e for e in entries
        if e.get("mechanism_present") is True and e.get("expected") == "ingest"
    ]
    mp_layer = _layer(mp_true)
    live_layer = _layer([e for e in mp_true if e.get("source") == "live"])
    fixture_layer = _layer([e for e in mp_true if e.get("source") == "fixture"])
    concept_layer = _layer([
        e for e in mp_true
        if str(e.get("content_lane", "")).lower() == "concept"
    ])
    procedural_layer = _layer([
        e for e in mp_true
        if str(e.get("content_lane", "")).lower() == "procedural"
    ])

    neg = [e for e in entries if e.get("expected") == "reject"]
    neg_rejected = sum(1 for e in neg if e.get("passed"))
    neg_reject_rate = (neg_rejected / len(neg)) if neg else 0.0

    needs_adj = [
        e for e in entries if e.get("review_status") == "needs_adjudication"
    ]
    procedural_hard_fail = (
        procedural_layer["total"] > 0 and procedural_layer["passed"] == 0
    )
    gate = (
        mp_layer["pass_rate"] >= 0.60
        and neg_reject_rate == 1.0
        and not procedural_hard_fail
    )
    return {
        "positive_pass_rate": mp_layer["pass_rate"],
        "mechanism_present_true_total": mp_layer["total"],
        "mechanism_present_true_passed": mp_layer["passed"],
        "negative_reject_rate": neg_reject_rate,
        "negative_total": len(neg),
        "negative_rejected": neg_rejected,
        "layers": {
            "live": live_layer,
            "fixture": fixture_layer,
            "concept": concept_layer,
            "procedural": procedural_layer,
        },
        "procedural_zero_pass_hard_fail": procedural_hard_fail,
        "needs_adjudication_count": len(needs_adj),
        "needs_adjudication_indices": [e.get("index") for e in needs_adj],
        "repair_acceptance_gate": gate,
        "AUTO_PROMOTE": os.environ.get("AKB2_AUTO_PROMOTE", "0"),
    }


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
    # SC-1 live floor 子契约：用 effective_live_positive_pass_floor 替代常量
    # （n=15 时 effective=0.9，不改变当前 factory_acceptance 行为；根因在 admission 包A）
    report["factory_acceptance"] = (
        report["positive_pass_rate"] >= 0.9
        and report["negative_reject_rate"] == 1.0
        and report["positive_live_total"] >= LIVE_POSITIVE_QUOTA
        and report["live_positive_intake_raw_failures"] == 0
        and report["positive_live_pass_rate"] >= report["effective_live_positive_pass_floor"]
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
    eff_floor = report['effective_live_positive_pass_floor']
    print(
        f"  live 正样本通过率: {lp_rate:.2%} "
        f"({report['positive_live_passed']}/{report['positive_live_total']}, "
        f"floor={eff_floor})",
        file=sys.stderr,
    )
    print(
        f"  live floor reason: {report['live_positive_pass_floor_reason']}",
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
