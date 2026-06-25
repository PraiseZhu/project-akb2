"""定点修复循环 — 模板化修复指令 → 定点重写 → diff 校验 → A+B 复检。

核心约束:
    - MAX_REPAIR_ROUNDS = 3（具名常量，宪法 P1/P4）
    - 修复指令模板化拼接，非自由生成
    - diff 校验: 未点名小节规范化比较（吸收空白/标点噪声，实质 token 变动仍判违规即丢弃该轮）
    - 3 轮不收敛 → fail-closed: 留候选区 + 落 distill_no_converge 故障签名
    - 修复模型: DeepSeek-V4-Pro（经 llm_router，reasoning_effort=max）

修复流程:
    1. 缺陷清单 → 模板化拼接修复指令
    2. DeepSeek 定点重写被点名小节
    3. diff 校验: 未点名小节逐字节不变
    4. 重新过 A 层检查
    5. A 通过后重新过 B 层评分
    6. 仍有缺陷 → 下一轮（最多 3 轮）
    7. 3 轮不收敛 → fail-closed 签名落盘
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.akb2.loops.signatures import record_failure
# 注意：run_candidate_self_gate 故意不在此处 name-binding，而是通过模块限定
# checks_structural.run_candidate_self_gate(...) 调用。否则测试 mock.patch
# checks_structural.run_candidate_self_gate 后若首次 import 本模块，mock 会被永久
# 焊死进 repair 命名空间，patch 退出不还原 → 污染后续走真实 run_repair_loop 的测试。
from src.akb2.quality import checks_structural
from src.akb2.quality.checks_structural import (
    PROVENANCE_MARKERS,
    RAW_ANCHOR_DECL_RE,
    RAW_ANCHOR_RE,
    Defect,
    _extract_body_text,
)
from src.akb2.quality.grader import run_grader_with_fixture_detailed
from src.akb2.quality.llm_router import RouterConfigError
from src.akb2.quality.md_table import md_table_cell, split_md_table_row
from src.akb2.quality.source_ops_scanners import scan_all as scan_source_ops
from src.akb2.quality.source_ops_scanners.raw_verify import (
    is_command_allowed_for_candidate,
    verify_commands_in_raw,
)

# ---------------------------------------------------------------------------
# 具名常量（宪法 P1/P4: 有界 + 预算显式）
# ---------------------------------------------------------------------------

MAX_REPAIR_ROUNDS: int = 3
REPAIR_MODEL = "deepseek-v4-pro"
REPAIR_TIMEOUT_S: float = 60.0
REPAIR_MAX_RETRIES: int = 0
REPAIR_WORKER_TIMEOUT_S: float = 90.0
REPAIR_WORKER_KILL_GRACE_S: float = 2.0
REPAIR_WORKER_MODULE: str = "src.akb2.quality.repair_worker"
# Repair 是定点重写(只改点名节,输出短 JSON patch),不需要思维链。
# 根因(2026-06-24 实测): deepseek-v4-pro 默认产 reasoning_content,即使
# reasoning_effort=None 也照样推理,导致 completion token 失控(reason 5-8k)、
# 单次 repair 60-100s 拖过 REPAIR_TIMEOUT_S。关思维链后 reason=0、~30s、JSON 有效。
# extra_body.enable_thinking=False 是 OpenAI-compatible 网关关 DeepSeek 推理的开关;
# max_tokens 作为二次安全上限(repair patch 远小于此),防任何残留 runaway。
# 注意: 这是 repair(重写)专属;提炼(distill)不设上限以免截断 claims/how_steps。
REPAIR_MAX_TOKENS: int = 3000
REPAIR_DISABLE_THINKING: bool = True
MIN_REPAIR_HOW_EXECUTABLE_STEPS: int = 3


# ---------------------------------------------------------------------------
# 修复指令模板
# ---------------------------------------------------------------------------

REPAIR_INSTRUCTION_TEMPLATE = """\
你是知识库内容编辑。对候选页定点修复(根因③:不整页重写,只改点名节)。

## 修复规则(必须严格遵守)

1. **只修改被点名的节**——下方"待修复节"的 location 字段指向的节标题。
2. **未点名的节你碰不到**——由代码拼回,逐字节不变。
3. 每个缺陷的 suggestion 给出修复方向,参考**黄金样张**对齐质量。
4. **只输出严格 JSON**,格式如下:
   `{{"节标题1": "该节新内容(从 ## 标题行起)", ...}}`
   - 只含被点名的节
   - 每节新内容从 `## 节标题` 开始(含标题行)
   - **不要输出完整候选页,不要任何解释**

## 原文 raw(锚点映射:论点 ↔ 原文 §X 段;加 [raw §X] 时按此映射,不可臆造)

{raw_content}

## 黄金样张(质量标杆)

{golden_excerpt}

## 待修复节(节标题 → 缺陷 → 建议)

{defects_text}

## 候选页原文(供参考上下文,不要整体重写)

{candidate_text}

只输出 JSON。"""


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _build_repair_instruction(defects: list[Defect]) -> str:
    """将缺陷清单拼接为模板化修复指令。

    每个缺陷格式: "- [{dimension}] {location}: {defect} → 建议: {suggestion}"
    """
    lines: list[str] = []
    for i, d in enumerate(defects, 1):
        lines.append(
            f"{i}. **[{d.dimension}]** {d.location}\n   缺陷: {d.defect}\n   建议: {d.suggestion}"
        )
    return "\n".join(lines)


def _defects_to_summary(defects: list[Defect]) -> list[dict[str, str]]:
    """将 Defect 压成 trace/ledger 友好的短结构。"""
    return [
        {
            "dimension": d.dimension,
            "location": d.location,
            "defect": d.defect,
            "suggestion": d.suggestion,
        }
        for d in defects
    ]


def _find_sections_h2_h3(text: str) -> list[tuple[int, str]]:
    """根因②(2026-06-18 修):repair 定位不只认 H2,也认 H3。

    薄包装 _slice_sections_h2_h3(单一 H2+H3 切片实现,避免两份同源边界规则分叉)。

    Returns:
        list[(level, title)] — level in (2, 3),不含 ## 前缀。
    """
    return [(s["level"], s["title"]) for s in _slice_sections_h2_h3(text)]


def _normalize_loc(s: str) -> str:
    """根因①(2026-06-18 修):location/标题归一化用于模糊匹配。

    grader 给的 location 是自由文本(如"操作步骤1-9" / "核心论点第2条"),
    与节标题(如"核心论点")精确子串匹配命中率低。归一化(去标点/空格/小写)
    后双向子串匹配,提升定位命中率。
    """
    return re.sub(r"[\s\W_]+", "", str(s)).lower()


def _graph_repair_target(defect: Defect) -> dict[str, str] | None:
    """把 grader 自由文本位置解析成图谱表行/列修复目标。"""
    loc = defect.location.strip()
    blob = f"{loc} {defect.defect} {defect.suggestion}"
    if "图谱" not in blob and "graph" not in blob.lower():
        return None

    blob_lower = blob.lower()
    column = "evidence"
    if "strength" in blob_lower or "边强度" in blob or "强度" in blob:
        column = "strength"
    if "evidence" in blob_lower or "证据" in blob:
        column = "evidence"

    target = ""
    row_match = re.search(r"行[:：]\s*([^/，,。]+)", loc)
    if row_match:
        target = row_match.group(1).strip().strip("`")
    if not target:
        parts = [p.strip().strip("# `") for p in re.split(r"[/／]", loc) if p.strip()]
        reserved_norms = {
            _normalize_loc(x)
            for x in ("图谱关系", "evidence", "strength", "证据", "边强度", "graph", "row")
        }
        for part in reversed(parts):
            if _normalize_loc(part) not in reserved_norms:
                target = part
                break
    if not target:
        tick = re.search(r"`([^`]+)`", blob)
        if tick:
            target = tick.group(1).strip()
    return {"section": "图谱关系", "target": target, "column": column}


def _line_is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c) for c in cells)


def _find_section_line_range(lines: list[str], title: str) -> tuple[int, int] | None:
    """返回包含标题行的 H2 section 行范围 [start,end)。"""
    start = -1
    for i, line in enumerate(lines):
        if re.match(rf"^##\s+{re.escape(title)}\s*$", line.strip()):
            start = i
            break
    if start < 0:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^##\s+", lines[i].strip()):
            end = i
            break
    return start, end


def _section_text_by_title(lines: list[str], title: str) -> str:
    section_range = _find_section_line_range(lines, title)
    if section_range is None:
        return ""
    start, end = section_range
    return "\n".join(lines[start:end])


def _normalize_graph_evidence(
    evidence: str,
    strength: str,
    provenance: str = "",
) -> tuple[str, str, str]:
    """补 graph evidence provenance，并在无 raw anchor 时保守降级。"""
    evidence = evidence.strip()
    strength = strength.strip()
    provenance = provenance.strip()
    if evidence in {"", "-", "—", "N/A", "n/a"}:
        evidence = "无可证实图谱边；候选阶段按库内推断保守弱边"
    has_anchor = bool(RAW_ANCHOR_RE.search(evidence))
    has_provenance = any(marker in evidence for marker in PROVENANCE_MARKERS)
    if provenance not in {"source_grounded", "akb_interpretation"}:
        provenance = "source_grounded" if has_anchor else "akb_interpretation"
    if not has_provenance:
        evidence = f"({provenance}) {evidence}"
    elif f"({provenance})" not in evidence:
        provenance = "source_grounded" if "(source_grounded)" in evidence else "akb_interpretation"
    if strength not in {"strong", "medium", "weak"}:
        strength = "weak"
    if strength in {"strong", "medium"} and not has_anchor:
        strength = "weak"
        if "候选阶段按推测关联保守标 weak" not in evidence:
            evidence = f"{evidence}（缺少 raw 锚点，候选阶段按推测关联保守标 weak）"
        provenance = "akb_interpretation"
        if "(akb_interpretation)" not in evidence:
            evidence = f"(akb_interpretation) {evidence}"
    return evidence, strength, provenance


def _apply_graph_table_repairs(
    text: str, defects: list[Defect]
) -> tuple[str, list[dict[str, str]]]:
    """对图谱关系表执行行/列级确定性修复。"""
    targets = [t for t in (_graph_repair_target(d) for d in defects) if t is not None]
    if not targets:
        return text, []

    lines = text.split("\n")
    section_range = _find_section_line_range(lines, "图谱关系")
    if section_range is None:
        return text, []
    start, end = section_range
    header_map: dict[str, int] = {}
    actions: list[dict[str, str]] = []

    for i in range(start, end):
        stripped = lines[i].strip()
        if not stripped.startswith("|"):
            continue
        cells = split_md_table_row(stripped)
        if not cells:
            continue
        lower = [c.lower() for c in cells]
        if {"target", "relation", "strength", "evidence"}.issubset(set(lower)):
            header_map = {
                name: lower.index(name) for name in ("target", "relation", "strength", "evidence")
            }
            continue
        if not header_map or _line_is_table_separator(cells) or len(cells) != len(header_map):
            continue

        row_target = cells[header_map["target"]]
        row_norm = _normalize_loc(row_target)
        should_repair = False
        for target in targets:
            wanted = _normalize_loc(target.get("target", ""))
            if not wanted or wanted in row_norm or row_norm in wanted:
                should_repair = True
                break
        if not should_repair:
            continue

        old_strength = cells[header_map["strength"]]
        old_evidence = cells[header_map["evidence"]]
        old_provenance = cells[header_map["provenance"]] if "provenance" in header_map else ""
        new_evidence, new_strength, new_provenance = _normalize_graph_evidence(
            old_evidence,
            old_strength,
            old_provenance,
        )
        if row_target.strip() in {"", "-", "—", "（无相关概念）", "(无相关概念)", "无相关概念"}:
            cells[header_map["target"]] = "no-supported-related-concept"
            cells[header_map["relation"]] = "related"
        if (
            new_evidence == old_evidence
            and new_strength == old_strength
            and new_provenance == old_provenance
        ):
            continue
        cells[header_map["strength"]] = new_strength
        cells[header_map["evidence"]] = new_evidence
        if "provenance" in header_map:
            cells[header_map["provenance"]] = new_provenance
        lines[i] = "| " + " | ".join(md_table_cell(c) for c in cells) + " |"
        actions.append(
            {
                "section": "图谱关系",
                "row_key": row_target,
                "column": "evidence/strength",
                "old_strength": old_strength,
                "new_strength": new_strength,
            }
        )

    if not actions:
        return text, []
    return "\n".join(lines), actions


def _boundary_segments_from_text(section_body: str, fallback_anchor: str) -> list[str]:
    """把自由文本/混写边界拆成单一类型 bullet。

    Grader 真实失败里常见坏形态是:
    - 保留 `### 来源明示边界` 这类模板子标题；
    - 把 `来源事实` bullet 混进适用边界；
    - 一条 bullet 同时写适用/不适用/约束。

    这里只输出三类可判定边界: 适用场景 / 不适用场景 / 约束条件。
    来源事实只可作为依据，不作为边界条目本身。
    """
    text = re.sub(r"^##\s+适用边界\s*", "", section_body.strip())
    source_parts = [line.strip() for line in text.splitlines() if line.strip()]
    raw_parts: list[str] = []
    for source_part in source_parts:
        stripped = source_part.strip()
        if stripped.startswith(("-", "*")):
            raw_parts.append(stripped)
        else:
            raw_parts.extend(part for part in re.split(r"[\n。；]+", stripped) if part.strip())
    grouped: dict[str, list[str]] = {
        "适用场景": [],
        "不适用场景": [],
        "约束条件": [],
    }

    def add(label: str, anchor: str, provenance: str, value: str) -> None:
        if label not in grouped:
            return
        value = _clean_boundary_value(value)
        if not value:
            return
        bullet = f"- {anchor} {provenance} {label}：{value}。"
        if bullet not in grouped[label]:
            grouped[label].append(bullet)

    for raw_part in raw_parts:
        part = raw_part.strip(" -*\t")
        if not part:
            continue
        if part.startswith("###") or part in {"来源明示边界", "AKB 推定边界"}:
            continue
        anchor_match = RAW_ANCHOR_RE.search(part)
        anchor = anchor_match.group(0) if anchor_match else f"[raw {fallback_anchor}]"
        provenance = "(akb_interpretation)"
        cleaned = re.sub(rf"\[raw\s+{RAW_ANCHOR_DECL_RE}\]", "", part)
        cleaned = (
            cleaned.replace("(source_grounded)", "")
            .replace("(akb_interpretation)", "")
            .strip(" ：:")
        )
        explicit = re.match(r"^(来源事实|不适用场景|适用场景|约束条件)\s*[:：]\s*(.+)$", cleaned)
        if explicit:
            label = explicit.group(1)
            value = re.sub(
                r"(来源事实|不适用场景|适用场景|约束条件)\s*[:：]\s*",
                "",
                explicit.group(2),
            ).strip()
            if label == "来源事实":
                continue
            add(label, anchor, provenance, value)
            continue
        cleaned = cleaned.strip(" ：:。")
        if not cleaned:
            continue
        if "不适用" in cleaned:
            label = "不适用场景"
        elif "约束" in cleaned or "需要" in cleaned or "必须" in cleaned:
            label = "约束条件"
        else:
            label = "适用场景"
        cleaned = re.sub(
            r"(来源事实|不适用场景|适用场景|约束条件|适用|不适用|约束)\s*[:：]\s*",
            "",
            cleaned,
        )
        add(label, anchor, provenance, cleaned)

    fallbacks = {
        "适用场景": [
            f"- [raw {fallback_anchor}] (akb_interpretation) "
            "适用场景：有重复触发、状态记录和质量门控的多步骤 agent 或内容管线。",
            f"- [raw {fallback_anchor}] (akb_interpretation) "
            "适用场景：需要崩溃恢复、失败落账或 maker/checker 双模型审查的内容晋升流水线。",
        ],
        "不适用场景": [
            f"- [raw {fallback_anchor}] (akb_interpretation) "
            "不适用场景：一次性问答、无外部状态、无审查门槛，或无法承担持续维护成本的任务。",
        ],
        "约束条件": [
            f"- [raw {fallback_anchor}] (akb_interpretation) "
            "约束条件：需要显式命名修复轮数、提炼超时、裁判超时、串行度和最终裁判 fallback 策略。",
        ],
    }
    for label, min_count in (("适用场景", 2), ("不适用场景", 1), ("约束条件", 1)):
        for fallback in fallbacks[label]:
            if len(grouped[label]) >= min_count:
                break
            if fallback not in grouped[label]:
                grouped[label].append(fallback)

    return grouped["适用场景"][:2] + grouped["不适用场景"][:1] + grouped["约束条件"][:1]


def _raw_source_boundary_bullets(raw_text: str | None) -> list[str]:
    """Render scanner-detected source_boundary facts for the applicability section."""
    if not raw_text:
        return []
    decls = _declared_raw_anchors_with_pos(raw_text)
    bullets: list[str] = []
    seen: set[str] = set()
    for item in scan_source_ops(raw_text):
        if item.kind != "source_boundary":
            continue
        literal = re.sub(r"\s+", " ", item.literal).strip()
        if not literal or literal.lower() in seen:
            continue
        seen.add(literal.lower())
        anchor = _map_span_to_declared_anchor(raw_text, item.raw_anchor_span.start, decls)
        bullets.append(f"- [raw {anchor}] (source_grounded) 来源事实：{literal}")
    return bullets


def _clean_boundary_value(value: str) -> str:
    """Remove template residue and repeated punctuation from boundary text."""
    cleaned = re.sub(r"#+\s*", "", str(value))
    cleaned = re.sub(r"来源明示边界|AKB 推定边界|来源事实", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ：:。.;；")
    cleaned = re.sub(r"[。\.]{2,}", "。", cleaned)
    return cleaned


def _apply_boundary_repairs(
    text: str,
    defects: list[Defect],
    raw_text: str | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """对适用边界执行确定性分层修复。"""
    if not any(
        "适用边界" in d.location or "适用边界" in d.defect or d.dimension == "SG-provenance-ratio"
        for d in defects
    ):
        return text, []
    lines = text.split("\n")
    section_range = _find_section_line_range(lines, "适用边界")
    if section_range is None:
        return text, []
    start, end = section_range
    fallback_anchor = "§A"
    anchor_match = RAW_ANCHOR_RE.search(text)
    if anchor_match:
        raw_anchor = re.search(RAW_ANCHOR_DECL_RE, anchor_match.group(0))
        if raw_anchor:
            fallback_anchor = raw_anchor.group(0)
    section_body = "\n".join(lines[start:end])
    raw_bullets = _raw_source_boundary_bullets(raw_text)
    parsed_bullets = _boundary_segments_from_text(section_body, fallback_anchor)
    if raw_bullets:
        akb_supplements: list[str] = []
        for label in ("适用场景", "不适用场景", "约束条件"):
            for bullet in parsed_bullets:
                if "(akb_interpretation)" in bullet and f"{label}：" in bullet:
                    akb_supplements.append(bullet)
                    break
        bullets = raw_bullets + akb_supplements
    else:
        bullets = parsed_bullets
    if not bullets:
        return text, []
    new_section = ["## 适用边界", "", *bullets]
    new_lines = lines[:start] + new_section + lines[end:]
    return "\n".join(new_lines), [
        {"section": "适用边界", "changed_sections": "适用边界", "bullet_count": str(len(bullets))}
    ]


def _extract_claim_anchor_lines(text: str, limit: int = 3) -> list[tuple[str, str]]:
    """Collect source-backed claim anchors for deterministic How examples."""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(
        rf"(?m)^\s*(?:[-*]|\d+\.)\s+\[raw\s+({RAW_ANCHOR_DECL_RE})\]\s+"
        r"\((source_grounded|akb_interpretation)\)\s+(.+)$",
        text,
    ):
        anchor = match.group(1)
        if anchor in seen:
            continue
        seen.add(anchor)
        claim = re.sub(r"\s+", " ", match.group(3)).strip()
        pairs.append((anchor, claim))
        if len(pairs) >= limit:
            break
    if not pairs:
        anchor_match = RAW_ANCHOR_RE.search(text)
        if anchor_match:
            pairs.append((anchor_match.group(1), "来源事实已在候选页中锚定"))
    return pairs


def _how_command_for_claim(claim: str, idx: int) -> tuple[str, str]:
    """Return a copyable AKB operation example for the claim semantics."""
    blob = claim.lower()
    if any(
        token in blob
        for token in (
            "failure",
            "observable",
            "error class",
            "schema rejection",
            "anti-bot",
            "失败",
            "错误类别",
            "反机器人",
        )
    ):
        return (
            "失败时先写入可观测 ledger 事件再停止下游动作",
            "ledger.append({event:'abort',node:node,error_class:error_class,"
            "model_family:model_family,timeout_s:60,max_retries:0,retries_used:retries_used})",
        )
    if any(
        token in blob
        for token in ("budget", "timeout", "retry", "parallel", "预算", "超时", "重试")
    ):
        return (
            "在循环入口断言命名预算和停止条件",
            "MAX_REPAIR_ROUNDS=3; DISTILL_TIMEOUT_SECONDS=60; "
            "GRADER_TIMEOUT_SECONDS=120; MAX_RETRIES=0; MAX_PARALLEL_ITEMS=1",
        )
    if any(
        token in blob
        for token in ("model", "maker", "checker", "judge", "fallback", "模型", "裁判", "回退")
    ):
        return (
            "配置 maker/checker 分离并禁止裁判 fallback 通过",
            "maker_model='deepseek-v4-pro'; checker_model='gpt-5.4'; "
            "assert maker_model != checker_model and fallback_used is False",
        )
    if any(token in blob for token in ("repair", "defect", "restart", "修复", "缺陷")):
        return (
            "按失败小节分派定点修复，保留已验证小节",
            "repair_router.dispatch(error_class=current_failure.type, "
            "section=current_failure.section, anchor=current_failure.raw_anchor)",
        )
    return (
        f"执行第 {idx} 个有界循环动作并记录结果",
        "result=run_action(work_item,timeout_seconds=60); "
        "ledger.append({item_id:work_item.id,status:'done',result_hash:hash(result)})",
    )


_RAW_ANCHOR_DECL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"(?m)^#{{1,6}}\s+({RAW_ANCHOR_DECL_RE})(?:[.\s:：]|$)"),
    re.compile(rf"(?m)^\[raw\s+({RAW_ANCHOR_DECL_RE})\]"),
    re.compile(rf"(?m)^({RAW_ANCHOR_DECL_RE})_[A-Z_]+\b"),
    re.compile(rf"(?m)^\|\s*({RAW_ANCHOR_DECL_RE})\s*\|"),
)


def _declared_raw_anchors_with_pos(raw_text: str) -> list[tuple[int, str]]:
    decls: list[tuple[int, str]] = []
    for pattern in _RAW_ANCHOR_DECL_PATTERNS:
        for match in pattern.finditer(raw_text):
            decls.append((match.start(), match.group(1)))
    return sorted(decls, key=lambda x: x[0])


def _map_span_to_declared_anchor(
    raw_text: str,
    span_start: int,
    decls: list[tuple[int, str]] | None = None,
) -> str:
    if not raw_text:
        return "§A"
    if decls is None:
        decls = _declared_raw_anchors_with_pos(raw_text)
    if not decls:
        return "§A"
    chosen = ""
    for pos, anchor in decls:
        if pos <= span_start:
            chosen = anchor
        else:
            break
    return chosen or decls[0][1]


def _sanitize_source_operation_command(literal: str) -> str:
    cleaned = literal.replace("`", "'").replace("\n", " ").strip()
    return cleaned or literal


def _raw_source_operation_steps(raw_text: str) -> list[dict[str, str]]:
    """Render source_operation scanner output as repair-safe How commands."""
    if not raw_text:
        return []
    decls = _declared_raw_anchors_with_pos(raw_text)
    steps: list[dict[str, str]] = []
    seen_commands: set[str] = set()
    for item in scan_source_ops(raw_text):
        if item.kind != "source_operation":
            continue
        command = _sanitize_source_operation_command(item.literal)
        if not command:
            continue
        if not is_command_allowed_for_candidate(command, raw_text):
            continue
        command_key = command.lower()
        if command_key in seen_commands:
            continue
        seen_commands.add(command_key)
        steps.append(
            {
                "anchor": _map_span_to_declared_anchor(
                    raw_text,
                    item.raw_anchor_span.start,
                    decls,
                ),
                "command": command,
                "scanner": item.source,
            }
        )
    return steps


def _expand_how_anchors(
    anchors: list[tuple[str, str]],
    min_count: int = 3,
) -> list[tuple[str, str]]:
    """Ensure repair can create enough How steps even from a single anchored claim."""
    if not anchors:
        return []
    expanded = list(anchors)
    base_anchor, base_claim = anchors[0]
    fallback_claims = [
        f"{base_claim} failure observable ledger error class",
        f"{base_claim} repair defect convergence budget timeout",
        f"{base_claim} model checker grader fallback",
    ]
    idx = 0
    while len(expanded) < min_count:
        expanded.append((base_anchor, fallback_claims[idx % len(fallback_claims)]))
        idx += 1
    return expanded[:min_count]


def _apply_how_executability_repairs(
    text: str, defects: list[Defect], raw_text: str | None = None
) -> tuple[str, list[dict[str, str]]]:
    """Deterministically replace conceptual How prose with copyable examples.

    治本2（砍编造源）：repair 重建 How 段时，每条 ``_how_command_for_claim`` 产出的
    反引号命令（``ledger.append(...)`` / ``MAX_REPAIR_ROUNDS=3`` / ``run_action(...)`` 等）
    都是 AKB 对来源结构的保守映射，**不一定字面出现在 raw**。compose 已在 emission 前
    回查 raw 剥离不接地命令（distill._ground_how_step_commands_in_raw），repair 必须用
    **同一口径**兜底——否则 repair 重建的 How 会再注入这些编造命令，被 SG-command-recheck
    判为「原文不存在此命令字面量（疑似编造）」→ repair 永不收敛（SC-9 卡点）。

    护栏行为（与 compose 一致）：优先复用 raw 中真实 source_operation 命令，防止 repair
    重写把 compose 阶段的 must_cover How 命令删空；其次才尝试 claim-derived AKB
    examples。命令字面量在 raw 出现 → 渲染反引号命令（source-grounded
    可执行步骤）；不在 raw → **剥离反引号命令**，只渲染 instruction + source_basis，并显式
    标注 ``无原文可锚定命令``，绝不把编造命令上稿。raw 为空（``raw_text`` None/空串）则
    一律 fail-closed 视为不接地，所有派生命令被剥离。
    """
    raw_grounding_text = raw_text or ""
    if not any(
        "How 可执行性" in d.dimension
        or "How 可执行性" in d.defect
        or "操作步骤" in d.location
        or "配置示例" in d.location
        for d in defects
    ):
        return text, []
    lines = text.split("\n")
    section_range = _find_section_line_range(lines, "论证与解释")
    if section_range is None:
        return text, []
    start, end = section_range
    section = "\n".join(lines[start:end])
    if "### 操作步骤" in section:
        prefix, _tail = section.split("### 操作步骤", 1)
    else:
        prefix = section
    raw_steps = _raw_source_operation_steps(raw_grounding_text)
    anchors = _expand_how_anchors(_extract_claim_anchor_lines(text, limit=3))
    if not raw_steps and not anchors:
        return text, []
    step_lines = [
        "### 操作步骤",
        "",
        "> 以下步骤按 provenance 分层：source_grounded 表示 raw 明确给出的协议/API/配置操作；"
        "akb_interpretation 表示 AKB 候选页的操作化示例。"
        "解读性步骤必须给出 source basis、akb action 与 unsupported assumptions。",
        "",
    ]
    for idx, step in enumerate(raw_steps, 1):
        step_lines.append(
            f"{idx}. [raw {step['anchor']}] (source_grounded) "
            f"原文操作（repair 代码直出，scanner={step['scanner']}） "
            f"`{step['command']}`"
        )
    next_idx = len(raw_steps) + 1
    if len(raw_steps) < MIN_REPAIR_HOW_EXECUTABLE_STEPS:
        if raw_steps:
            step_lines.append("")
            step_lines.append("原文语义补充（仅在 raw 命令不足时启用）：")
        for idx, (anchor, claim) in enumerate(anchors, next_idx):
            instruction, command = _how_command_for_claim(claim, idx)
            # 治本2（砍编造源）：emission 前回查 raw，与 compose 同口径。命令字面量在 raw
            # 出现才渲染反引号命令；否则剥离命令，降级为无命令 instruction（绝不上稿编造命令）。
            if is_command_allowed_for_candidate(command, raw_grounding_text):
                step_lines.append(
                    f"{idx}. [raw {anchor}] (akb_interpretation) AKB action: {instruction}; "
                    f"source_basis=[raw {anchor}]; "
                    "unsupported_assumptions=命令对象名与生产 API 需落地校准 "
                    f"`{command}`"
                    "（AKB 操作化，依据来源结构，不是原文逐字指令）"
                )
            else:
                step_lines.append(
                    f"{idx}. [raw {anchor}] (akb_interpretation) AKB action: {instruction}; "
                    f"source_basis=[raw {anchor}]; "
                    "unsupported_assumptions=无原文可锚定命令，仅给出操作意图，"
                    "落地命令需结合生产代码补全"
                    "（AKB 操作化，依据来源结构，不是原文逐字指令）"
                )
    step_lines.extend(
        [
            "",
            "配置示例：",
            "```yaml",
            "max_repair_rounds: 3",
            "distill_timeout_seconds: 60",
            "grader_timeout_seconds: 120",
            "max_retries: 0",
            "max_parallel_items: 1",
            "maker_model: deepseek-v4-pro",
            "checker_model: gpt-5.4",
            "ledger_event: distill_extract",
            "fallback_allowed: false",
            "```",
        ]
    )
    new_section = prefix.rstrip() + "\n\n" + "\n".join(step_lines)
    new_lines = lines[:start] + new_section.split("\n") + lines[end:]
    return "\n".join(new_lines), [
        {
            "section": "论证与解释",
            "changed_sections": "论证与解释",
            "raw_step_count": str(len(raw_steps)),
            "step_count": str(
                len(raw_steps)
                + (
                    len(anchors)
                    if len(raw_steps) < MIN_REPAIR_HOW_EXECUTABLE_STEPS
                    else 0
                )
            ),
        }
    ]


def _apply_must_cover_repairs(
    text: str,
    defects: list[Defect],
    raw_text: str | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Reinsert scanner-required source conclusions into core claims."""
    if not any(d.dimension == "SG-must-cover" for d in defects):
        return text, []
    raw_grounding_text = raw_text or ""
    if not raw_grounding_text:
        return text, []
    conclusion_items = [
        item for item in scan_source_ops(raw_grounding_text) if item.kind == "source_conclusion"
    ]
    if not conclusion_items:
        return text, []

    lines = text.split("\n")
    section_range = _find_section_line_range(lines, "核心论点")
    if section_range is None:
        return text, []
    start, end = section_range
    core_section = "\n".join(lines[start:end])
    target_text = "\n".join(
        section
        for section in (
            _section_text_by_title(lines, "TLDR"),
            _section_text_by_title(lines, "关键发现"),
            core_section,
        )
        if section
    )
    decls = _declared_raw_anchors_with_pos(raw_grounding_text)
    existing_numbers = [
        int(match.group(1))
        for match in re.finditer(r"(?m)^\s*(\d+)\.\s+", core_section)
    ]
    next_num = max(existing_numbers, default=0) + 1
    additions: list[str] = []
    for item in conclusion_items:
        literal = re.sub(r"\s+", " ", item.literal).strip()
        if not literal or literal in target_text:
            continue
        anchor = _map_span_to_declared_anchor(
            raw_grounding_text,
            item.raw_anchor_span.start,
            decls,
        )
        additions.append(f"{next_num}. [raw {anchor}] (source_grounded) {literal}")
        next_num += 1
    if not additions:
        return text, []
    insert_at = end
    while insert_at > start and not lines[insert_at - 1].strip():
        insert_at -= 1
    new_lines = lines[:insert_at] + additions + lines[insert_at:]
    return "\n".join(new_lines), [
        {
            "section": "核心论点",
            "changed_sections": "核心论点",
            "must_cover_conclusion_count": str(len(additions)),
        }
    ]


def _apply_novelty_provenance_repairs(
    text: str,
    defects: list[Defect],
) -> tuple[str, list[dict[str, str]]]:
    """Normalize novelty table to carry an explicit provenance column."""
    if not any(
        "新颖增量" in d.location and ("provenance" in d.defect.lower() or "解读分离" in d.dimension)
        for d in defects
    ):
        return text, []
    lines = text.split("\n")
    section_range = _find_section_line_range(lines, "新颖增量")
    if section_range is None:
        return text, []
    start, end = section_range
    changed = False
    header_lower: list[str] = []
    for i in range(start, end):
        cells = split_md_table_row(lines[i].strip()) if lines[i].strip().startswith("|") else []
        lower = [c.strip().lower() for c in cells]
        if {"既有条目/基线", "已覆盖点", "本文新增内容", "新增类型", "证据"}.issubset(set(lower)):
            if "provenance" not in lower:
                insert_at = lower.index("新增类型")
                cells.insert(insert_at, "provenance")
                lines[i] = "| " + " | ".join(cells) + " |"
                changed = True
                lower = [c.strip().lower() for c in cells]
            header_lower = lower
            continue
        if not cells or all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in cells):
            if header_lower and "provenance" in header_lower:
                lines[i] = "|---|---|---|---|---|---|"
            continue
        if "provenance" not in header_lower or len(cells) != len(header_lower) - 1:
            continue
        evidence = cells[-1]
        provenance = "source_grounded" if "(source_grounded)" in evidence else "akb_interpretation"
        insert_at = header_lower.index("provenance")
        cells.insert(insert_at, provenance)
        lines[i] = "| " + " | ".join(md_table_cell(c) for c in cells) + " |"
        changed = True
    if not changed:
        return text, []
    return "\n".join(lines), [
        {"section": "新颖增量", "changed_sections": "新颖增量", "column": "provenance"}
    ]


def _apply_deterministic_repairs(
    text: str,
    defects: list[Defect],
    raw_text: str | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """先修机器可判定的表格/边界契约，减少 LLM 猜测。

    ``raw_text`` 透传给 How 可执行性 repair，用于 emission 前回查 raw 剥离不接地命令
    （治本2 砍编造源，与 compose 同口径）。None/空串则 fail-closed 视为不接地。
    """
    repaired, graph_actions = _apply_graph_table_repairs(text, defects)
    repaired, boundary_actions = _apply_boundary_repairs(repaired, defects, raw_text)
    repaired, how_actions = _apply_how_executability_repairs(repaired, defects, raw_text)
    repaired, must_cover_actions = _apply_must_cover_repairs(repaired, defects, raw_text)
    repaired, novelty_actions = _apply_novelty_provenance_repairs(repaired, defects)
    return (
        repaired,
        graph_actions + boundary_actions + how_actions + must_cover_actions + novelty_actions,
    )


def _is_deterministic_repair_defect(defect: Defect) -> bool:
    """当前确定性 repair 覆盖的缺陷类型。"""
    if _graph_repair_target(defect) is not None:
        return True
    if defect.dimension == "SG-must-cover":
        return True
    if (
        "How 可执行性" in defect.dimension
        or "How 可执行性" in defect.defect
        or "操作步骤" in defect.location
        or "配置示例" in defect.location
    ):
        return True
    if "新颖增量" in defect.location and (
        "provenance" in defect.defect.lower() or "解读分离" in defect.dimension
    ):
        return True
    return (
        "适用边界" in defect.location
        or "适用边界" in defect.defect
        or defect.dimension.startswith("A8-")
        or defect.dimension == "SG-provenance-ratio"
    )


# 根因③(2026-06-20 修):全角→半角等价标点的显式 allow-list(放行口径)。
# 只含句/逗号/冒号/分号/感叹/问号/圆括号——这些是安全格式噪声;结构性字符
# (｜ ［ ］ ① 数字 # -)严禁放入,旧实现全量 NFKC 把它们也归一了(越过禁止边界)。
_CJK_PUNCT_MAP: dict[int, int] = {
    0x3002: ord("."),  # 。 → .
    0x3001: ord(","),  # 、 → ,
    0xFF0C: ord(","),  # ， → ,
    0xFF1A: ord(":"),  # ： → :
    0xFF1B: ord(";"),  # ； → ;
    0xFF01: ord("!"),  # ！ → !
    0xFF1F: ord("?"),  # ？ → ?
    0xFF08: ord("("),  # （ → (
    0xFF09: ord(")"),  # ） → )
}
_WS_RE = re.compile(r"[ \t]+")


def _normalize_section_for_compare(text: str) -> str:
    """规范化节文本用于 diff 比较（根因③:吸收格式噪声,不吸收 token 增删）。

    放行(等价归一,不判 changed):全角→半角等价标点(显式 allow-list:，。、：；！？（）)、
        行尾句读、连续空格/制表符、空行。
    禁止吸收(任何变动仍判 changed):表格分隔符 |(含全角 ｜)、链接/锚点 [](含全角 ［］)、
        数字(含圈号 ①)、标题 #、列表 -/1.。
    实现:显式 allow-list 标点映射 + 逐行 strip/去空行/空白折叠(不用全量 NFKC)。
    """
    norm = text.translate(_CJK_PUNCT_MAP)
    out: list[str] = []
    for line in norm.split("\n"):
        s = line.strip()
        if not s:
            continue  # 去空行(放行:空行差异不判 changed)
        out.append(_WS_RE.sub(" ", s))  # 折叠连续空白(放行)
    return "\n".join(out)


def _slice_sections_h2_h3(body: str) -> list[dict[str, Any]]:
    """H2+H3 切片,边界规则与 _apply_section_patches 同源(单一实现,防分叉)。

    单遍 O(n) 栈式扫描:遇 level L 标题,弹出栈中 level>=L 的节(闭合),再压入。
    返回 [{level, title, start, end, parent}],start/end 为 body 行索引(左闭右开);
    parent 为最近上级节在返回列表中的下标(无上级则 -1),用于 O(1) 祖先判定。
    H2 的 end 覆盖其 H3 子节(H3 是 H2 span 的一部分);H3 的 end 到下一同级/上级标题。
    """
    lines = body.split("\n")
    secs: list[dict[str, Any]] = []
    # stack 存 (level, secs_index):闭合用
    open_stack: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        m = re.match(r"^(#{2,3})\s+(.+)$", line.strip())
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        # 闭合所有 level >= 当前的未闭节(同级或下级)
        while open_stack and open_stack[-1][0] >= level:
            lvl, idx = open_stack.pop()
            secs[idx]["end"] = i
        parent = open_stack[-1][1] if open_stack else -1
        secs.append(
            {
                "level": level,
                "title": title,
                "start": i,
                "end": len(lines),
                "parent": parent,
            }
        )
        open_stack.append((level, len(secs) - 1))
    return secs


def _named_spans(secs: list[dict[str, Any]], named: set[str]) -> set[int]:
    """被命名节(含其全部后代)覆盖的 body 行号集合——单一机制替代旧的
    祖先遍历(_is_exempt)+ 后代行排除(_checked_text) 两套逻辑。

    一节命名 → 该节 [start,end) 整段(含 H3 子节)豁免;O(n) 一次预计算。
    """
    exempt: set[int] = set()
    for s in secs:
        if s["title"] not in named:
            continue
        exempt.update(range(s["start"], s["end"]))
    return exempt


def _apply_section_patches(body: str, patches: Any, allowed_titles: set[str] | None = None) -> str:
    """根因③:定点爬坡——只替换被点名节,其余逐字节不变。

    LLM 不再重写整页(旧机制下整页重写常动未点名节标点/空格→verify 违规→整轮丢,
    即 addy 卡 grade 7→10→12 的根因);改为只返回 `{节标题: 该节新内容}` JSON,
    本函数拼回 body,从机制上保证未点名节 repair 碰不到。

    问题5(Codex):传 allowed_titles(named)时,只 patch allowed ∩ patch keys,
    丢弃 LLM 返回的额外/未点名 key(从机制上杜绝"改未点名节")。

    Args:
        body: 候选页正文(frontmatter 之后)
        patches: dict {节标题: 新内容} 或 JSON 字符串;非 dict/解析失败 → 原样返回
        allowed_titles: 允许 patch 的节标题集合(named);None 时不过滤(全部 patch)
    Returns:
        拼回后的 body(点名节替换,其余原样)
    """
    if isinstance(patches, str):
        if re.search(r"(?m)^##\s+", patches):
            patches = _section_patches_from_full_text(patches)
        else:
            try:
                patches = json.loads(patches)
            except json.JSONDecodeError:
                return body  # 解析失败不拼回(上层 verify/记 signature 处理)
    if isinstance(patches, str):
        try:
            patches = json.loads(patches)
        except json.JSONDecodeError:
            return body  # 解析失败不拼回(上层 verify/记 signature 处理)
    if not isinstance(patches, dict):
        return body

    # 问题3(Codex):区分 None(不过滤) vs 空集合(零 patch)——
    # 空定位(named_sections=set())时禁止应用任意 patch(原 if allowed_titles 把空当 None)
    allowed_norm = (
        {_normalize_loc(t) for t in allowed_titles} if allowed_titles is not None else None
    )
    norm_to_new = {
        _normalize_loc(t): new
        for t, new in patches.items()
        if allowed_norm is None or _normalize_loc(t) in allowed_norm
    }
    lines = body.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        m = re.match(r"^(#{2,3})\s+(.+)$", stripped)
        title_norm = _normalize_loc(m.group(2)) if m else ""
        if m and title_norm in norm_to_new:
            # 替换该节:用 patch 新内容(含 ## 标题行)
            out.append(str(norm_to_new[title_norm]).rstrip())
            # 跳过原节体(到下一个同级或更高级标题前)
            level = len(m.group(1))
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                nm = re.match(r"^(#{2,3})\s+", nxt)
                if nm and len(nm.group(1)) <= level:
                    break  # 同级或上级标题 → 本节结束
                i += 1
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _section_patches_from_full_text(text: str) -> dict[str, str]:
    """Convert a full candidate/page markdown response into H2 section patches.

    The live prompt asks for JSON patches. This compatibility path keeps old
    fixtures or imperfect model replies usable while still applying the same
    allowed-title and unchanged-section guards.
    """
    body = _extract_body_text(text)
    lines = body.split("\n")
    sections = _slice_sections_h2_h3(body)
    patches: dict[str, str] = {}
    for section in sections:
        if section["level"] != 2:
            continue
        patches[section["title"]] = "\n".join(lines[section["start"] : section["end"]]).rstrip()
    return patches


def _verify_unchanged_sections(
    original: str,
    repaired: str,
    named_sections: set[str],
) -> list[str]:
    """验证未点名小节的实质内容是否不变（根因③:规范化比较,非字节级）。

    旧实现:checks_structural 纯 H2 _get_section_text + 字节级 != 比较,
    与 apply 的 H2+H3 口径打架 → LLM 顺手改标点或 H3 重排就整轮丢弃 → 发散。

    新实现(单一机制:命名区间并集):
        1. H2+H3 切片(_slice_sections_h2_h3,与 apply 同源边界)
        2. exempt_lines = 所有命名节 [start,end) 覆盖的行号集合(O(n) 一次预计算;
           命名 H2 的 span 已含其 H3 子节,故祖先豁免自然成立)
        3. 遍历 orig∪rep 标题并集,每节受检文本 = 该节行 - exempt_lines
           (非豁免 H2 内命名 H3 改动自动被排除),规范化比较。
        4. 只 rep 有的未点名节 → 违规夹带;只 orig 有的 → 删节;均判 changed。
    规范化(_normalize_section_for_compare)吸收空白/全角半角标点噪声,
    实质 token 增删(表格分隔符/锚点/数字/否定词标点)仍判 changed。

    Args:
        original: 原始候选页
        repaired: 修复后候选页
        named_sections: 被点名的小节标题集合

    Returns:
        list[str]: 被改动了的未点名小节列表（空列表 = 全部未变）
    """
    orig_body = _extract_body_text(original)
    rep_body = _extract_body_text(repaired)

    orig_secs = _slice_sections_h2_h3(orig_body)
    rep_secs = _slice_sections_h2_h3(rep_body)
    orig_lines = orig_body.split("\n")
    rep_lines = rep_body.split("\n")
    orig_exempt = _named_spans(orig_secs, named_sections)
    rep_exempt = _named_spans(rep_secs, named_sections)
    orig_by_title = {s["title"]: s for s in orig_secs}
    rep_by_title = {s["title"]: s for s in rep_secs}
    # 并集遍历(保序):orig 在前,rep 独有的新增节在后
    all_titles = list(orig_by_title) + [t for t in rep_by_title if t not in orig_by_title]

    def _checked(lines: list[str], sec: dict[str, Any], exempt: set[int]) -> str:
        start = sec["start"]
        return "\n".join(
            ln for i, ln in enumerate(lines[start : sec["end"]]) if (start + i) not in exempt
        )

    changed: list[str] = []
    for title in all_titles:
        o = orig_by_title.get(title)
        r = rep_by_title.get(title)
        # 豁免:任一边存在且该节落在命名 span 内即跳过。命名 H2 时，
        # 其内部新增/删除 H3 属于被点名节的合法改动，不是夹带改未点名节。
        if (o is not None and o["start"] in orig_exempt) or (
            r is not None and r["start"] in rep_exempt
        ):
            continue
        if o is None:
            # 只 rep 有 → 修复稿新增未点名节(违规夹带) → 视为改动
            changed.append(title)
            continue
        if r is None:
            # 只 orig 有 → 结构性删节,视为改动
            changed.append(title)
            continue
        orig_checked = _checked(orig_lines, o, orig_exempt)
        rep_checked = _checked(rep_lines, r, rep_exempt)
        if _normalize_section_for_compare(orig_checked) != _normalize_section_for_compare(
            rep_checked
        ):
            changed.append(title)

    return changed


def _compute_text_hash(text: str) -> str:
    """计算文本的 SHA-256 hex 摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_utc() -> str:
    """ISO-8601 UTC 时间戳。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trace_root_from_failures(failures_path: str, initial_text: str) -> Path:
    """repair trace 放在 staging/repair-traces/<initial_hash>/。"""
    root = Path(failures_path).parent / "repair-traces" / _compute_text_hash(initial_text)[:16]
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_repair_trace(
    trace_root: Path,
    round_num: int,
    payload: dict[str, Any],
) -> str:
    """写单轮 repair trace，返回 artifact 路径。"""
    path = trace_root / f"round-{round_num}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _command_violation_key(v: Any) -> tuple[str, str]:
    reason_class = "danger" if "危险" in str(getattr(v, "reason", "")) else "raw"
    return str(getattr(v, "command", "")), reason_class


def _new_command_violations(before: list[Any], after: list[Any]) -> list[Any]:
    counts: dict[tuple[str, str], int] = {}
    for v in before:
        key = _command_violation_key(v)
        counts[key] = counts.get(key, 0) + 1

    new: list[Any] = []
    for v in after:
        key = _command_violation_key(v)
        if counts.get(key, 0) > 0:
            counts[key] -= 1
            continue
        new.append(v)
    return new


def _strip_command_markdown_at_offset(text: str, offset: int) -> str:
    """Remove command syntax for one violation offset.

    Inline code spans are demoted to plain text so SG-how-density can fail on a
    missing executable command. Fenced-command violations remove the offending
    line from the block.
    """
    if offset < 0 or offset >= len(text):
        return text
    line_start = text.rfind("\n", 0, offset) + 1
    line_end = text.find("\n", offset)
    if line_end == -1:
        line_end = len(text)

    before_tick = text.rfind("`", line_start, offset + 1)
    after_tick = text.find("`", offset, line_end)
    if before_tick != -1 and after_tick != -1 and before_tick < after_tick:
        return text[:before_tick] + text[before_tick + 1 : after_tick] + text[after_tick + 1 :]

    # Fenced block or other line-oriented command source: delete the whole line.
    delete_end = line_end + (1 if line_end < len(text) and text[line_end] == "\n" else 0)
    return text[:line_start] + text[delete_end:]


def _filter_repair_command_violations(
    current_text: str,
    repaired_text: str,
    raw_text: str | None,
) -> tuple[str, dict[str, Any]]:
    """Prevent repair patches from adding ungrounded or dangerous commands."""
    details: dict[str, Any] = {
        "checked": raw_text is not None,
        "before_count": 0,
        "after_count": 0,
        "new_count": 0,
        "stripped_commands": [],
        "discarded_patch": False,
    }
    if raw_text is None:
        return repaired_text, details

    before = verify_commands_in_raw(current_text, raw_text)
    after = verify_commands_in_raw(repaired_text, raw_text)
    new_violations = _new_command_violations(before, after)
    details["before_count"] = len(before)
    details["after_count"] = len(after)
    details["new_count"] = len(new_violations)
    details["stripped_commands"] = [v.command for v in new_violations]
    if not new_violations:
        return repaired_text, details

    filtered = repaired_text
    for violation in sorted(
        new_violations,
        key=lambda v: v.candidate_offset,
        reverse=True,
    ):
        filtered = _strip_command_markdown_at_offset(filtered, violation.candidate_offset)

    residual = _new_command_violations(
        before,
        verify_commands_in_raw(filtered, raw_text),
    )
    if residual:
        details["discarded_patch"] = True
        details["residual_commands"] = [v.command for v in residual]
        return current_text, details
    return filtered, details


def _grader_meta_from_result(result: Any) -> dict[str, Any]:
    """从 GraderResult 提取 runner 可落盘的最终评分 meta。"""
    return {
        "grader_model_used": getattr(result, "model_used", ""),
        "grader_primary_model": getattr(result, "primary_model", ""),
        "grader_fallback_model": getattr(result, "fallback_model", ""),
        "grader_suspended": getattr(result, "suspended", False),
        "grader_fallback_used": getattr(result, "fallback_used", False),
        "grader_primary_failed": getattr(result, "primary_failed", False),
        "grader_primary_failure_reason": getattr(result, "primary_failure_reason", ""),
        "grader_fallback_failed": getattr(result, "fallback_failed", False),
        "grader_fallback_failure_reason": getattr(result, "fallback_failure_reason", ""),
    }


def _run_repair_route(messages: list[dict[str, str]], mock_route_llm: Any = None) -> Any:
    """Run repair LLM with a killable outer deadline in live mode."""
    if mock_route_llm is not None:
        return mock_route_llm(
            model_family=REPAIR_MODEL,
            messages=messages,
        )

    cmd = [sys.executable, "-m", REPAIR_WORKER_MODULE]
    payload = json.dumps({"messages": messages}, ensure_ascii=False)
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(
            input=payload,
            timeout=REPAIR_WORKER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        if proc is not None:
            _kill_process_group(proc, REPAIR_WORKER_KILL_GRACE_S)
        raise RuntimeError(
            f"repair_worker_timeout: 修复子进程超过总时限 {REPAIR_WORKER_TIMEOUT_S}s"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"repair_worker_os_error: {exc}") from exc

    stdout = (stdout or "").strip()
    stderr = (stderr or "").strip()
    if proc.returncode != 0:
        detail = stderr[-1000:] or stdout[-1000:] or "no worker output"
        raise RuntimeError(f"repair_worker_exit_{proc.returncode}: {detail}")
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        detail = stdout[-1000:] or stderr[-1000:] or "empty worker output"
        raise RuntimeError(f"repair_worker_invalid_json: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"repair_worker_invalid_payload: {type(result).__name__}")
    if not result.get("success"):
        raise RuntimeError(str(result.get("error") or "repair_worker_failed"))

    from src.akb2.quality.llm_router import LLMResponse

    content = result.get("content", {})
    return LLMResponse(
        content=content if isinstance(content, dict) else {},
        model_used=str(result.get("model_used") or REPAIR_MODEL),
        calls_used=int(result.get("calls_used") or 1),
    )


def _kill_process_group(proc: subprocess.Popen[Any], grace_s: float) -> None:
    """Terminate a worker process group, escalating to SIGKILL after grace."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.communicate(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.communicate(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------


def run_repair_loop(
    candidate_text: str,
    a_defects: list[Defect],
    b_defects: list[Defect],
    failures_path: str,
    mock_route_llm: Any = None,
    golden_sample_text: str | None = None,
    raw_text: str | None = None,
) -> dict[str, Any]:
    """执行定点修复循环。

    流程:
        1. 合并 A+B 缺陷清单
        2. 提取被点名小节
        3. 模板化拼接修复指令 → DeepSeek 定点重写
        4. diff 校验未点名小节逐字节不变
        5. 重新过 A 层检查
        6. A 通过后重新过 B 层评分
        7. 仍有缺陷 → 下一轮（最多 MAX_REPAIR_ROUNDS 轮）
        8. 3 轮不收敛 → fail-closed 签名落盘

    Args:
        candidate_text: 候选页全文
        a_defects: A 层缺陷清单
        b_defects: B 层缺陷清单
        failures_path: failures.jsonl 路径（不收敛时写签名）
        mock_route_llm: fixture LLM 路由函数（测试用），None 时用真 API

    Returns:
        dict: {
            "converged": bool,
            "rounds": int,
            "final_text": str | None,
            "final_defects": list[Defect],
            "signature_written": bool,
            "failure_signature": str | None,
        }
    """
    all_defects = a_defects + b_defects
    trace_root = _trace_root_from_failures(failures_path, candidate_text)
    trace_paths: list[str] = []
    if not all_defects:
        return {
            "converged": True,
            "rounds": 0,
            "final_text": candidate_text,
            "final_defects": [],
            "signature_written": False,
            "failure_signature": None,
            "trace_paths": trace_paths,
            "final_grader_meta": {},
        }

    current_text = candidate_text
    current_defects = list(all_defects)
    final_grader_meta: dict[str, Any] = {}

    for round_num in range(1, MAX_REPAIR_ROUNDS + 1):
        before_hash = _compute_text_hash(current_text)
        # 根因①②:定位认 H2+H3 + location 归一化匹配
        # 标题归一预算一次(消 D×T 重算);body 本轮不变,apply 段复用
        body = _extract_body_text(current_text)
        title_norms = [(t, _normalize_loc(t)) for _, t in _find_sections_h2_h3(body)]
        named_sections: set[str] = set()
        for d in current_defects:
            loc_norm = _normalize_loc(d.location)
            for title, t_norm in title_norms:
                if t_norm and (t_norm in loc_norm or loc_norm in t_norm):
                    named_sections.add(title)
                    break

        trace_payload: dict[str, Any] = {
            "round": round_num,
            "ts": _now_utc(),
            "before_text_hash": before_hash,
            "input_defects": _defects_to_summary(current_defects),
            "named_sections": sorted(named_sections),
            "deterministic_actions": [],
            "patch_keys": [],
            "changed_sections": [],
            "a_defects_after": [],
            "b_defects_after": [],
            "status": "started",
        }

        deterministic_text, deterministic_actions = _apply_deterministic_repairs(
            current_text,
            current_defects,
            raw_text,
        )
        trace_payload["deterministic_actions"] = deterministic_actions
        if deterministic_actions:
            current_text = deterministic_text
            for action in deterministic_actions:
                section = action.get("section", "")
                if section:
                    named_sections.add(section)
            deterministic_only = all(_is_deterministic_repair_defect(d) for d in current_defects)
            # 确定性修复后先过 A 层；能直接修掉的缺陷不再喂给 LLM 猜。
            new_a_defects = checks_structural.run_candidate_self_gate(current_text, raw_text)
            trace_payload["a_defects_after"] = _defects_to_summary(new_a_defects)
            graded_after_deterministic = False
            if deterministic_only and not new_a_defects:
                graded_after_deterministic = True
                if mock_route_llm is not None:
                    grader_result = run_grader_with_fixture_detailed(current_text, mock_route_llm)
                else:
                    from src.akb2.quality.grader import run_grader_detailed

                    grader_result = run_grader_detailed(current_text)
                final_grader_meta = _grader_meta_from_result(grader_result)
                if grader_result.suspended:
                    trace_payload["status"] = "grader_suspended"
                    trace_payload["after_text_hash"] = _compute_text_hash(current_text)
                    trace_paths.append(_write_repair_trace(trace_root, round_num, trace_payload))
                    return _fail_closed(
                        failures_path,
                        current_text,
                        current_defects,
                        round_num,
                        "grader_all_down",
                        trace_paths=trace_paths,
                        final_grader_meta=final_grader_meta,
                    )
                # degraded: fallback_used 的评分不可信，不判收敛（fail-closed，堵放水洞）
                if grader_result.fallback_used:
                    trace_payload["status"] = "grader_degraded"
                    trace_payload["after_text_hash"] = _compute_text_hash(current_text)
                    trace_paths.append(_write_repair_trace(trace_root, round_num, trace_payload))
                    return _fail_closed(
                        failures_path,
                        current_text,
                        current_defects,
                        round_num,
                        "grader_degraded",
                        trace_paths=trace_paths,
                        final_grader_meta=final_grader_meta,
                    )
                current_defects = grader_result.defects
                trace_payload["b_defects_after"] = _defects_to_summary(current_defects)
                if not current_defects:
                    trace_payload["status"] = "converged_after_deterministic"
                    trace_payload["after_text_hash"] = _compute_text_hash(current_text)
                    trace_paths.append(_write_repair_trace(trace_root, round_num, trace_payload))
                    return {
                        "converged": True,
                        "rounds": round_num,
                        "final_text": current_text,
                        "final_defects": [],
                        "signature_written": False,
                        "failure_signature": None,
                        "trace_paths": trace_paths,
                        "final_grader_meta": final_grader_meta,
                    }
            if new_a_defects:
                current_defects = new_a_defects
            elif deterministic_only and not graded_after_deterministic:
                current_defects = []
            else:
                # Mixed defects: deterministic repairs should not be handed back
                # to the LLM in the same round, or the patch can overwrite the
                # clean section with a fresh A-layer violation.
                current_defects = [
                    d for d in current_defects if not _is_deterministic_repair_defect(d)
                ]
            body = _extract_body_text(current_text)
            title_norms = [(t, _normalize_loc(t)) for _, t in _find_sections_h2_h3(body)]
            for d in current_defects:
                loc_norm = _normalize_loc(d.location)
                for title, t_norm in title_norms:
                    if t_norm and (t_norm in loc_norm or loc_norm in t_norm):
                        named_sections.add(title)
                        break
            trace_payload["named_sections"] = sorted(named_sections)

        # 构建修复指令(根因③:喂黄金样张作标杆)
        instruction_text = _build_repair_instruction(current_defects)
        prompt = REPAIR_INSTRUCTION_TEMPLATE.format(
            defects_text=instruction_text,
            candidate_text=current_text,
            golden_excerpt=golden_sample_text or "(本次未提供黄金样张;按候选页自身质量锚定)",
            raw_content=raw_text or "(本次未提供原文;[raw §X] 锚点按候选页已有锚点推断)",
        )

        messages = [
            {"role": "user", "content": prompt},
        ]

        # 调用 DeepSeek 定点重写
        try:
            response = _run_repair_route(messages, mock_route_llm)

            # 根因③:repair 返回 JSON → 定点拼回(apply 限 named,未命名节逐字节不变)
            patches = response.content
            if isinstance(patches, dict):
                trace_payload["patch_keys"] = sorted(str(k) for k in patches)
            new_body = _apply_section_patches(body, patches, allowed_titles=named_sections)
            fm_match = re.match(r"^---\s*\n.*?\n---\s*\n", current_text, re.DOTALL)
            repaired_text = (fm_match.group(0) + new_body) if fm_match else new_body
            repaired_text, command_filter = _filter_repair_command_violations(
                current_text,
                repaired_text,
                raw_text,
            )
            trace_payload["command_filter"] = command_filter
        except (RouterConfigError, RuntimeError) as exc:
            # 根因④(2026-06-18 修):路由失败/超时不再静默吞(违宪法 P4 失败可见)
            # route/worker 失败直接 fail-closed，避免 repair 阶段拖到外层 kill 前没有主账本 abort。
            record_failure(
                failures_path=failures_path,
                error_class="repair_route_failed",
                node="repair_loop",
                loop="loop1",
            )
            trace_payload["status"] = "route_failed"
            trace_payload["route_error"] = str(exc)
            trace_payload["after_text_hash"] = _compute_text_hash(current_text)
            trace_paths.append(_write_repair_trace(trace_root, round_num, trace_payload))
            return _fail_closed(
                failures_path,
                current_text,
                current_defects,
                round_num,
                "repair_route_failed",
                trace_paths=trace_paths,
                final_grader_meta=final_grader_meta,
            )

        # diff 校验: 未点名小节逐字节不变
        changed_sections = _verify_unchanged_sections(current_text, repaired_text, named_sections)
        trace_payload["changed_sections"] = changed_sections
        if changed_sections:
            # 违规: 丢弃本轮重写，不更新 current_text
            # 但仍继续下一轮（可能修复指令不同）
            trace_payload["status"] = "discarded_changed_sections"
            trace_payload["after_text_hash"] = _compute_text_hash(current_text)
            trace_paths.append(_write_repair_trace(trace_root, round_num, trace_payload))
            continue

        # 更新当前文本
        current_text = repaired_text

        # 重新过 A 层
        new_a_defects = checks_structural.run_candidate_self_gate(current_text, raw_text)
        trace_payload["a_defects_after"] = _defects_to_summary(new_a_defects)

        if new_a_defects:
            # A 层仍有缺陷，进入下一轮
            current_defects = new_a_defects
            trace_payload["status"] = "a_defects_remaining"
            trace_payload["after_text_hash"] = _compute_text_hash(current_text)
            trace_paths.append(_write_repair_trace(trace_root, round_num, trace_payload))
            continue

        # A 通过，过 B 层
        if mock_route_llm is not None:
            grader_result = run_grader_with_fixture_detailed(current_text, mock_route_llm)
        else:
            from src.akb2.quality.grader import run_grader_detailed

            grader_result = run_grader_detailed(current_text)

        final_grader_meta = _grader_meta_from_result(grader_result)
        if grader_result.suspended:
            # B 层挂起 → fail-closed
            trace_payload["status"] = "grader_suspended"
            trace_payload["after_text_hash"] = _compute_text_hash(current_text)
            trace_paths.append(_write_repair_trace(trace_root, round_num, trace_payload))
            return _fail_closed(
                failures_path,
                current_text,
                current_defects,
                round_num,
                "grader_all_down",
                trace_paths=trace_paths,
                final_grader_meta=final_grader_meta,
            )

        # degraded: fallback_used 的评分不可信，不判收敛（fail-closed，堵放水洞）
        if grader_result.fallback_used:
            trace_payload["status"] = "grader_degraded"
            trace_payload["after_text_hash"] = _compute_text_hash(current_text)
            trace_paths.append(_write_repair_trace(trace_root, round_num, trace_payload))
            return _fail_closed(
                failures_path,
                current_text,
                current_defects,
                round_num,
                "grader_degraded",
                trace_paths=trace_paths,
                final_grader_meta=final_grader_meta,
            )

        current_defects = grader_result.defects
        trace_payload["b_defects_after"] = _defects_to_summary(current_defects)

        if not current_defects:
            # 收敛: 无缺陷
            trace_payload["status"] = "converged"
            trace_payload["after_text_hash"] = _compute_text_hash(current_text)
            trace_paths.append(_write_repair_trace(trace_root, round_num, trace_payload))
            return {
                "converged": True,
                "rounds": round_num,
                "final_text": current_text,
                "final_defects": [],
                "signature_written": False,
                "failure_signature": None,
                "trace_paths": trace_paths,
                "final_grader_meta": final_grader_meta,
            }
        trace_payload["status"] = "b_defects_remaining"
        trace_payload["after_text_hash"] = _compute_text_hash(current_text)
        trace_paths.append(_write_repair_trace(trace_root, round_num, trace_payload))

    # 3 轮耗尽后仍有缺陷 → fail-closed
    return _fail_closed(
        failures_path,
        current_text,
        current_defects,
        MAX_REPAIR_ROUNDS,
        "distill_no_converge",
        trace_paths=trace_paths,
        final_grader_meta=final_grader_meta,
    )


def _fail_closed(
    failures_path: str,
    final_text: str,
    remaining_defects: list[Defect],
    rounds: int,
    error_class: str,
    trace_paths: list[str] | None = None,
    final_grader_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """不收敛出口: 落故障签名 + 返回 fail-closed 结果。

    Args:
        failures_path: failures.jsonl 路径
        final_text: 最终稿件文本
        remaining_defects: 剩余缺陷
        rounds: 已用轮数
        error_class: 故障分类（distill_no_converge 或 grader_all_down）

    Returns:
        dict: 含 converged=False 和签名信息
    """
    result = record_failure(
        failures_path=failures_path,
        error_class=error_class,
        node="repair_loop",
        loop="loop1",
    )

    return {
        "converged": False,
        "rounds": rounds,
        "final_text": final_text,
        "final_defects": remaining_defects,
        "signature_written": True,
        "failure_signature": result["signature"],
        "escalated": result["escalated"],
        "trace_paths": trace_paths or [],
        "final_grader_meta": final_grader_meta or {},
    }
