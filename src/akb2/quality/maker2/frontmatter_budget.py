"""Deterministic frontmatter line budget for loop2 compose.

Priority: identity scalars stay; questions → 2; related → 2;
proposed_graph_nodes compact to one flow line or move to a body pending
section; drop trailing optional source_*; fail-closed if still over
MAX_FRONTMATTER_LINES.

Line count matches check_a2_frontmatter: interior of the YAML block
between --- fences, splitlines(), fences themselves excluded.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from akb2.quality.checks_structural import MAX_FRONTMATTER_LINES
from akb2.quality.distill import _FrontmatterDumper

IDENTITY_KEYS = (
    "canonical_title",
    "slug",
    "template_type",
    "status",
    "source_url",
    "source_refs",
    "gate_decision",
    "gate_reason",
    "star_level",
    "graph_role",
)

QUESTIONS_KEYS = ("future_questions", "questions")
RELATED_KEYS = ("related_concepts",)
PROPOSED_KEY = "proposed_graph_nodes"

OPTIONAL_DROP_KEYS = (
    "source_author",
    "source_handle",
    "source_date",
    "source_published_at",
    "source_platform",
    "source_id",
    "source_kind",
    "source_lang",
    "harvest_id",
    "confidence",
)

QUESTIONS_CAP = 2
RELATED_CAP = 2
GATE_REASON_MAX_CHARS = 150

_FM_BLOCK_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


class FrontmatterBudgetError(ValueError):
    """Budget could not bring YAML interior to MAX_FRONTMATTER_LINES."""


class _FlowSeq(list):
    """List dumped in flow style (one line)."""


class _FlowMap(dict):
    """Mapping dumped in flow style."""


class _BudgetDumper(_FrontmatterDumper):
    """Frontmatter dumper plus flow representers for compacted nodes."""


def _represent_flow_seq(dumper: yaml.Dumper, data: _FlowSeq) -> yaml.Node:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


def _represent_flow_map(dumper: yaml.Dumper, data: _FlowMap) -> yaml.Node:
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True)


_BudgetDumper.add_representer(_FlowSeq, _represent_flow_seq)
_BudgetDumper.add_representer(_FlowMap, _represent_flow_map)


def count_frontmatter_lines(text: str) -> int:
    """Count YAML interior lines the same way check_a2_frontmatter does."""
    match = _FM_BLOCK_RE.match(text)
    if match is None:
        return 10**9
    return len(match.group(1).splitlines())


def dump_frontmatter(frontmatter: dict[str, Any]) -> str:
    """Dump a frontmatter dict with lint-compatible block-list indent."""
    dumped = yaml.dump(
        frontmatter,
        Dumper=_BudgetDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=10_000,
    )
    if not dumped.endswith("\n"):
        dumped += "\n"
    return f"---\n{dumped}---\n"


def _cap_list(value: Any, cap: int) -> Any:
    if isinstance(value, list) and len(value) > cap:
        return value[:cap]
    return value


def _as_flow_nodes(nodes: Any) -> _FlowSeq | None:
    if not isinstance(nodes, list) or not nodes:
        return None
    flow = _FlowSeq()
    for item in nodes:
        if isinstance(item, dict):
            flow.append(_FlowMap(item))
        else:
            flow.append(item)
    return flow


def _pending_section(nodes: Any) -> str:
    lines = ["## pending_graph_nodes", ""]
    if not isinstance(nodes, list):
        lines.append(str(nodes))
        return "\n".join(lines).rstrip() + "\n"
    for item in nodes:
        if isinstance(item, dict):
            slug = item.get("slug", "")
            label = item.get("label", "")
            reason = item.get("reason", "")
            anchor = item.get("source_anchor", "")
            lines.append(f"- slug: {slug}")
            if label:
                lines.append(f"  label: {label}")
            if reason:
                lines.append(f"  reason: {reason}")
            if anchor:
                lines.append(f"  source_anchor: {anchor}")
        else:
            lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def _truncate_gate_reason(frontmatter: dict[str, Any]) -> bool:
    reason = frontmatter.get("gate_reason")
    if not isinstance(reason, str) or len(reason) <= GATE_REASON_MAX_CHARS:
        return False
    frontmatter["gate_reason"] = reason[:GATE_REASON_MAX_CHARS]
    return True


def apply_frontmatter_budget(
    frontmatter: dict[str, Any],
) -> tuple[dict[str, Any], list[str], str]:
    """Return (budgeted_fm, degraded_reasons, pending_body).

    Identity scalars are never dropped. Raises FrontmatterBudgetError if the
    dumped YAML interior still exceeds MAX_FRONTMATTER_LINES.
    """
    fm: dict[str, Any] = dict(frontmatter)
    degraded: list[str] = []
    pending_body = ""

    def _over() -> bool:
        return count_frontmatter_lines(dump_frontmatter(fm)) > MAX_FRONTMATTER_LINES

    if not _over():
        return fm, degraded, pending_body

    for key in QUESTIONS_KEYS:
        if key in fm and isinstance(fm[key], list) and len(fm[key]) > QUESTIONS_CAP:
            fm[key] = _cap_list(fm[key], QUESTIONS_CAP)
            degraded.append(f"{key}_capped_{QUESTIONS_CAP}")
    if not _over():
        return fm, degraded, pending_body

    for key in RELATED_KEYS:
        if key in fm and isinstance(fm[key], list) and len(fm[key]) > RELATED_CAP:
            fm[key] = _cap_list(fm[key], RELATED_CAP)
            degraded.append(f"{key}_capped_{RELATED_CAP}")
    if not _over():
        return fm, degraded, pending_body

    proposed = fm.get(PROPOSED_KEY)
    flow_nodes = _as_flow_nodes(proposed)
    if flow_nodes is not None:
        fm[PROPOSED_KEY] = flow_nodes
        degraded.append("proposed_graph_nodes_flow")
        if not _over():
            return fm, degraded, pending_body

    if PROPOSED_KEY in fm and fm[PROPOSED_KEY]:
        pending_body = _pending_section(frontmatter.get(PROPOSED_KEY, fm[PROPOSED_KEY]))
        del fm[PROPOSED_KEY]
        degraded.append("proposed_graph_nodes_moved_to_body")
        if not _over():
            return fm, degraded, pending_body

    for key in OPTIONAL_DROP_KEYS:
        if key in fm and key not in IDENTITY_KEYS:
            fm.pop(key)
            degraded.append(f"dropped_{key}")
            if not _over():
                return fm, degraded, pending_body

    if _truncate_gate_reason(fm):
        degraded.append("gate_reason_truncated")
        if not _over():
            return fm, degraded, pending_body

    aliases = fm.get("aliases")
    if isinstance(aliases, list) and len(aliases) > 2:
        fm["aliases"] = aliases[:2]
        degraded.append("aliases_capped_2")
        if not _over():
            return fm, degraded, pending_body

    n_lines = count_frontmatter_lines(dump_frontmatter(fm))
    raise FrontmatterBudgetError(
        f"frontmatter still {n_lines} lines after budget "
        f"(max {MAX_FRONTMATTER_LINES}); degraded={degraded}"
    )
