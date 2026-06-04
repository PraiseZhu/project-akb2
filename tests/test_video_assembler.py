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
from src.akb2.video.extractor import ExtractedFields, KeyFinding, SourceSpan
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
    """gate=needs_repair → raise GateRejected，staging 中转删除，不写 AI TRENDS/Video（SC-2/SC-3）。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    with mock.patch.object(assembler, "_gate_decision", return_value="needs_repair"):
        with pytest.raises(GateRejected):
            assembler.commit(candidate_md, slug="test-rejected", date_str="2026-05-28")

    # staging 中转已删除（SC-2）
    staging = temp_config.staging_dir
    candidates = list(staging.glob("video-test-rejected-*.md"))
    assert len(candidates) == 0, f"staging 中转未删除: {candidates}"

    # promotion_target_path 无文件（SC-2）
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