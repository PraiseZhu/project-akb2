"""AKB2 只读检索引擎（promoted 笔记关键词检索）。

形态：仓内引擎 + CLI（search / serve）+ 可选 HTTP（token 保护）。
"""

from akb2.retrieval.engine import (
    NoteDocument,
    SearchResult,
    extract_executable_commands,
    extract_how_section,
    is_promoted,
    read_promoted,
    search,
)

__all__ = [
    "NoteDocument",
    "SearchResult",
    "extract_executable_commands",
    "extract_how_section",
    "is_promoted",
    "read_promoted",
    "search",
]
