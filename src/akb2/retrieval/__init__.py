"""AKB2 只读检索引擎（promoted 笔记关键词检索）。

MVP 形态：仓内引擎 + CLI（python3 -m akb2.retrieval.cli search）。
"""

from akb2.retrieval.engine import (
    SearchResult,
    extract_executable_commands,
    extract_how_section,
    is_promoted,
    search,
)

__all__ = [
    "SearchResult",
    "extract_executable_commands",
    "extract_how_section",
    "is_promoted",
    "search",
]
