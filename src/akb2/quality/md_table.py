r"""Markdown 表格 cell 转义/拆分——单一 `\|` 契约(写/读两半)。

distill 成稿时 cell 含 `|` 会拆列 → A4 假绿(>4 列却报零缺陷)或畸形表。
本模块集中:
    - md_table_cell(value): 写侧,`|` → `\|`、换行压空格
    - split_md_table_row(line): 读侧,按非转义 `|` 切分 + 反转义

读写两侧共享同一契约,改一处即可(避免 distill/checks_structural 各持一份分叉)。
"""

from __future__ import annotations

import re
from typing import Any

# 按"非转义的 |"切分(转义的 \| 是 cell 内字面量,不拆列)
_TABLE_SPLIT_RE = re.compile(r"(?<!\\)\|")


def md_table_cell(value: Any) -> str:
    """转义 Markdown 表格 cell:`|` → `\\|`,换行 → 空格。"""
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def split_md_table_row(line: str) -> list[str]:
    """按非转义 `|` 切分表格行,返回去首尾空段后的 cell 列表(已反转义 \\| → |)。"""
    parts = _TABLE_SPLIT_RE.split(line)
    return [c.strip().replace("\\|", "|") for c in parts[1:-1]]

