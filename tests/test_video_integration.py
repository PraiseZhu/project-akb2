"""集成测试 — 全链路验证。

覆盖：全链路 mock NLM、降级路径、gate rejection 回滚、fixture 真实性、
CLI 入口 smoke、dry-run 无副作用。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.video.assembler import Assembler, GateRejected
from src.akb2.video.collector import VideoCollector
from src.akb2.video.config import VideoConfig
from src.akb2.video.extractor import ExtractedFields, Extractor
from src.akb2.video.reasoner import ConceptStore, ReasonedLinks, Reasoner
from src.akb2.video.researcher import VideoResearcher

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class MockLLMAdapter:
    """Mock LLM for integration tests."""

    def chat(self, task: str, system: str, user: str, temperature: float = 0.3) -> str:
        if task == "extract":
            return json.dumps({
                "findings": [
                    {
                        "assertion": "模型在基准测试中表现优异",
                        "source_span": {"line_start": 5, "line_end": 7},
                        "confidence": 0.9,
                        "category": "benchmark",
                    },
                ],
            })
        if task == "summarize":
            return "# 集成测试报告\n\n## 执行摘要\n\n测试摘要内容。\n\n## 核心观点\n\n- 观点 1\n- 观点 2"
        return json.dumps({"concepts": ["TestConcept"]})


@pytest.fixture
def integration_config() -> VideoConfig:
    """集成测试配置。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = VideoConfig(
            staging_dir=tmp_path / "staging" / "candidate",
            akb2_root=tmp_path,
            nlm_bin="/usr/bin/true",
            ytdlp_bin="/usr/bin/true",
            video_sources_path=tmp_path / "config" / "video_sources.json",
            keywords_path=tmp_path / "config" / "keywords.json",
        )
        yield config


# ---------------------------------------------------------------------------
# Test: full pipeline mock NLM
# ---------------------------------------------------------------------------


def test_full_pipeline_mock_nlm(integration_config):
    """验证全链路：collect → research → extract → reason → assemble → gate dry-run。"""
    mock_llm = MockLLMAdapter()
    config = integration_config

    # 创建最小 video_sources.json
    sources_path = config.video_sources_path
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources_path.write_text(json.dumps({
        "creators": [{
            "id": "yt-test", "platform": "youtube",
            "channel_url": "https://www.youtube.com/@TestChannel",
            "name": "Test Channel", "enabled": True,
            "max_per_day": 1, "keywords": [], "tier": "quality",
        }],
        "settings": {
            "min_video_duration_minutes": 3, "max_video_duration_minutes": 0,
            "days_lookback": 14, "coldstart": True, "coldstart_max_per_day": 3,
            "skip_relevance_filter": True, "skip_keyword_filter": True,
            "dedup_strategy": "none",
        },
    }))
    # 创建最小 keywords.json
    config.keywords_path.parent.mkdir(parents=True, exist_ok=True)
    config.keywords_path.write_text(json.dumps({
        "include": {"core": ["AI", "LLM"]},
        "exclude": {"non_ai": ["cooking"]},
    }))

    # 构造 mock 数据
    collector = VideoCollector(config)
    researcher = VideoResearcher(config, llm_adapter=mock_llm)
    extractor = Extractor(llm_adapter=mock_llm)
    reasoner = Reasoner(ConceptStore(), llm_adapter=mock_llm)
    assembler = Assembler(config)

    # Mock collector: 返回 1 个视频
    with mock.patch.object(collector, "_detect_yt_videos", return_value=[
        {
            "platform": "youtube", "id": "int-test-1",
            "title": "Integration Test Video",
            "url": "https://youtube.com/watch?v=int-test-1",
            "duration_minutes": 15, "upload_date": "20260601",
            "creator": "Test Channel",
        },
    ]):
        # Mock researcher: health check fail → fallback
        with mock.patch.object(researcher, "_health_check", return_value=False):
            with mock.patch.object(researcher, "_get_transcript", return_value="Test transcript " * 100):
                # 创建 promotion_target_path 目录
                config.promotion_target_path.mkdir(parents=True, exist_ok=True)
                # Mock gate decision → active
                with mock.patch.object(assembler, "_gate_decision", return_value="active"):
                    video_results = collector.collect()
                    assert len(video_results) > 0, "Collector should return videos"

                    outputs = researcher.run(video_results)
                    assert len(outputs) > 0, "Researcher should return outputs"

                    for output in outputs:
                        extracted = extractor.extract(output.report_markdown)
                        assert isinstance(extracted, ExtractedFields)

                        reasoned = reasoner.reason(extracted.concepts_mentioned)
                        assert isinstance(reasoned, ReasonedLinks)

                        candidate_md = assembler.assemble(
                            output.video_meta, output, extracted, reasoned
                        )
                        assert "page_status: candidate" in candidate_md

                        formal_path = assembler.commit(candidate_md, slug="integration-test", date_str="2026-06-01")
                        assert formal_path.exists(), f"正式路径不存在: {formal_path}"
                        assert formal_path.parent == config.promotion_target_path
                        # staging 中转已删除
                        staging_copy = config.staging_dir / "video-integration-test-2026-06-01.md"
                        assert not staging_copy.exists()
                        # Cleanup
                        formal_path.unlink()


# ---------------------------------------------------------------------------
# Test: degraded path integration
# ---------------------------------------------------------------------------


def test_degraded_path_integration(integration_config):
    """验证 mock NLM auth 失败 → 降级到 yt-dlp 字幕路径。"""
    mock_llm = MockLLMAdapter()
    config = integration_config

    researcher = VideoResearcher(config, llm_adapter=mock_llm)

    video_result = {
        "video": {
            "title": "Degraded Test",
            "url": "https://youtube.com/watch?v=degraded",
            "platform": "youtube",
            "creator": "Test",
        },
        "creator": {"name": "Test"},
    }

    with mock.patch.object(researcher, "_health_check", return_value=False):
        with mock.patch.object(researcher, "_get_transcript", return_value="Degraded transcript " * 100):
            outputs = researcher.run([video_result])

    assert len(outputs) == 1
    assert outputs[0].degraded is True
    assert "降级研究" in outputs[0].report_markdown


# ---------------------------------------------------------------------------
# Test: gate rejection rollback
# ---------------------------------------------------------------------------


def test_gate_rejection_rollback(integration_config):
    """验证 mock gate 拒绝(reject) → candidate 被回滚删除。"""
    config = integration_config
    assembler = Assembler(config)

    # 构造最简 candidate
    candidate_md = "---\npage_status: candidate\nsource_type: video\ntemplate_type: source\ntitle: Test\ngate_decision: null\n---\n\n# Test"

    with mock.patch.object(assembler, "_gate_decision", return_value="reject"):
        with pytest.raises(GateRejected):
            assembler.commit(candidate_md, slug="gate-reject-test", date_str="2026-06-01")

    # 验证 candidate 被删除
    staging = config.staging_dir
    candidates = list(staging.glob("video-gate-reject-test-*.md"))
    assert len(candidates) == 0


def test_gate_needs_repair_preserves_staging(integration_config):
    """验证 mock gate needs_repair → staging 保留，pending 写入。"""
    config = integration_config
    assembler = Assembler(config)

    candidate_md = "---\npage_status: candidate\nsource_type: video\ntemplate_type: source\ntitle: Test Repairable\ngate_decision: null\n---\n\n# Test Repairable"

    with mock.patch.object(assembler, "_gate_decision", return_value="needs_repair"):
        # needs_repair 不应抛异常
        result_path = assembler.commit(candidate_md, slug="gate-repair-test", date_str="2026-06-01")

    # staging 文件保留
    assert result_path.exists(), "needs_repair 应保留 staging"

    # pending 已写入
    pending_path = config.staging_dir.parent / "assembler_repair_pending.jsonl"
    assert pending_path.exists(), "needs_repair 应写入 pending"


# ---------------------------------------------------------------------------
# Test: fixture authenticity
# ---------------------------------------------------------------------------


def test_fixture_authenticity():
    """验证 integration fixture 含真实 NLM 报告结构标记。"""
    fixture_path = Path(__file__).parent / "fixtures" / "video" / "integration" / "nlm_report_1.md"
    assert fixture_path.exists(), f"fixture 不存在: {fixture_path}"

    content = fixture_path.read_text(encoding="utf-8")
    # NLM 报告应包含典型结构标记
    assert len(content) > 1000, "fixture 内容过短，可能不是真实 NLM 报告"
    # 检查是否有 markdown 结构
    assert "#" in content, "fixture 应包含 markdown 标题"


# ---------------------------------------------------------------------------
# Test: CLI entry smoke
# ---------------------------------------------------------------------------


def test_cli_entry_smoke():
    """验证 python3 -m akb2.video --dry-run --fixture exit 0。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        ["python3", "-m", "akb2.video", "--dry-run", "--fixture"],
        capture_output=True, text=True, timeout=10,
        env=env,
    )
    assert result.returncode == 0, f"CLI exit {result.returncode}: {result.stderr}"
    output = result.stdout + result.stderr
    assert "gate_dry_run" in output or "PASS" in output


# ---------------------------------------------------------------------------
# Test: CLI dry-run no durable side effects
# ---------------------------------------------------------------------------


def test_cli_dry_run_no_durable_side_effects(integration_config):
    """验证 --dry-run --fixture 不写 staging/candidate/ 文件。"""
    staging = integration_config.staging_dir
    staging.mkdir(parents=True, exist_ok=True)

    before = set(staging.glob("*")) if staging.exists() else set()

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    subprocess.run(
        ["python3", "-m", "akb2.video", "--dry-run", "--fixture"],
        capture_output=True, text=True, timeout=10,
        env=env,
    )

    after = set(staging.glob("*")) if staging.exists() else set()
    new_files = after - before
    assert len(new_files) == 0, f"dry-run 不应创建文件: {new_files}"


# ---------------------------------------------------------------------------
# Test: _report_results exit codes（SC-7 fail-visibly）
# ---------------------------------------------------------------------------


def test_report_results_partial_failure_returns_1():
    """验证 部分失败 + 部分成功 → exit 1（非 exit 0）。"""
    from src.akb2.video.__main__ import _report_results

    results = [
        {"status": "ok", "video": {"title": "Good"}},
        {"status": "failed", "video": {"title": "Bad"}, "error": "boom"},
    ]
    exit_code = _report_results(results)
    assert exit_code == 1, f"部分失败应返回 1，实际返回 {exit_code}"


def test_report_results_all_ok_returns_0():
    """验证 全成功 → exit 0。"""
    from src.akb2.video.__main__ import _report_results

    results = [
        {"status": "ok", "video": {"title": "A"}},
        {"status": "ok", "video": {"title": "B"}},
    ]
    exit_code = _report_results(results)
    assert exit_code == 0


def test_report_results_all_failed_returns_1():
    """验证 全失败 → exit 1。"""
    from src.akb2.video.__main__ import _report_results

    results = [
        {"status": "failed", "video": {"title": "X"}, "error": "e1"},
        {"status": "failed", "video": {"title": "Y"}, "error": "e2"},
    ]
    exit_code = _report_results(results)
    assert exit_code == 1


def test_report_results_needs_repair_returns_1():
    """needs_repair 表示未发布，launchd 必须看到非 0。"""
    from src.akb2.video.__main__ import _report_results

    results = [
        {"status": "needs_repair", "video": {"title": "Repair"}, "candidate_path": "staging/x.md"},
    ]
    exit_code = _report_results(results)
    assert exit_code == 1
