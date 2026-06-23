"""Source operations scanners — deterministic evidence inventory from raw text.

Builds a must_cover evidence inventory by scanning raw text (no LLM) in three
classes:

- source_operation: CLI commands, code blocks, API signatures, numbered steps,
  MCP protocol commands (migrated from distill.py:2412 _SOURCE_PROTOCOL_COMMANDS).
- source_boundary: explicit scope / precondition / limitation sentences.
- source_conclusion: explicit author conclusion sentences.

Each EvidenceItem carries a raw_anchor_span so the literal can be traced back to
its position in the raw text: raw_text[span.start:span.end] contains item.literal.

Public API: scan_all(raw_text) -> list[EvidenceItem].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MustCoverClass = Literal[
    "source_operation",
    "source_boundary",
    "source_conclusion",
]


@dataclass(frozen=True)
class Span:
    """Character offset span into raw_text.

    raw_text[start:end] yields the covered text. end is exclusive.
    """

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class EvidenceItem:
    """One piece of evidence that must be covered in the How section.

    Attributes:
        kind: one of source_operation / source_boundary / source_conclusion.
        literal: the command or sentence text as it appears in the raw.
        raw_anchor_span: Span into raw_text; raw_text[span.start:span.end]
            contains literal.
        must_cover_class: same as kind (reserved for future sub-classification).
        source: which scanner produced this (e.g. "mcp_protocol").
    """

    kind: MustCoverClass
    literal: str
    raw_anchor_span: Span
    must_cover_class: MustCoverClass
    source: str


def scan_all(raw_text: str) -> list[EvidenceItem]:
    """Aggregate entry: run all scanners, dedupe by (kind, literal), sort by span.

    Dedup keeps the earliest span for each (kind, literal) pair so that the same
    operation mentioned multiple times in the raw is reported once, anchored to
    its first occurrence.
    """
    # Late imports to avoid circular import (submodules import Span/EvidenceItem
    # from this module; they are already defined above when submodules load).
    from . import boundaries, conclusions, operations

    items: list[EvidenceItem] = []
    items.extend(operations.scan(raw_text))
    items.extend(boundaries.scan(raw_text))
    items.extend(conclusions.scan(raw_text))

    seen: dict[tuple[str, str], EvidenceItem] = {}
    for item in items:
        key = (item.kind, item.literal)
        existing = seen.get(key)
        if existing is None or item.raw_anchor_span.start < existing.raw_anchor_span.start:
            seen[key] = item
    return sorted(seen.values(), key=lambda x: (x.raw_anchor_span.start, x.kind, x.literal))


__all__ = [
    "EvidenceItem",
    "Span",
    "MustCoverClass",
    "scan_all",
]
