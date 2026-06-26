"""VideoConfig — 视频管线配置管理。

解决方案 #5（硬编码路径）：所有外部依赖路径通过 dataclass 注入，
从环境变量或 vault.toml 加载，不硬编码任何绝对路径。

配置加载顺序：环境变量 > vault.toml > 默认值。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def _resolve_ytdlp(default_bin: str = "yt-dlp") -> str:
    """定位 yt-dlp 可执行路径（避免 LaunchAgent / shell 的 PATH 差异）。

    顺序：环境变量 YTDLP_BIN → PATH 中的 yt-dlp → 常见安装位置。
    全部找不到时返回 default_bin，让 subprocess 抛出可读的 FileNotFoundError。
    """
    env_bin = os.environ.get("YTDLP_BIN")
    if env_bin and Path(env_bin).exists():
        return env_bin

    found = shutil.which(default_bin)
    if found:
        return found

    candidates = [
        Path.home() / ".pyenv/versions/3.13.12/bin/yt-dlp",
        Path("/opt/homebrew/bin/yt-dlp"),
        Path("/usr/local/bin/yt-dlp"),
        Path.home() / ".local/bin/yt-dlp",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return default_bin


def _resolve_llm_api_key() -> str | None:
    """读取 LLM API key：env AKB2_LLM_API_KEY → keychain claude-ai-gateway → None。"""
    env_key = os.environ.get("AKB2_LLM_API_KEY")
    if env_key:
        return env_key
    try:
        import subprocess as _sp
        result = _sp.run(
            ["security", "find-generic-password", "-s", "claude-ai-gateway", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _resolve_nlm() -> str:
    """定位 NLM CLI 可执行路径。"""
    env_bin = os.environ.get("NLM_BIN")
    if env_bin and Path(env_bin).exists():
        return env_bin
    found = shutil.which("nlm")
    if found:
        return found
    default = Path.home() / ".local/bin/nlm"
    return str(default) if default.exists() else "nlm"


def _resolve_akb2_root() -> Path:
    """解析 AKB2.0 子库根：env / 本机默认 / vault config。"""
    try:
        from src.akb2.loops.paths import get_akb2_root

        return get_akb2_root()
    except Exception:
        env_value = os.environ.get("AKB2_ROOT") or os.environ.get("AKB2_VAULT_ROOT")
        if env_value:
            return Path(env_value).expanduser().resolve()
        vault_root = os.environ.get("VAULT_ROOT")
        if vault_root:
            return Path(vault_root).expanduser().joinpath(
                "Praise Space", "AKB2.0"
            ).resolve()
        return Path.home().joinpath("Obsidian", "Praise Space", "AKB2.0").resolve()


@dataclass
class VideoConfig:
    """视频管线配置 — 所有外部依赖路径在此注入。"""

    # -- 二进制路径 --
    ytdlp_bin: str = field(default_factory=_resolve_ytdlp)
    nlm_bin: str = field(default_factory=_resolve_nlm)

    # -- 配置文件路径 --
    video_sources_path: Path = field(default_factory=lambda: Path("config/video_sources.json"))
    keywords_path: Path = field(default_factory=lambda: Path("config/keywords.json"))

    # -- AKB2 Obsidian vault 路径 --
    akb2_root: Path = field(default_factory=_resolve_akb2_root)

    # -- staging 目录 --
    staging_dir: Path = field(default_factory=lambda: Path("staging/candidate"))

    # -- pending 文件路径 --
    pending_filename: str = "pending_research.json"

    # -- cookies 路径 --
    cookies_path: Path | None = None  # None = 自动探测

    # -- NLM profile --
    nlm_profile: str = "default"
    nlm_alt_profile: str = "alt"

    # -- LLM router 键（用于 L5 话题去重和降级摘要）--
    llm_router_key: str = "default"

    # -- LLM adapter 配置（gateway + deepseek v4pro）--
    # key 优先级：env AKB2_LLM_API_KEY → keychain claude-ai-gateway → None（无 key 降级）
    llm_api_key: str | None = field(default_factory=_resolve_llm_api_key)
    llm_base_url: str = field(default_factory=lambda: os.environ.get(
        "AKB2_LLM_BASE_URL", "https://llm-proxy.tapsvc.com/v1"
    ))
    llm_model: str = field(default_factory=lambda: os.environ.get(
        "AKB2_LLM_MODEL", "deepseek/deepseek-v4-pro"
    ))

    # -- L4 promotion 目标路径（必须指向已有目录，不新建）--
    @property
    def promotion_target_path(self) -> Path:
        """L4 promotion 目标路径：<AKB2_ROOT>/AI TRENDS/Video/"""
        return self.akb2_root / "AI TRENDS" / "Video"

    @property
    def obsidian_research_dir(self) -> Path:
        """Obsidian research 目录（Researcher 参考，不再写入）。"""
        return self.akb2_root / "research"

    def resolve_pending_path(self) -> Path:
        """返回 pending_research.json 的完整路径。"""
        return self.staging_dir.parent / self.pending_filename
