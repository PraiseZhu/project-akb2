"""Shared utilities for source_ops_scanners.

Provides sentence-expansion and fenced-code-block detection helpers used by
boundaries.py and conclusions.py. Only depends on Span from the package root.
"""

from __future__ import annotations

import re

from . import Span

# Fenced code block: ```lang\n body ```
_FENCED_BLOCK_RE = re.compile(r"```(?P<lang>[^\n`]*)\n(?P<body>.*?)```", re.DOTALL)

# Common abbreviations that contain a period but should NOT split sentences.
_ABBREV_TAIL_RE = re.compile(r"(?:e\.g|i\.e|etc)\.$", re.IGNORECASE)


def fenced_block_spans(raw_text: str) -> list[tuple[int, int]]:
    """Return (start, end) char ranges of all fenced code blocks in raw_text."""
    return [(m.start(), m.end()) for m in _FENCED_BLOCK_RE.finditer(raw_text)]


def is_in_fenced_block(pos: int, block_spans: list[tuple[int, int]]) -> bool:
    """True if char position pos falls inside any fenced code block range."""
    return any(start <= pos < end for start, end in block_spans)


def expand_to_sentence(raw_text: str, match_start: int, match_end: int) -> Span:
    """Expand a marker match to the containing sentence.

    Expands backwards to the previous sentence terminator (. or 。 or newline),
    skipping common abbreviations (e.g., i.e., etc.). Expands forwards to the
    next sentence terminator (inclusive of the terminating period).

    Returns a Span where raw_text[start:end] is the sentence (possibly with
    surrounding whitespace/quotes that callers should strip).
    """
    start = _expand_backwards(raw_text, match_start)
    end = _expand_forwards(raw_text, match_end)
    return Span(start=start, end=min(end, len(raw_text)))


def _expand_backwards(raw_text: str, pos: int) -> int:
    start = pos
    while start > 0:
        prev = raw_text[start - 1]
        if prev in "\n。":
            break
        if prev == ".":
            # Skip abbreviation periods like "e.g." / "i.e." / "etc."
            if start >= 4:
                snippet = raw_text[start - 4 : start]
                if _ABBREV_TAIL_RE.search(snippet):
                    start -= 1
                    continue
            break
        start -= 1
    return start


def _expand_forwards(raw_text: str, pos: int) -> int:
    end = pos
    n = len(raw_text)
    while end < n:
        curr = raw_text[end]
        if curr in "\n。":
            break
        if curr == ".":
            end += 1  # include the terminating period
            break
        end += 1
    return min(end, n)


def make_item(
    raw_text: str,
    kind: str,
    marker_start: int,
    marker_end: int,
    source: str,
) -> tuple[str, Span, str] | None:
    """Expand a marker match to its sentence, strip, and recompute exact span.

    Returns (literal, span, source) or None if the sentence is empty after
    stripping. The span is adjusted so that raw_text[span.start:span.end] ==
    literal (exact, no surrounding whitespace or quotes).
    """
    span = expand_to_sentence(raw_text, marker_start, marker_end)
    raw_slice = raw_text[span.start : span.end]
    # Strip whitespace, blockquote markers, and surrounding quotes together
    # so that '> "sentence."' becomes 'sentence.'.
    literal = raw_slice.strip(' \t\r\n>"')
    if not literal:
        return None
    # Recompute exact span for the stripped literal within the original slice.
    offset = raw_slice.find(literal)
    if offset == -1:
        return literal, span, source
    exact_start = span.start + offset
    return literal, Span(start=exact_start, end=exact_start + len(literal)), source
