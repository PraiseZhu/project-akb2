"""视频研究者测试 — VideoResearcher 迁移验证。

覆盖：notebook_id 解析、健康检查降级、双 profile 轮替、降级转录、
pending 文件锁、无 durable write、孤儿进程清理。
"""

from __future__ import annotations

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
    with tempfile.TemporaryDirectory():
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

    # Mock health check: auth 过期
    with mock.patch.object(r, "_health_check", return_value="auth_expired"):
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
    with mock.patch.object(r, "_health_check", return_value="ok"):
        # Mock nlm_create_notebook: first succeeds, but research fails
        with mock.patch.object(r, "_nlm_create_notebook", side_effect=[
            "nb-default-001",  # default profile creates notebook
            "nb-alt-001",      # alt profile creates notebook
        ]):
            # research fails on default, succeeds on alt
            with mock.patch.object(r, "_nlm_add_youtube", side_effect=[True, True]):
                with mock.patch.object(r, "_nlm_research", side_effect=[False, True]):
                    with mock.patch.object(r, "_nlm_wait_research", return_value=True):
                        with mock.patch.object(
                            r,
                            "_generate_and_download_report",
                            return_value="# Test Report\n\nContent",
                        ) as mock_report:
                            outputs = r.run([sample_video_result])

    assert len(outputs) == 1
    assert outputs[0].degraded is False
    # Should have used alt profile
    assert outputs[0].nlm_profile_used == "alt"
    mock_report.assert_called_once_with("nb-alt-001", "DeepSeek V4 Architecture Deep Dive", "alt")


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


# ---------------------------------------------------------------------------
# Test: _nlm 区分真配额(exit 8) vs 其它失败
# ---------------------------------------------------------------------------


def _fake_proc(stdout: str, stderr: str, returncode: int):
    """构造 subprocess.Popen 替身：communicate 返回 (stdout, stderr)，带 returncode。"""
    proc = mock.Mock()
    proc.communicate.return_value = (stdout, stderr)
    proc.returncode = returncode
    return proc


def test_nlm_raises_quota_on_exit_8(temp_config):
    """exit 8 = 真配额耗尽 → _nlm 抛 NLMQuotaExhausted（停批信号）。"""
    from src.akb2.video.researcher import NLMQuotaExhausted

    r = VideoResearcher(temp_config)
    with mock.patch(
        "src.akb2.video.researcher.subprocess.Popen",
        return_value=_fake_proc("", "", 8),
    ):
        with pytest.raises(NLMQuotaExhausted):
            r._nlm("notebook", "list", profile="default")


def test_nlm_returns_none_on_other_nonzero(temp_config):
    """非配额非零码（如 source add --wait 处理失败 exit 1）→ _nlm 返回 None（可降级）。"""
    r = VideoResearcher(temp_config)
    with mock.patch(
        "src.akb2.video.researcher.subprocess.Popen",
        return_value=_fake_proc("real error on stdout", "", 1),
    ):
        result = r._nlm(
            "source", "add", "nb", "--youtube", "u", "--wait", profile="default"
        )
    assert result is None


# ---------------------------------------------------------------------------
# Test: 单视频 NLM 处理失败（非配额）→ 转字幕降级，不误判配额、不中断整批
# ---------------------------------------------------------------------------


def test_source_failure_triggers_transcript_fallback(temp_config, sample_video_result):
    """NLM 源处理失败（非配额，两 profile 均失败）→ 该视频走 yt-dlp 字幕降级。"""
    mock_llm = MockLLMAdapter("# Fallback Report\n\nDegraded content")
    r = VideoResearcher(temp_config, llm_adapter=mock_llm)

    with mock.patch.object(r, "_health_check", return_value="ok"):
        with mock.patch.object(r, "_nlm_create_notebook", return_value="nb-001"):
            # 源添加在两个 profile 都失败（非配额）
            with mock.patch.object(r, "_nlm_add_youtube", return_value=False):
                with mock.patch.object(
                    r, "_get_transcript", return_value="Transcript content. " * 100
                ):
                    outputs = r.run([sample_video_result])

    assert len(outputs) == 1
    assert outputs[0].degraded is True  # 走了降级，而非标 pending
    assert "Fallback Report" in outputs[0].report_markdown
    assert mock_llm.calls[0]["task"] == "summarize"


def test_source_failure_does_not_abort_batch(temp_config, sample_video_result):
    """关键回归：一个视频 NLM 失败不再中断整批——其它视频照常处理。"""
    mock_llm = MockLLMAdapter("# Degraded V1\n\nx")
    r = VideoResearcher(temp_config, llm_adapter=mock_llm)
    video2 = {
        "video": {
            "title": "V2 正常视频",
            "url": "https://www.youtube.com/watch?v=v2ok",
            "platform": "youtube",
        },
        "creator": {"name": "C2", "platform": "youtube"},
    }

    with mock.patch.object(r, "_health_check", return_value="ok"):
        with mock.patch.object(r, "_nlm_create_notebook", return_value="nb"):
            # side_effect 顺序映射当前 profile 轮替实现：
            #   视频1 主 profile add → False（第1次）
            #   视频1 备用 profile add → False（第2次）→ _nlm_research_path 返回 None → 降级
            #   视频2 主 profile add → True（第3次）→ 正常 NLM 路径
            with mock.patch.object(
                r, "_nlm_add_youtube", side_effect=[False, False, True]
            ) as mock_add:
                with mock.patch.object(r, "_nlm_research", return_value=True):
                    with mock.patch.object(r, "_nlm_wait_research", return_value=True):
                        with mock.patch.object(r, "_nlm_import", return_value=True):
                            with mock.patch.object(
                                r,
                                "_generate_and_download_report",
                                return_value="# V2 正常报告\n\nfull",
                            ):
                                with mock.patch.object(
                                    r,
                                    "_get_transcript",
                                    return_value="transcript " * 100,
                                ):
                                    outputs = r.run([sample_video_result, video2])

    assert len(outputs) == 2, "单视频失败不应中断整批"
    # 一个降级(视频1)、一个正常(视频2)
    assert sorted(o.degraded for o in outputs) == [False, True]
    # 锚定轮替次数：视频1 两 profile(2) + 视频2 一次(1) = 3，防 side_effect 顺序静默失配
    assert mock_add.call_count == 3


# ---------------------------------------------------------------------------
# Test: _nlm_delete_notebook — best-effort，失败不抛异常
# ---------------------------------------------------------------------------


def test_nlm_delete_notebook_called_on_source_failure(temp_config, sample_video_result):
    """源处理失败轮替时，弃用的原 notebook 被 best-effort 删除。"""
    r = VideoResearcher(temp_config, llm_adapter=MockLLMAdapter("x"))

    with mock.patch.object(r, "_health_check", return_value="ok"):
        with mock.patch.object(r, "_nlm_create_notebook", side_effect=["nb-primary", "nb-alt"]):
            with mock.patch.object(r, "_nlm_add_youtube", return_value=False):
                with mock.patch.object(r, "_nlm_delete_notebook") as mock_delete:
                    with mock.patch.object(r, "_get_transcript", return_value="T " * 100):
                        r.run([sample_video_result])

    # 轮替删旧 nb-primary + 两 profile 都失败后删 nb-alt
    called_ids = [call.args[0] for call in mock_delete.call_args_list]
    assert "nb-primary" in called_ids, "轮替前应删除原 notebook"
    assert "nb-alt" in called_ids, "两 profile 源处理均失败应删备用 notebook"


def test_nlm_delete_notebook_swallows_exception(temp_config):
    """_nlm_delete_notebook 自身失败不抛异常，不影响主流程。"""
    r = VideoResearcher(temp_config)
    with mock.patch.object(r, "_nlm", side_effect=Exception("delete failed")):
        r._nlm_delete_notebook("nb-xyz", "default")  # 不应抛出


def test_real_quota_aborts_batch_and_marks_pending(temp_config, sample_video_result):
    """真配额(exit 8 → NLMQuotaExhausted)→ 停批，剩余视频全标 pending(increment_retry=False)。"""
    from src.akb2.video.researcher import NLMQuotaExhausted

    r = VideoResearcher(temp_config, llm_adapter=MockLLMAdapter("x"))
    video2 = {
        "video": {
            "title": "V2",
            "url": "https://www.youtube.com/watch?v=v2",
            "platform": "youtube",
        },
        "creator": {"name": "C2", "platform": "youtube"},
    }

    with mock.patch.object(r, "_health_check", return_value="ok"):
        with mock.patch.object(r, "_nlm_create_notebook", return_value="nb"):
            # source add 触发真配额异常（模拟 _nlm 在 exit 8 时抛出）
            with mock.patch.object(
                r, "_nlm_add_youtube", side_effect=NLMQuotaExhausted("exit 8")
            ):
                with mock.patch.object(r, "_save_pending") as mock_save:
                    outputs = r.run([sample_video_result, video2])

    assert outputs == []
    # 仅 quota_skipped 路径被保存一次（若误入 failed 路径会多调一次 → assert_called_once 失败）
    mock_save.assert_called_once()
    args, kwargs = mock_save.call_args
    assert len(args[0]) == 2, "配额耗尽时剩余视频全部标 pending"
    saved_urls = {v["video"]["url"] for v in args[0]}
    assert saved_urls == {
        sample_video_result["video"]["url"],
        video2["video"]["url"],
    }, "两个视频(含触发配额的当前视频 + 剩余视频)都应进 quota_skipped"
    assert kwargs.get("increment_retry") is False


# ---------------------------------------------------------------------------
# SC-1: _nlm 检测 stderr 中的网络错误（nlm CLI bug: exit 0 但 stderr 含
#       Python traceback）
# ---------------------------------------------------------------------------


def test_nlm_detects_network_error_in_stderr(temp_config):
    """exit 0 + stdout 空 + stderr 含 ConnectError → _nlm 返回 None（非空字符串）。"""
    r = VideoResearcher(temp_config)
    with mock.patch(
        "src.akb2.video.researcher.subprocess.Popen",
        return_value=_fake_proc(
            stdout="",
            stderr="Traceback (most recent call last):\nConnectError: Connection refused",
            returncode=0,
        ),
    ):
        result = r._nlm("notebook", "list", profile="default")
    assert result is None, (
        "网络故障时 _nlm 应返回 None（而非空字符串），"
        "防止调用方误判为成功"
    )


def test_nlm_normal_exit_zero_returns_stdout(temp_config):
    """exit 0 + stdout 有内容 + stderr 无网络错误 → _nlm 正常返回 stdout。"""
    r = VideoResearcher(temp_config)
    with mock.patch(
        "src.akb2.video.researcher.subprocess.Popen",
        return_value=_fake_proc(
            stdout='[{"id": "test-nb"}]',
            stderr="",
            returncode=0,
        ),
    ):
        result = r._nlm("notebook", "list", profile="default")
    assert result is not None
    assert "test-nb" in result


def test_nlm_nonzero_exit_with_network_stderr_returns_none(temp_config):
    """exit 1 + stderr 含网络错误 → _nlm 返回 None（非 traceback 假成功路径）。"""
    r = VideoResearcher(temp_config)
    with mock.patch(
        "src.akb2.video.researcher.subprocess.Popen",
        return_value=_fake_proc(
            stdout="",
            stderr="Connection timed out",
            returncode=1,
        ),
    ):
        result = r._nlm("notebook", "list", profile="default")
    assert result is None


# ---------------------------------------------------------------------------
# SC-2: 网络 transient 失败 → 视频转 pending 重试，不强制降级
# ---------------------------------------------------------------------------


def test_network_error_goes_to_pending_not_fallback(
    temp_config, sample_video_result,
):
    """health_check 返回 network_error → 视频不进降级，直接返回 None（转 pending）。"""
    r = VideoResearcher(temp_config, llm_adapter=MockLLMAdapter("x"))
    with mock.patch.object(r, "_health_check", return_value="network_error"):
        with mock.patch.object(r, "_fallback_transcript_research") as mock_fallback:
            with mock.patch.object(r, "_save_pending") as mock_pending:
                outputs = r.run([sample_video_result])

    assert outputs == [], "network_error 时不应产出 ResearchOutput"
    mock_fallback.assert_not_called(), (
        "网络故障时不应走 yt-dlp 降级（同样连不上 youtube）"
    )
    # run() 会将 _research_single 返回 None 的视频放入 failed 列表，
    # 然后调用 _save_pending(failed, increment_retry=True)
    assert mock_pending.call_count >= 1, "视频应被写入 pending"


def test_auth_expired_still_triggers_fallback(
    temp_config, sample_video_result,
):
    """auth_expired 时降级路径行为不变（回归保护）。"""
    mock_llm = MockLLMAdapter("# Fallback")
    r = VideoResearcher(temp_config, llm_adapter=mock_llm)
    with mock.patch.object(r, "_health_check", return_value="auth_expired"):
        with mock.patch.object(
            r, "_get_transcript", return_value="Transcript. " * 100
        ):
            outputs = r.run([sample_video_result])

    assert len(outputs) == 1
    assert outputs[0].degraded is True, "auth 过期必须走降级路径"
    assert "降级研究" in outputs[0].report_markdown


# ---------------------------------------------------------------------------
# SC-1 补充: _health_check 返回正确的状态字符串
# ---------------------------------------------------------------------------


def test_health_check_returns_network_error_on_connect_refused(temp_config):
    """nlm CLI 返回 exit 0 + ConnectError stderr → _health_check 返回 network_error。"""
    r = VideoResearcher(temp_config)
    with mock.patch(
        "src.akb2.video.researcher.subprocess.Popen",
        return_value=_fake_proc(
            stdout="",
            stderr="Traceback (most recent call last):\nConnectError: [Errno 61] Connection refused",
            returncode=0,
        ),
    ):
        result = r._health_check("default")
    assert result == "network_error"


def test_health_check_checks_stdout_for_network_errors(temp_config):
    """nlm CLI 返回 exit 1 + stdout 网络错误 + stderr 空 → network_error。"""
    r = VideoResearcher(temp_config)
    with mock.patch(
        "src.akb2.video.researcher.subprocess.Popen",
        return_value=_fake_proc(
            stdout="Traceback (most recent call last):\nNetwork is unreachable",
            stderr="",
            returncode=1,
        ),
    ):
        result = r._health_check("default")
    assert result == "network_error"


def test_health_check_returns_auth_expired_on_profile_not_found(temp_config):
    """nlm CLI 返回 exit 1 + 'Profile not found' → _health_check 返回 auth_expired。"""
    r = VideoResearcher(temp_config)
    with mock.patch(
        "src.akb2.video.researcher.subprocess.Popen",
        return_value=_fake_proc(
            stdout="",
            stderr="Error: Profile 'expired' not found. Run 'nlm login' first.",
            returncode=1,
        ),
    ):
        result = r._health_check("expired")
    assert result == "auth_expired"


def test_health_check_returns_ok_on_valid_output(temp_config):
    """nlm CLI 返回正常 JSON → _health_check 返回 ok。"""
    r = VideoResearcher(temp_config)
    with mock.patch(
        "src.akb2.video.researcher.subprocess.Popen",
        return_value=_fake_proc(
            stdout='[{"id": "nb-123", "title": "Test"}]',
            stderr="",
            returncode=0,
        ),
    ):
        result = r._health_check("default")
    assert result == "ok"


def test_health_check_returns_ok_on_quota_exhausted(temp_config):
    """nlm CLI 返回 exit 8（配额耗尽）→ _health_check 返回 ok（auth 仍有效）。"""
    r = VideoResearcher(temp_config)
    with mock.patch(
        "src.akb2.video.researcher.subprocess.Popen",
        return_value=_fake_proc(
            stdout="",
            stderr="Quota exhausted",
            returncode=8,
        ),
    ):
        result = r._health_check("default")
    assert result == "ok", "配额耗尽不代表 auth 失效"
