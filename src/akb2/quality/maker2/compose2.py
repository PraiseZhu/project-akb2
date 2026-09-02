"""loop2 compose shell.

Stage 1: frontmatter budget + assemble. extract_contract is a sibling
module (stage 3). fixpoint lands in stage 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .frontmatter_budget import (
    FrontmatterBudgetError,
    apply_frontmatter_budget,
    dump_frontmatter,
)


@dataclass
class Compose2Result:
    success: bool
    candidate_path: Path | None
    frontmatter: dict[str, Any]
    error: str
    frontmatter_degraded: list[str] = field(default_factory=list)
    pending_body: str = ""
    page_text: str = ""


def assemble_page(frontmatter: dict[str, Any], body: str) -> str:
    dumped = dump_frontmatter(frontmatter).rstrip() + "\n"
    body_text = body.lstrip("\n")
    if body_text and not body_text.endswith("\n"):
        body_text += "\n"
    return dumped + "\n" + body_text


def compose_candidate(
    frontmatter: dict[str, Any],
    body: str,
    *,
    candidate_path: Path | None = None,
    write: bool = False,
) -> Compose2Result:
    """Budget frontmatter, assemble the page, optionally write.

    Does not fork distill.compose_candidate. Caller (runner2) owns vault IO.
    """
    try:
        budgeted, degraded, pending = apply_frontmatter_budget(frontmatter)
    except FrontmatterBudgetError as exc:
        return Compose2Result(
            success=False,
            candidate_path=None,
            frontmatter=dict(frontmatter),
            error=str(exc),
            frontmatter_degraded=[],
        )

    page_body = body
    if pending:
        page_body = body.rstrip() + "\n\n" + pending
    page_text = assemble_page(budgeted, page_body)

    written: Path | None = None
    if write:
        if candidate_path is None:
            return Compose2Result(
                success=False,
                candidate_path=None,
                frontmatter=budgeted,
                error="compose_candidate write=True requires candidate_path",
                frontmatter_degraded=degraded,
                pending_body=pending,
                page_text=page_text,
            )
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(page_text, encoding="utf-8")
        written = candidate_path

    return Compose2Result(
        success=True,
        candidate_path=written,
        frontmatter=budgeted,
        error="",
        frontmatter_degraded=degraded,
        pending_body=pending,
        page_text=page_text,
    )
