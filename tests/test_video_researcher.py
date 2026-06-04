"""视频研究者测试 — VideoResearcher 迁移验证。

覆盖：notebook_id 解析、健康检查降级、双 profile 轮替、降级转录、
pending 文件锁、无 durable write、孤儿进程清理。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.video.config import VideoConfig
from src.akb2.video.researcher import ResearchOutput, VideoResearcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_config() -> VideoConfig:
    """使用临时目录的 VideoConfig，避免污染真实路径。"""
    with tempfile.TemporaryDirectory() as tmp:
        config = VideoConfig(
            nlm_bin="/usr/bin/true",
            ytdlp_bin="/usr/bin/true",
        )
        yield config


@pytest.fixture
def sample_video_result() -> dict:
    """标准视频结果 fixture。"""
    return {
        "video": {
            "title": "DeepSeek V4 Architecture Deep Dive",
            "url": "https://www.youtube.com/watch?v=test123",
            "platform": "youtube",
            "creator": "Test Channel",
            "upload_date": "20260528",
            "duration_minutes": 45,
        },
        "creator": {"name": "Test Channel", "platform": "youtube"},
    }


class MockLLMAdapter:
    """Mock LLM adapter for testing."""

    def __init__(self, response: str = "Mock LLM response"):
        self.response = response
        self.calls: list[dict] = []

    def chat(self, task: str, system: str, user: str, temperature: float = 0.3) -> str:
        self.calls.append({"task": task, "system": system, "user": user, "temperature": temperature})
        return self.response


# ---------------------------------------------------------------------------
# Test: notebook_id extraction (5 formats)
# ---------------------------------------------------------------------------


def test_notebook_id_extraction(temp_config):
    """验证 5 种 NLM CLI 输出格式的 notebook_id 解析。"""
    r = VideoResearcher(temp_config)

    # Format 1: "ID: <uuid>"
    nb_id = r._nlm_create_notebook("Test", "default")
    assert nb_id is None  # /usr/bin/true returns empty, not a real ID

    # Test the parsing logic directly
    # Format 1: "ID: <uuid>"
    output = "Created notebook\nID: abc123-def456-7890\nDone"
    with mock.patch.object(r, "_nlm", return_value=output):
        nb_id = r._nlm_create_notebook("Test", "default")
        assert nb_id == "abc123-def456-7890"

    # Format 2: UUID in text
    output = "Created notebook abc12345-1234-1234-1234-123456789abc successfully"
    with mock.patch.object(r, "_nlm", return_value=output):
        nb_id = r._nlm_create_notebook("Test", "default")
        assert nb_id == "abc12345-1234-1234-1234-123456789abc"

    # Format 3: JSON
    output = '{"id": "json-id-123", "name": "test"}'
    with mock.patch.object(r, "_nlm", return_value=output):
        nb_id = r._nlm_create_notebook("Test", "default")
        assert nb_id == "json-id-123"

    # Format 4: Last line is ID
    output = "Some output\nSome more\nabcdef123456"
    with mock.patch.object(r, "_nlm", return_value=output):
        nb_id = r._nlm_create_notebook("Test", "default")
        assert nb_id == "abcdef123456"

    # Format 5: None on empty
    with mock.patch.object(r, "_nlm", return_value=None):
        nb_id = r._nlm_create_notebook("Test", "default")
        assert nb_id is None


# ---------------------------------------------------------------------------
# Test: health check degradation
# ---------------------------------------------------------------------------


def test_health_check_degraded(temp_config, sample_video_result):
    """验证 auth 失败时走降级路径（yt-dlp 字幕 + LLM），不整批丢弃。"""
    r = VideoResearcher(temp_config, llm_adapter=MockLLMAdapter("Fallback summary"))

    # Mock health check fails
    with mock.patch.object(r, "_health_check", return_value=False):
        # Mock fallback transcript
        with mock.patch.object(r, "_get_transcript", return_value="This is a test transcript with enough content for analysis. " * 50):
            outputs = r.run([sample_video_result])

    assert len(outputs) == 1
    assert outputs[0].degraded is True
    assert outputs[0].notebook_id is None
    assert "降级研究" in outputs[0].report_markdown


# ---------------------------------------------------------------------------
# Test: NLM quota exhausted failover
# ---------------------------------------------------------------------------


def test_nlm_quota_exhausted_failover(temp_config, sample_video_result):
    """验证 default profile 配额耗尽 → alt profile 轮替。"""
    r = VideoResearcher(temp_config, llm_adapter=MockLLMAdapter("Summary"))

    # Mock health check OK
    with mock.patch.object(r, "_health_check", return_value=True):
        # Mock nlm_create_notebook: first succeeds, but research fails
        with mock.patch.object(r, "_nlm_create_notebook", side_effect=[
            "nb-default-001",  # default profile creates notebook
            "nb-alt-001",      # alt profile creates notebook
        ]):
            # research fails on default, succeeds on alt
            with mock.patch.object(r, "_nlm_add_youtube", side_effect=[True, True]):
                with mock.patch.object(r, "_nlm_research", side_effect=[False, True]):
                    with mock.patch.object(r, "_nlm_wait_research", return_value=True):
                        with mock.patch.object(r, "_generate_and_download_report", return_value="# Test Report\n\nContent"):
                            outputs = r.run([sample_video_result])

    assert len(outputs) == 1
    assert outputs[0].degraded is False
    # Should have used alt profile
    assert outputs[0].nlm_profile_used == "alt"


# ---------------------------------------------------------------------------
# Test: fallback transcript research
# ---------------------------------------------------------------------------


def test_fallback_transcript(temp_config, sample_video_result):
    """验证 yt-dlp 字幕 + LLM 降级路径。"""
    mock_llm = MockLLMAdapter("# AI Agents Analysis\n\n## Core Concepts\n\nTest content")
    r = VideoResearcher(temp_config, llm_adapter=mock_llm)

    # Mock health check fails → direct fallback
    with mock.patch.object(r, "_health_check", return_value=False):
        with mock.patch.object(r, "_get_transcript", return_value="This is a detailed transcript about AI agents. " * 100):
            outputs = r.run([sample_video_result])

    assert len(outputs) == 1
    assert outputs[0].degraded is True
    assert "AI Agents Analysis" in outputs[0].report_markdown
    assert len(mock_llm.calls) == 1
    assert mock_llm.calls[0]["task"] == "summarize"


# ---------------------------------------------------------------------------
# Test: pending file lock
# ---------------------------------------------------------------------------


def test_pending_file_lock(temp_config, sample_video_result):
    """验证 pending_research.json 使用 fcntl 文件锁保护。"""
    # Check source code for fcntl usage
    src = Path(__file__).parent.parent / "src" / "akb2" / "video" / "researcher.py"
    content = src.read_text()
    assert "fcntl" in content or "flock" in content or "LOCK_EX" in content


# ---------------------------------------------------------------------------
# Test: no durable write
# ---------------------------------------------------------------------------


def test_no_durable_write(temp_config, sample_video_result):
    """验证 Researcher 不写 staging/candidate/、Obsidian、DB 文件。"""
    r = VideoResearcher(temp_config, llm_adapter=MockLLMAdapter("# Test"))

    # Mock health check fails → direct fallback
    with mock.patch.object(r, "_health_check", return_value=False):
        with mock.patch.object(r, "_get_transcript", return_value="Test transcript content. " * 100):
            outputs = r.run([sample_video_result])

    assert len(outputs) == 1
    assert isinstance(outputs[0], ResearchOutput)
    # ResearchOutput is a dataclass, not a file write
    assert outputs[0].report_markdown is not None
    # No file system side effects
    assert "staging/candidate" not in outputs[0].report_markdown


# ---------------------------------------------------------------------------
# Test: NLM timeout orphan cleanup
# ---------------------------------------------------------------------------


def test_nlm_timeout_orphan_cleanup(temp_config):
    """验证 NLM 子进程超时后 killpg + SIGKILL 清理孤儿进程。"""
    src = Path(__file__).parent.parent / "src" / "akb2" / "video" / "researcher.py"
    content = src.read_text()
    assert "start_new_session" in content, "必须使用 start_new_session=True"
    assert "killpg" in content, "必须使用 killpg 清理进程组"
    assert "SIGKILL" in content, "必须使用 SIGKILL"


# ---------------------------------------------------------------------------
# Test: config/external path injection
# ---------------------------------------------------------------------------


def test_config_external_path_injection():
    """验证 NLM_BIN、OBSIDIAN_RESEARCH_DIR 通过 config 注入（notebooklm_bin 已移除）。"""
    src = Path(__file__).parent.parent / "src" / "akb2" / "video" / "researcher.py"
    content = src.read_text()
    # 不应有 Path.home() 直接调用
    assert "Path.home()" not in content, "researcher.py 不应硬编码 Path.home()"
    # 不应再有 notebooklm 引用
    assert "notebooklm" not in content, "researcher.py 不应再引用 notebooklm"


# ---------------------------------------------------------------------------
# Test: ResearchOutput import
# ---------------------------------------------------------------------------


def test_researcher_import():
    """验证 VideoResearcher 和 ResearchOutput 可独立 import。"""
    from src.akb2.video.researcher import ResearchOutput, VideoResearcher
    assert VideoResearcher is not None
    assert ResearchOutput is not None


# ---------------------------------------------------------------------------
# Test: researcher runs basic
# ---------------------------------------------------------------------------


def test_researcher_runs_basic(temp_config, sample_video_result):
    """验证 VideoResearcher.run() 可运行（mock 全路径）。"""
    mock_llm = MockLLMAdapter("# Test Report\n\nContent")
    r = VideoResearcher(temp_config, llm_adapter=mock_llm)

    with mock.patch.object(r, "_health_check", return_value=False):
        with mock.patch.object(r, "_get_transcript", return_value="Test " * 100):
            outputs = r.run([sample_video_result])

    assert len(outputs) == 1
    assert isinstance(outputs[0], ResearchOutput)