"""Boundary scanner — identifies explicit scope/precondition/limitation sentences.

Produces kind=source_boundary EvidenceItems. Conservative: only matches sentences
containing explicit boundary connector words (English and Chinese). Sentences
without these connectors are left to the LLM.
"""

from __future__ import annotations

import re

from . import EvidenceItem
from ._common import make_item

# High-precision boundary markers. A sentence containing any of these is likely
# an explicit scope limit, precondition, or exclusion statement.
_BOUNDARY_MARKERS: tuple[str, ...] = (
    # English scope limitations
    "focuses solely",
    "does not dictate",
    # English preconditions / failure conditions
    "should be terminated",
    "prior to execution",
    "require user consent",
    "requiring explicit invocation",
    # English exclusions
    "rather than automatic",
    # Chinese connectors
    "仅适用",
    "仅限",
    "不支持",
    "限于",
    "前提是",
    "必须先",
)

_BOUNDARY_RE = re.compile(
    "|".join(re.escape(m) for m in _BOUNDARY_MARKERS),
    re.IGNORECASE,
)


def scan(raw_text: str) -> list[EvidenceItem]:
    """Scan for boundary sentences containing explicit connector markers.

    For each marker match, expands to the containing sentence (handling common
    abbreviations), strips surrounding whitespace/quotes, and produces an
    EvidenceItem with kind=source_boundary.
    """
    items: list[EvidenceItem] = []
    for m in _BOUNDARY_RE.finditer(raw_text):
        result = make_item(
            raw_text,
            kind="source_boundary",
            marker_start=m.start(),
            marker_end=m.end(),
            source="boundary_connector",
        )
        if result is None:
            continue
        literal, span, source = result
        items.append(
            EvidenceItem(
                kind="source_boundary",
                literal=literal,
                raw_anchor_span=span,
                must_cover_class="source_boundary",
                source=source,
            )
        )
    return items
