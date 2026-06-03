"""Markdown → 飞书 lark_md 格式转换（自 Project Doki llm/lark_md.py 原样移植，纯函数）。"""

from __future__ import annotations

import re


def markdown_to_lark_md(text: str) -> str:
    """将标准 Markdown 转换为飞书卡片支持的 lark_md 格式。

    - 标题 `# ...` → `**加粗**`（飞书卡片 markdown 不渲染标题标签）
    - 表格行 → 纯文本（飞书卡片 markdown 不支持表格），跳过分隔行
    - 加粗/斜体/链接 → 保持不变
    """
    lines = text.split("\n")
    converted = []

    for line in lines:
        heading_match = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading_match:
            title = heading_match.group(2).strip()
            converted.append(f"**{title}**")
            continue

        if line.strip().startswith("|") and line.strip().endswith("|"):
            if re.match(r"^\|[\s\-:|]+\|$", line.strip()):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            cells = [c for c in cells if c]
            if cells:
                converted.append("  ".join(cells))
            continue

        converted.append(line)

    return "\n".join(converted)


def split_for_card(text: str, max_length: int = 3000) -> list[str]:
    """文本过长时按行拆分为多块（飞书卡片单个 markdown 元素有长度上限）。"""
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_length:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line

    if current:
        chunks.append(current)

    return chunks
