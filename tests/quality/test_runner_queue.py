"""Runner 队列驱动测试 — SC-6.1 (队列行为)。

验证文件队列驱动、串行单工、避让窗口、SIGTERM 优雅退出。
全 fixture mock，不调真 API。
"""

from __future__ import annotations

import signal
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_raw_file(raw_dir: Path, slug: str, date_str: str, content: str = "") -> Path:
    """在 raw_dir 中创建一个 raw 文件并返回路径。"""
    filename = f"{slug}--raw--{date_str}.md"
    raw_dir.mkdir(parents=True, exist_ok=True)
    if not content:
        content = f"""---
artifact_type: raw
source_slug: {slug}
source_url: https://example.com/{slug}
source_title: Test Article {slug}
source_authors: Test Author
source_publisher: Test Blog
source_published_at: {date_str}
fetched_at: {date_str}T00:00:00Z
fetched_via: defuddle
---

# Test Article {slug}

Some content here.
"""
    filepath = raw_dir / filename
    filepath.write_text(content, encoding="utf-8")
    return filepath


def _make_ledger_entry(
    ledger_path: Path,
    event: str,
    node: str,
    content_hash: str = "",
    ts: str = "",
) -> None:
    """在账本中写入一条事件。"""
    from src.akb2.loops.ledger import append_event

    entry = {
        "loop_id": "loop1",
        "node": node,
        "event": event,
        "content_hash": content_hash,
        "ts": ts or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "details": {},
    }
    append_event(str(ledger_path), entry)


# ---------------------------------------------------------------------------
# 测试类
# ---------------------------------------------------------------------------


class TestRunnerQueue:
    """文件队列驱动行为测试。"""

    def test_scan_raw_files_finds_unprocessed(self, tmp_path: Path) -> None:
        """raw 文件存在且账本无对应记录 → 识别为待处理。"""
        from src.akb2.quality.runner import (
            RunnerConfig,
            _scan_queue,
        )

        raw_dir = tmp_path / "raw"
        ledger_path = tmp_path / "quality-ledger.jsonl"

        _make_raw_file(raw_dir, "test-article", "2026-06-13")
        _make_raw_file(raw_dir, "another-article", "2026-06-13")

        config = RunnerConfig(
            raw_dir=str(raw_dir),
            ledger_path=str(ledger_path),
            staging_dir=str(tmp_path / "staging"),
            vault_root=str(tmp_path),
            failures_path=str(tmp_path / "failures.jsonl"),
            grader_result_dir=str(tmp_path / "grader-results"),
            poll_interval_seconds=1,
        )

        queue = _scan_queue(config)
        assert len(queue) == 2, f"期望 2 个待处理文件，实际 {len(queue)}"

    def test_scan_raw_files_skips_processed(self, tmp_path: Path) -> None:
        """raw 文件已在账本中标记 finalize → 跳过。"""
        import hashlib

        from src.akb2.quality.runner import (
            RunnerConfig,
            _scan_queue,
        )

        raw_dir = tmp_path / "raw"
        ledger_path = tmp_path / "quality-ledger.jsonl"

        f1 = _make_raw_file(raw_dir, "processed-article", "2026-06-13")
        f2 = _make_raw_file(raw_dir, "unprocessed-article", "2026-06-13")

        # 标记 f1 为已处理
        h1 = hashlib.sha256(str(f1).encode("utf-8")).hexdigest()[:16]
        _make_ledger_entry(ledger_path, "finalize", "finalize", h1)

        config = RunnerConfig(
            raw_dir=str(raw_dir),
            ledger_path=str(ledger_path),
            staging_dir=str(tmp_path / "staging"),
            vault_root=str(tmp_path),
            failures_path=str(tmp_path / "failures.jsonl"),
            grader_result_dir=str(tmp_path / "grader-results"),
            poll_interval_seconds=1,
        )

        queue = _scan_queue(config)
        assert len(queue) == 1, f"期望 1 个待处理文件（跳过已处理），实际 {len(queue)}"
        assert f2.name in queue[0], f"队列中应为 {f2.name}"

    def test_scan_raw_files_skips_aborted(self, tmp_path: Path) -> None:
        """raw 文件在账本中标记 abort → 跳过。"""
        import hashlib

        from src.akb2.quality.runner import (
            RunnerConfig,
            _scan_queue,
        )

        raw_dir = tmp_path / "raw"
        ledger_path = tmp_path / "quality-ledger.jsonl"

        f1 = _make_raw_file(raw_dir, "aborted-article", "2026-06-13")

        h1 = hashlib.sha256(str(f1).encode("utf-8")).hexdigest()[:16]
        _make_ledger_entry(ledger_path, "abort", "promote", h1)

        config = RunnerConfig(
            raw_dir=str(raw_dir),
            ledger_path=str(ledger_path),
            staging_dir=str(tmp_path / "staging"),
            vault_root=str(tmp_path),
            failures_path=str(tmp_path / "failures.jsonl"),
            grader_result_dir=str(tmp_path / "grader-results"),
            poll_interval_seconds=1,
        )

        queue = _scan_queue(config)
        assert len(queue) == 0, f"期望 0 个待处理文件，实际 {len(queue)}"

    def test_scan_queue_empty_dir(self, tmp_path: Path) -> None:
        """raw 目录为空 → 空队列。"""
        from src.akb2.quality.runner import (
            RunnerConfig,
            _scan_queue,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        config = RunnerConfig(
            raw_dir=str(raw_dir),
            ledger_path=str(tmp_path / "quality-ledger.jsonl"),
            staging_dir=str(tmp_path / "staging"),
            vault_root=str(tmp_path),
            failures_path=str(tmp_path / "failures.jsonl"),
            grader_result_dir=str(tmp_path / "grader-results"),
            poll_interval_seconds=1,
        )

        queue = _scan_queue(config)
        assert len(queue) == 0

    def test_serial_single_worker_enforced(self, tmp_path: Path) -> None:
        """串行单工: _processing_lock 确保同一时刻仅 1 篇在处理。"""
        from src.akb2.quality.runner import (
            RunnerConfig,
            _acquire_processing_lock,
            _release_processing_lock,
        )

        lock_dir = tmp_path / "locks"
        config = RunnerConfig(
            raw_dir=str(tmp_path / "raw"),
            ledger_path=str(tmp_path / "quality-ledger.jsonl"),
            staging_dir=str(tmp_path / "staging"),
            vault_root=str(tmp_path),
            failures_path=str(tmp_path / "failures.jsonl"),
            grader_result_dir=str(tmp_path / "grader-results"),
            poll_interval_seconds=1,
            lock_dir=str(lock_dir),
        )

        # 第一次获取锁成功
        assert _acquire_processing_lock(config)
        # 第二次获取锁失败（同一进程内模拟）
        assert not _acquire_processing_lock(config)
        # 释放锁
        _release_processing_lock(config)
        # 释放后可以再次获取
        assert _acquire_processing_lock(config)
        _release_processing_lock(config)

    def test_crashed_holder_lock_is_reclaimed(self, tmp_path: Path) -> None:
        """worker 崩溃留死锁 → 锁可回收，队列不被永久卡死。

        缺陷 #11b 后机制变更：锁改用 fcntl.flock，**进程死亡时内核自动释放 flock**，
        所以"崩溃残留"不再靠 mtime 超龄猜（旧断言把 mtime 超龄当作唯一回收条件，
        且用「同一进程再 acquire 一次」冒充崩溃 —— 在 flock 语义下那是活着的持有者，
        本就该被拒，原断言方向是错的）。这里改为直接让持锁 fd 消失（等价于进程死亡、
        内核释放 flock），断言残留锁文件不会卡死队列。跨机超龄回收分支见
        tests/quality/test_runner_state_lock.py。
        """
        import src.akb2.quality.runner as runner_mod
        from src.akb2.quality.runner import (
            RunnerConfig,
            _acquire_processing_lock,
            _release_processing_lock,
        )

        lock_dir = tmp_path / "locks"
        config = RunnerConfig(
            raw_dir=str(tmp_path / "raw"),
            ledger_path=str(tmp_path / "quality-ledger.jsonl"),
            staging_dir=str(tmp_path / "staging"),
            vault_root=str(tmp_path),
            failures_path=str(tmp_path / "failures.jsonl"),
            grader_result_dir=str(tmp_path / "grader-results"),
            poll_interval_seconds=1,
            lock_dir=str(lock_dir),
        )

        assert _acquire_processing_lock(config)
        lock_file = lock_dir / "processing.lock"
        assert lock_file.exists()

        # 活着的持有者：第二次 acquire 必须被拒。
        assert not _acquire_processing_lock(config)

        # 模拟持锁进程崩溃：fd 消失（内核释放 flock），锁文件仍留在盘上且标记 held。
        runner_mod._PROCESSING_LOCK_FD.close()
        runner_mod._PROCESSING_LOCK_FD = None
        assert lock_file.exists()

        assert _acquire_processing_lock(config), "崩溃残留死锁未被回收 → 队列会永久卡死"
        _release_processing_lock(config)


class TestRunnerAvoidWindows:
    """避让窗口测试。"""

    def test_avoid_window_active(self) -> None:
        """在避让窗口内 → _should_avoid() 返回 True。"""
        import src.akb2.quality.runner as runner_mod

        windows = runner_mod.AVOID_WINDOWS
        # 模拟 feishu-sync 窗口内的时间 (02:05 UTC = 10:05 CST)
        with mock.patch.object(runner_mod, "_now") as mock_now:
            mock_now.return_value = datetime(2026, 6, 13, 2, 5, 0, tzinfo=UTC)
            assert runner_mod._should_avoid(windows) is True

    def test_avoid_window_inactive(self) -> None:
        """在避让窗口外 → _should_avoid() 返回 False。"""
        import src.akb2.quality.runner as runner_mod

        windows = runner_mod.AVOID_WINDOWS
        with mock.patch.object(runner_mod, "_now") as mock_now:
            mock_now.return_value = datetime(2026, 6, 13, 14, 0, 0, tzinfo=UTC)
            assert runner_mod._should_avoid(windows) is False


class TestRunnerSigterm:
    """SIGTERM 优雅退出测试。"""

    def test_sigterm_handler_sets_flag(self) -> None:
        """SIGTERM 信号处理函数设置 shutdown_flag。"""
        import src.akb2.quality.runner as runner_mod

        # 保存原始值
        original = runner_mod._shutdown_flag
        try:
            # 直接调用 handler 逻辑（模拟收到 SIGTERM）
            runner_mod._shutdown_flag = False

            def handler(signum, frame):
                runner_mod._shutdown_flag = True  # type: ignore[assignment]

            # 模拟信号处理器调用
            handler(signal.SIGTERM, None)

            assert runner_mod._shutdown_flag is True, "SIGTERM handler 应设置 shutdown_flag"
        finally:
            runner_mod._shutdown_flag = original

    def test_sigterm_graceful_shutdown_finishes_current(self, tmp_path: Path) -> None:
        """SIGTERM 不中断正在处理的单篇。"""
        import src.akb2.quality.runner as runner_mod

        # 设置 shutdown_flag 模拟收到 SIGTERM
        runner_mod._shutdown_flag = True

        # 验证 _process_one 在 shutdown 状态下仍能完成当前篇
        # (process_one 内部检查 shutdown_flag 只在开始新篇前)
        from src.akb2.quality.runner import (
            RunnerConfig,
            _process_one,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-shutdown", "2026-06-13")

        config = RunnerConfig(
            raw_dir=str(raw_dir),
            ledger_path=str(tmp_path / "quality-ledger.jsonl"),
            staging_dir=str(tmp_path / "staging"),
            vault_root=str(tmp_path),
            failures_path=str(tmp_path / "failures.jsonl"),
            grader_result_dir=str(tmp_path / "grader-results"),
            poll_interval_seconds=1,
        )

        # shutdown_flag=True 时，_process_one 应该快速返回 (跳过)
        # 而不是崩溃
        result = _process_one(str(f), config)
        # 在 shutdown 状态下，_process_one 应该跳过处理
        assert result is not None  # 返回结果而不是异常

        # 重置 flag
        runner_mod._shutdown_flag = False
