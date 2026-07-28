"""残余风险 P2（跨机互斥不完整）：raw 队列在同步 vault，处理锁只在单机内互斥。

背景（上一轮修复的副作用）：处理锁从 vault 移到机器本地状态根并改用
`fcntl.flock` 后，**单机内**真互斥了，但 flock 是内核 inode 级语义、锁目录本身
也各机独立，所以两台机器根本不再抢同一把锁；而 raw 队列目录仍在 Syncthing 同步的
vault（`<vault>/raw`）、账本也已改成机器本地 → 两台机器各自都把同一篇 raw 看成
"未处理"，会同时全链处理它（重复 LLM 花费 + 双写 vault）。
`docs/mac-mini-deployment-notes.md` 指导第二台机器装同一套 plist、指向同一个
Syncthing vault，所以这不是假设。

本文件断言的可观测行为（全部走生产路径 `main()` / `_scan_queue`，分片配置只从
env 进 `RunnerConfig.__post_init__`，与 plist/wrapper 注入方式一致）：

- 未配分片时两台机器（两个进程 + 各自机器本地状态根）会挑中**同一篇** raw
  —— 把"锁不跨机"这个事实钉住，说明启动告警不是空话
- 配上互不相交的分片后两台机器挑中**不同**篇，且分片划分对整个队列既无重复也无遗漏
- 外机分片项滞留超阈值 → 可被接管（单机故障不永久占住工作项）
- 未超阈值 / 未开接管 → 不得抢外机分片项
- 非法分片配置 fail-closed 抛错（不静默退化成"独占全部"）
- raw 队列在同步范围内且未配分片 → 启动告警；`AKB2_RUNNER_REQUIRE_SHARDING=1`
  时拒绝启动（返回码 1）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_RAW_TEMPLATE = """---
artifact_type: raw
source_slug: {slug}
source_url: https://example.com/{slug}
fetched_at: 2026-07-28T00:00:00Z
---

# {slug}

body of {slug}
"""

# 子进程：模拟一台机器跑 `runner --once`（真走 main() → _scan_queue → 取锁），
# 只把最终的单篇处理换成记录桩，避免真调 LLM。
_CHILD_ONCE = """
import json, sys
sys.path.insert(0, {repo!r})
import src.akb2.quality.runner as r

picked = []


def _stub_process_one(raw_path, config):
    picked.append(raw_path)
    return {{"final_status": "stub"}}


r._process_one = _stub_process_one
rc = r.main(["--once", "--vault-root", sys.argv[1]])
print("RC " + str(rc))
print("PICKED " + json.dumps(picked))
"""


def _make_vault(tmp_path: Path, count: int = 16) -> tuple[Path, list[Path]]:
    """建一个 AKB2.0 形状的 vault + count 篇 raw（内容确定 → 分片确定）。"""
    vault = tmp_path / "AKB2.0"
    raw_dir = vault / "raw"
    raw_dir.mkdir(parents=True)
    files: list[Path] = []
    for idx in range(count):
        slug = f"src-{idx:02d}"
        fpath = raw_dir / f"{slug}--raw--2026-07-28.md"
        fpath.write_text(_RAW_TEMPLATE.format(slug=slug), encoding="utf-8")
        files.append(fpath)
    return vault, files


def _run_machine(
    vault: Path, state_root: Path, shard_spec: str | None
) -> list[str]:
    """以独立进程 + 独立机器本地状态根跑一台"机器"，返回它挑中的 raw 列表。"""
    script = _CHILD_ONCE.format(repo=str(REPO_ROOT))
    env = os.environ.copy()
    env["AKB2_STATE_ROOT"] = str(state_root)
    for name in ("VAULT_ROOT", "AKB2_ROOT", "AKB2_VAULT_ROOT", "AKB2_RUNNER_SHARDS"):
        env.pop(name, None)
    if shard_spec is not None:
        env["AKB2_RUNNER_SHARDS"] = shard_spec
    proc = subprocess.run(
        [sys.executable, "-c", script, str(vault)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"子进程失败: {proc.stdout}\n{proc.stderr}"
    line = next(
        ln for ln in proc.stdout.splitlines() if ln.startswith("PICKED ")
    )
    return json.loads(line[len("PICKED "):])


def _make_config(tmp_path: Path, raw_dir: Path, **kwargs):
    from src.akb2.quality.runner import RunnerConfig

    return RunnerConfig(
        raw_dir=str(raw_dir),
        ledger_path=str(tmp_path / "ledgers" / "quality-ledger.jsonl"),
        staging_dir=str(tmp_path / "staging"),
        vault_root=str(tmp_path),
        failures_path=str(tmp_path / "failures.jsonl"),
        grader_result_dir=str(tmp_path / "grader-results"),
        lock_dir=str(tmp_path / "locks"),
        poll_interval_seconds=1,
        **kwargs,
    )


def _pick_two_distinct_shards(files: list[Path]) -> tuple[int, int]:
    """从确定内容的 raw 列表里取两个不同的分片号（确定性，无随机）。"""
    from src.akb2.quality.runner import _shard_of

    shards = [_shard_of(str(f)) for f in files]
    first = shards[0]
    for shard in shards[1:]:
        if shard != first:
            return first, shard
    raise AssertionError(f"测试样本全落在同一分片 {first}，无法验证跨机分区")


class TestCrossMachineDuplicateProcessing:
    def test_two_machines_without_sharding_pick_the_same_raw(
        self, tmp_path: Path
    ) -> None:
        """未配分片：两台机器（独立状态根）挑中同一篇 → 锁确实不跨机。"""
        vault, _files = _make_vault(tmp_path)
        picked_a = _run_machine(vault, tmp_path / "state-a", shard_spec=None)
        picked_b = _run_machine(vault, tmp_path / "state-b", shard_spec=None)
        assert len(picked_a) == 1 and len(picked_b) == 1
        assert picked_a == picked_b, (
            "两台机器竟没挑中同一篇——本测试的前提（锁不跨机）不成立，需重新体检"
        )

    def test_two_machines_with_disjoint_shards_pick_different_raw(
        self, tmp_path: Path
    ) -> None:
        """配互不相交分片：两台机器挑中不同篇 → 跨机不重复处理同一篇。"""
        from src.akb2.quality.runner import _shard_of

        vault, files = _make_vault(tmp_path)
        shard_a, shard_b = _pick_two_distinct_shards(files)

        picked_a = _run_machine(vault, tmp_path / "state-a", shard_spec=str(shard_a))
        picked_b = _run_machine(vault, tmp_path / "state-b", shard_spec=str(shard_b))

        assert len(picked_a) == 1, f"A 机未取到自己分片的活: {picked_a}"
        assert len(picked_b) == 1, f"B 机未取到自己分片的活: {picked_b}"
        assert picked_a[0] != picked_b[0], (
            f"两台机器仍挑中同一篇 {picked_a[0]} → 跨机重复处理未修"
        )
        assert _shard_of(picked_a[0]) == shard_a
        assert _shard_of(picked_b[0]) == shard_b

    def test_shard_partition_has_no_overlap_and_no_loss(self, tmp_path: Path) -> None:
        """两半分片对整个队列既无交集也无遗漏（不重复、不丢件）。"""
        from src.akb2.quality.runner import _scan_queue

        vault, files = _make_vault(tmp_path)
        raw_dir = vault / "raw"

        os.environ["AKB2_RUNNER_SHARDS"] = "0-7"
        try:
            queue_a = set(_scan_queue(_make_config(tmp_path, raw_dir)))
            os.environ["AKB2_RUNNER_SHARDS"] = "8-15"
            queue_b = set(_scan_queue(_make_config(tmp_path, raw_dir)))
        finally:
            os.environ.pop("AKB2_RUNNER_SHARDS", None)

        assert queue_a and queue_b, f"分片划分把队列压成空: A={len(queue_a)} B={len(queue_b)}"
        assert queue_a & queue_b == set(), f"两机分片有交集: {queue_a & queue_b}"
        assert queue_a | queue_b == {str(f) for f in files}, "分片划分漏了 raw"


class TestForeignShardTakeover:
    def test_fresh_foreign_shard_item_is_not_stolen(self, tmp_path: Path) -> None:
        """外机分片项未超龄 → 即使开了接管也不许抢（否则又变重复处理）。"""
        from src.akb2.quality.runner import _scan_queue, _shard_of

        vault, files = _make_vault(tmp_path, count=4)
        raw_dir = vault / "raw"
        mine, _other = _pick_two_distinct_shards(files)

        os.environ["AKB2_RUNNER_SHARDS"] = str(mine)
        os.environ["AKB2_RUNNER_SHARD_TAKEOVER"] = "1"
        try:
            queue = _scan_queue(_make_config(tmp_path, raw_dir))
        finally:
            os.environ.pop("AKB2_RUNNER_SHARDS", None)
            os.environ.pop("AKB2_RUNNER_SHARD_TAKEOVER", None)

        foreign = [str(f) for f in files if _shard_of(str(f)) != mine]
        assert foreign, "样本里没有外机分片项，测试无效"
        assert not (set(queue) & set(foreign)), (
            f"抢了外机分片的新鲜项 {set(queue) & set(foreign)} → 跨机重复处理"
        )

    def test_stale_foreign_shard_item_is_taken_over(self, tmp_path: Path) -> None:
        """外机分片项滞留超阈值 → 可接管（单机故障不永久占住工作项）。"""
        from src.akb2.quality.runner import (
            SHARD_TAKEOVER_AFTER_SEC,
            _scan_queue,
            _shard_of,
        )

        vault, files = _make_vault(tmp_path, count=4)
        raw_dir = vault / "raw"
        mine, _other = _pick_two_distinct_shards(files)
        foreign = [f for f in files if _shard_of(str(f)) != mine]
        assert foreign
        stale = time.time() - (SHARD_TAKEOVER_AFTER_SEC + 600)
        for f in foreign:
            os.utime(f, (stale, stale))

        os.environ["AKB2_RUNNER_SHARDS"] = str(mine)
        os.environ["AKB2_RUNNER_SHARD_TAKEOVER"] = "1"
        try:
            queue = set(_scan_queue(_make_config(tmp_path, raw_dir)))
        finally:
            os.environ.pop("AKB2_RUNNER_SHARDS", None)
            os.environ.pop("AKB2_RUNNER_SHARD_TAKEOVER", None)

        assert {str(f) for f in foreign} <= queue, (
            "超龄外机分片项未被接管 → 所属机器一旦长期离线这些 raw 永久卡死"
        )

    def test_takeover_is_off_by_default(self, tmp_path: Path) -> None:
        """默认不接管：超龄外机分片项也不进队列（接管必须显式开，避免双机同抢）。"""
        from src.akb2.quality.runner import (
            SHARD_TAKEOVER_AFTER_SEC,
            _scan_queue,
            _shard_of,
        )

        vault, files = _make_vault(tmp_path, count=4)
        raw_dir = vault / "raw"
        mine, _other = _pick_two_distinct_shards(files)
        foreign = [f for f in files if _shard_of(str(f)) != mine]
        stale = time.time() - (SHARD_TAKEOVER_AFTER_SEC + 600)
        for f in foreign:
            os.utime(f, (stale, stale))

        os.environ["AKB2_RUNNER_SHARDS"] = str(mine)
        os.environ.pop("AKB2_RUNNER_SHARD_TAKEOVER", None)
        try:
            queue = set(_scan_queue(_make_config(tmp_path, raw_dir)))
        finally:
            os.environ.pop("AKB2_RUNNER_SHARDS", None)

        assert not (queue & {str(f) for f in foreign}), (
            "默认就接管了外机分片项——两台都开着时会同时抢同一篇"
        )


class TestShardKeyStability:
    def test_shard_is_independent_of_mount_point(self) -> None:
        """同一篇 raw 在两机不同挂载点下必须落同一分片，否则分片互斥当场失效。"""
        from src.akb2.quality.runner import _shard_of

        name = "addy-osmani-loop-engineering--raw--2026-07-28.md"
        mac_a = f"/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/raw/{name}"
        mac_b = f"/Users/other/Obsidian-sync/Praise Space/AKB2.0/raw/{name}"
        assert _shard_of(mac_a) == _shard_of(mac_b), (
            "分片键含挂载点 → 两机对同一篇算出不同分片，两边都会认领"
        )

    def test_distinct_names_spread_across_shards(self) -> None:
        """分片函数不得把所有 raw 压到同一片（否则分区退化成"一台机器干全部"）。"""
        from src.akb2.quality.runner import RUNNER_SHARD_COUNT, _shard_of

        shards = {
            _shard_of(f"/raw/src-{i:03d}--raw--2026-07-28.md") for i in range(200)
        }
        assert len(shards) == RUNNER_SHARD_COUNT, (
            f"200 篇只覆盖 {len(shards)}/{RUNNER_SHARD_COUNT} 个分片，分布不可用"
        )


class TestShardSpecParsing:
    def test_env_spec_reaches_config_without_explicit_kwargs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """生产注入路径：env → __post_init__ → owned_shards（不靠测试手传参数）。"""
        monkeypatch.setenv("AKB2_RUNNER_SHARDS", "0,3,5-6")
        config = _make_config(tmp_path, tmp_path / "raw")
        assert config.owned_shards == frozenset({0, 3, 5, 6})

    def test_unset_env_means_own_all_shards(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AKB2_RUNNER_SHARDS", raising=False)
        config = _make_config(tmp_path, tmp_path / "raw")
        assert config.owned_shards is None

    @pytest.mark.parametrize("spec", ["abc", "0-999", "-1", "7-3", "16"])
    def test_invalid_spec_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: str
    ) -> None:
        """非法分片配置抛错，不静默退化成"独占全部"（那会悄悄恢复重复处理）。"""
        monkeypatch.setenv("AKB2_RUNNER_SHARDS", spec)
        with pytest.raises(ValueError, match="AKB2_RUNNER_SHARDS"):
            _make_config(tmp_path, tmp_path / "raw")


class TestStartupCrossMachineDetection:
    def _synced_vault(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """建一个"在同步范围内"的 vault（用 VAULT_ROOT env 让 is_synced_path 命中）。"""
        vault_parent = tmp_path / "synced"
        vault = vault_parent / "AKB2.0"
        (vault / "raw").mkdir(parents=True)
        monkeypatch.setenv("VAULT_ROOT", str(vault_parent))
        monkeypatch.setenv("AKB2_STATE_ROOT", str(tmp_path / "state"))
        monkeypatch.delenv("AKB2_RUNNER_SHARDS", raising=False)
        return vault

    def test_synced_queue_without_sharding_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """队列在同步范围 + 未配分片 → 启动打印可观测告警（默认不阻断）。"""
        import src.akb2.quality.runner as runner

        vault = self._synced_vault(tmp_path, monkeypatch)
        monkeypatch.delenv("AKB2_RUNNER_REQUIRE_SHARDING", raising=False)
        rc = runner.main(["--once", "--vault-root", str(vault)])
        assert rc == 0
        err = capsys.readouterr().err
        assert "AKB2_RUNNER_SHARDS" in err and "跨机" in err, (
            f"未打印跨机风险告警，运维无从发现: {err!r}"
        )

    def test_require_sharding_refuses_to_start(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AKB2_RUNNER_REQUIRE_SHARDING=1 + 未配分片 → fail-closed 拒绝启动。"""
        import src.akb2.quality.runner as runner

        vault = self._synced_vault(tmp_path, monkeypatch)
        monkeypatch.setenv("AKB2_RUNNER_REQUIRE_SHARDING", "1")
        rc = runner.main(["--once", "--vault-root", str(vault)])
        assert rc == 1, "严格模式下仍然启动了 → 双机重复处理无闸"

    def test_lock_dir_is_never_in_sync_range(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """默认锁目录不在跨机同步范围内（否则两机互删锁）。"""
        from akb2.common import state_paths
        from src.akb2.quality.runner import RunnerConfig

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("AKB2_STATE_ROOT", raising=False)
        monkeypatch.delenv("AKB2_RUNNER_SHARDS", raising=False)
        config = RunnerConfig(
            raw_dir=str(tmp_path / "raw"),
            ledger_path=str(tmp_path / "l.jsonl"),
            staging_dir=str(tmp_path / "staging"),
            vault_root=str(tmp_path),
            failures_path=str(tmp_path / "f.jsonl"),
            grader_result_dir=str(tmp_path / "g"),
        )
        assert not state_paths.is_synced_path(config.lock_dir)
        assert Path(config.lock_dir) == (
            home / "Library" / "Application Support" / "akb2" / "locks"
        )


class TestShardKeyIsArticleId:
    """分片键必须是 `article_id`，不是整个文件名（2026-07-28 /simplify altitude 复核）。

    改前用整个文件名做键：文件名带 `--<date>--` 段，同一篇文章隔天被重新抓一次就
    产生两个名字 → 实测分别落 shard 2 与 shard 15 → 两台机器可能各占一片、同一
    内容仍被双跑，恰好是分片要防的那件事。`article_id` 由
    `compute_article_id(url)` 从 URL 派生，跨机跨日恒定。
    """

    def test_same_article_different_date_same_shard(self) -> None:
        from src.akb2.quality.runner import _shard_of

        d27 = "/v/raw/addy-osmani--a1b2c3d4e5f6--raw--2026-07-27.md"
        d28 = "/v/raw/addy-osmani--a1b2c3d4e5f6--raw--2026-07-28.md"
        assert _shard_of(d27) == _shard_of(d28), (
            "同一 article_id 跨日必须同片，否则两机各占一片 → 同一内容双跑"
        )

    def test_same_article_different_mount_point_same_shard(self) -> None:
        """两台机器 vault 挂载点不同时也必须同片（这是不用绝对路径的原因）。"""
        from src.akb2.quality.runner import _shard_of

        here = "/Volumes/AKB2/Obsidian/ws/raw/blog--deadbeef1234--raw--2026-07-28.md"
        there = "/mnt/other/ws/raw/blog--deadbeef1234--raw--2026-07-28.md"
        assert _shard_of(here) == _shard_of(there)

    def test_different_article_same_slug_different_key(self) -> None:
        """反向：同一 source 的两篇不同文章不得被并到同一键（否则分布退化）。"""
        from src.akb2.quality.runner import _shard_key

        a = "/v/raw/blog--aaaaaaaaaaaa--raw--2026-07-28.md"
        b = "/v/raw/blog--bbbbbbbbbbbb--raw--2026-07-28.md"
        assert _shard_key(a) != _shard_key(b)

    def test_slug_containing_double_dash_still_extracts_article_id(self) -> None:
        """source_slug 自身含 `--` 时仍取到 article_id（不能按固定下标取）。"""
        from src.akb2.quality.runner import _shard_key

        p = "/v/raw/some--weird--slug--cafebabe0001--raw--2026-07-28.md"
        assert _shard_key(p) == "cafebabe0001"

    def test_missing_raw_segment_falls_back_visibly(self, capsys) -> None:
        """不含 `--raw--` 时回退整个文件名并**明示**（不静默挤到同一片）。"""
        from src.akb2.quality.runner import _shard_key

        assert _shard_key("/v/raw/weird-name.md") == "weird-name.md"
        assert "不含 `--raw--` 段" in capsys.readouterr().err
