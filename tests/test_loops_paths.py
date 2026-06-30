"""loops.paths resolver tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.akb2.loops import paths


def _reset_paths_cache() -> None:
    paths._vault_config_cache = None
    paths._akb2_root_cache = None
    paths._staging_root_cache = None


def test_akb2_root_env_overrides_shared_vault_toml(monkeypatch, tmp_path):
    root = tmp_path / "Obsidian" / "Praise Space" / "AKB2.0"
    root.mkdir(parents=True)
    monkeypatch.setenv("AKB2_ROOT", str(root))
    _reset_paths_cache()
    try:
        assert paths.get_akb2_root() == root.resolve()
        assert paths.get_staging_root() == root.resolve() / "staging"
    finally:
        _reset_paths_cache()


def test_vault_root_env_derives_akb2_child_root(monkeypatch, tmp_path):
    vault_root = tmp_path / "Obsidian"
    akb2_root = vault_root / "Praise Space" / "AKB2.0"
    akb2_root.mkdir(parents=True)
    monkeypatch.delenv("AKB2_ROOT", raising=False)
    monkeypatch.delenv("AKB2_VAULT_ROOT", raising=False)
    monkeypatch.setenv("VAULT_ROOT", str(vault_root))
    _reset_paths_cache()
    try:
        assert paths.get_akb2_root() == akb2_root.resolve()
        assert paths.get_staging_root() == akb2_root.resolve() / "staging"
    finally:
        _reset_paths_cache()


# ---------------------------------------------------------------------------
# 路径陷阱回归（iCloud / ~/Obsidian）：fail-closed，绝不静默写错盘
# ---------------------------------------------------------------------------


def test_machine_default_only_volumes_no_home_fallback(monkeypatch):
    """machine default 只认 /Volumes/AKB2；卷未挂载时返回 None，绝不回退 ~/Obsidian。

    断言绑定可观测行为：即使 ~/Obsidian/Praise Space/AKB2.0 真实存在，且 /Volumes/AKB2
    未挂载，_existing_machine_default 也必须返回 None（旧实现会回退到 home → iCloud 老库）。
    """
    home_akb2 = Path.home().joinpath("Obsidian", "Praise Space", "AKB2.0")

    def fake_exists(p: Path) -> bool:
        # /Volumes/AKB2 视为未挂载；~/Obsidian 视为存在
        return str(p).startswith(str(home_akb2))

    monkeypatch.setattr(paths, "_path_exists", fake_exists)
    assert paths._existing_machine_default() is None, "卷未挂载时不应回退 ~/Obsidian"


def test_vault_toml_fallback_unmounted_volume_fails_closed(monkeypatch):
    """env 无 + machine default 无 + vault.toml 指向未挂载 /Volumes 卷 → fail-closed RuntimeError。

    不允许静默把路径解析成未挂载卷（否则 mkdir 会在启动盘建影子目录）。
    """
    monkeypatch.delenv("AKB2_ROOT", raising=False)
    monkeypatch.delenv("AKB2_VAULT_ROOT", raising=False)
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    # 模拟 /Volumes/AKB2 未挂载：所有存在性检查返回 False
    monkeypatch.setattr(paths, "_path_exists", lambda p: False)
    _reset_paths_cache()
    try:
        with pytest.raises(RuntimeError, match="未挂载的卷"):
            paths.get_akb2_root()
    finally:
        _reset_paths_cache()
