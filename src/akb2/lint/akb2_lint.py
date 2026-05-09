#!/usr/bin/env python3
"""AKB2.0 LINT CLI — v0.2 (default) + v0.3 (explicit --version v0.3).

Implements 18 rules grounded in standards (each maps to a § citation).

v0.1 rules (kept):
  R1 METADATA-LONG-FIELD                       P1  fm string field > 150 chars (INTAKE-GATE §5.1)
  R2 BODY-MAINTENANCE-LOG-OVERFLOW             P2  ## 维护记录 entries > 5 (INTAKE-GATE §5.2.1)
  R3 BODY-MAINTENANCE-LOG-VERBOSE              P2  single 维护记录 entry > 200 chars (INTAKE-GATE §5.2.2)
  R4 TOOL-UNDERSTANDING-MISSING-STRUCTURE      P1  tool-understanding active missing §4.7 5 sections
  R5 WIKI-TOOLS-CALL-CONTRACT-LEAK             P2  wiki/tools active body contains complete call contract
  R6 BODY-QUALITY-MISSING-STRUCTURE            P1  active missing 9 canonical sections (alias-aware §3.7.3)

v0.2 new rules (all standards-backed):
  R7  SOURCE-GROUNDED-MISSING-FIELD            P1  active missing evidence_provenance (LINT-RULES §11.2)
  R8  SOURCE-GROUNDED-INFERENCE-ONLY           P0  active marked evidence_provenance: inference_only (INTAKE-GATE §4 第 8 条)
  R9  SOURCE-KEY-FINDINGS-MISSING-FRONTMATTER  P1  source template missing frontmatter key_findings ≥3 (PAGE-TEMPLATES §4.2 H7)
  R10 SOURCE-KEY-FINDINGS-MISSING-PROSE        P2  source w/ key_findings but no ## 关键发现 section (§4.2 H7)
  R11 GRAPH-ROLE-MISSING                       P1  active missing graph_role (GRAPH-LINKING §6.1)
  R12 GRAPH-ROLE-INVALID                       P1  graph_role not in 7 enum (GRAPH-LINKING §6.1)
  R13 GRAPH-STATUS-INVALID                     P2  graph_status not in 3 enum (GRAPH-LINKING §6.2)
  R14 ACTIVE-MISSING-RELATED                   P1  active has 0 non-empty related_* (PAGE-TEMPLATES §3 active 必填)
  R15 NAMING-FILENAME-SLUG-MISMATCH            P1  file basename slug ≠ frontmatter slug (NAMING-CONVENTION §17 #5)
  R16 TEMPLATE-DIRECTORY-MISMATCH              P1  template_type ↔ directory mismatch (PAGE-TEMPLATES §4.x)
  R17 RELATED-FIELD-INVALID                    P2  related_X field name not in 12 whitelist (GRAPH-LINKING §9.3)
  R18 WIKI-TOOLS-TOOLBOX-PAIRING               P1  wiki/tools active w/o paired toolbox/tools/<slug>.md (PAGE-TEMPLATES §4.7 必须链接)
       [v0.3 enhanced: also checks body contains toolbox wikilink]

v0.3 new rules (only active with --version v0.3):
  R19 RAW-NO-WIKILINK                          P2  raw/ 文件含 [[wikilink（禁止）(GRAPH-LINKING §37)
  R20 ACTIVE-SLUG-NO-DATE                      P2  wiki/ active 文件名含日期 \\d{4}-\\d{2}-\\d{2}（NAMING-CONVENTION §39）
  R21 DEPRECATED-SUPERSEDED-BY                 P1  status:deprecated 页缺 superseded_by（CONTENT-LIFECYCLE §134）
  R22 WIKILINK-NO-RAW-PATH                     P2  wiki/ active 正文含指向 raw/ 的 wikilink（GRAPH-LINKING §367）

Per LINT-RULES §1.5 (规范层声明 + Phase D v0.2 default timestamp output).

Output paths (per LINT-RULES §1.1 + §13 v0.2 wording):
  v0.2 default:       manifests/lint/<YYYY-MM-DD>T<HHMMSS>-v0-2.jsonl
                      docs/phase-c-reports/lint-cli-v0-2-<YYYY-MM-DD>T<HHMMSS>.md
  v0.2 --stable:      manifests/lint/<YYYY-MM-DD>.jsonl
                      docs/phase-c-reports/lint-cli-bootstrap-<YYYY-MM-DD>.md
  v0.3 default:       manifests/lint/<YYYY-MM-DD>T<HHMMSS>-v0-3.jsonl
                      docs/phase-d-reports/lint-cli-v0.3-<YYYY-MM-DD>T<HHMMSS>.md

Exit code:
  0 = no P0/P1
  1 = at least one P0 or P1
  2 = script error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import os

# NOTE: VAULT_ROOT is overridden at runtime by main() via --vault-root or vault.toml.
# This computed default (parents[2] = Project AKB2/src/) is intentionally wrong when
# running from the engineering root — it acts as a sentinel that surfaces misconfiguration.
VAULT_ROOT = Path(__file__).resolve().parents[2]

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # Project AKB2/
_VAULT_TOML = _PROJECT_ROOT / "config" / "vault.toml"


def _resolve_vault_root_from_toml() -> Path | None:
    """Read vault.toml and return the akb2_root path, or None if unavailable."""
    if not _VAULT_TOML.exists():
        return None
    try:
        for line in _VAULT_TOML.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("akb2_root"):
                val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                val = val.replace("${HOME}", os.environ.get("HOME", "~"))
                return Path(val).expanduser().resolve()
    except Exception:
        pass
    return None

# -----------------------------------------------------------------------------
# Standards-backed enums and whitelists
# -----------------------------------------------------------------------------

# §3.7.3 EXTEND alias map (9 canonical sections)
ALIASES: dict[str, list[str]] = {
    "TLDR":            [r"^## TLDR\b"],
    "入选理由":         [r"^## 入选理由\b", r"^## 为什么值得入库\b"],
    "证据地图":         [r"^## 证据地图\b"],
    "核心论点":         [r"^## 核心论点\b", r"^## 核心观点\b", r"^## 概念定义\b",
                       r"^## 标签级主题清单\b", r"^## 设计原则陈述\b",
                       r"^## 综合论点\b", r"^## 主题要点\b"],
    "论证与解释":       [r"^## 论证与解释\b"],
    "适用边界":         [r"^## 适用边界\b"],
    "图谱关系":         [r"^## 图谱关系\b"],
    "Future Questions": [r"^## Future Questions\b", r"^## 后续缺口\b"],
    "维护记录":         [r"^## 维护记录\b"],
}

# §4.7 + §4.3 own-ecosystem variant
TU_REQUIRED_SECTIONS: dict[str, list[str]] = {
    "是什么":        [r"^## 是什么\b", r"^## 这工具是什么\b", r"^## TLDR\b"],
    "解决什么问题":   [r"^## 解决什么问题\b", r"^## 入选理由\b", r"^## 为什么值得入库\b"],
    "何时用":        [r"^## 何时用\b"],
    "何时不用":      [r"^## 何时不用\b"],
    "与替代品对比":   [r"^## 与替代品对比\b", r"^## 与替代路径对比\b"],
    "相关概念":      [r"^## 相关概念\b", r"^## 关联概念\b"],
}

# GRAPH-LINKING §6.1 7 enum
GRAPH_ROLE_VALUES = {
    "hub", "cluster_center", "bridge", "leaf", "evidence", "procedure", "design-memory",
}

# GRAPH-LINKING §6.2 3 enum
GRAPH_STATUS_VALUES = {"healthy", "orphan_risk", "hub_overload"}

# GRAPH-LINKING §9.3 12 fields whitelist
RELATED_FIELDS_WHITELIST = {
    "related_concepts", "related_sources", "related_entities", "related_projects",
    "related_designs", "related_tools", "related_synthesis", "related_workflows",
    "related_prompts", "related_checklists", "related_agent_procedures", "related_to",
}

# PAGE-TEMPLATES §4.x template_type → directory canonical mapping
TEMPLATE_DIRECTORY_MAP = {
    "concept":            "wiki/concepts/",
    "source":             "wiki/sources/",
    "entity":             "wiki/entities/",
    "synthesis":          "wiki/synthesis/",
    "project":            "wiki/projects/",
    "design":             "wiki/designs/",
    "tool-understanding": "wiki/tools/",
    "tool-card":          "toolbox/tools/",
    "workflow":           "toolbox/workflows/",
    "prompt":             "toolbox/prompts/",
    "checklist":          "toolbox/checklists/",
    "agent-procedure":    "toolbox/agent-procedures/",
}

# evidence_provenance enum (INTAKE-GATE §4 第 8 条)
EVIDENCE_PROVENANCE_VALUES = {
    "source_grounded", "bridging_source_grounded", "partial_source_grounded", "inference_only",
}

# tool-understanding own-ecosystem subtype variant: also accepts "n/a" prefix
EVIDENCE_PROVENANCE_TU_EXTRA_OK = re.compile(r"^n/a(_.+)?$")

# R5 leak heuristic
LEAK_HINT_PATTERNS = [
    r'"inputSchema"\s*:',
    r'"outputSchema"\s*:',
    r'"properties"\s*:\s*\{',
    r'"required"\s*:\s*\[',
    r'function_call\s*:',
    r'\$\{[A-Z_]+\}',
]
LEAK_CODE_BLOCK_THRESHOLD_LINES = 25

ACTIVE_MARK = re.compile(r"^status:\s*active\b", re.MULTILINE)
DEPRECATED_MARK = re.compile(r"^status:\s*deprecated\b", re.MULTILINE)
TEMPLATE_RE = re.compile(r"^template_type:\s*(\S+)", re.MULTILINE)
SLUG_RE = re.compile(r"^slug:\s*(\S+)", re.MULTILINE)

# v0.3 patterns
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
DATE_IN_SLUG_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# raw/ path wikilinks: [[raw/...]], [[../raw/...]], [[/anything/raw/...]]
RAW_WIKILINK_PATH_RE = re.compile(r"(?:(?:\.\.\/)?raw\/|\/[^\]]*raw\/)")


# -----------------------------------------------------------------------------
# Frontmatter parsing
# -----------------------------------------------------------------------------

def split_frontmatter(text: str) -> tuple[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return "", text
    return m.group(1), m.group(2)


def parse_fm_string_fields(fm: str) -> list[tuple[str, str, int]]:
    """Top-level scalar string fields. Skips list/dict/multi-line."""
    out = []
    for i, line in enumerate(fm.split("\n")):
        if line.startswith((" ", "\t")):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        if not k or not k[0].isalpha():
            continue
        v = v.strip()
        if v.startswith(("[", "{")) or v in ("", "|", ">"):
            continue
        v = v.strip('"').strip("'")
        out.append((k, v, i + 1))
    return out


def parse_fm_field_raw(fm: str, name: str) -> str | None:
    """Return raw value of top-level field (string after colon, including list inline)."""
    for line in fm.split("\n"):
        if line.startswith((" ", "\t")):
            continue
        if line.startswith(name + ":"):
            _, _, v = line.partition(":")
            return v.strip()
    return None


def parse_fm_inline_array(fm: str, name: str) -> list[str] | None:
    """Parse `key: [a, b, "c d"]` style inline array. Returns None if field missing or not inline list."""
    raw = parse_fm_field_raw(fm, name)
    if raw is None:
        return None
    if not raw.startswith("["):
        return None
    # close bracket may be on later line; for v0.2 only support same-line inline arrays
    if not raw.endswith("]"):
        return None
    inner = raw[1:-1]
    if not inner.strip():
        return []
    items = []
    cur = ""
    in_quote = None
    bracket_depth = 0
    for ch in inner:
        if in_quote:
            cur += ch
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ('"', "'"):
            in_quote = ch
            cur += ch
            continue
        if ch == "[":
            bracket_depth += 1
            cur += ch
            continue
        if ch == "]":
            bracket_depth -= 1
            cur += ch
            continue
        if ch == "," and bracket_depth == 0:
            items.append(cur.strip().strip('"').strip("'"))
            cur = ""
            continue
        cur += ch
    if cur.strip():
        items.append(cur.strip().strip('"').strip("'"))
    return items


def parse_fm_yaml_array_block(fm: str, name: str) -> list[str] | None:
    """Parse YAML block array (key: \\n  - a \\n  - b)."""
    lines = fm.split("\n")
    items = None
    for i, line in enumerate(lines):
        if line.startswith(name + ":"):
            after = line[len(name) + 1:].strip()
            if after:  # inline value
                return None
            items = []
            for j in range(i + 1, len(lines)):
                ln = lines[j]
                if ln.startswith("  - "):
                    items.append(ln[4:].strip().strip('"').strip("'"))
                elif ln.startswith("    "):
                    continue  # continuation
                elif ln.strip() == "":
                    continue
                else:
                    break
            return items
    return None


def parse_fm_array(fm: str, name: str) -> list[str] | None:
    """Try inline list first, then YAML block."""
    inline = parse_fm_inline_array(fm, name)
    if inline is not None:
        return inline
    return parse_fm_yaml_array_block(fm, name)


# -----------------------------------------------------------------------------
# Body helpers
# -----------------------------------------------------------------------------

def find_section(body: str, patterns: list[str]) -> int | None:
    for i, line in enumerate(body.split("\n"), 1):
        for p in patterns:
            if re.match(p, line):
                return i
    return None


def section_lines(body: str, header_patterns: list[str]) -> list[str]:
    out = []
    in_section = False
    for line in body.split("\n"):
        if not in_section:
            if any(re.match(p, line) for p in header_patterns):
                in_section = True
                continue
        else:
            if line.startswith("## "):
                break
            out.append(line)
    return out


def code_blocks(body: str) -> list[list[str]]:
    blocks = []
    cur = None
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            if cur is None:
                cur = []
            else:
                blocks.append(cur)
                cur = None
            continue
        if cur is not None:
            cur.append(line)
    return blocks


def strip_fenced_code_blocks(text: str) -> str:
    """Replace fenced code block content lines with empty lines (preserves line offsets).

    Used by v0.3 rules to avoid false positives from wikilink-like text in code blocks.
    Handles both ``` and ~~~ fences.
    """
    result = []
    in_fence = False
    fence_marker = ""
    for line in text.split("\n"):
        stripped = line.lstrip()
        if not in_fence:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = True
                fence_marker = stripped[:3]
                result.append(line)  # keep opening fence line
            else:
                result.append(line)
        else:
            if stripped.startswith(fence_marker):
                in_fence = False
                result.append(line)  # keep closing fence line
            else:
                result.append("")  # blank fenced content (preserve line count)
    return "\n".join(result)


def make_finding(rule_id, severity, path, **kwargs):
    return {
        "rule_id": rule_id,
        "severity": severity,
        "page_path": str(path),
        **kwargs,
    }


# -----------------------------------------------------------------------------
# Rule implementations (R1–R18, v0.2)
# -----------------------------------------------------------------------------

def rule_r1_metadata_long_field(path, fm, body, ctx):
    out = []
    for name, value, lineno in parse_fm_string_fields(fm):
        if len(value) > 150:
            out.append(make_finding(
                "METADATA-LONG-FIELD", "P1", path,
                line=lineno, field=name, actual=len(value), threshold=150,
                evidence=f"{name} = {len(value)} chars (> 150)",
                proposed_action="移到正文 ## 维护记录 或 manifest record",
                upstream_standard="INTAKE-GATE §5.1 + LINT-RULES §11.4",
            ))
    return out


def rule_r2_maintenance_log_overflow(path, fm, body, ctx):
    sec_lines = section_lines(body, ALIASES["维护记录"])
    rows = [l for l in sec_lines if re.match(r"^\| 20\d{2}-", l)]
    if len(rows) > 5:
        return [make_finding(
            "BODY-MAINTENANCE-LOG-OVERFLOW", "P2", path,
            actual=len(rows), threshold=5,
            evidence=f"## 维护记录 has {len(rows)} entries (> 5)",
            proposed_action="最早条目移到 archive/changelogs/<slug>--changelog.md",
            upstream_standard="INTAKE-GATE §5.2.1 + LINT-RULES §11.4",
        )]
    return []


def rule_r3_maintenance_log_verbose(path, fm, body, ctx):
    out = []
    for line in section_lines(body, ALIASES["维护记录"]):
        if re.match(r"^\| 20\d{2}-", line) and len(line) > 200:
            out.append(make_finding(
                "BODY-MAINTENANCE-LOG-VERBOSE", "P2", path,
                actual=len(line), threshold=200,
                evidence=f"维护记录 row {len(line)} chars: {line[:60]}…",
                proposed_action="拆分到 manifest record 或 changelog",
                upstream_standard="INTAKE-GATE §5.2.2 + LINT-RULES §11.4",
            ))
    return out


def rule_r4_tu_missing_structure(path, fm, body, ctx):
    if ctx.get("template_type") != "tool-understanding":
        return []
    missing = [k for k, ps in TU_REQUIRED_SECTIONS.items() if find_section(body, ps) is None]
    if missing:
        return [make_finding(
            "TOOL-UNDERSTANDING-MISSING-STRUCTURE", "P1", path,
            missing_sections=missing,
            actual=f"missing {len(missing)} of {len(TU_REQUIRED_SECTIONS)} sections",
            evidence="§4.7 必备节缺失 (own-ecosystem 子型已应用 alias)",
            proposed_action="补缺失节或 demote_candidate",
            upstream_standard="PAGE-TEMPLATES §4.7 + INTAKE-GATE §4.3",
        )]
    return []


def rule_r5_wiki_tools_call_contract_leak(path, fm, body, ctx):
    if "/wiki/tools/" not in str(path):
        return []
    blocks = code_blocks(body)
    long_blocks = [b for b in blocks if len(b) > LEAK_CODE_BLOCK_THRESHOLD_LINES]
    schema_hits = [p for p in LEAK_HINT_PATTERNS if re.search(p, body)]
    if long_blocks or schema_hits:
        return [make_finding(
            "WIKI-TOOLS-CALL-CONTRACT-LEAK", "P2", path,
            long_code_blocks=len(long_blocks),
            schema_pattern_hits=schema_hits,
            evidence=f"long blocks: {len(long_blocks)}; schema patterns: {schema_hits}",
            proposed_action="调用契约移到 toolbox/tools/ 或 toolbox/interfaces/",
            upstream_standard="INTAKE-GATE §4.3.3 + FOLDER-STRUCTURE §3.4",
        )]
    return []


def rule_r6_body_quality_missing_structure(path, fm, body, ctx):
    if ctx.get("template_type") == "tool-understanding":
        return []  # R4 handles
    missing = [k for k, ps in ALIASES.items() if find_section(body, ps) is None]
    if missing:
        return [make_finding(
            "BODY-QUALITY-MISSING-STRUCTURE", "P1", path,
            missing_sections=missing,
            actual=f"missing {len(missing)} of {len(ALIASES)} sections",
            evidence="9 必备节缺失 (alias-aware §3.7.3 EXTEND)",
            proposed_action="补缺失节或 demote",
            upstream_standard="PAGE-TEMPLATES §3.7.3 + LINT-RULES §11.2",
        )]
    return []


def rule_r7_source_grounded_missing_field(path, fm, body, ctx):
    """LINT-RULES §11.2: active 缺 evidence_provenance.
    §3 universal 契约要求所有 active 模板有此字段; §4.7 tool-understanding 的"推荐附加"是 ADDITIONAL 而非 SUBSTITUTE,
    标准未豁免 tool-understanding。tool-understanding 缺此字段 → 真缺陷,不是 CLI 误伤(Codex review 2026-05-07 修正)。"""
    val = parse_fm_field_raw(fm, "evidence_provenance")
    if not val:
        return [make_finding(
            "SOURCE-GROUNDED-MISSING-FIELD", "P1", path,
            evidence="active 缺 frontmatter `evidence_provenance` 字段",
            proposed_action="补 4 enum 之一: source_grounded / bridging_source_grounded / partial_source_grounded / inference_only",
            upstream_standard="INTAKE-GATE §4 第 8 条 + PAGE-TEMPLATES §3.7.2 + LINT-RULES §11.2",
        )]
    return []


def rule_r8_source_grounded_inference_only(path, fm, body, ctx):
    """LINT-RULES §11.2: evidence_provenance: inference_only on active P0"""
    val = parse_fm_field_raw(fm, "evidence_provenance")
    if val and val.strip() == "inference_only":
        return [make_finding(
            "SOURCE-GROUNDED-INFERENCE-ONLY", "P0", path,
            evidence="active 标 evidence_provenance: inference_only",
            proposed_action="**不允许 active**;阻断 + 降级 needs_repair 直到补 source",
            upstream_standard="INTAKE-GATE §4 第 8 条不允许 inference_only active",
        )]
    return []


def rule_r9_source_key_findings_missing_frontmatter(path, fm, body, ctx):
    """PAGE-TEMPLATES §4.2 H7: source 模板 active 缺 frontmatter key_findings ≥3"""
    if ctx.get("template_type") != "source":
        return []
    arr = parse_fm_array(fm, "key_findings")
    if arr is None:
        return [make_finding(
            "SOURCE-KEY-FINDINGS-MISSING-FRONTMATTER", "P1", path,
            evidence="source 模板 active 缺 frontmatter `key_findings` array",
            proposed_action="补 frontmatter `key_findings: [...]` ≥ 3 条精简陈述",
            upstream_standard="PAGE-TEMPLATES §4.2 H7 闭环",
        )]
    if len(arr) < 3:
        return [make_finding(
            "SOURCE-KEY-FINDINGS-MISSING-FRONTMATTER", "P1", path,
            actual=len(arr), threshold=3,
            evidence=f"frontmatter `key_findings` 仅 {len(arr)} 条 (< 3)",
            proposed_action="补到 ≥ 3 条",
            upstream_standard="PAGE-TEMPLATES §4.2 H7",
        )]
    return []


def rule_r10_source_key_findings_missing_prose(path, fm, body, ctx):
    """PAGE-TEMPLATES §4.2 H7: source 有 key_findings 但缺 ## 关键发现 章节"""
    if ctx.get("template_type") != "source":
        return []
    arr = parse_fm_array(fm, "key_findings")
    if arr is None or len(arr) == 0:
        return []  # missing-frontmatter 由 R9 处理
    if find_section(body, [r"^## 关键发现\b"]) is None:
        return [make_finding(
            "SOURCE-KEY-FINDINGS-MISSING-PROSE", "P2", path,
            evidence="frontmatter key_findings 非空但正文缺 ## 关键发现 章节",
            proposed_action="补正文 ## 关键发现 章节",
            upstream_standard="PAGE-TEMPLATES §4.2 H7",
        )]
    return []


def rule_r11_graph_role_missing(path, fm, body, ctx):
    """GRAPH-LINKING §6.1: active 缺 graph_role.
    §3 universal 契约 + GRAPH-LINKING §6 都要求所有 active 含 graph_role,无 tool-understanding 豁免(Codex review 2026-05-07 修正)。"""
    val = parse_fm_field_raw(fm, "graph_role")
    if not val:
        return [make_finding(
            "GRAPH-ROLE-MISSING", "P1", path,
            evidence="active 缺 frontmatter `graph_role` 字段",
            proposed_action=f"补 graph_role 7 enum 之一: {', '.join(sorted(GRAPH_ROLE_VALUES))}",
            upstream_standard="GRAPH-LINKING §6.1 + PAGE-TEMPLATES §4.0",
        )]
    return []


def rule_r12_graph_role_invalid(path, fm, body, ctx):
    """GRAPH-LINKING §6.1: graph_role 不在 7 枚举"""
    val = parse_fm_field_raw(fm, "graph_role")
    if val and val.strip() not in GRAPH_ROLE_VALUES:
        return [make_finding(
            "GRAPH-ROLE-INVALID", "P1", path,
            actual=val.strip(),
            evidence=f"graph_role = `{val.strip()}` 不在 7 enum",
            proposed_action=f"改为 7 enum 之一: {', '.join(sorted(GRAPH_ROLE_VALUES))}",
            upstream_standard="GRAPH-LINKING §6.1",
        )]
    return []


def rule_r13_graph_status_invalid(path, fm, body, ctx):
    """GRAPH-LINKING §6.2: graph_status 不在 3 枚举"""
    val = parse_fm_field_raw(fm, "graph_status")
    if val and val.strip() not in GRAPH_STATUS_VALUES:
        return [make_finding(
            "GRAPH-STATUS-INVALID", "P2", path,
            actual=val.strip(),
            evidence=f"graph_status = `{val.strip()}` 不在 3 enum",
            proposed_action=f"改为 3 enum 之一: {', '.join(sorted(GRAPH_STATUS_VALUES))}",
            upstream_standard="GRAPH-LINKING §6.2",
        )]
    return []


def rule_r14_active_missing_related(path, fm, body, ctx):
    """PAGE-TEMPLATES §3 active 必填: 至少 1 个 related_* 字段非空.
    §3 universal 适用所有 active 模板; §4.7 tool-understanding 必须链接 tool-card + ≥1 related_concepts 是 ADDITIONAL 而非 SUBSTITUTE,
    body wikilink 不替代 frontmatter related_* 字段(GRAPH-LINKING §9.2 双轨原则: frontmatter 必填, 正文推荐)。"""
    has_any = False
    for fname in RELATED_FIELDS_WHITELIST:
        arr = parse_fm_array(fm, fname)
        if arr is not None and len(arr) > 0:
            has_any = True
            break
    if not has_any:
        return [make_finding(
            "ACTIVE-MISSING-RELATED", "P1", path,
            evidence="active 0 个非空 related_* 字段(必备至少 1 个)",
            proposed_action="补任一 related_* 字段(12 whitelist 之一,非空)",
            upstream_standard="PAGE-TEMPLATES §3 active 必填 + GRAPH-LINKING §9.2 双轨原则 + §9.5",
        )]
    return []


def rule_r15_naming_filename_slug_mismatch(path, fm, body, ctx):
    """NAMING-CONVENTION §17 #5: 文件名 slug 与 frontmatter slug 一致"""
    fm_slug_match = SLUG_RE.search(fm)
    if not fm_slug_match:
        return []  # missing slug 由其它规则覆盖(若有)
    fm_slug = fm_slug_match.group(1).strip()
    file_slug = path.stem
    # active 不带日期时间后缀,直接比较 stem == slug
    if fm_slug != file_slug:
        return [make_finding(
            "NAMING-FILENAME-SLUG-MISMATCH", "P1", path,
            actual=f"file stem={file_slug}, fm slug={fm_slug}",
            evidence=f"文件名 `{file_slug}.md` ≠ frontmatter slug `{fm_slug}`",
            proposed_action="走重命名迁移协议(NAMING §10);旧 slug 进 aliases",
            upstream_standard="NAMING-CONVENTION §17 #5(LINT 待实现 backlog 闭环)",
        )]
    return []


def rule_r16_template_directory_mismatch(path, fm, body, ctx):
    """PAGE-TEMPLATES §4.x: template_type ↔ 目录 canonical 映射"""
    tt = ctx.get("template_type")
    if not tt:
        return []  # 由 R6 / 其它规则处理
    expected = TEMPLATE_DIRECTORY_MAP.get(tt)
    if expected is None:
        return [make_finding(
            "TEMPLATE-DIRECTORY-MISMATCH", "P1", path,
            actual=f"template_type={tt}",
            evidence=f"template_type `{tt}` 不在 12 类映射",
            proposed_action=f"改为 12 类之一: {', '.join(sorted(TEMPLATE_DIRECTORY_MAP))}",
            upstream_standard="PAGE-TEMPLATES §4.x",
        )]
    rel_path = str(path.relative_to(VAULT_ROOT)) if path.is_absolute() else str(path)
    if expected not in rel_path:
        return [make_finding(
            "TEMPLATE-DIRECTORY-MISMATCH", "P1", path,
            actual=f"template_type={tt}, path={rel_path}",
            evidence=f"template_type `{tt}` 应在 `{expected}` 但实际在 `{rel_path}`",
            proposed_action=f"移动到 `{expected}` 或修正 template_type",
            upstream_standard="PAGE-TEMPLATES §4.x",
        )]
    return []


def rule_r17_related_field_invalid(path, fm, body, ctx):
    """GRAPH-LINKING §9.3: related_X 字段名必须在 12 whitelist"""
    out = []
    for line in fm.split("\n"):
        if line.startswith((" ", "\t")):
            continue
        m = re.match(r"^(related_\w+)\s*:", line)
        if not m:
            continue
        name = m.group(1)
        if name not in RELATED_FIELDS_WHITELIST:
            out.append(make_finding(
                "RELATED-FIELD-INVALID", "P2", path,
                actual=name,
                evidence=f"related_* 字段 `{name}` 不在 12 whitelist",
                proposed_action=f"改为 12 whitelist 之一: {', '.join(sorted(RELATED_FIELDS_WHITELIST))}",
                upstream_standard="GRAPH-LINKING §9.3",
            ))
    return out


def rule_r18_wiki_tools_toolbox_pairing(path, fm, body, ctx):
    """PAGE-TEMPLATES §4.7 必须链接 toolbox/tools/<slug> tool-card.

    v0.3 enhancement (ctx['v03_mode'] = True): additionally checks that the body
    contains a wikilink to toolbox/tools/<slug> (not just that the file exists).
    The standard wikilink format is [[../../toolbox/tools/<slug>|...]] in wiki/tools/ pages.
    """
    if ctx.get("template_type") != "tool-understanding":
        return []
    slug = path.stem
    toolbox_card = VAULT_ROOT / "toolbox" / "tools" / f"{slug}.md"
    findings = []
    if not toolbox_card.exists():
        findings.append(make_finding(
            "WIKI-TOOLS-TOOLBOX-PAIRING", "P1", path,
            actual=f"missing toolbox/tools/{slug}.md",
            evidence=f"tool-understanding active 但配对 tool-card `toolbox/tools/{slug}.md` 不存在",
            proposed_action="创建 toolbox/tools/<slug>.md tool-card 或 demote 本 understanding 页",
            upstream_standard="PAGE-TEMPLATES §4.7 必须链接 + INTAKE-GATE §4.3.3",
        ))
    # v0.3 enhancement: also verify body contains wikilink to the paired toolbox card
    if ctx.get("v03_mode") and toolbox_card.exists():
        body_clean = strip_fenced_code_blocks(body)
        # Pattern: [[anything/toolbox/tools/<slug>]] or [[toolbox/tools/<slug>|alias]]
        toolbox_link_re = re.compile(
            r"\[\[[^\]]*toolbox/tools/" + re.escape(slug) + r"(?:\|[^\]]*)?\]\]"
        )
        if not toolbox_link_re.search(body_clean):
            findings.append(make_finding(
                "WIKI-TOOLS-TOOLBOX-PAIRING", "P1", path,
                actual=f"missing body wikilink to toolbox/tools/{slug}",
                evidence=(
                    f"tool-understanding active: tool-card `toolbox/tools/{slug}.md` 存在"
                    f" 但正文缺 [[toolbox/tools/{slug}]] wikilink（R18 v0.3 body check）"
                ),
                proposed_action=(
                    f"在正文 ## 图谱关系 中添加 [[../../toolbox/tools/{slug}|{slug} tool-card]] wikilink"
                ),
                upstream_standard="PAGE-TEMPLATES §4.7 必须链接 + INTAKE-GATE §4.3.3",
            ))
    return findings


# -----------------------------------------------------------------------------
# v0.3-only rule implementations (R19–R22)
# These run on active pages; RAW-NO-WIKILINK and DEPRECATED-SUPERSEDED-BY
# are invoked separately (different file scopes).
# -----------------------------------------------------------------------------

def rule_v03_active_slug_no_date(path, fm, body, ctx):
    """v0.3 R20: ACTIVE-SLUG-NO-DATE — wiki/ active 文件名不应含日期.

    NAMING-CONVENTION §39: active 页 slug 不带日期；
    raw/staging/archive/manifests 必须带时间后缀。
    """
    # Only applies to wiki/ pages (toolbox tools are named without dates by convention)
    if "/wiki/" not in str(path):
        return []
    if DATE_IN_SLUG_RE.search(path.stem):
        return [make_finding(
            "ACTIVE-SLUG-NO-DATE", "P2", path,
            actual=f"slug contains date: {path.stem}",
            evidence=f"active 文件名 `{path.stem}` 含日期后缀（active 不应带日期）",
            proposed_action="走重命名迁移协议（NAMING §10）去除日期后缀；旧 slug 进 aliases",
            upstream_standard="AKB-2.0-NAMING-CONVENTION.md:L39（active slug 不带日期）",
        )]
    return []


def rule_v03_wikilink_no_raw_path(path, fm, body, ctx):
    """v0.3 R22: WIKILINK-NO-RAW-PATH — wiki/ active 正文禁止 wikilink 指向 raw/ 路径.

    GRAPH-LINKING §367: 禁止绝对路径、纯 slug 跨目录。
    Fenced code blocks are stripped to avoid false positives.

    Exemption: source template pages MAY link to raw/ in ## 证据地图 as the
    intended evidence provenance chain (raw archive → source page → wiki knowledge).
    Only concept/design/entity/tool pages are checked.
    """
    if ctx.get("template_type") == "source":
        return []  # source pages: raw/ links in 证据地图 are the intended provenance chain
    body_clean = strip_fenced_code_blocks(body)
    hits = []
    for m in WIKILINK_RE.finditer(body_clean):
        target = m.group(1).split("|")[0].strip()
        # Check for raw/ path patterns: raw/, ../raw/, /path/raw/
        if RAW_WIKILINK_PATH_RE.search(target) or target.startswith("raw/"):
            hits.append(target[:80])
    if hits:
        return [make_finding(
            "WIKILINK-NO-RAW-PATH", "P2", path,
            actual=f"{len(hits)} wikilink(s) to raw/",
            evidence=f"wiki/ 正文含 {len(hits)} 个指向 raw/ 的 wikilink: {hits[:3]}",
            proposed_action="删除或替换为指向 wiki/sources/ 的正规 wikilink（raw/ 是证据层，不应被 wiki 直链）",
            upstream_standard="AKB-2.0-GRAPH-LINKING.md:L367（禁止：绝对路径、纯 slug 跨目录）",
        )]
    return []


# v0.3 rules added to the per-page active scan (in addition to RULES)
V03_ACTIVE_RULES = [
    rule_v03_active_slug_no_date,
    rule_v03_wikilink_no_raw_path,
]


# -----------------------------------------------------------------------------
# Rule lists and stable IDs
# -----------------------------------------------------------------------------

RULES = [
    rule_r1_metadata_long_field,
    rule_r2_maintenance_log_overflow,
    rule_r3_maintenance_log_verbose,
    rule_r4_tu_missing_structure,
    rule_r5_wiki_tools_call_contract_leak,
    rule_r6_body_quality_missing_structure,
    rule_r7_source_grounded_missing_field,
    rule_r8_source_grounded_inference_only,
    rule_r9_source_key_findings_missing_frontmatter,
    rule_r10_source_key_findings_missing_prose,
    rule_r11_graph_role_missing,
    rule_r12_graph_role_invalid,
    rule_r13_graph_status_invalid,
    rule_r14_active_missing_related,
    rule_r15_naming_filename_slug_mismatch,
    rule_r16_template_directory_mismatch,
    rule_r17_related_field_invalid,
    rule_r18_wiki_tools_toolbox_pairing,
]

# Stable rule_id list (for manifest/report)
STABLE_RULE_IDS = [
    "METADATA-LONG-FIELD",
    "BODY-MAINTENANCE-LOG-OVERFLOW",
    "BODY-MAINTENANCE-LOG-VERBOSE",
    "TOOL-UNDERSTANDING-MISSING-STRUCTURE",
    "WIKI-TOOLS-CALL-CONTRACT-LEAK",
    "BODY-QUALITY-MISSING-STRUCTURE",
    "SOURCE-GROUNDED-MISSING-FIELD",
    "SOURCE-GROUNDED-INFERENCE-ONLY",
    "SOURCE-KEY-FINDINGS-MISSING-FRONTMATTER",
    "SOURCE-KEY-FINDINGS-MISSING-PROSE",
    "GRAPH-ROLE-MISSING",
    "GRAPH-ROLE-INVALID",
    "GRAPH-STATUS-INVALID",
    "ACTIVE-MISSING-RELATED",
    "NAMING-FILENAME-SLUG-MISMATCH",
    "TEMPLATE-DIRECTORY-MISMATCH",
    "RELATED-FIELD-INVALID",
    "WIKI-TOOLS-TOOLBOX-PAIRING",
]

# v0.3: full 22-rule list (18 + 4 new)
V03_STABLE_RULE_IDS = STABLE_RULE_IDS + [
    "RAW-NO-WIKILINK",
    "ACTIVE-SLUG-NO-DATE",
    "DEPRECATED-SUPERSEDED-BY",
    "WIKILINK-NO-RAW-PATH",
]


# -----------------------------------------------------------------------------
# Driver helpers
# -----------------------------------------------------------------------------

def collect_active_pages(scope: str) -> list[Path]:
    if scope == "wiki":
        roots = [VAULT_ROOT / "wiki"]
    else:
        roots = [VAULT_ROOT / "wiki", VAULT_ROOT / "toolbox"]
    pages = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.md"):
            if p.name == "00-Index.md":
                continue
            try:
                txt = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if ACTIVE_MARK.search(txt):
                pages.append(p)
    return sorted(pages)


def collect_raw_files() -> list[Path]:
    """Collect all .md files under raw/ for RAW-NO-WIKILINK scan."""
    raw_root = VAULT_ROOT / "raw"
    if not raw_root.exists():
        return []
    return sorted(raw_root.rglob("*.md"))


def collect_deprecated_wiki_pages() -> list[Path]:
    """Collect wiki/ pages with status: deprecated for DEPRECATED-SUPERSEDED-BY scan."""
    wiki_root = VAULT_ROOT / "wiki"
    if not wiki_root.exists():
        return []
    pages = []
    for p in wiki_root.rglob("*.md"):
        if p.name == "00-Index.md":
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if DEPRECATED_MARK.search(txt):
            pages.append(p)
    return sorted(pages)


def lint_raw_no_wikilink(raw_files: list[Path]) -> list[dict]:
    """v0.3 R19: scan raw/ files for [[wikilinks (forbidden per GRAPH-LINKING §37)."""
    findings = []
    for p in raw_files:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        hits = WIKILINK_RE.findall(text)
        if hits:
            findings.append(make_finding(
                "RAW-NO-WIKILINK", "P2", p,
                actual=f"{len(hits)} wikilink(s) found",
                evidence=f"raw/ 文件含 {len(hits)} 个 [[wikilink（禁止：raw/ 是证据层，禁止加 wikilink）",
                proposed_action="删除 raw/ 文件中的 [[wikilink；wikilink 只能在 staging/wiki/toolbox 中使用",
                upstream_standard="AKB-2.0-GRAPH-LINKING.md:L37（raw/ 内文件禁止加 wikilink）",
            ))
    return findings


def lint_deprecated_superseded_by(deprecated_pages: list[Path]) -> list[dict]:
    """v0.3 R21: check deprecated wiki pages for missing superseded_by field."""
    findings = []
    for p in deprecated_pages:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = split_frontmatter(text)
        val = parse_fm_field_raw(fm, "superseded_by")
        if not val:
            findings.append(make_finding(
                "DEPRECATED-SUPERSEDED-BY", "P1", p,
                evidence="status:deprecated 页缺 frontmatter `superseded_by` 字段",
                proposed_action="补 `superseded_by: <slug>` 指向替代页",
                upstream_standard="AKB-2.0-CONTENT-LIFECYCLE.md:L134（active→deprecated 必须写 superseded_by）",
            ))
    return findings


def lint_page(path: Path, rules=None, ctx_extra=None) -> list[dict]:
    """Lint a single active page with the given rule list.

    Args:
        path: Path to the page.
        rules: List of rule functions. Defaults to RULES (v0.2 set).
        ctx_extra: Optional dict merged into ctx (e.g. {'v03_mode': True}).
    """
    if rules is None:
        rules = RULES
    text = path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(text)
    tt_match = TEMPLATE_RE.search(fm)
    ctx = {"template_type": tt_match.group(1).strip() if tt_match else None}
    if ctx_extra:
        ctx.update(ctx_extra)
    findings = []
    for rule in rules:
        try:
            findings.extend(rule(path, fm, body, ctx))
        except Exception as e:
            findings.append({
                "rule_id": rule.__name__,
                "severity": "P3",
                "page_path": str(path),
                "evidence": f"rule raised {type(e).__name__}: {e}",
                "proposed_action": "fix CLI rule implementation",
                "upstream_standard": "akb2-lint internal",
            })
    return findings


# -----------------------------------------------------------------------------
# Output writers
# -----------------------------------------------------------------------------

def write_manifest(findings, path, op_id, scanned, version,
                   raw_files=None, deprecated_pages=None):
    """Write JSONL manifest. v0.3 adds auxiliary scope fields."""
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    p0 = sum(1 for x in findings if x["severity"] == "P0")
    p1 = sum(1 for x in findings if x["severity"] == "P1")
    p2 = sum(1 for x in findings if x["severity"] == "P2")
    rule_ids = V03_STABLE_RULE_IDS if version == "v0.3" else STABLE_RULE_IDS
    with path.open("w", encoding="utf-8") as f:
        header = {
            "record_type": "lint_run_summary",
            "schema_version": f"akb-lint-{version}",
            "operation_id": op_id,
            "tool": "akb2-lint.py",
            "tool_version": version,
            "ran_at": now,
            "scope": [str(p.relative_to(VAULT_ROOT)) for p in scanned],
            "scanned_count": len(scanned),
            "rules_executed": rule_ids,
            "p0_count": p0,
            "p1_count": p1,
            "p2_count": p2,
            "total_findings": len(findings),
        }
        if version == "v0.3":
            header["scan_scope"] = "wiki+raw+deprecated"
            header["auxiliary_scopes"] = ["raw", "deprecated"]
            header["scan_counts_by_scope"] = {
                "wiki_active": len(scanned),
                "raw_files": len(raw_files or []),
                "deprecated": len(deprecated_pages or []),
            }
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for x in findings:
            rec = {
                "record_type": "lint_finding",
                "schema_version": f"akb-lint-{version}",
                "operation_id": op_id,
                "ran_at": now,
                **x,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_report(findings, path, scanned, op_id, version, baseline,
                 raw_files=None, deprecated_pages=None):
    """Write Markdown report. v0.3 adds auxiliary scope sections and 22-rule table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    p0 = [x for x in findings if x["severity"] == "P0"]
    p1 = [x for x in findings if x["severity"] == "P1"]
    p2 = [x for x in findings if x["severity"] == "P2"]
    by_page = {}
    for x in findings:
        by_page.setdefault(x["page_path"], []).append(x)

    today = datetime.now().strftime("%Y-%m-%d")
    is_v03 = version == "v0.3"
    rule_ids = V03_STABLE_RULE_IDS if is_v03 else STABLE_RULE_IDS
    manifest_dir = "manifests/lint/"

    lines = [
        "---",
        "report_type: phase_d_lint_cli_run",
        f"report_id: lint-cli-{version}-{today}",
        f"date: {today}",
        f"operation_id: {op_id}",
        f"tool: akb2-lint.py {version}",
        f"baseline: {baseline}",
        f"scanned_count: {len(scanned)}",
        f"p0_count: {len(p0)}",
        f"p1_count: {len(p1)}",
        f"p2_count: {len(p2)}",
        "---",
        "",
        f"# AKB2.0 LINT CLI {version} Run Report",
        "",
        "## 摘要",
        "",
        f"- 工具：`akb2-lint.py` {version}（Python stdlib，无依赖）",
        f"- 位置：`.akb-runtime/lint/akb2-lint.py`",
        f"- 规则：{len(rule_ids)} 条（all standards-backed）",
        f"- 扫描 active 页数：{len(scanned)}",
    ]
    if is_v03:
        lines += [
            f"- 扫描 raw/ 文件数：{len(raw_files or [])}",
            f"- 扫描 deprecated 页数：{len(deprecated_pages or [])}",
        ]
    lines += [
        f"- baseline: {baseline}",
        f"- P0 findings：**{len(p0)}**",
        f"- P1 findings：**{len(p1)}**",
        f"- P2 findings：**{len(p2)}**",
        "",
        "## 规则清单",
        "",
        "| Rule ID | Severity | 上游标准 |",
        "|---|---|---|",
        "| METADATA-LONG-FIELD | P1 | INTAKE-GATE §5.1 |",
        "| BODY-MAINTENANCE-LOG-OVERFLOW | P2 | INTAKE-GATE §5.2.1 |",
        "| BODY-MAINTENANCE-LOG-VERBOSE | P2 | INTAKE-GATE §5.2.2 |",
        "| TOOL-UNDERSTANDING-MISSING-STRUCTURE | P1 | PAGE-TEMPLATES §4.7 + INTAKE-GATE §4.3 |",
        "| WIKI-TOOLS-CALL-CONTRACT-LEAK | P2 | INTAKE-GATE §4.3.3 + FOLDER-STRUCTURE §3.4 |",
        "| BODY-QUALITY-MISSING-STRUCTURE | P1 | PAGE-TEMPLATES §3.7.3 + LINT-RULES §11.2 |",
        "| SOURCE-GROUNDED-MISSING-FIELD | P1 | INTAKE-GATE §4 第 8 条 + LINT-RULES §11.2 |",
        "| SOURCE-GROUNDED-INFERENCE-ONLY | P0 | INTAKE-GATE §4 第 8 条 不允许 inference_only active |",
        "| SOURCE-KEY-FINDINGS-MISSING-FRONTMATTER | P1 | PAGE-TEMPLATES §4.2 H7 |",
        "| SOURCE-KEY-FINDINGS-MISSING-PROSE | P2 | PAGE-TEMPLATES §4.2 H7 |",
        "| GRAPH-ROLE-MISSING | P1 | GRAPH-LINKING §6.1 + PAGE-TEMPLATES §4.0 |",
        "| GRAPH-ROLE-INVALID | P1 | GRAPH-LINKING §6.1(7 enum) |",
        "| GRAPH-STATUS-INVALID | P2 | GRAPH-LINKING §6.2(3 enum) |",
        "| ACTIVE-MISSING-RELATED | P1 | PAGE-TEMPLATES §3 active 必填 + GRAPH-LINKING §9.5 |",
        "| NAMING-FILENAME-SLUG-MISMATCH | P1 | NAMING-CONVENTION §17 #5 |",
        "| TEMPLATE-DIRECTORY-MISMATCH | P1 | PAGE-TEMPLATES §4.x 12 模板 |",
        "| RELATED-FIELD-INVALID | P2 | GRAPH-LINKING §9.3(12 whitelist) |",
        "| WIKI-TOOLS-TOOLBOX-PAIRING | P1 | PAGE-TEMPLATES §4.7 必须链接 |",
    ]
    if is_v03:
        lines += [
            "| RAW-NO-WIKILINK | P2 | AKB-2.0-GRAPH-LINKING.md:L37（v0.3 新增）|",
            "| ACTIVE-SLUG-NO-DATE | P2 | AKB-2.0-NAMING-CONVENTION.md:L39（v0.3 新增）|",
            "| DEPRECATED-SUPERSEDED-BY | P1 | AKB-2.0-CONTENT-LIFECYCLE.md:L134（v0.3 新增）|",
            "| WIKILINK-NO-RAW-PATH | P2 | AKB-2.0-GRAPH-LINKING.md:L367（v0.3 新增）|",
        ]
    lines += [
        "",
        "## 扫描清单（active pages）",
        "",
    ]
    for p in scanned:
        rel = p.relative_to(VAULT_ROOT)
        n = len(by_page.get(str(p), []))
        marker = "✅" if n == 0 else f"⚠️ {n}"
        lines.append(f"- {marker} `{rel}`")
    lines.append("")

    if is_v03:
        # Auxiliary scope: raw files
        lines += ["## v0.3 扫描清单（raw/ 文件）", ""]
        if raw_files:
            for p in raw_files:
                rel = p.relative_to(VAULT_ROOT)
                n = len(by_page.get(str(p), []))
                marker = "✅" if n == 0 else f"⚠️ {n}"
                lines.append(f"- {marker} `{rel}`")
        else:
            lines.append("- （raw/ 目录为空或不存在）")
        lines.append("")

        # Auxiliary scope: deprecated pages
        lines += ["## v0.3 扫描清单（deprecated pages）", ""]
        if deprecated_pages:
            for p in deprecated_pages:
                rel = p.relative_to(VAULT_ROOT)
                n = len(by_page.get(str(p), []))
                marker = "✅" if n == 0 else f"⚠️ {n}"
                lines.append(f"- {marker} `{rel}`")
        else:
            lines.append("- （当前无 deprecated 页面）")
        lines.append("")

    lines += [
        "## P0 violations",
        "",
        "**none**" if not p0 else "",
    ]
    for x in p0:
        lines.append(f"- **{x['rule_id']}** at `{x['page_path']}`")
        lines.append(f"  - {x['evidence']}")
        lines.append(f"  - proposed_action: {x['proposed_action']}")
    lines += [
        "",
        "## P1 violations",
        "",
        "**none** ✅" if not p1 else "",
    ]
    for x in p1:
        lines.append(f"- **{x['rule_id']}** at `{x['page_path']}`")
        lines.append(f"  - {x['evidence']}")
        lines.append(f"  - proposed_action: {x['proposed_action']}")
    lines += [
        "",
        "## P2 violations",
        "",
        "**none**" if not p2 else "",
    ]
    # Group P2 by rule_id for compactness
    p2_by_rule = {}
    for x in p2:
        p2_by_rule.setdefault(x["rule_id"], []).append(x)
    for rid, xs in sorted(p2_by_rule.items()):
        lines.append(f"### {rid} ({len(xs)} hits)")
        for x in xs:
            lines.append(f"- `{x['page_path']}`: {x['evidence']}")
        lines.append("")
    lines += [
        "## 不变量",
        "",
        "- CLI 不修改任何 wiki/** active 内容（read-only scan）",
        "- CLI 不修改 standards doc / toolbox / FOLDER-STRUCTURE",
        f"- manifest 写入 `{manifest_dir}`（per LINT-RULES §1.1）",
    ]
    if is_v03:
        lines.append("- v0.3 report 写入 `docs/phase-d-reports/`（Census Week Phase D 产出）")
    path.write_text("\n".join(lines), encoding="utf-8")


# -----------------------------------------------------------------------------
# v0.3 driver
# -----------------------------------------------------------------------------

def run_v03(args) -> int:
    """Execute full v0.3 lint run (22 rules, three scopes)."""
    # 1. Collect and lint active pages with all v0.2 rules + v0.3 active rules + enhanced R18
    scanned = collect_active_pages(args.scope)
    v03_rules = RULES + V03_ACTIVE_RULES
    v03_ctx = {"v03_mode": True}
    findings = []
    for p in scanned:
        findings.extend(lint_page(p, rules=v03_rules, ctx_extra=v03_ctx))

    # 2. R19 RAW-NO-WIKILINK: scan raw/ files
    raw_files = collect_raw_files()
    findings.extend(lint_raw_no_wikilink(raw_files))

    # 3. R21 DEPRECATED-SUPERSEDED-BY: scan deprecated wiki pages
    deprecated_pages = collect_deprecated_wiki_pages()
    findings.extend(lint_deprecated_superseded_by(deprecated_pages))

    # 4. Build output paths
    today = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().strftime("%H%M%S")
    op_id = f"akb2-lint-{today}T{ts}"

    if args.manifest:
        manifest_path = Path(args.manifest)
    elif args.stable_output:
        manifest_path = VAULT_ROOT / "manifests" / "lint" / f"{today}-v0-3.jsonl"
    else:
        manifest_path = VAULT_ROOT / "manifests" / "lint" / f"{today}T{ts}-v0-3.jsonl"

    if args.report:
        report_path = Path(args.report)
    elif args.stable_output:
        report_path = VAULT_ROOT / "docs" / "phase-d-reports" / f"lint-cli-v0.3-bootstrap-{today}.md"
    else:
        report_path = VAULT_ROOT / "docs" / "phase-d-reports" / f"lint-cli-v0.3-{today}T{ts}.md"

    write_manifest(findings, manifest_path, op_id, scanned, "v0.3",
                   raw_files=raw_files, deprecated_pages=deprecated_pages)
    write_report(findings, report_path, scanned, op_id, "v0.3", args.baseline,
                 raw_files=raw_files, deprecated_pages=deprecated_pages)

    p0 = sum(1 for x in findings if x["severity"] == "P0")
    p1 = sum(1 for x in findings if x["severity"] == "P1")
    p2 = sum(1 for x in findings if x["severity"] == "P2")
    n_raw = len(raw_files)
    n_dep = len(deprecated_pages)
    print(f"akb2-lint v0.3 — active: {len(scanned)}, raw: {n_raw}, deprecated: {n_dep}")
    print(f"  P0: {p0}")
    print(f"  P1: {p1}")
    print(f"  P2: {p2}")
    print(f"  total: {len(findings)}")
    print(f"  manifest: {manifest_path.relative_to(VAULT_ROOT)}")
    print(f"  report:   {report_path.relative_to(VAULT_ROOT)}")
    return 1 if (p0 + p1) > 0 else 0


# -----------------------------------------------------------------------------
# v0.2 driver (default)
# -----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["wiki", "all"], default="wiki")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--stable-output", action="store_true",
                    help="use stable date-only filenames (overwrites prior runs); default uses timestamp filename")
    ap.add_argument("--baseline", default="v0.2 (15 active P0=0/P1=0/P2=0 2026-05-08)")
    ap.add_argument("--version", default="v0.2", choices=["v0.2", "v0.3"],
                    help="v0.2 = 18 rules (default, backward-compat); v0.3 = 22 rules + 3 scopes")
    ap.add_argument("--vault-root", default=None, metavar="PATH",
                    help="Path to AKB2.0 vault root (Obsidian sub-library). "
                         "Overrides vault.toml default. Required when running from Project AKB2/.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print findings to stdout only; do not write manifest or report files.")
    args = ap.parse_args()

    # ── Resolve VAULT_ROOT early (must happen before any function that uses it) ──
    global VAULT_ROOT  # noqa: PLW0603
    if args.vault_root:
        VAULT_ROOT = Path(args.vault_root).expanduser().resolve()
    else:
        _toml_root = _resolve_vault_root_from_toml()
        if _toml_root is not None:
            VAULT_ROOT = _toml_root
        # else: leave the computed sentinel — callers will see a clear FileNotFoundError

    # v0.3 mode: separate driver
    if args.version == "v0.3":
        return run_v03(args)

    # v0.2 default mode (all existing behavior preserved)
    scanned = collect_active_pages(args.scope)
    findings = []
    for p in scanned:
        findings.extend(lint_page(p))

    today = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().strftime("%H%M%S")
    op_id = f"akb2-lint-{today}T{ts}"

    if args.manifest:
        manifest_path = Path(args.manifest)
    elif args.stable_output:
        manifest_path = VAULT_ROOT / "manifests" / "lint" / f"{today}.jsonl"
    else:
        manifest_path = VAULT_ROOT / "manifests" / "lint" / f"{today}T{ts}-{args.version.replace('.', '-')}.jsonl"

    if args.report:
        report_path = Path(args.report)
    elif args.stable_output:
        report_path = VAULT_ROOT / "docs" / "phase-c-reports" / f"lint-cli-bootstrap-{today}.md"
    else:
        report_path = VAULT_ROOT / "docs" / "phase-c-reports" / f"lint-cli-{args.version}-{today}T{ts}.md"

    p0 = sum(1 for x in findings if x["severity"] == "P0")
    p1 = sum(1 for x in findings if x["severity"] == "P1")
    p2 = sum(1 for x in findings if x["severity"] == "P2")
    print(f"akb2-lint {args.version} — scanned {len(scanned)} active page(s)")
    print(f"  P0: {p0}")
    print(f"  P1: {p1}")
    print(f"  P2: {p2}")
    print(f"  total: {len(findings)}")

    if args.dry_run:
        print("  [dry-run] manifest and report not written")
        return 1 if (p0 + p1) > 0 else 0

    write_manifest(findings, manifest_path, op_id, scanned, args.version)
    write_report(findings, report_path, scanned, op_id, args.version, args.baseline)
    print(f"  manifest: {manifest_path.relative_to(VAULT_ROOT)}")
    print(f"  report:   {report_path.relative_to(VAULT_ROOT)}")
    return 1 if (p0 + p1) > 0 else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"akb2-lint ERROR: {e!r}", file=sys.stderr)
        sys.exit(2)
