"""隔离区与 staging 路径常量。

路径解析规则：vault 根由 config/vault.toml 解析，禁止硬编码。
tests 中用 AKB2_TEST_HOME 环境变量隔离，不接触真 vault。
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 具名常量
# ---------------------------------------------------------------------------

# 隔离区子目录名（相对 staging 根）
QUARANTINE_SUBDIR = "quarantine"

# vault.toml 在本工程中的固定位置
_VAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "vault.toml"


def _read_vault_config() -> configparser.ConfigParser:
    """读取 vault.toml 配置（单次 IO，被两个 resolver 共用）。"""
    config = configparser.ConfigParser()
    config.read(_VAULT_CONFIG_PATH)
    return config


def _resolve_akb2_root(config: configparser.ConfigParser) -> Path:
    """解析 vault.toml 中的 akb2_root，支持 ${HOME} 展开。"""
    raw = config.get("vault", "akb2_root", fallback="")
    resolved = os.path.expandvars(raw)
    return Path(resolved).resolve()


def _resolve_staging_root(config: configparser.ConfigParser) -> Path:
    """解析 staging 完整路径。"""
    raw = config.get("vault.layers", "staging", fallback="")
    resolved = os.path.expandvars(raw)
    return Path(resolved).resolve()


# 懒加载缓存（单次 config.read() 后两个值一并缓存）
_vault_config_cache: configparser.ConfigParser | None = None
_akb2_root_cache: Path | None = None
_staging_root_cache: Path | None = None


def _ensure_config() -> configparser.ConfigParser:
    """确保 vault config 已读取（懒加载，单次 IO）。"""
    global _vault_config_cache
    if _vault_config_cache is None:
        _vault_config_cache = _read_vault_config()
    return _vault_config_cache


def get_akb2_root() -> Path:
    """获取 vault AKB2.0 根目录（缓存）。"""
    global _akb2_root_cache
    if _akb2_root_cache is None:
        _akb2_root_cache = _resolve_akb2_root(_ensure_config())
    return _akb2_root_cache


def get_staging_root() -> Path:
    """获取 staging 完整路径（缓存）。"""
    global _staging_root_cache
    if _staging_root_cache is None:
        _staging_root_cache = _resolve_staging_root(_ensure_config())
    return _staging_root_cache


def get_quarantine_path() -> Path:
    """获取隔离区完整路径: <staging>/quarantine/。"""
    return get_staging_root() / QUARANTINE_SUBDIR
