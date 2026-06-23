"""Conclusion scanner — identifies explicit author conclusion sentences.

Produces kind=source_conclusion EvidenceItems. Conservative: only matches
sentences containing explicit conclusion connector phrases. Uncertain sentences
are left to the LLM.
"""

from __future__ import annotations

import re

from . import EvidenceItem
from ._common import make_item

# High-precision conclusion markers. A sentence containing any of these is
# likely an explicit author conclusion or core thesis statement.
_CONCLUSION_MARKERS: tuple[str, ...] = (
    # English definitive / thesis statements
    "is a stateful protocol",
    "The purpose of lifecycle management is",
    "The real power of",
    # English conclusion connectors
    "Therefore",
    "In conclusion",
    "The key insight",
    # Chinese conclusion connectors
    "因此",
    "综上",
    "核心是",
    "关键是",
    "总之",
)

_CONCLUSION_RE = re.compile(
    "|".join(re.escape(m) for m in _CONCLUSION_MARKERS),
    re.IGNORECASE,
)


def scan(raw_text: str) -> list[EvidenceItem]:
    """Scan for conclusion sentences containing explicit conclusion markers.

    For each marker match, expands to the containing sentence (handling common
    abbreviations), strips surrounding whitespace/quotes, and produces an
    EvidenceItem with kind=source_conclusion.
    """
    items: list[EvidenceItem] = []
    for m in _CONCLUSION_RE.finditer(raw_text):
        result = make_item(
            raw_text,
            kind="source_conclusion",
            marker_start=m.start(),
            marker_end=m.end(),
            source="conclusion_connector",
        )
        if result is None:
            continue
        literal, span, source = result
        items.append(
            EvidenceItem(
                kind="source_conclusion",
                literal=literal,
                raw_anchor_span=span,
                must_cover_class="source_conclusion",
                source=source,
            )
        )
    return items
