"""隔离区与 staging 路径常量。

路径解析规则：机器本地 env/local override 优先，config/vault.toml 兜底。
使用 tomllib（Python 3.11+）正确解析 TOML 格式，替代 configparser。
tests 中用 AKB2_TEST_HOME 环境变量隔离，不接触真 vault。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

# ---------------------------------------------------------------------------
# 具名常量
# ---------------------------------------------------------------------------

# 隔离区子目录名（相对 staging 根）
QUARANTINE_SUBDIR = "quarantine"

# vault.toml 在本工程中的固定位置
_VAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "vault.toml"
_VAULT_LOCAL_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "vault.local.toml"
_AKB2_RELATIVE_PARTS = ("Praise Space", "AKB2.0")

# ---------------------------------------------------------------------------
# 懒加载缓存（单次 IO 后一并缓存全部解析值）
# ---------------------------------------------------------------------------

_vault_config_cache: dict[str, object] | None = None
_akb2_root_cache: Path | None = None
_staging_root_cache: Path | None = None


def _resolve_refs(value: str, akb2_root_resolved: str) -> str:
    """展开 value 中的 ${vault.akb2_root} 交叉引用和 ${HOME} 环境变量。

    tomllib 解析后 value 已经是干净的字符串（无外围引号），只需展开变量引用。
    """
    # 先展开 HOME（os.path.expandvars 处理 ${HOME}）
    expanded = os.path.expandvars(value)
    # 再展开 ${vault.akb2_root} 交叉引用
    expanded = expanded.replace("${vault.akb2_root}", akb2_root_resolved)
    return expanded


def _read_toml(path: Path) -> dict[str, object]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _read_vault_config() -> dict[str, object]:
    """读取 vault 配置（local override 优先，使用 tomllib 正确解析 TOML）。"""
    if _VAULT_LOCAL_CONFIG_PATH.exists():
        return _read_toml(_VAULT_LOCAL_CONFIG_PATH)
    return _read_toml(_VAULT_CONFIG_PATH)


def _normalise_akb2_root(raw_path: str) -> Path:
    """把 vault 父根或 AKB2 子根规范化为 AKB2.0 子根。"""
    expanded = Path(os.path.expandvars(raw_path)).expanduser()
    if expanded.name == "AKB2.0":
        return expanded.resolve()
    return expanded.joinpath(*_AKB2_RELATIVE_PARTS).resolve()


def _env_akb2_root() -> Path | None:
    """解析机器本地环境变量，返回 AKB2.0 子根。"""
    for name in ("AKB2_ROOT", "AKB2_VAULT_ROOT"):
        value = os.environ.get(name)
        if value:
            return _normalise_akb2_root(value)
    vault_root = os.environ.get("VAULT_ROOT")
    if vault_root:
        return _normalise_akb2_root(vault_root)
    return None


def _existing_machine_default() -> Path | None:
    """本机常见 vault 根。存在才启用，避免跨设备共享配置写错盘。"""
    candidates = [
        Path("/Volumes/AKB2/Obsidian").joinpath(*_AKB2_RELATIVE_PARTS),
        Path.home().joinpath("Obsidian", *_AKB2_RELATIVE_PARTS),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _ensure_config() -> Path:
    """确保 vault config 已读取并解析，返回解析后的 akb2_root Path。

    一次性完成：env/local/default/config → vault.akb2_root 解析 →
    交叉引用 ${vault.akb2_root} 展开 → 缓存两个 Path。
    """
    global _vault_config_cache, _akb2_root_cache, _staging_root_cache

    if _vault_config_cache is None:
        config = _read_vault_config()
        _vault_config_cache = config

        # 1. 读取原始 akb2_root（tomllib 已去掉外围引号），env/本机默认优先。
        vault = config.get("vault", {})
        assert isinstance(vault, dict)
        env_root = _env_akb2_root()
        default_root = _existing_machine_default()
        raw_akb2_root = str(vault.get("akb2_root", "")).strip()
        if env_root is not None:
            _akb2_root_cache = env_root
        elif default_root is not None:
            _akb2_root_cache = default_root
        elif raw_akb2_root:
            _akb2_root_cache = _normalise_akb2_root(raw_akb2_root)
        else:
            raise RuntimeError(
                "config/vault.toml: [vault] akb2_root 未设置，无法解析路径"
            )

        akb2_root_expanded = str(_akb2_root_cache)

        # 2. 读取 staging（含 ${vault.akb2_root} 交叉引用）
        layers = vault.get("layers", {})
        assert isinstance(layers, dict)
        raw_staging = str(layers.get("staging", ""))
        if not raw_staging:
            raise RuntimeError(
                "config/vault.toml: [vault.layers] staging 未设置，无法解析路径"
            )

        # 3. 展开 ${vault.akb2_root} 交叉引用 + ${HOME}
        staging_expanded = _resolve_refs(raw_staging, akb2_root_expanded)
        _staging_root_cache = Path(staging_expanded).resolve()

    assert _akb2_root_cache is not None
    return _akb2_root_cache


def get_akb2_root() -> Path:
    """获取 vault AKB2.0 根目录（缓存，tomllib 解析）。"""
    _ensure_config()
    assert _akb2_root_cache is not None
    return _akb2_root_cache


def get_staging_root() -> Path:
    """获取 staging 完整路径（缓存，tomllib 解析 + 交叉引用展开）。"""
    _ensure_config()
    assert _staging_root_cache is not None
    return _staging_root_cache


def get_quarantine_path() -> Path:
    """获取隔离区完整路径: <staging>/quarantine/。"""
    return get_staging_root() / QUARANTINE_SUBDIR
