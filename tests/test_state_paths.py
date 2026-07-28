"""缺陷 #11a: 机器本地状态根解析（状态/锁必须在跨机同步范围外）。

断言的可观测行为：
- 默认状态根落在 `~/Library/Application Support/akb2`，且不在同步范围内
- 状态根被指到同步范围（Syncthing folder / vault 卷 / iCloud）→ fail-closed 抛错
- 旧位置存在而新位置缺失 → 复制继承且旧文件仍在（不静默丢账本 → 不全量重跑）
- 迁移后旧位置仍被写（mtime 更新）→ 打印状态分裂告警（不静默）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from akb2.common import state_paths


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 HOME 与 vault 类环境变量，返回假 HOME。"""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(state_paths.STATE_ROOT_ENV, raising=False)
    for name in ("AKB2_ROOT", "AKB2_VAULT_ROOT", "VAULT_ROOT"):
        monkeypatch.delenv(name, raising=False)
    return home


class TestStateRootOutsideSyncRange:
    def test_default_state_root_is_application_support(self, isolated_env: Path) -> None:
        root = state_paths.get_state_root()
        assert root == isolated_env / "Library" / "Application Support" / "akb2"
        assert not state_paths.is_synced_path(root)

    def test_ledger_and_lock_dirs_under_state_root(self, isolated_env: Path) -> None:
        root = state_paths.get_state_root()
        assert state_paths.get_ledger_dir() == root / "ledgers"
        assert state_paths.get_lock_dir() == root / "locks"
        # 纯路径计算，不得建目录（避免 import 即产生副作用）
        assert not (root / "ledgers").exists()
        assert not (root / "locks").exists()

    def test_state_root_inside_syncthing_folder_rejected(
        self, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """状态根指进 ~/AI-Agent（Syncthing folder）→ 拒绝，不静默接受。"""
        monkeypatch.setenv(
            state_paths.STATE_ROOT_ENV, str(isolated_env / "AI-Agent" / "state")
        )
        with pytest.raises(RuntimeError, match="同步"):
            state_paths.get_state_root()

    def test_state_root_inside_vault_env_rejected(
        self, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """状态根指进 VAULT_ROOT（Obsidian 同步 vault）→ 拒绝。"""
        vault = isolated_env / "vault"
        monkeypatch.setenv("VAULT_ROOT", str(vault))
        monkeypatch.setenv(state_paths.STATE_ROOT_ENV, str(vault / "staging"))
        with pytest.raises(RuntimeError, match="同步"):
            state_paths.get_state_root()

    def test_icloud_and_volume_paths_are_synced(self, isolated_env: Path) -> None:
        assert state_paths.is_synced_path("/Volumes/AKB2/Obsidian/x.jsonl")
        assert state_paths.is_synced_path(
            isolated_env / "Library" / "Mobile Documents" / "x.jsonl"
        )

    def test_state_file_path_rejects_path_traversal(self, isolated_env: Path) -> None:
        with pytest.raises(ValueError):
            state_paths.state_file_path("../../etc/passwd")


class TestLegacyStateInheritance:
    def test_legacy_state_is_copied_and_kept(
        self, isolated_env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """旧账本存在 + 新位置缺失 → 复制继承；旧文件保留（历史是证据）。"""
        legacy = tmp_path / "legacy-staging" / "quality-ledger.jsonl"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text('{"event":"finalize"}\n', encoding="utf-8")

        target = state_paths.resolve_state_file(
            "quality-ledger.jsonl",
            subdir=state_paths.LEDGER_SUBDIR,
            legacy_path=legacy,
        )

        assert target.is_file(), "旧账本未被继承 → 全量重跑 / 重复入库"
        assert target.read_text(encoding="utf-8") == '{"event":"finalize"}\n'
        assert legacy.is_file(), "旧账本被删了 —— 历史是证据，只能复制不能搬走"
        assert "已继承旧状态" in capsys.readouterr().err

    def test_existing_target_not_overwritten(
        self, isolated_env: Path, tmp_path: Path
    ) -> None:
        legacy = tmp_path / "legacy-staging" / "quality-ledger.jsonl"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text('{"event":"legacy"}\n', encoding="utf-8")

        target = state_paths.state_file_path(
            "quality-ledger.jsonl", subdir=state_paths.LEDGER_SUBDIR
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"event":"local"}\n', encoding="utf-8")

        resolved = state_paths.resolve_state_file(
            "quality-ledger.jsonl",
            subdir=state_paths.LEDGER_SUBDIR,
            legacy_path=legacy,
        )
        assert resolved.read_text(encoding="utf-8") == '{"event":"local"}\n'

    def test_legacy_newer_than_target_warns_about_split(
        self, isolated_env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """旧位置仍被写（mtime 更新）→ 状态分裂必须可见（宪法 P4）。"""
        import os
        import time

        legacy = tmp_path / "legacy-staging" / "quality-ledger.jsonl"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text('{"event":"legacy"}\n', encoding="utf-8")

        target = state_paths.state_file_path(
            "quality-ledger.jsonl", subdir=state_paths.LEDGER_SUBDIR
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"event":"local"}\n', encoding="utf-8")
        old = time.time() - 600
        os.utime(target, (old, old))

        state_paths.resolve_state_file(
            "quality-ledger.jsonl",
            subdir=state_paths.LEDGER_SUBDIR,
            legacy_path=legacy,
        )
        assert "状态分裂" in capsys.readouterr().err
