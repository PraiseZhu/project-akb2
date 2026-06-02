"""Assembler 测试 — candidate 组装 + 写入 + gate dry-run + 回滚 + CLI 入口。

覆盖：frontmatter 完整性、gate dry-run pass/rejected、回滚、candidate 预存在 fail-closed、
promotion 目标路径预检、CLI 入口 smoke、dry-run 无副作用。
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


# ---------------------------------------------------------------------------
# Test: gate dry-run pass
# ---------------------------------------------------------------------------


def test_gate_dry_run_pass(
    temp_config, sample_video_meta, sample_research_output,
    sample_extracted_fields, sample_reasoned_links,
):
    """验证 gate dry-run 通过（mock gate check）。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    # Mock gate dry-run to pass
    with mock.patch.object(assembler, "_gate_dry_run", return_value=None):
        # Mock promotion target path exists
        with mock.patch.object(Path, "is_dir", return_value=True):
            candidate_path = assembler.commit(candidate_md, slug="test-video", date_str="2026-05-28")

    assert candidate_path.exists()
    # Cleanup
    candidate_path.unlink()


# ---------------------------------------------------------------------------
# Test: gate dry-run rejected
# ---------------------------------------------------------------------------


def test_gate_dry_run_rejected(
    temp_config, sample_video_meta, sample_research_output,
    sample_extracted_fields, sample_reasoned_links,
):
    """验证 gate 失败 → candidate 被回滚删除。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    # Mock gate dry-run to reject
    with mock.patch.object(assembler, "_gate_dry_run", side_effect=GateRejected("test rejection")):
        with pytest.raises(GateRejected):
            assembler.commit(candidate_md, slug="test-rejected", date_str="2026-05-28")

    # Verify candidate was deleted
    staging = temp_config.staging_dir
    candidates = list(staging.glob("video-test-rejected-*.md"))
    assert len(candidates) == 0, f"candidate should be rolled back: {candidates}"


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
    """验证 promotion_target_path 不存在 → raise PromotionTargetNotFound。"""
    assembler = Assembler(temp_config)
    candidate_md = assembler.assemble(
        sample_video_meta, sample_research_output,
        sample_extracted_fields, sample_reasoned_links,
    )

    # Mock gate pass, but promotion target doesn't exist
    with mock.patch.object(assembler, "_gate_dry_run", return_value=None):
        with pytest.raises(PromotionTargetNotFound):
            assembler.commit(candidate_md, slug="test-promo", date_str="2026-05-28")


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