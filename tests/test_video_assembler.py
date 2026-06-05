"""Assembler 测试 — candidate 组装 + 发布 + gate 决策 + 丢弃 + CLI 入口。

覆盖：frontmatter 完整性、gate active 发布到 AI TRENDS/Video、gate 非 active 丢弃、
staging 中转清理、candidate 预存在 fail-closed、promotion 目标路径预检、
CLI 入口 smoke、dry-run 无副作用。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.video.assembler import (
    Assembler,
    CandidateTargetExists,
    GateRejected,
    PromotionTargetNotFound,
)
from src.akb2.video.config import VideoConfig
from src.akb2.video.extractor import (
    ExtractedFields,
    FutureQuestion,
    KeyFinding,
    SourceSpan,
)
from src.akb2.video.reasoner import ReasonedLinks, RelatedConcept, ProposedNode
from src.akb2.video.researcher import ResearchOutput


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_config() -> VideoConfig:
    """使用临时目录的 VideoConfig。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = VideoConfig(
            staging_dir=tmp_path / "staging" / "candidate",
            akb2_root=tmp_path,
        )
        yield config


@pytest.fixture
def sample_video_meta() -> dict:
    return {
        "title": "DeepSeek V4 Architecture Deep Dive",
        "url": "https://www.youtube.com/watch?v=test123",
        "platform": "youtube",
        "creator": "Test Channel",
        "upload_date": "20260528",
    }


@pytest.fixture
def sample_research_output(sample_video_meta) -> ResearchOutput:
    return ResearchOutput(
        report_markdown="# DeepSeek V4 架构分析\n\n## 执行摘要\n\nDeepSeek V4 是一个强大的模型。\n\n## 关键发现\n\n模型在 MMLU 上达到 92.5%。\n\n## 核心观点\n\n- 16 万亿参数\n- 128K 上下文窗口",
        notebook_id="nb-test-123",
        video_meta=sample_video_meta,
        degraded=False,
    )


@pytest.fixture
def sample_extracted_fields() -> ExtractedFields:
    return ExtractedFields(
        tldr="DeepSeek V4 是一个强大的模型。",
        key_findings=[
            KeyFinding(
                assertion="模型在 MMLU 上达到 92.5%",
                source_span=SourceSpan(line_start=7, line_end=7),
                confidence=0.9,
            ),
        ],
        future_questions=[],
        concepts_mentioned=["DeepSeek", "MoE"],
    )


@pytest.fixture
def sample_reasoned_links() -> ReasonedLinks:
    return ReasonedLinks(
        related_concept=[
            RelatedConcept(
                concept_id="concept:deepseek",
                relationship_type="evaluates",
                evidence="报告对比了 DeepSeek V4 的性能",
            ),
        ],
        proposed_graph_nodes=[
            ProposedNode(
                name="MoE",
                type="concept",
                reason="报告提到但 AKB 概念图未收录",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Test: assemble candidate frontmatter
# ---------------------------------------------------------------------------


def test_assemble_candidate_frontmatter(
    temp_config, sample_video_meta, sample_research_output,
    sample_extracted_fields, sample_reasoned_links,
):
    """验证 candidate frontmatter 字段完整性（HC-01~HC-09）。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    assert "page_status: candidate" in candidate_md
    assert "source_type: video" in candidate_md
    assert "template_type: source" in candidate_md
    assert "gate_decision: null" in candidate_md
    assert "evidence_paths:" in candidate_md
    assert "related_concepts:" in candidate_md
    assert "key_findings:" in candidate_md
    assert "tags:" in candidate_md


def test_assemble_body_gate_sections(
    temp_config, sample_video_meta, sample_research_output,
    sample_extracted_fields, sample_reasoned_links,
):
    """验证 body 含 ## TLDR + ## 关键发现（HC-08）。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    # frontmatter 结束后的 body 部分
    body_start = candidate_md.find("---\n", candidate_md.find("---\n") + 1)
    body = candidate_md[body_start:] if body_start != -1 else candidate_md

    assert "## TLDR" in body, "body 必须包含 ## TLDR 章节（HC-08）"
    assert "## 关键发现" in body, "body 必须包含 ## 关键发现 章节（HC-08）"
    assert sample_extracted_fields.tldr in body, "body 必须包含 tldr 文本"
    assert sample_extracted_fields.key_findings[0].assertion in body, "body 必须包含 key_finding assertion"


# ---------------------------------------------------------------------------
# Test: gate active → 发布到 promotion_target_path，staging 中转删除
# ---------------------------------------------------------------------------


def test_commit_active_publishes_to_promotion_path(
    temp_config, sample_video_meta, sample_research_output,
    sample_extracted_fields, sample_reasoned_links,
):
    """gate=active → 报告写到 AI TRENDS/Video，staging 中转被删除，返回正式路径。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    # 创建 promotion_target_path 目录
    temp_config.promotion_target_path.mkdir(parents=True, exist_ok=True)

    with mock.patch.object(assembler, "_gate_decision", return_value="active"):
        formal_path = assembler.commit(candidate_md, slug="test-video", date_str="2026-05-28")

    # 正式路径存在
    assert formal_path.exists(), f"正式路径不存在: {formal_path}"
    assert formal_path.parent == temp_config.promotion_target_path

    # staging 中转副本已删除
    staging_copy = temp_config.staging_dir / "video-test-video-2026-05-28.md"
    assert not staging_copy.exists(), f"staging 中转未删除: {staging_copy}"

    # 返回路径在 promotion_target_path 下（SC-1）
    assert formal_path == temp_config.promotion_target_path / "video-test-video-2026-05-28.md"

    # 正式文件已标记发布态：gate_decision: active（非 null）；page_status 保持 candidate（SC-4）
    formal_content = formal_path.read_text()
    assert "gate_decision: active" in formal_content, "正式文件应含 gate_decision: active"
    assert "gate_decision: null" not in formal_content, "正式文件不应含 gate_decision: null"
    assert "page_status: candidate" in formal_content, "page_status 应保持 candidate（SC-4）"


# ---------------------------------------------------------------------------
# Test: gate 非 active → 丢弃，staging 中转删除
# ---------------------------------------------------------------------------


def test_gate_rejected_discard(
    temp_config, sample_video_meta, sample_research_output,
    sample_extracted_fields, sample_reasoned_links,
):
    """gate=reject → raise GateRejected，staging 中转删除，不写 AI TRENDS/Video。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    with mock.patch.object(assembler, "_gate_decision", return_value="reject"):
        with pytest.raises(GateRejected):
            assembler.commit(candidate_md, slug="test-rejected", date_str="2026-05-28")

    # staging 中转已删除
    staging = temp_config.staging_dir
    candidates = list(staging.glob("video-test-rejected-*.md"))
    assert len(candidates) == 0, f"staging 中转未删除: {candidates}"

    # promotion_target_path 无文件
    if temp_config.promotion_target_path.exists():
        promo_files = list(temp_config.promotion_target_path.glob("*.md"))
        assert len(promo_files) == 0, f"非 active 不应写 AI TRENDS/Video: {promo_files}"


# ---------------------------------------------------------------------------
# Test: candidate target exists fail-closed
# ---------------------------------------------------------------------------


def test_candidate_target_exists_fail_closed(
    temp_config, sample_video_meta, sample_research_output,
    sample_extracted_fields, sample_reasoned_links,
):
    """验证预置同名 candidate → raise CandidateTargetExists，旧内容不变。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    staging = temp_config.staging_dir
    staging.mkdir(parents=True, exist_ok=True)

    # 预置同名 candidate
    existing_path = staging / "video-test-exists-2026-05-28.md"
    existing_content = "pre-existing content"
    existing_path.write_text(existing_content)

    with pytest.raises(CandidateTargetExists):
        assembler.commit(candidate_md, slug="test-exists", date_str="2026-05-28")

    # 验证旧内容不变
    assert existing_path.read_text() == existing_content

    # Cleanup
    existing_path.unlink()


# ---------------------------------------------------------------------------
# Test: promotion target not found
# ---------------------------------------------------------------------------


def test_promotion_target_not_found(
    temp_config, sample_video_meta, sample_research_output,
    sample_extracted_fields, sample_reasoned_links,
):
    """gate=active 但 promotion_target_path 不存在 → raise PromotionTargetNotFound，staging 中转删除。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    # gate=active，但 promotion_target_path 未创建（temp_config 不自动创建）
    with mock.patch.object(assembler, "_gate_decision", return_value="active"):
        with pytest.raises(PromotionTargetNotFound):
            assembler.commit(candidate_md, slug="test-promo", date_str="2026-05-28")

    # staging 中转已删除（不残留）
    staging_copy = temp_config.staging_dir / "video-test-promo-2026-05-28.md"
    assert not staging_copy.exists(), f"staging 中转应已删除: {staging_copy}"


# ---------------------------------------------------------------------------
# Test: promotion 目标同名 fail-closed
# ---------------------------------------------------------------------------


def test_promotion_file_exists_fail_closed(
    temp_config, sample_video_meta, sample_research_output,
    sample_extracted_fields, sample_reasoned_links,
):
    """gate=active 但正式路径同名已存在 → raise CandidateTargetExists，staging 中转删除（fail-closed）。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    # 创建 promotion 目录 + 预置同名文件
    temp_config.promotion_target_path.mkdir(parents=True, exist_ok=True)
    existing_promo = temp_config.promotion_target_path / "video-test-promo2-2026-05-28.md"
    existing_promo.write_text("pre-existing promotion content")

    with mock.patch.object(assembler, "_gate_decision", return_value="active"):
        with pytest.raises(CandidateTargetExists):
            assembler.commit(candidate_md, slug="test-promo2", date_str="2026-05-28")

    # 旧文件内容不变
    assert existing_promo.read_text() == "pre-existing promotion content"

    # staging 中转已删除
    staging_copy = temp_config.staging_dir / "video-test-promo2-2026-05-28.md"
    assert not staging_copy.exists(), f"staging 中转应已删除: {staging_copy}"


# ---------------------------------------------------------------------------
# Test: CLI entry point
# ---------------------------------------------------------------------------


def test_cli_entry_point():
    """验证 CLI 入口 --help 可用。"""
    import os
    import subprocess
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        ["python3", "-m", "akb2.video", "--help"],
        capture_output=True, text=True, timeout=10,
        env=env,
    )
    assert result.returncode == 0
    assert "dry-run" in result.stdout


# ---------------------------------------------------------------------------
# Test: CLI dry-run fixture
# ---------------------------------------------------------------------------


def test_cli_dry_run_fixture():
    """验证 python3 -m akb2.video --dry-run --fixture exit 0。"""
    import os
    import subprocess
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        ["python3", "-m", "akb2.video", "--dry-run", "--fixture"],
        capture_output=True, text=True, timeout=10,
        env=env,
    )
    assert result.returncode == 0
    output = result.stdout + result.stderr
    assert "gate_dry_run" in output or "PASS" in output


# ---------------------------------------------------------------------------
# Test: CLI dry-run no durable side effects
# ---------------------------------------------------------------------------


def test_cli_dry_run_no_durable_side_effects(temp_config):
    """验证 --dry-run --fixture 不写 staging/candidate/ 文件。"""
    staging = temp_config.staging_dir
    staging.mkdir(parents=True, exist_ok=True)

    # 快照 dry-run 前的文件列表
    before = set(staging.glob("*")) if staging.exists() else set()

    import os
    import subprocess
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        ["python3", "-m", "akb2.video", "--dry-run", "--fixture"],
        capture_output=True, text=True, timeout=10,
        env=env,
    )

    after = set(staging.glob("*")) if staging.exists() else set()
    new_files = after - before
    assert len(new_files) == 0, f"dry-run 不应创建文件: {new_files}"


# ---------------------------------------------------------------------------
# Test: import
# ---------------------------------------------------------------------------


def test_assembler_import():
    """验证 Assembler 可 import。"""
    from src.akb2.video.assembler import Assembler, CandidateTargetExists, GateRejected
    assert Assembler is not None
    assert CandidateTargetExists is not None
    assert GateRejected is not None


# ---------------------------------------------------------------------------
# Test: _gate_decision 容错分支
# ---------------------------------------------------------------------------


def test_gate_decision_cli_missing_returns_parse_error(temp_config):
    """gate CLI 不存在 → _gate_decision 返回 'parse_error'，不抛异常。"""
    assembler = Assembler(temp_config)
    candidate = temp_config.staging_dir / "test.md"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("# test")

    with mock.patch("pathlib.Path.exists", return_value=False):
        result = assembler._gate_decision(candidate, temp_config.staging_dir)
    assert result == "parse_error"


def test_gate_decision_timeout_returns_parse_error(temp_config):
    """gate CLI subprocess timeout → _gate_decision 返回 'parse_error'。"""
    import subprocess as sp

    assembler = Assembler(temp_config)
    candidate = temp_config.staging_dir / "test.md"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("# test")

    with mock.patch.object(sp, "run", side_effect=sp.TimeoutExpired(cmd=["python3"], timeout=60)):
        result = assembler._gate_decision(candidate, temp_config.staging_dir)
    assert result == "parse_error"


def test_gate_decision_no_proposed_decision_returns_parse_error(temp_config):
    """gate CLI stdout 无 proposed_gate_decision → _gate_decision 返回 'parse_error'。"""
    import subprocess as sp

    assembler = Assembler(temp_config)
    candidate = temp_config.staging_dir / "test.md"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("# test")

    completed = sp.CompletedProcess(args=["python3"], returncode=0, stdout="no decision here\n", stderr="")
    with mock.patch.object(sp, "run", return_value=completed):
        result = assembler._gate_decision(candidate, temp_config.staging_dir)
    assert result == "parse_error"


# ---------------------------------------------------------------------------
# Test: run_pipeline 全链路编排（采集/研究/逐视频容错）
# ---------------------------------------------------------------------------


def _pipeline_stages(sample_research_output, *, extract_fields=None):
    """构造 run_pipeline 所需的 collector/researcher/extractor/reasoner mock。"""
    collector = mock.Mock()
    collector.collect.return_value = [{"id": "v1"}]
    researcher = mock.Mock()
    researcher.run.return_value = [sample_research_output]
    extractor = mock.Mock()
    extractor.extract.return_value = extract_fields or ExtractedFields(
        tldr="t", key_findings=[], future_questions=[], concepts_mentioned=["DeepSeek"]
    )
    reasoner = mock.Mock()
    reasoner.reason.return_value = ReasonedLinks()
    return collector, researcher, extractor, reasoner


def test_run_pipeline_dry_run_no_commit(temp_config, sample_research_output):
    """dry_run=True → 组装但不写文件，status=ok 且 candidate_path=None。"""
    assembler = Assembler(temp_config)
    collector, researcher, extractor, reasoner = _pipeline_stages(sample_research_output)

    results = assembler.run_pipeline(
        collector, researcher, extractor, reasoner, dry_run=True
    )

    assert len(results) == 1
    assert results[0]["status"] == "ok"
    assert results[0]["dry_run"] is True
    assert results[0]["candidate_path"] is None


def test_run_pipeline_commit_publishes(temp_config, sample_research_output):
    """dry_run=False + gate=active → 实际发布，candidate_path 非空。"""
    temp_config.promotion_target_path.mkdir(parents=True, exist_ok=True)
    assembler = Assembler(temp_config)
    collector, researcher, extractor, reasoner = _pipeline_stages(sample_research_output)

    with mock.patch.object(assembler, "_gate_decision", return_value="active"):
        results = assembler.run_pipeline(
            collector, researcher, extractor, reasoner, dry_run=False
        )

    assert results[0]["status"] == "ok"
    assert results[0]["candidate_path"] is not None
    published = Path(results[0]["candidate_path"])
    assert published.exists()
    # 绑定真实产出：发布文件落在 promotion_target_path，且已标记 gate_decision: active
    assert published.parent == temp_config.promotion_target_path
    content = published.read_text(encoding="utf-8")
    assert "gate_decision: active" in content
    assert "page_status: candidate" in content


def test_run_pipeline_collect_failure_short_circuits(temp_config):
    """collector.collect 抛异常 → 整链路返回 collect 阶段失败。"""
    assembler = Assembler(temp_config)
    collector = mock.Mock()
    collector.collect.side_effect = RuntimeError("collect boom")

    results = assembler.run_pipeline(
        collector, mock.Mock(), mock.Mock(), mock.Mock()
    )

    assert results == [{"status": "failed", "stage": "collect", "error": "collect boom"}]


def test_run_pipeline_no_videos_returns_empty(temp_config):
    """collect 返回空 → 直接返回 []，不进研究阶段。"""
    assembler = Assembler(temp_config)
    collector = mock.Mock()
    collector.collect.return_value = []
    researcher = mock.Mock()

    results = assembler.run_pipeline(collector, researcher, mock.Mock(), mock.Mock())

    assert results == []
    researcher.run.assert_not_called()


def test_run_pipeline_research_failure_short_circuits(temp_config):
    """researcher.run 抛异常 → 返回 research 阶段失败。"""
    assembler = Assembler(temp_config)
    collector = mock.Mock()
    collector.collect.return_value = [{"id": "v1"}]
    researcher = mock.Mock()
    researcher.run.side_effect = RuntimeError("research boom")

    results = assembler.run_pipeline(collector, researcher, mock.Mock(), mock.Mock())

    assert results[0]["status"] == "failed"
    assert results[0]["stage"] == "research"


def test_run_pipeline_per_video_gate_rejected_isolated(temp_config, sample_research_output):
    """单视频 gate 拒绝 → 该视频 failed，不抛出整链路。"""
    assembler = Assembler(temp_config)
    collector, researcher, extractor, reasoner = _pipeline_stages(sample_research_output)

    with mock.patch.object(assembler, "_gate_decision", return_value="reject"):
        results = assembler.run_pipeline(
            collector, researcher, extractor, reasoner, dry_run=False
        )

    assert results[0]["status"] == "failed"
    assert "reject" in results[0]["error"]


def test_run_pipeline_per_video_generic_error_isolated(temp_config, sample_research_output):
    """单视频 extract 抛非预期异常 → 捕获为 failed，链路继续。"""
    assembler = Assembler(temp_config)
    collector, researcher, _extractor, reasoner = _pipeline_stages(sample_research_output)
    extractor = mock.Mock()
    extractor.extract.side_effect = ValueError("extract boom")

    results = assembler.run_pipeline(
        collector, researcher, extractor, reasoner, dry_run=False
    )

    assert results[0]["status"] == "failed"
    assert "extract boom" in results[0]["error"]


# ---------------------------------------------------------------------------
# Test: 工具方法 _extract_h1 / _make_slug / _compute_hash
# ---------------------------------------------------------------------------


def test_extract_h1_variants(temp_config):
    """_extract_h1：空内容→None、无 H1→None、跳过 frontmatter 取首个 H1。"""
    assembler = Assembler(temp_config)
    assert assembler._extract_h1("") is None
    assert assembler._extract_h1("no heading here\njust text") is None
    md = "---\ntitle: x\n---\n# Real Title\n\nbody"
    assert assembler._extract_h1(md) == "Real Title"


def test_make_slug(temp_config):
    """_make_slug：去特殊字符；无 H1 → untitled。"""
    assembler = Assembler(temp_config)
    slug = assembler._make_slug("# Hello World Special@#$")
    assert slug
    assert " " not in slug
    assert assembler._make_slug("plain text no h1") == "untitled"


def test_compute_hash(temp_config):
    """_compute_hash 返回 64 位 sha256 hex，且对内容敏感。"""
    assembler = Assembler(temp_config)
    h1 = assembler._compute_hash("content-a")
    h2 = assembler._compute_hash("content-b")
    assert len(h1) == 64
    assert h1 != h2


# ---------------------------------------------------------------------------
# Test: frontmatter future_questions + 空 TLDR/关键发现 warning 分支
# ---------------------------------------------------------------------------


def test_assemble_future_questions_and_empty_sections(
    temp_config, sample_video_meta, sample_research_output, sample_reasoned_links
):
    """future_questions 注入 frontmatter；tldr/key_findings 为空时正文章节为空壳。"""
    extracted = ExtractedFields(
        tldr="",  # 触发 ## TLDR 空分支
        key_findings=[],  # 触发 ## 关键发现 空分支
        future_questions=[
            FutureQuestion(
                question="模型在长上下文下的稳定性如何？",
                gap_anchor_span=SourceSpan(line_start=1, line_end=1),
                gap_type="unexplored_concept",
            ),
        ],
        concepts_mentioned=[],
    )
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output, extracted, sample_reasoned_links
    )

    # future_questions 写入 frontmatter
    assert "future_questions:" in candidate_md
    assert "模型在长上下文下的稳定性如何？" in candidate_md
    # 空 tldr/key_findings 仍渲染 H2 标题（gate 结构要求）
    assert "## TLDR" in candidate_md
    assert "## 关键发现" in candidate_md


# ---------------------------------------------------------------------------
# SC-4: needs_repair 保留 staging + 写入 pending；reject 丢弃
# ---------------------------------------------------------------------------


def test_needs_repair_preserves_staging_and_writes_pending(
    temp_config, sample_video_meta, sample_research_output,
    sample_extracted_fields, sample_reasoned_links,
):
    """gate=needs_repair → 保留 staging 文件 + 写入 pending，不抛 GateRejected。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    slug = "test-needs-repair"
    date = "2026-06-05"

    with mock.patch.object(assembler, "_gate_decision", return_value="needs_repair"):
        # 不应抛 GateRejected
        result_path = assembler.commit(candidate_md, slug=slug, date_str=date)

    # staging 文件保留
    assert result_path.exists(), f"needs_repair 应保留 staging 文件: {result_path}"

    # pending 文件已写入
    pending_path = temp_config.staging_dir.parent / "assembler_repair_pending.jsonl"
    assert pending_path.exists(), "needs_repair 应写入 pending 文件"
    pending_lines = pending_path.read_text().strip().split("\n")
    assert len(pending_lines) >= 1
    import json
    record = json.loads(pending_lines[-1])
    assert record["gate_decision"] == "needs_repair"


def test_reject_deletes_staging_and_raises(
    temp_config, sample_video_meta, sample_research_output,
    sample_extracted_fields, sample_reasoned_links,
):
    """gate=reject → 删除 staging + raise GateRejected（永久丢弃，行为不变）。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    with mock.patch.object(assembler, "_gate_decision", return_value="reject"):
        with pytest.raises(GateRejected):
            assembler.commit(candidate_md, slug="test-reject", date_str="2026-06-05")

    # staging 文件已删除
    staging = temp_config.staging_dir
    remaining = list(staging.glob("video-test-reject-*.md"))
    assert len(remaining) == 0, f"reject 应删除 staging: {remaining}"