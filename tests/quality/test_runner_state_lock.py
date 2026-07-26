"""缺陷 #11a/#11b: runner 状态路径落机器本地 + 处理锁真正互斥。

原实现（runner.py `_acquire_processing_lock`）是 check-then-write：
- 无 O_EXCL / 无 fcntl.flock → 两个进程可同时"拿到"锁
- 锁内容只有 pid + ts，不含 hostname → 跨机 pid 无可比性
- `_release_processing_lock` 无条件 unlink → B 机可以删掉 A 机的锁

断言的可观测行为：
- 两个进程竞争同一锁 → 只有一个 acquire 成功
- 锁文件内容含本机 hostname（跨机唯一可比证据）
- 持锁进程崩溃（不 release 直接退出）→ 锁可被回收，队列不永久卡死
- 未持锁时调用 release → 不删别人的锁文件
- 锁目录/账本路径默认落机器本地状态根，不在同步范围内
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from akb2.common import state_paths

REPO_ROOT = Path(__file__).resolve().parents[2]

# 子进程：抢锁 → 打印结果 → 按 mode 崩溃退出或长期持锁
_CHILD_SCRIPT = """
import sys, time
sys.path.insert(0, {repo!r})
from src.akb2.quality.runner import RunnerConfig, _acquire_processing_lock

lock_dir, mode = sys.argv[1], sys.argv[2]
config = RunnerConfig(
    raw_dir="/tmp/akb2-child-raw",
    ledger_path="/tmp/akb2-child/quality-ledger.jsonl",
    staging_dir="/tmp/akb2-child",
    vault_root="/tmp/akb2-child",
    failures_path="/tmp/akb2-child/failures.jsonl",
    grader_result_dir="/tmp/akb2-child/grader-results",
    lock_dir=lock_dir,
)
ok = _acquire_processing_lock(config)
print("ACQUIRED" if ok else "DENIED", flush=True)
if mode == "crash":
    import os as _os
    _os._exit(0)          # 模拟崩溃：不 release，直接死
time.sleep(120)
"""


def _spawn_holder(lock_dir: Path, mode: str) -> subprocess.Popen:
    script = _CHILD_SCRIPT.format(repo=str(REPO_ROOT))
    return subprocess.Popen(
        [sys.executable, "-c", script, str(lock_dir), mode],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _make_config(tmp_path: Path, lock_dir: Path | None = None):
    from src.akb2.quality.runner import RunnerConfig

    kwargs = {}
    if lock_dir is not None:
        kwargs["lock_dir"] = str(lock_dir)
    return RunnerConfig(
        raw_dir=str(tmp_path / "raw"),
        ledger_path=str(tmp_path / "quality-ledger.jsonl"),
        staging_dir=str(tmp_path / "staging"),
        vault_root=str(tmp_path),
        failures_path=str(tmp_path / "failures.jsonl"),
        grader_result_dir=str(tmp_path / "grader-results"),
        poll_interval_seconds=1,
        **kwargs,
    )


class TestProcessingLockCrossProcess:
    def test_second_process_cannot_acquire_and_lock_records_hostname(
        self, tmp_path: Path
    ) -> None:
        """跨进程互斥：子进程持锁时父进程 acquire 必须失败；锁内容含 hostname。"""
        from src.akb2.quality.runner import _acquire_processing_lock

        lock_dir = tmp_path / "locks"
        holder = _spawn_holder(lock_dir, mode="hold")
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "ACQUIRED"

            config = _make_config(tmp_path, lock_dir)
            assert not _acquire_processing_lock(config), (
                "子进程正持锁，父进程仍拿到锁 → 两个 runner 同时处理同一篇"
            )

            payload = json.loads((lock_dir / "processing.lock").read_text("utf-8"))
            assert payload.get("host") == socket.gethostname(), (
                f"锁内容缺 hostname，跨机无法判断持有者: {payload}"
            )
            assert payload.get("pid") == holder.pid
        finally:
            holder.kill()
            holder.wait(timeout=30)

    def test_lock_released_when_holder_dies_without_release(
        self, tmp_path: Path
    ) -> None:
        """持锁进程崩溃（不 release）→ 锁可被回收，队列不永久卡死。"""
        from src.akb2.quality.runner import (
            _acquire_processing_lock,
            _release_processing_lock,
        )

        lock_dir = tmp_path / "locks"
        holder = _spawn_holder(lock_dir, mode="crash")
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"
        holder.wait(timeout=30)
        assert (lock_dir / "processing.lock").exists(), "崩溃后锁文件应保留（记录持有者）"

        config = _make_config(tmp_path, lock_dir)
        assert _acquire_processing_lock(config), "崩溃残留锁未被回收 → 队列永久卡死"
        _release_processing_lock(config)

    def test_foreign_host_fresh_lock_is_not_stolen(self, tmp_path: Path) -> None:
        """另一台机器的新鲜锁 → 拒绝获取，且 release 不删它的锁文件。"""
        from src.akb2.quality.runner import (
            _acquire_processing_lock,
            _release_processing_lock,
        )

        lock_dir = tmp_path / "locks"
        lock_dir.mkdir(parents=True)
        lock_file = lock_dir / "processing.lock"
        foreign = {
            "host": "mac-mini-other",
            "pid": 4242,
            "ts": "2026-07-26T00:00:00+00:00",
            "state": "held",
        }
        lock_file.write_text(json.dumps(foreign), encoding="utf-8")

        config = _make_config(tmp_path, lock_dir)
        assert not _acquire_processing_lock(config), (
            "抢了另一台机器正持有的锁 → 两机同时处理同一篇"
        )

        # 未持锁时 release 绝不能删别人的锁（旧实现无条件 unlink）
        _release_processing_lock(config)
        assert lock_file.exists(), "release 删掉了他人的锁文件"
        assert json.loads(lock_file.read_text("utf-8")) == foreign

    def test_foreign_stale_lock_is_reclaimed(self, tmp_path: Path) -> None:
        """另一台机器的超龄锁（崩溃残留）→ 可回收，避免永久卡死。"""
        from src.akb2.quality.runner import (
            MAX_PROCESSING_LOCK_AGE_SEC,
            _acquire_processing_lock,
            _release_processing_lock,
        )

        lock_dir = tmp_path / "locks"
        lock_dir.mkdir(parents=True)
        lock_file = lock_dir / "processing.lock"
        lock_file.write_text(
            json.dumps({"host": "mac-mini-other", "pid": 4242, "state": "held"}),
            encoding="utf-8",
        )
        stale = time.time() - (MAX_PROCESSING_LOCK_AGE_SEC + 60)
        os.utime(lock_file, (stale, stale))

        config = _make_config(tmp_path, lock_dir)
        assert _acquire_processing_lock(config), "跨机超龄死锁未被回收 → 队列永久卡死"
        _release_processing_lock(config)

    def test_release_marks_lock_released_for_next_acquire(self, tmp_path: Path) -> None:
        """release 后锁被标记 released，下一次 acquire 不被 hostname 规则误拒。"""
        from src.akb2.quality.runner import (
            _acquire_processing_lock,
            _release_processing_lock,
        )

        lock_dir = tmp_path / "locks"
        config = _make_config(tmp_path, lock_dir)
        assert _acquire_processing_lock(config)
        _release_processing_lock(config)

        payload = json.loads((lock_dir / "processing.lock").read_text("utf-8"))
        assert payload.get("state") == "released"
        assert _acquire_processing_lock(config)
        _release_processing_lock(config)


class TestRunnerStatePathsOutsideSyncRange:
    def test_default_lock_dir_is_machine_local(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未显式给 lock_dir → 落机器本地状态根，不落 vault（同步范围）。"""
        state_root = tmp_path / "state"
        monkeypatch.setenv(state_paths.STATE_ROOT_ENV, str(state_root))

        config = _make_config(tmp_path)
        assert Path(config.lock_dir) == state_root / state_paths.LOCK_SUBDIR
        assert not Path(config.lock_dir).is_relative_to(tmp_path / "staging")

    def test_main_resolves_ledger_to_state_root_and_inherits_legacy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() 的账本/失败账本落机器本地，且旧 staging 账本被继承（不重跑）。"""
        import src.akb2.quality.runner as runner

        vault = tmp_path / "vault" / "Praise Space" / "AKB2.0"
        legacy_staging = vault / "staging"
        legacy_staging.mkdir(parents=True)
        legacy_ledger = legacy_staging / "quality-ledger.jsonl"
        legacy_ledger.write_text('{"event":"finalize"}\n', encoding="utf-8")

        state_root = tmp_path / "state"
        monkeypatch.setenv(state_paths.STATE_ROOT_ENV, str(state_root))

        captured: dict[str, object] = {}

        def fake_scan_queue(config):
            captured["config"] = config
            return []

        monkeypatch.setattr(runner, "_scan_queue", fake_scan_queue)
        rc = runner.main(["--once", "--vault-root", str(vault)])
        assert rc == 0

        config = captured["config"]
        assert Path(config.ledger_path) == (
            state_root / state_paths.LEDGER_SUBDIR / "quality-ledger.jsonl"
        )
        assert Path(config.failures_path) == (
            state_root / state_paths.LEDGER_SUBDIR / "failures.jsonl"
        )
        # 旧账本被继承（复制），旧文件仍在
        assert Path(config.ledger_path).read_text("utf-8") == '{"event":"finalize"}\n'
        assert legacy_ledger.exists()
        assert not state_paths.is_synced_path(config.ledger_path)
