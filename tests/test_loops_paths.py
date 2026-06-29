"""loops.paths resolver tests."""

from __future__ import annotations

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
