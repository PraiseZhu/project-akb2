"""VideoConfig 测试 — 外部依赖路径解析 fallback 链 + 派生属性。

覆盖核心逻辑（绑定真实 fallback 行为，非 mock 自证）：
- _resolve_ytdlp：env → which → 候选位置 → default 四级回退
- _resolve_llm_api_key：env → keychain → None
- _resolve_nlm：env → which → default
- promotion_target_path / obsidian_research_dir / resolve_pending_path 派生

外部依赖（shutil.which / subprocess / Path.exists）全部 mock，不触真实环境。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.video.config import (
    VideoConfig,
    _resolve_akb2_root,
    _resolve_llm_api_key,
    _resolve_nlm,
    _resolve_ytdlp,
)

# ---------------------------------------------------------------------------
# _resolve_ytdlp：env → which → candidates → default
# ---------------------------------------------------------------------------


def test_resolve_ytdlp_env_override(monkeypatch, tmp_path):
    """YTDLP_BIN 指向存在的文件 → 直接返回该路径。"""
    fake_bin = tmp_path / "yt-dlp"
    fake_bin.write_text("#!/bin/sh\n")
    monkeypatch.setenv("YTDLP_BIN", str(fake_bin))
    assert _resolve_ytdlp() == str(fake_bin)


def test_resolve_ytdlp_env_missing_falls_to_which(monkeypatch):
    """YTDLP_BIN 指向不存在路径 → 跳过 env，用 PATH 中的 which 结果。"""
    monkeypatch.setenv("YTDLP_BIN", "/nonexistent/yt-dlp")
    with mock.patch("src.akb2.video.config.shutil.which", return_value="/usr/bin/yt-dlp"):
        assert _resolve_ytdlp() == "/usr/bin/yt-dlp"


def test_resolve_ytdlp_candidates_fallback(monkeypatch):
    """env 无 + which 无 → 命中候选安装位置。"""
    monkeypatch.delenv("YTDLP_BIN", raising=False)
    with mock.patch("src.akb2.video.config.shutil.which", return_value=None):
        with mock.patch("src.akb2.video.config.Path.exists", return_value=True):
            result = _resolve_ytdlp()
    # Path.exists 全 True → 按候选顺序命中第一个（~/.pyenv/.../bin/yt-dlp）
    expected_first = str(Path.home() / ".pyenv/versions/3.13.12/bin/yt-dlp")
    assert result == expected_first
    assert result != "yt-dlp"  # 不是 default 兜底


def test_resolve_ytdlp_default_when_nothing(monkeypatch):
    """env / which / 候选全无 → 返回 default_bin。"""
    monkeypatch.delenv("YTDLP_BIN", raising=False)
    with mock.patch("src.akb2.video.config.shutil.which", return_value=None):
        with mock.patch("src.akb2.video.config.Path.exists", return_value=False):
            assert _resolve_ytdlp("yt-dlp") == "yt-dlp"


# ---------------------------------------------------------------------------
# _resolve_llm_api_key：env → keychain → None
# ---------------------------------------------------------------------------


def test_resolve_llm_api_key_env(monkeypatch):
    """AKB2_LLM_API_KEY 存在 → 直接返回。"""
    monkeypatch.setenv("AKB2_LLM_API_KEY", "sk-from-env")
    assert _resolve_llm_api_key() == "sk-from-env"


def test_resolve_llm_api_key_keychain(monkeypatch):
    """env 无 → keychain security 命中。"""
    monkeypatch.delenv("AKB2_LLM_API_KEY", raising=False)
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="sk-from-keychain\n", stderr=""
    )
    # 源码内是 `import subprocess as _sp; _sp.run(...)`，_sp 是局部别名，
    # 无法直接 patch _sp.run；patch subprocess.run 才命中（_sp.run 与之同一引用）。
    with mock.patch("subprocess.run", return_value=completed):
        assert _resolve_llm_api_key() == "sk-from-keychain"


def test_resolve_llm_api_key_none_when_keychain_fails(monkeypatch):
    """env 无 + keychain 异常 → 返回 None（不抛异常）。"""
    monkeypatch.delenv("AKB2_LLM_API_KEY", raising=False)
    with mock.patch("subprocess.run", side_effect=FileNotFoundError("no security")):
        assert _resolve_llm_api_key() is None


def test_resolve_llm_api_key_none_when_keychain_empty(monkeypatch):
    """env 无 + keychain 返回非零码 → None。"""
    monkeypatch.delenv("AKB2_LLM_API_KEY", raising=False)
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not found")
    with mock.patch("subprocess.run", return_value=completed):
        assert _resolve_llm_api_key() is None


# ---------------------------------------------------------------------------
# _resolve_nlm：env → which → default
# ---------------------------------------------------------------------------


def test_resolve_nlm_env(monkeypatch, tmp_path):
    """NLM_BIN 指向存在文件 → 返回该路径。"""
    fake = tmp_path / "nlm"
    fake.write_text("")
    monkeypatch.setenv("NLM_BIN", str(fake))
    assert _resolve_nlm() == str(fake)


def test_resolve_nlm_which(monkeypatch):
    """env 无 → which 命中。"""
    monkeypatch.delenv("NLM_BIN", raising=False)
    with mock.patch("src.akb2.video.config.shutil.which", return_value="/usr/bin/nlm"):
        assert _resolve_nlm() == "/usr/bin/nlm"


def test_resolve_nlm_default(monkeypatch):
    """env / which / 默认位置全无 → 返回 'nlm'。"""
    monkeypatch.delenv("NLM_BIN", raising=False)
    with mock.patch("src.akb2.video.config.shutil.which", return_value=None):
        with mock.patch("src.akb2.video.config.Path.exists", return_value=False):
            assert _resolve_nlm() == "nlm"


# ---------------------------------------------------------------------------
# 派生属性
# ---------------------------------------------------------------------------


def test_promotion_target_path(tmp_path):
    """promotion_target_path = <akb2_root>/AI TRENDS/Video。"""
    config = VideoConfig(akb2_root=tmp_path)
    assert config.promotion_target_path == tmp_path / "AI TRENDS" / "Video"


def test_obsidian_research_dir(tmp_path):
    """obsidian_research_dir = <akb2_root>/research。"""
    config = VideoConfig(akb2_root=tmp_path)
    assert config.obsidian_research_dir == tmp_path / "research"


def test_resolve_pending_path(tmp_path):
    """resolve_pending_path = staging_dir.parent / pending_research.json。"""
    config = VideoConfig(staging_dir=tmp_path / "staging" / "candidate")
    assert config.resolve_pending_path() == tmp_path / "staging" / "pending_research.json"


def test_config_defaults_constructible():
    """默认构造不抛异常（field default_factory 全部可解析）。"""
    config = VideoConfig()
    assert config.pending_filename == "pending_research.json"
    assert config.nlm_profile == "default"
    assert isinstance(config.video_sources_path, Path)


# ---------------------------------------------------------------------------
# _resolve_akb2_root：fail-closed，不回退 ~/Obsidian（iCloud 老库陷阱）
# ---------------------------------------------------------------------------


def test_resolve_akb2_root_env_vault_root(monkeypatch, tmp_path):
    """get_akb2_root 不可用但有 VAULT_ROOT → 用 env 派生 AKB2.0 子根。"""
    def boom():
        raise RuntimeError("volume unmounted")

    monkeypatch.setattr("src.akb2.loops.paths.get_akb2_root", boom)
    monkeypatch.delenv("AKB2_ROOT", raising=False)
    monkeypatch.delenv("AKB2_VAULT_ROOT", raising=False)
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "Obsidian"))
    assert _resolve_akb2_root() == (tmp_path / "Obsidian" / "Praise Space" / "AKB2.0").resolve()


def test_resolve_akb2_root_fail_closed_no_env(monkeypatch):
    """get_akb2_root 不可用 + 无任何 env → fail-closed RuntimeError，绝不回退 ~/Obsidian。

    锁死 iCloud 陷阱回归：宁可抛错也不静默把视频产物写进 ~/Obsidian（历史 iCloud 软链）。
    """
    def boom():
        raise RuntimeError("volume unmounted")

    monkeypatch.setattr("src.akb2.loops.paths.get_akb2_root", boom)
    monkeypatch.delenv("AKB2_ROOT", raising=False)
    monkeypatch.delenv("AKB2_VAULT_ROOT", raising=False)
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="iCloud"):
        _resolve_akb2_root()
