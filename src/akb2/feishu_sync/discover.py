"""发现当天简报 — 扫 vault 两个目录，解析 frontmatter。

行为（Codex P2 / SC-2、SC-5、SC-7）：
- 文件缺失 → 不在 briefings 里；若该 kind 在 expected 中 → 计入 missing_expected
  （上层 strict 模式据此 fail-visible，不再静默跳过）
- 文件**存在但解析失败** → 计入 parse_errors（上层 exit 1 + 告警），不静默
- expected=None（默认）→ 不校验缺失（开发/历史日期/单测用）
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .state import Briefing

_AKB2 = ("Praise Space", "AKB2.0", "AI TRENDS")

# 生产默认期望齐全的两份简报（SC-7）
DEFAULT_EXPECTED: tuple[str, ...] = ("ai_trends", "github")


def ai_trends_path(vault_root: Path, date: str) -> Path:
    return vault_root.joinpath(*_AKB2, "Social", f"{date} Daily Briefing.md")


def github_md_path(vault_root: Path, date: str) -> Path:
    return vault_root.joinpath(*_AKB2, "GitHub", f"{date} GitHub Briefing.md")


def github_json_path(vault_root: Path, date: str) -> Path:
    return vault_root.joinpath(*_AKB2, "GitHub", f"{date} GitHub Briefing.json")


def expected_path(vault_root: Path, date: str, kind: str) -> Path | None:
    """期望简报的落点路径（用于缺失错误文案）。"""
    if kind == "ai_trends":
        return ai_trends_path(vault_root, date)
    if kind == "github":
        return github_md_path(vault_root, date)
    return None


def split_frontmatter(text: str) -> tuple[dict, str]:
    """拆 `---\\n<yaml>\\n---\\n<body>`，返回 (frontmatter, body)。malformed 抛 ValueError。"""
    if not text.lstrip().startswith("---"):
        raise ValueError("缺少 frontmatter 起始 ---")
    stripped = text.lstrip()
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        raise ValueError("frontmatter 缺少结束 ---")
    fm = yaml.safe_load(parts[1])
    if not isinstance(fm, dict):
        raise ValueError("frontmatter 非 mapping")
    body = parts[2].lstrip("\n")
    return fm, body


def _load_ai_trends(path: Path, date: str) -> Briefing:
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return Briefing(
        kind="ai_trends",
        date=date,
        path=path,
        title=f"AI 趋势日报 · {date}",
        frontmatter=fm,
        body=body,
    )


def _load_github(md_path: Path, json_path: Path, date: str) -> Briefing:
    fm, body = split_frontmatter(md_path.read_text(encoding="utf-8"))
    json_data = None
    if json_path.exists():
        json_data = json.loads(json_path.read_text(encoding="utf-8"))
    return Briefing(
        kind="github",
        date=date,
        path=md_path,
        title=f"GitHub 推荐 · {date}",
        frontmatter=fm,
        body=body,
        json_data=json_data,
    )


def discover(
    vault_root: Path,
    date: str,
    *,
    expected: tuple[str, ...] | list[str] | None = None,
) -> tuple[list[Briefing], list[str], list[tuple[str, str]]]:
    """返回 (briefings, parse_errors, missing_expected)。

    - briefings: 已解析的简报
    - parse_errors: 存在但解析失败的错误文案
    - missing_expected: expected 中缺失的 (kind, 期望路径) 列表（仅当 expected 非 None）
    """
    briefings: list[Briefing] = []
    parse_errors: list[str] = []
    found_kinds: set[str] = set()

    social = ai_trends_path(vault_root, date)
    if social.exists():
        try:
            briefings.append(_load_ai_trends(social, date))
            found_kinds.add("ai_trends")
        except Exception as exc:  # noqa: BLE001 — 已存在文件解析失败必须上报
            parse_errors.append(f"ai_trends 解析失败 ({social.name}): {exc}")

    gh_md = github_md_path(vault_root, date)
    if gh_md.exists():
        try:
            briefings.append(_load_github(gh_md, github_json_path(vault_root, date), date))
            found_kinds.add("github")
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"github 解析失败 ({gh_md.name}): {exc}")

    missing_expected: list[tuple[str, str]] = []
    if expected:
        for kind in expected:
            if kind not in found_kinds:
                path = expected_path(vault_root, date, kind)
                missing_expected.append((kind, str(path) if path else ""))

    return briefings, parse_errors, missing_expected
