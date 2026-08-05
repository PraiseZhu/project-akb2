"""Render `docs/allowed-residuals.md` entries for PR-body copy-paste.

lead 生成 PR 正文时直接引用本脚本的输出（渲染文本 + 条目总数），不再手抄
`docs/allowed-residuals.md` 的内容，消除"声称已生效但运行态未生效"一类的
正文与文档脱节（SC-R4-04 / SC-R4-05）。

解析规则：
- `## ` 行是批次（batch）标题，之后所有 `### ` 条目归属该批次，直到下一个
  `## ` 或文件结束。
- `### ` 行是条目标题；条目正文为其后到下一个 `##`/`###` 之间的原始行。
- 条目总数由本脚本自己数 `### ` 行得出，不由任何人手写常量。
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "allowed-residuals.md"

_BATCH_RE = re.compile(r"^##\s+(?!#)(.+?)\s*$")
_ENTRY_RE = re.compile(r"^###\s+(?!#)(.+?)\s*$")


@dataclass(frozen=True)
class ResidualEntry:
    """一条 `### ` 级残留登记条目。"""

    title: str
    batch: str
    body: str


def parse_entries(doc_path: Path = DEFAULT_DOC_PATH) -> list[ResidualEntry]:
    """解析文档，返回所有 `### ` 级条目（含所属批次与正文）。"""
    text = doc_path.read_text(encoding="utf-8")

    entries: list[ResidualEntry] = []
    current_batch = ""
    current_title: str | None = None
    current_body_lines: list[str] = []

    def flush() -> None:
        if current_title is not None:
            entries.append(
                ResidualEntry(
                    title=current_title,
                    batch=current_batch,
                    body="\n".join(current_body_lines).strip(),
                )
            )

    for raw_line in text.splitlines():
        batch_match = _BATCH_RE.match(raw_line)
        if batch_match:
            flush()
            current_title = None
            current_body_lines = []
            current_batch = batch_match.group(1)
            continue

        entry_match = _ENTRY_RE.match(raw_line)
        if entry_match:
            flush()
            current_title = entry_match.group(1)
            current_body_lines = []
            continue

        if current_title is not None:
            current_body_lines.append(raw_line)

    flush()
    return entries


def render_entry(entry: ResidualEntry) -> str:
    """把单条条目渲染成 PR 正文可直接粘贴的一段文本。"""
    lines = [f"[{entry.batch}] {entry.title}"]
    if entry.body:
        lines.append(entry.body)
    return "\n".join(lines)


def render_summary(doc_path: Path = DEFAULT_DOC_PATH) -> str:
    """渲染文档内所有条目，供 PR 正文整段引用。"""
    entries = parse_entries(doc_path)
    return "\n\n".join(render_entry(entry) for entry in entries)


def count_entries(doc_path: Path = DEFAULT_DOC_PATH) -> int:
    """条目总数——由 `parse_entries` 解析文档得出，不硬编码。"""
    return len(parse_entries(doc_path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc-path", type=Path, default=DEFAULT_DOC_PATH)
    args = parser.parse_args()

    entries = parse_entries(args.doc_path)
    for entry in entries:
        print(render_entry(entry))
        print()
    print(f"共 {len(entries)} 条")


if __name__ == "__main__":
    main()
