"""A 层结构校验器 — 6 项纯代码检查，零 LLM 调用。

检查项:
    A1: 模板 10 节齐（检查 10 个 section 标题存在）
    A2: frontmatter 硬字段（source_refs≥1, future_questions 2-3,
        related_concepts≥2, gate_decision 必须 null, gate_reason≤150 字符）
    A3: [raw §X] 锚点在 raw 文件中真实存在
    A4: 图谱边四列齐（target/relation/strength/evidence）
    A7: 图谱 evidence 可追溯（raw anchor 或 akb_interpretation）
    A8: 适用边界逐条 provenance 分层
    A5: wikilink 死链检测（需在 pending 队列或已知概念列表中）
    A6: 维护记录 ≤5 条

输出: Defect 清单，与 B 层 grader 同构。

宪法对齐:
    - P6: 黄金样张质量锚定 — 10 节模板来自黄金样张 14 步清单
    - P7: Maker/Checker 异族分离 — A 层纯代码，与 B 层 LLM 裁决分离
    - P4: 预算显式 — 无 LLM 调用，零成本
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from src.akb2.quality.md_table import split_md_table_row
from src.akb2.quality.raw_anchors import (
    ANCHOR_TOKEN_PAT,
    RAW_ANCHOR_REF_RE,
    canonicalize_anchor,
    declared_anchor_set,
)
from src.akb2.quality.source_ops_scanners import scan_all as _scan_source_evidence
from src.akb2.quality.source_ops_scanners.raw_verify import (
    verify_commands_in_raw as _verify_commands_in_raw,
)

# ---------------------------------------------------------------------------
# 公开类型
# ---------------------------------------------------------------------------


@dataclass
class Defect:
    """可定位缺陷 — 与 B 层 grader 输出同构。

    dimension: 缺陷所属维度（A1-A6 或 B 层 10 维量规名）
    location:  缺陷在稿件中的位置（节标题 / frontmatter 字段 / 具体行）
    defect:    缺陷描述
    suggestion: 修复建议
    """

    dimension: str
    location: str
    defect: str
    suggestion: str


# ---------------------------------------------------------------------------
# 具名常量（宪法 P4: 预算显式）
# ---------------------------------------------------------------------------

# 10 节模板标题（黄金样张 14 步清单 §4 候选页组装）
TEN_SECTION_TITLES: list[str] = [
    "TLDR",
    "关键发现",
    "入选理由",
    "证据地图",
    "核心论点",
    "论证与解释",
    "适用边界",
    "图谱关系",
    "Future Questions",
    "维护记录",
]

# A2 约束
MAX_GATE_REASON_CHARS: int = 150
MIN_SOURCE_REFS: int = 1
MIN_FUTURE_QUESTIONS: int = 2
MAX_FUTURE_QUESTIONS: int = 3
MIN_RELATED_CONCEPTS: int = 2

# A6 约束
MAX_MAINTENANCE_RECORDS: int = 5

# A4 图谱边契约。新候选页输出 5 列，legacy 4 列仍可读取并按
# evidence 内的 provenance marker 校验，避免旧 fixture 全量失效。
GRAPH_EDGE_REQUIRED_COLUMNS: tuple[str, ...] = ("target", "relation", "strength", "evidence")
GRAPH_EDGE_COLUMNS: tuple[str, ...] = (
    "target",
    "relation",
    "strength",
    "provenance",
    "evidence",
)
GRAPH_EDGE_STRENGTHS: set[str] = {"strong", "medium", "weak"}
GRAPH_EDGE_PROVENANCES: set[str] = {"source_grounded", "akb_interpretation"}
PROVENANCE_MARKERS: tuple[str, ...] = ("(source_grounded)", "(akb_interpretation)")
RAW_ANCHOR_TOKEN_RE = re.compile(ANCHOR_TOKEN_PAT)
RAW_ANCHOR_RE = RAW_ANCHOR_REF_RE
RAW_ANCHOR_DECL_RE = ANCHOR_TOKEN_PAT
MD_TABLE_SEPARATOR_RE = re.compile(r":?-{3,}:?")

# Candidate self-gate 约束：这些不是基础结构格式检查，而是 maker 侧出稿前
# 必须满足的内容质量契约，防止坏候选流入 grader/promote。
MAX_AKB_INTERPRETATION_RATIO: float = 0.40
MIN_HOW_EXECUTABLE_STEPS: int = 3
# step-3 content_lane 枚举（concept/procedural，默认 procedural 兼容）
CONTENT_LANE_CONCEPT: str = "concept"
# step-3 SC-3e：concept 图谱可调用性满分（边 resolve 到真实节点/pending 共指）/ 假边封顶。
GRAPH_RESOLVE_FULL_SCORE: float = 2.0
# F14-A: GRAPH_RESOLVE_UNRESOLVED_CAP(旧 0.5)是死常量无消费方,已删。
# pending 队列共指阈值：边 target 未命中已有节点但 pending 中 ≥此数共指也算可 resolve。
GRAPH_PENDING_COREF_MIN: int = 3


def _is_concept_lane(content_lane: str) -> bool:
    """判断 content_lane 是否为概念型车道。其余（含空串）按 procedural 兼容。"""
    return str(content_lane or "").strip().lower() == CONTENT_LANE_CONCEPT
PROVENANCE_RATIO_CORE_SECTIONS: tuple[str, ...] = (
    "关键发现",
    "入选理由",
    "证据地图",
    "核心论点",
    "论证与解释",
    "新颖增量",
)
FORBIDDEN_GENERIC_COMMANDS: tuple[str, ...] = (
    "assert evidence_map.contains",
    "maker_family",
    "checker_family",
    "value_score=measure_usage",
    "measure_usage(",
    "akb2_step_",
    "akb2_",
)
FORBIDDEN_HOW_FALLBACK_MARKERS: tuple[str, ...] = (
    "原文无可安全锚定的操作步骤",
    "候选阶段不强行生成 AKB 操作化步骤",
    "无可复制命令",
)
HOW_STEP_COMMAND_RE = re.compile(
    rf"(?m)^\s*(?:[-*]\s*)?\d+\.\s+\[raw\s+{ANCHOR_TOKEN_PAT}\]\s+"
    r"\((source_grounded|akb_interpretation)\)\s+.+?`([^`\n]+)`"
)
FORBIDDEN_HARDCODED_COMPARISONS: tuple[str, ...] = (
    "wiki/sources/addy-osmani-loop-engineering.md",
    "wiki/concepts/concept-harness-engineering.md",
    "wiki/concepts/concept-mcp-tool-use.md",
    "wiki/sources/langchain-plan-and-execute-agents.md",
    "wiki/sources/anthropic-multi-agent-research-system.md",
)
# P2-C：去掉裸字"占位"——它过宽，误伤正文里的"占位/占位符"（模型把英文 placeholder
# 译成中文触发，满仓 #16）。真模板残留有 {{}}/<TODO/待补充 等明确标记兜底。
TEMPLATE_RESIDUE_MARKERS: tuple[str, ...] = (
    "{{",
    "}}",
    "<TODO",
    "TODO:",
    "待补充",
    "lorem ipsum",
)


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict[str, object]:
    """解析 YAML frontmatter（两 --- 之间的内容）。

    返回 dict；缺失 frontmatter 返回空 dict；YAML 非法时抛 ValueError。
    """
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}

    raw = m.group(1)
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter YAML 解析失败: {exc}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError("frontmatter YAML 必须是 mapping/object")
    return {str(key): value for key, value in parsed.items()}


def _find_section_boundaries(text: str) -> list[tuple[str, int, int]]:
    """找出正文中所有 ## 级标题及其起止位置。

    Returns:
        list of (title, start_line, end_line) — title 不含 ## 前缀。
    """
    lines = text.split("\n")
    sections: list[tuple[str, int, int]] = []
    current_title: str | None = None
    current_start: int = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        # 匹配 ## 标题（二级标题）
        if re.match(r"^##\s+", stripped):
            # 保存上一个 section
            if current_title is not None:
                sections.append((current_title, current_start, i))
            current_title = re.sub(r"^##\s+", "", stripped).strip()
            current_start = i

    # 保存最后一个 section
    if current_title is not None:
        sections.append((current_title, current_start, len(lines)))

    return sections


def _get_section_text(text: str, section_title: str) -> str:
    """获取指定节标题的完整文本（从 ## 标题到下一个 ## 标题）。"""
    sections = _find_section_boundaries(text)
    lines = text.split("\n")
    for title, start, end in sections:
        if title == section_title:
            return "\n".join(lines[start:end])
    return ""


def _extract_body_text(text: str) -> str:
    """提取 frontmatter 之后的正文部分。"""
    m = re.match(r"^---\s*\n.*?\n---\s*\n(.*)", text, re.DOTALL)
    if m:
        return m.group(1)
    return text


def _extract_raw_anchors(raw_content: str) -> set[str]:
    """提取 raw 文件中声明过的 canonical `§X` 锚点集合。

    P1a:口径统一到 ``src.akb2.quality.raw_anchors.declared_anchor_set``——heading 与
    marker 形态全 raw 生效;表格单元 `| §X |` 与行首 `[raw §X]` 只在 `## 锚点目录` 块内
    识别(防正文表格/示例行偶然提到 `§Z` 被当声明)。marker 后缀 `_CONTENT_START`/
    `_CONTENT_END` 剥离归一,业务下划线锚(`§I_LIMITATIONS` 类)保留全名。
    """
    return declared_anchor_set(raw_content)


# ---------------------------------------------------------------------------
# A1: 模板 10 节齐
# ---------------------------------------------------------------------------


def check_a1_sections(text: str) -> list[Defect]:
    """检查 10 个 section 标题是否全部存在。

    A1: 模板 10 节齐（检查 10 个 section 标题存在）
    """
    defects: list[Defect] = []
    body = _extract_body_text(text)
    sections = _find_section_boundaries(body)
    found_titles = {title for title, _, _ in sections}

    for expected in TEN_SECTION_TITLES:
        if expected not in found_titles:
            defects.append(
                Defect(
                    dimension="A1-模板节齐",
                    location=f"正文 ## {expected}",
                    defect=f"缺少必需节: {expected}",
                    suggestion=f"添加 ## {expected} 节及其内容",
                )
            )

    return defects


# ---------------------------------------------------------------------------
# A2: frontmatter 硬字段
# ---------------------------------------------------------------------------


def check_a2_frontmatter(
    text: str, *, content_lane: str = "", promotion_mode: str = "proposal"
) -> list[Defect]:
    """检查 frontmatter 硬字段约束。

    A2: source_refs≥1, future_questions 2-3, related_concepts≥2,
        gate_decision 必须 null, gate_reason≤150 字符

    §0.2/D8：concept + proposal 模式接受 ``related_concepts>=2 OR proposed_graph_nodes>=1``
    （冷启动：resolver 未 resolve 足够 related 时，写 proposed_graph_nodes 待 adjudication）。
    active/lint（promotion_mode != proposal）仍要求真实 related_concepts>=2（proposed
    不替代 active 的 real_link）。
    """
    defects: list[Defect] = []
    try:
        fm = _parse_frontmatter(text)
    except ValueError as exc:
        defects.append(
            Defect(
                dimension="A2-frontmatter",
                location="frontmatter",
                defect=str(exc),
                suggestion=(
                    "修复 YAML frontmatter 语法；含冒号、方括号或括号的字符串"
                    "必须合法转义/引用"
                ),
            )
        )
        return defects

    # source_refs >= 1
    source_refs = fm.get("source_refs", [])
    if isinstance(source_refs, list):
        if len(source_refs) < MIN_SOURCE_REFS:
            defects.append(
                Defect(
                    dimension="A2-frontmatter",
                    location="frontmatter.source_refs",
                    defect=f"source_refs 数量不足: {len(source_refs)} < {MIN_SOURCE_REFS}",
                    suggestion="添加至少 1 个 source_ref（含 raw 文件路径）",
                )
            )
    else:
        defects.append(
            Defect(
                dimension="A2-frontmatter",
                location="frontmatter.source_refs",
                defect="source_refs 缺失或格式错误（应为列表）",
                suggestion="添加 source_refs 列表，至少含 raw 文件路径",
            )
        )

    # future_questions: 2-3
    fq = fm.get("future_questions", [])
    if isinstance(fq, list):
        if len(fq) < MIN_FUTURE_QUESTIONS:
            defects.append(
                Defect(
                    dimension="A2-frontmatter",
                    location="frontmatter.future_questions",
                    defect=f"future_questions 数量不足: {len(fq)} < {MIN_FUTURE_QUESTIONS}",
                    suggestion="添加 2-3 条具体的未来研究问题",
                )
            )
        elif len(fq) > MAX_FUTURE_QUESTIONS:
            defects.append(
                Defect(
                    dimension="A2-frontmatter",
                    location="frontmatter.future_questions",
                    defect=f"future_questions 数量超标: {len(fq)} > {MAX_FUTURE_QUESTIONS}",
                    suggestion="精简到 2-3 条最核心的问题",
                )
            )
    else:
        defects.append(
            Defect(
                dimension="A2-frontmatter",
                location="frontmatter.future_questions",
                defect="future_questions 缺失或格式错误（应为列表）",
                suggestion="添加 future_questions 列表，含 2-3 条",
            )
        )

    key_findings = fm.get("key_findings", [])
    if isinstance(key_findings, list):
        for idx, item in enumerate(key_findings, 1):
            item_text = str(item)
            if RAW_ANCHOR_RE.search(item_text) and not any(
                marker in item_text for marker in PROVENANCE_MARKERS
            ):
                defects.append(
                    Defect(
                        dimension="A2-frontmatter",
                        location=f"frontmatter.key_findings[{idx}]",
                        defect=(
                            "key_findings 中带 raw 锚点的论点必须显式标注 provenance"
                        ),
                        suggestion=(
                            "改为 [raw §X] (source_grounded) ... 或 "
                            "[raw §X] (akb_interpretation) ..."
                        ),
                    )
                )

    # related_concepts >= 2  （§0.2/D8：concept+proposal 接受 proposed_graph_nodes>=1 替代）
    rc = fm.get("related_concepts", [])
    proposed_nodes = fm.get("proposed_graph_nodes", [])
    is_concept_proposal = (
        str(content_lane).lower() == "concept"
        and str(promotion_mode).lower() == "proposal"
    )
    if isinstance(rc, list):
        rc_ok = len(rc) >= MIN_RELATED_CONCEPTS
        proposed_ok = (
            is_concept_proposal
            and isinstance(proposed_nodes, list)
            and len(proposed_nodes) >= 1
        )
        if not (rc_ok or proposed_ok):
            defects.append(
                Defect(
                    dimension="A2-frontmatter",
                    location="frontmatter.related_concepts",
                    defect=(
                        f"related_concepts 数量不足: {len(rc)} < {MIN_RELATED_CONCEPTS}"
                        + (
                            "（concept+proposal 可用 proposed_graph_nodes>=1 替代）"
                            if is_concept_proposal
                            else ""
                        )
                    ),
                    suggestion=(
                        "添加至少 2 个可 resolve 的相关概念"
                        + ("，或 concept 冷启动写 proposed_graph_nodes>=1"
                           if is_concept_proposal else "")
                    ),
                )
            )
    else:
        defects.append(
            Defect(
                dimension="A2-frontmatter",
                location="frontmatter.related_concepts",
                defect="related_concepts 缺失或格式错误（应为列表）",
                suggestion="添加 related_concepts 列表，≥2 条",
            )
        )

    # gate_decision 必须 null
    gd = fm.get("gate_decision")
    if gd is not None:
        defects.append(
            Defect(
                dimension="A2-frontmatter",
                location="frontmatter.gate_decision",
                defect=f"gate_decision 必须为 null，当前值: {gd}",
                suggestion="将 gate_decision 设为 null（候选页禁止预填 active）",
            )
        )

    # gate_reason <= 150 字符
    gr = fm.get("gate_reason")
    if isinstance(gr, str) and len(gr) > MAX_GATE_REASON_CHARS:
        defects.append(
            Defect(
                dimension="A2-frontmatter",
                location="frontmatter.gate_reason",
                defect=f"gate_reason 超长: {len(gr)} > {MAX_GATE_REASON_CHARS} 字符",
                suggestion=f"精简 gate_reason 到 ≤{MAX_GATE_REASON_CHARS} 字符",
            )
        )

    return defects


# ---------------------------------------------------------------------------
# A3: [raw §X] 锚点真实存在
# ---------------------------------------------------------------------------


def check_a3_raw_anchors(text: str, raw_content: str | None = None) -> list[Defect]:
    """检查正文中所有 [raw §X] 锚点在 raw 文件中真实存在。

    A3: [raw §X] 锚点在 raw 文件中真实存在

    Args:
        text: 候选页全文
        raw_content: raw 文件内容（可选）。为 None 时跳过检查（不报缺陷）。
    """
    defects: list[Defect] = []
    if raw_content is None:
        return defects

    body = _extract_body_text(text)

    # 提取所有 [raw §X] 引用,canonicalize 后与 raw 声明集比对
    # (引用 §A_CONTENT_START 与声明 §A 经 canonicalize 都归一为 §A)
    referenced_anchors = {
        canonicalize_anchor(a) for a in RAW_ANCHOR_RE.findall(body)
    }

    raw_anchors = _extract_raw_anchors(raw_content)

    for anchor in referenced_anchors:
        if anchor not in raw_anchors:
            defects.append(
                Defect(
                    dimension="A3-锚点真实",
                    location=f"正文 [raw {anchor}]",
                    defect=f"引用的锚点 {anchor} 在 raw 文件中不存在",
                    suggestion=f"确认 raw 文件中存在 {anchor} 锚点，或修正引用",
                )
            )

    return defects


# ---------------------------------------------------------------------------
# A4: 图谱边四列齐
# ---------------------------------------------------------------------------


_CONCEPT_PREFIX = "concept-"


def _slug_normalize_variants(slug: str) -> set[str]:
    """返回一个 slug 的归一化等价形式集合（裸名 + concept- 前缀互译 + 下划线↔连字符互认）。

    vault 概念页命名规范为 concept-<name>（main-pipeline-golden-sample.md 回边
    concept-{harness-engineering,...}；wiki/concepts/ 文件名同前缀；现有概念页
    正文 68 处 [[concept-X]] wikilink 一致），但候选生成器可能输出裸名 <name>。
    resolver/A5 若只做精确成员判定，裸名引用即使指向已存在概念页也会被判死链
    （SC-2a 实测：context-engineering 对不上 concept-context-engineering）。
    本函数让两种写法互为等价，纯字符串操作、不调 LLM、不做语义猜测。

    非 concept- 开头的 slug 会额外生成 concept- 前缀形式参与匹配——若对方
    集合无该前缀项则自然不命中，不产生误报。

    F10-B(probe-p8):下划线↔连字符互认——LLM/D8 产 loop_engineering(下划线 slug),
    vault 文件名 loop-engineering(连字符),旧版精确匹配永远 miss → 4 样本图谱维度
    全挂。本函数把 _ 与 - 互译加入等价集,两侧互认。只修口径,不放宽 resolve 语义
    (真不存在的概念仍 miss:fake_concept 生成 fake_concept/fake-concept/...,
    known 无任一形式仍 miss)。
    """
    s = slug.strip().lower()
    if not s:
        return set()
    variants = {s}
    # F10-B: 下划线↔连字符互认
    if "_" in s:
        variants.add(s.replace("_", "-"))
    if "-" in s:
        variants.add(s.replace("-", "_"))
    # concept- 前缀互译(对每个变体都生成 concept- 前缀形式)
    expanded: set[str] = set()
    for v in variants:
        if v.startswith(_CONCEPT_PREFIX):
            expanded.add(v[len(_CONCEPT_PREFIX):])
        else:
            expanded.add(f"{_CONCEPT_PREFIX}{v}")
    variants |= expanded
    return variants


def _target_in_proposed_nodes(target: str, proposed_ids: set[str]) -> bool:
    """F14-A: target 是否 ∈ proposed_graph_nodes ids(归一化口径,_↔-/concept- 互认)。

    proposed slug 用 _slugify_component_id(下划线),target 是原 concept 串(可能连字符)。
    用 _slug_normalize_variants 两侧归一化后交集判定(同 F10-B 口径)。
    """
    if not target or not proposed_ids:
        return False
    tvars = _slug_normalize_variants(target)
    if not tvars:
        return False
    return any(tvars & _slug_normalize_variants(pid) for pid in proposed_ids)


def _resolve_graph_node(
    target: str,
    known_concepts: list[str] | None,
    pending_queue: list[str] | None,
) -> bool:
    """A3-8 纯代码确定性图谱节点 resolve（绝不调 LLM）。

    边 target 能 resolve 当且仅当：命中 known_concepts（已有概念页/源），
    或 pending_queue 中 ≥GRAPH_PENDING_COREF_MIN 个共指（同名候选累积到阈值）。

    SC-2a：target 与 known/pending 均经 _slug_normalize_variants 归一化，
    使 concept-<name> 与裸名 <name> 互为等价（vault 概念页命名规范为带前缀）。

    本函数刻意只做集合/计数（确定性），不 import llm_router、不做语义猜测——
    SC-3e 用 monkeypatch 一切 router 入口 raise 后本函数仍正确来硬证（import allowlist 断言）。
    """
    norm = target.strip().lower()
    if not norm:
        return False
    target_variants = _slug_normalize_variants(norm)
    known: set[str] = set()
    for c in (known_concepts or []):
        known |= _slug_normalize_variants(str(c))
    if target_variants & known:
        return True
    pending = [str(c).strip().lower() for c in (pending_queue or [])]
    coref = sum(
        1 for c in pending if _slug_normalize_variants(c) & target_variants
    )
    return coref >= GRAPH_PENDING_COREF_MIN


def check_a4_graph_edges(
    text: str,
    known_concepts: list[str] | None = None,
    pending_queue: list[str] | None = None,
    content_lane: str = "",
) -> list[Defect]:
    """检查图谱关系表中每条边是否列齐且 provenance 清晰。

    A4: 图谱边列齐（target/relation/strength/[provenance]/evidence）

    step-3 SC-3e/A3-8：concept 车道额外做确定性 resolve——每条边 target 必须 resolve 到
    真实节点（known_concepts）或 pending ≥3 共指（_resolve_graph_node，纯代码不调 LLM）；
    未 resolve 的假边报 A4-图谱resolve 缺陷（图谱可调用性得分由 weighted_score 据此封顶）。
    procedural 不启用 resolve（零回归）。
    """
    defects: list[Defect] = []
    section_text = _get_section_text(text, "图谱关系")

    if not section_text:
        # 图谱关系节不存在由 A1 报告，这里不重复
        return defects

    resolve_enabled = _is_concept_lane(content_lane)
    # F14-A: 冷启动 pending 边豁免——frontmatter graph_pending=true 且 target ∈
    # proposed_graph_nodes ids 且 evidence 含 [raw §X](锚∈声明集由 compose 已确保)
    # 的边,是 D8 proposal 语义(待 adjudication)不是假边,不报 A4-图谱resolve。
    # 三条件缺一不可(防编造 target 借豁免放行)。
    fm = _parse_frontmatter(text)
    graph_pending = bool(fm.get("graph_pending", False))
    proposed_node_ids: set[str] = set()
    for pn in fm.get("proposed_graph_nodes", []) or []:
        if isinstance(pn, dict):
            slug = str(pn.get("slug", "")).strip()
            if slug:
                proposed_node_ids.add(slug)

    # 检测表格行: | target | relation | strength | provenance | evidence |
    # 在 图谱关系 节中查找表格
    lines = section_text.split("\n")
    in_table = False
    header_seen = False
    header_map: dict[str, int] = {}
    row_index = 0

    for _i, line in enumerate(lines):
        stripped = line.strip()

        # 表格行以 | 开头
        if stripped.startswith("|") and "|" in stripped[1:]:
            # 按非转义的 | 切分(共享 md_table 契约:distill 侧 _md_table_cell 转义,此处反转义)
            cells = split_md_table_row(stripped)

            if not header_seen:
                # 检查是否是表头（graph-related 列）
                header_lower = [c.lower() for c in cells]
                graph_related = any(
                    col in header_lower
                    for col in ["target", "relation", "strength", "evidence"]
                )
                if not graph_related:
                    # 不是图谱关系表，跳过
                    continue

                # 检查是否包含必需列；provenance 列新格式推荐但旧 4 列可兼容。
                if all(
                    col in header_lower
                    for col in GRAPH_EDGE_REQUIRED_COLUMNS
                ):
                    header_seen = True
                    in_table = True
                    header_map = {name: header_lower.index(name) for name in header_lower}
                    continue
                else:
                    # 表头存在但不全（如缺 evidence 列）→ 报告缺陷
                    header_seen = True
                    in_table = False
                    missing_cols = [
                        col for col in GRAPH_EDGE_REQUIRED_COLUMNS
                        if col not in header_lower
                    ]
                    defects.append(
                        Defect(
                            dimension="A4-图谱边",
                            location="图谱关系表 表头",
                            defect=f"图谱关系表缺少列: {', '.join(missing_cols)}",
                            suggestion=(
                                "补齐列: target | relation | "
                                "strength | provenance | evidence"
                            ),
                        )
                    )
                    continue

            if in_table and len(cells) >= 1 and cells[0]:
                if all(MD_TABLE_SEPARATOR_RE.fullmatch(c) for c in cells):
                    continue
                row_index += 1
                expected_cols = len(header_map) if header_map else 0
                if expected_cols and len(cells) != expected_cols:
                    defects.append(
                        Defect(
                            dimension="A4-图谱边",
                            location=f"图谱关系表第 {row_index} 行",
                            defect=(
                                f"图谱边列数不符: 需要 {expected_cols} 列"
                                f"(target/relation/strength/provenance/evidence)，"
                                f"实际 {len(cells)} 列"
                                f"（evidence 含未转义的 | 会拆列）"
                            ),
                            suggestion=(
                                "补齐/收齐列: target | relation | "
                                "strength | provenance | evidence；"
                                "evidence 含 | 时转义为 \\|"
                            ),
                        )
                    )
                else:
                    # 检查每列非空
                    required_for_row = list(GRAPH_EDGE_REQUIRED_COLUMNS)
                    if "provenance" in header_map:
                        required_for_row.append("provenance")
                    for col_name in required_for_row:
                        j = header_map[col_name]
                        if not cells[j]:
                            defects.append(
                                Defect(
                                    dimension="A4-图谱边",
                                    location=f"图谱关系表第 {row_index} 行 {col_name} 列",
                                    defect=f"图谱边 {col_name} 列为空",
                                    suggestion=f"填写 {col_name} 列",
                                )
                            )
                    if expected_cols and len(cells) == expected_cols:
                        target = cells[header_map["target"]]
                        strength = cells[header_map["strength"]]
                        evidence = cells[header_map["evidence"]]
                        provenance = (
                            cells[header_map["provenance"]]
                            if "provenance" in header_map
                            else ""
                        )
                        # step-3 SC-3e/A3-8：concept 边 target 必须确定性 resolve（纯代码）。
                        # 未 resolve = 假边/悬空引用，报缺陷（weighted_score 据此把图谱分封顶）。
                        # F14-A: 冷启动 pending 边豁免——graph_pending=true 且 target ∈
                        # proposed_graph_nodes ids(归一化口径)且 evidence 含 [raw §X](D8 proposal,非假边)。
                        is_pending_exempt = (
                            graph_pending
                            and _target_in_proposed_nodes(target, proposed_node_ids)
                            and bool(RAW_ANCHOR_RE.search(evidence))
                        )
                        if (
                            resolve_enabled
                            and target
                            and not is_pending_exempt
                            and not _resolve_graph_node(
                                target, known_concepts, pending_queue
                            )
                        ):
                            defects.append(
                                Defect(
                                    dimension="A4-图谱resolve",
                                    location=f"图谱关系/{target}",
                                    defect=(
                                        f"图谱边 target '{target}' 未 resolve 到已知概念，"
                                        f"pending 共指也 <{GRAPH_PENDING_COREF_MIN}（假边/悬空）"
                                    ),
                                    suggestion=(
                                        "把 target 指向已存在的概念页/源，"
                                        "或在 pending 队列累积足量共指后再建边，"
                                        "或冷启动写 proposed_graph_nodes + graph_pending=true"
                                        "（带真实 raw 锚证据的 pending 边豁免 A4）"
                                    ),
                                )
                            )
                        if strength not in GRAPH_EDGE_STRENGTHS:
                            defects.append(
                                Defect(
                                    dimension="A4-图谱边",
                                    location=f"图谱关系/{target}/strength",
                                    defect=(
                                        "图谱边 strength 必须是 strong/medium/weak，"
                                        f"当前: {strength}"
                                    ),
                                    suggestion="按 evidence 强度改为 strong、medium 或 weak",
                                )
                            )
                        has_anchor = bool(RAW_ANCHOR_RE.search(evidence))
                        has_provenance = any(marker in evidence for marker in PROVENANCE_MARKERS)
                        if provenance:
                            if provenance not in GRAPH_EDGE_PROVENANCES:
                                defects.append(
                                    Defect(
                                        dimension="A7-图谱证据",
                                        location=f"图谱关系/provenance/{target}",
                                        defect=(
                                            "图谱 provenance 必须是 "
                                            "source_grounded 或 akb_interpretation，"
                                            f"当前: {provenance}"
                                        ),
                                        suggestion=(
                                            "把 provenance 列改为 source_grounded "
                                            "或 akb_interpretation"
                                        ),
                                    )
                                )
                            marker = f"({provenance})"
                            if marker not in evidence:
                                defects.append(
                                    Defect(
                                        dimension="A7-图谱证据",
                                        location=f"图谱关系/evidence/{target}",
                                        defect=(
                                            "图谱 evidence 未同步标注 "
                                            "provenance 列对应的 marker"
                                        ),
                                        suggestion=f"在 evidence 中加入 {marker}",
                                    )
                                )
                            has_provenance = True
                        if not has_anchor and "(akb_interpretation)" not in evidence:
                            defects.append(
                                Defect(
                                    dimension="A7-图谱证据",
                                    location=f"图谱关系/evidence/{target}",
                                    defect=(
                                        "图谱 evidence 缺少 [raw §X] 锚点，"
                                        "也未明确标注 akb_interpretation"
                                    ),
                                    suggestion=(
                                        "补充 [raw §X] 来源锚点；若只是库内推断，"
                                        "必须标注 (akb_interpretation)"
                                    ),
                                )
                            )
                        if not has_provenance:
                            defects.append(
                                Defect(
                                    dimension="A7-图谱证据",
                                    location=f"图谱关系/evidence/{target}",
                                    defect=(
                                        "图谱 evidence 未标注 provenance，无法区分 "
                                        "source_grounded 与 akb_interpretation"
                                    ),
                                    suggestion=(
                                        "在 evidence 中加入 (source_grounded) 或 "
                                        "(akb_interpretation)"
                                    ),
                                )
                            )
                        if strength in {"strong", "medium"} and not has_anchor:
                            defects.append(
                                Defect(
                                    dimension="A7-边强度匹配",
                                    location=f"图谱关系/{target}",
                                    defect=(
                                        f"{strength} 边缺少 [raw §X] 锚点，"
                                        "证据强度不足以支撑 strong/medium"
                                    ),
                                    suggestion=(
                                        "补充可追溯 raw 锚点；否则把 strength 降为 weak "
                                        "并标注 (akb_interpretation)"
                                    ),
                                )
                            )
        elif in_table and not stripped.startswith("|"):
            # 表格结束
            in_table = False

    return defects


# ---------------------------------------------------------------------------
# A8: 适用边界 provenance 分层
# ---------------------------------------------------------------------------


def check_a8_applicability_boundary(text: str) -> list[Defect]:
    """检查适用边界每条 bullet 都有 raw anchor 与 provenance，且不混写多类判断。"""
    defects: list[Defect] = []
    section_text = _get_section_text(text, "适用边界")
    if not section_text:
        return defects

    bullets = [
        line.strip()
        for line in section_text.split("\n")
        if line.strip().startswith(("-", "*"))
    ]
    if not bullets:
        defects.append(
            Defect(
                dimension="A8-适用边界",
                location="适用边界",
                defect="适用边界缺少逐条 bullet，无法检查 provenance 分层",
                suggestion="改为每条一行的 bullet，并为每条补 [raw §X] 与 provenance",
            )
        )
        return defects

    category_patterns = {
        "来源事实": re.compile(r"来源事实"),
        "不适用场景": re.compile(r"不适用场景|不适用\s*[:：]"),
        "适用场景": re.compile(r"(?<!不)适用场景|(?<!不)适用\s*[:：]"),
        "约束条件": re.compile(r"约束条件|约束\s*[:：]"),
    }
    for idx, bullet in enumerate(bullets, 1):
        location = f"适用边界/第 {idx} 条"
        if not RAW_ANCHOR_RE.search(bullet):
            defects.append(
                Defect(
                    dimension="A8-适用边界",
                    location=location,
                    defect="适用边界条目缺少 [raw §X] 锚点",
                    suggestion="为该条补充对应 [raw §X] 锚点",
                )
            )
        markers = [marker for marker in PROVENANCE_MARKERS if marker in bullet]
        if len(markers) != 1:
            defects.append(
                Defect(
                    dimension="A8-适用边界",
                    location=location,
                    defect="适用边界条目必须且只能标注一个 provenance",
                    suggestion="为该条明确标注 (source_grounded) 或 (akb_interpretation)",
                )
            )
        content_after_label = re.sub(
            rf"^[-*]\s*\[raw\s+{RAW_ANCHOR_DECL_RE}\]\s+\((?:source_grounded|akb_interpretation)\)\s+"
            r"(?:来源事实|适用场景|不适用场景|约束条件)[:：]\s*",
            "",
            bullet,
        )
        term_count = sum(
            1 for pattern in category_patterns.values() if pattern.search(content_after_label)
        )
        if term_count > 1:
            defects.append(
                Defect(
                    dimension="A8-适用边界",
                    location=location,
                    defect="同一条适用边界混写了多种判断类型",
                    suggestion="拆成独立条目：来源事实、适用场景、不适用场景、约束条件分开写",
                )
            )

    return defects


# ---------------------------------------------------------------------------
# A5: wikilink 死链检测
# ---------------------------------------------------------------------------


def check_a5_wikilinks(
    text: str,
    pending_queue: list[str] | None = None,
    known_concepts: list[str] | None = None,
) -> list[Defect]:
    """检查 wikilink 目标是否在已知概念列表或 pending 队列中。

    A5: wikilink 死链检测（需在 pending 队列或已知概念列表中）

    Args:
        text: 候选页全文
        pending_queue: 已知的 pending 队列概念列表
        known_concepts: 已知的概念列表（可选）
    """
    defects: list[Defect] = []
    # SC-2a：allowed 经 _slug_normalize_variants 归一化，使 concept-<name> 与裸名等价。
    allowed: set[str] = set()
    for _s in (pending_queue or []) + (known_concepts or []):
        allowed |= _slug_normalize_variants(str(_s))

    if not allowed:
        # 没有已知概念列表时跳过检查（空索引不报死链——SC-2a 调查铁证）
        return defects

    body = _extract_body_text(text)

    # 匹配 [[概念名]] 或 [[概念名|显示文本]]
    wikilink_pattern = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
    links = wikilink_pattern.findall(body)

    for link in links:
        link_clean = link.strip()
        if not (_slug_normalize_variants(link_clean) & allowed):
            defects.append(
                Defect(
                    dimension="A5-wikilink死链",
                    location=f"正文 [[{link_clean}]]",
                    defect=f"wikilink 目标 '{link_clean}' 不在已知概念或 pending 队列中",
                    suggestion=f"将 '{link_clean}' 加入 pending 队列，或确认概念页存在",
                )
            )

    return defects


# ---------------------------------------------------------------------------
# A6: 维护记录 ≤5 条
# ---------------------------------------------------------------------------


def check_a6_maintenance_records(text: str) -> list[Defect]:
    """检查维护记录是否不超过 5 条。

    A6: 维护记录 ≤5 条
    """
    defects: list[Defect] = []
    section_text = _get_section_text(text, "维护记录")

    if not section_text:
        # 维护记录节不存在由 A1 报告
        return defects

    # 统计维护记录条目（以 - 开头的列表项，或以日期开头的行）
    lines = section_text.split("\n")
    record_count = 0
    for line in lines:
        stripped = line.strip()
        # 列表项: - YYYY-MM-DD ...
        if stripped.startswith("-") and re.search(r"\d{4}-\d{2}-\d{2}", stripped):
            record_count += 1
        # 或者直接以日期开头: YYYY-MM-DD ...
        elif re.match(r"^\d{4}-\d{2}-\d{2}", stripped):
            record_count += 1

    if record_count > MAX_MAINTENANCE_RECORDS:
        defects.append(
            Defect(
                dimension="A6-维护记录",
                location="维护记录节",
                defect=f"维护记录条目过多: {record_count} > {MAX_MAINTENANCE_RECORDS}",
                suggestion=f"合并同日条目或精简到 ≤{MAX_MAINTENANCE_RECORDS} 条",
            )
        )

    return defects


# ---------------------------------------------------------------------------
# 聚合入口
# ---------------------------------------------------------------------------


def run_structural_checks(
    text: str,
    raw_content: str | None = None,
    pending_queue: list[str] | None = None,
    known_concepts: list[str] | None = None,
    content_lane: str = "",
) -> list[Defect]:
    """运行全部 A 层 6 项检查，返回缺陷清单。

    Args:
        text: 候选页全文（含 frontmatter）
        raw_content: raw 文件内容（A3 检查需要，可选）
        pending_queue: pending 队列概念列表（A5 + A4 图谱 resolve 需要，可选）
        known_concepts: 已知概念列表（A5 + A4 图谱 resolve 需要，可选）
        content_lane: 内容车道（step-3：A4 图谱 resolve 仅 concept 启用确定性 resolve）

    Returns:
        list[Defect]: 缺陷清单，空列表表示全部通过。
    """
    defects: list[Defect] = []

    defects.extend(check_a1_sections(text))
    # §0.2/D8：传 content_lane + promotion_mode=proposal（concept 冷启动接受 proposed_graph_nodes）
    defects.extend(
        check_a2_frontmatter(text, content_lane=content_lane, promotion_mode="proposal")
    )
    defects.extend(check_a3_raw_anchors(text, raw_content))
    # step-3 SC-3e/A3-8：check_a4 增 known_concepts/pending_queue 形参——concept 图谱边
    # 必须 resolve 到真实节点或 pending ≥3 共指（纯代码确定性查，不调 LLM）。
    defects.extend(
        check_a4_graph_edges(
            text,
            known_concepts=known_concepts,
            pending_queue=pending_queue,
            content_lane=content_lane,
        )
    )
    defects.extend(check_a5_wikilinks(text, pending_queue, known_concepts))
    defects.extend(check_a6_maintenance_records(text))
    defects.extend(check_a8_applicability_boundary(text))

    return defects


def _body_without_frontmatter(text: str) -> str:
    return _extract_body_text(text)


# §0.2/D7/C1：机制组件与因果边证据表 SG 检查
_VALID_EDGE_KIND_LABELS = {
    "source_causal", "llm_evidence", "inferred_from_structure",
    "inferred_from_structure (weak)", "",
}


def _parse_mechanism_tables(body: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """从 body 的 `## 机制组件与因果边证据` 节解析 components + edges 表行。

    返回 (components, edges)，每项是 {col: cell} dict。节缺失 → ([], [])。
    """
    import re

    section_match = re.search(
        r"## 机制组件与因果边证据\s*(.*?)(?=\n## |\Z)", body, re.DOTALL
    )
    if not section_match:
        return [], []
    section = section_match.group(1)
    # 拆 components / edges 两表（按 "组件：" / "因果边：" 标记）
    comp_part_match = re.search(
        r"组件：\s*\n\s*\|[^\n]*\n\|[-| ]+\n(.*?)(?=\n\s*因果边：|\Z)",
        section, re.DOTALL,
    )
    edge_part_match = re.search(
        r"因果边：\s*\n\s*\|[^\n]*\n\|[-| ]+\n(.*?)(?=\n## |\Z)",
        section, re.DOTALL,
    )
    comp_cols = ["component_id", "name", "role", "evidence_anchor", "evidence_sentence"]
    edge_cols = ["from", "to", "relation", "edge_kind", "evidence_anchor", "evidence_sentence"]

    def parse_rows(part: str, cols: list[str]) -> list[dict[str, str]]:
        rows = []
        for line in (part or "").strip().splitlines():
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= len(cols):
                rows.append(dict(zip(cols, cells[: len(cols)], strict=False)))
        return rows

    components = parse_rows(comp_part_match.group(1) if comp_part_match else "", comp_cols)
    edges = parse_rows(edge_part_match.group(1) if edge_part_match else "", edge_cols)
    return components, edges


def _check_sg_mechanism_edge_evidence(
    body: str, content_lane: str, raw_anchors: set[str] | None = None
) -> list[Defect]:
    """§0.2/D7/C1 + §0.2/F5：SG-mechanism-edge-evidence — 机制组件与因果边证据表完整性。

    - concept + 节缺失 → defect（concept 须渲染机制证据表供 grader H3/A 层消费）
    - eligible edge（source_causal/llm_evidence）空 evidence 或 evidence_anchor → defect
    - §0.2/F5：eligible edge 的 evidence_anchor 须 ∈ raw 声明 anchor 全集（与 F4 统一口径，
      复用 _extract_raw_anchors 同源判断）；**不再要求正文额外渲染 [raw §X]**——
      旧口径要求正文有 `[raw §X]`（只来自 claims，不覆盖所有 raw anchor）→ F4 认可的真边
      （anchor∈raw 但未被 claim 引用）被 SG 拒 → 7 条 compose_failed。统一为 raw 全集。
    - 未知 edge_kind → defect
    - edge 端点不在 components → defect
    - inferred 弱边须标 weak（edge_kind 含 inferred_from_structure）
    """
    defects: list[Defect] = []
    components, edges = _parse_mechanism_tables(body)
    comp_names = {c.get("name", "") for c in components if c.get("name")}

    if "## 机制组件与因果边证据" not in body:
        if str(content_lane).lower() == "concept":
            defects.append(
                Defect(
                    dimension="SG-mechanism-edge-evidence",
                    location="正文.机制组件与因果边证据",
                    defect="concept 候选缺机制组件与因果边证据节",
                    suggestion="concept 须渲染 ## 机制组件与因果边证据 两表供 grader H3 审语义",
                )
            )
        return defects

    for idx, e in enumerate(edges, 1):
        kind = (e.get("edge_kind") or "").strip()
        # 未知 edge_kind（"inferred_from_structure (weak)" 渲染标签合法）
        if kind and kind not in _VALID_EDGE_KIND_LABELS:
            defects.append(
                Defect(
                    dimension="SG-mechanism-edge-evidence",
                    location=f"正文.机制组件与因果边证据.edges[{idx}]",
                    defect=f"未知 edge_kind: {kind}",
                    suggestion="edge_kind 用 source_causal/llm_evidence/inferred_from_structure",
                )
            )
        # eligible edge 须带 evidence + evidence_anchor
        is_eligible = kind in ("source_causal", "llm_evidence")
        if is_eligible:
            if not (e.get("evidence_sentence") or "").strip():
                defects.append(
                    Defect(
                        dimension="SG-mechanism-edge-evidence",
                        location=f"正文.机制组件与因果边证据.edges[{idx}]",
                        defect=f"eligible 边 {kind} 缺 evidence 句",
                        suggestion="source_causal/llm_evidence 边须带原文 evidence 句",
                    )
                )
            anchor = (e.get("evidence_anchor") or "").strip()
            if not anchor:
                defects.append(
                    Defect(
                        dimension="SG-mechanism-edge-evidence",
                        location=f"正文.机制组件与因果边证据.edges[{idx}]",
                        defect=f"eligible 边 {kind} 缺 evidence_anchor",
                        suggestion="source_causal/llm_evidence 边须带 §X evidence_anchor",
                    )
                )
            elif raw_anchors is not None and anchor not in raw_anchors:
                # §0.2/F1 + §0.2/F5：evidence_anchor 须 ∈ raw 声明 anchor 全集（与 F4 统一口径，
                # 复用 _extract_raw_anchors 同源判断）；不再要求正文额外渲染 [raw §X]。
                # 防伪仍成立：伪 §Z/合成 §raw 不在 raw 声明集 → 不计 eligible（破坏 traceability）。
                # raw_anchors=None 时跳过此检查（无 raw_content 兜底，不阻断）。
                defects.append(
                    Defect(
                        dimension="SG-mechanism-edge-evidence",
                        location=f"正文.机制组件与因果边证据.edges[{idx}]",
                        defect=(
                            f"eligible 边 evidence_anchor {anchor} 不在 raw 声明 anchor 集"
                            "（伪/合成 anchor 不计 eligible）"
                        ),
                        suggestion=(
                            "evidence_anchor 须指向真实 raw §-anchor（∈ raw 声明全集）"
                        ),
                    )
                )
        # 端点须在 components
        for end in ("from", "to"):
            val = (e.get(end) or "").strip()
            if val and val not in comp_names:
                defects.append(
                    Defect(
                        dimension="SG-mechanism-edge-evidence",
                        location=f"正文.机制组件与因果边证据.edges[{idx}]",
                        defect=f"edge.{end}={val} 不在 mechanism_components 名单",
                        suggestion=(
                            "edge from/to 须指向 mechanism_components 的 name/alias"
                        ),
                    )
                )
    return defects


def _provenance_counts(body: str) -> tuple[int, int]:
    return body.count("(source_grounded)"), body.count("(akb_interpretation)")


_PROVENANCE_SPLIT_SUMMARY_RE = re.compile(
    r"<!--\s*provenance_split:\s*"
    r"source_operation=(?P<op>\d+)\s+"
    r"source_fact=(?P<fact>\d+)\s+"
    r"akb_interpretation=(?P<akb>\d+)\s*-->"
)


def _provenance_split_summary(text: str) -> dict[str, int] | None:
    """Read the three-way provenance split summary comment (Finding 6).

    compose_candidate emits ``<!-- provenance_split: source_operation=N
    source_fact=M akb_interpretation=K -->`` at the end of the How section,
    derived from how_steps' ``provenance_split`` annotation + code-direct
    must_cover operations. This is the SG consumer of the three-state split.

    Returns ``{source_operation, source_fact, akb_interpretation}`` or ``None``
    when the comment is absent (e.g. hand-written fixtures, or how_steps lacked
    the ``provenance_split`` annotation layer).
    """
    m = _PROVENANCE_SPLIT_SUMMARY_RE.search(text)
    if m is None:
        return None
    return {
        "source_operation": int(m.group("op")),
        "source_fact": int(m.group("fact")),
        "akb_interpretation": int(m.group("akb")),
    }


def _core_provenance_body(text: str) -> str:
    """Return only source-claim sections for the AKB interpretation ratio.

    Boundary, graph and future-question sections are intentionally allowed to
    carry AKB interpretation markers; counting them globally turns the ratio
    gate into a penalty for honest provenance labelling.
    """
    body = _body_without_frontmatter(text)
    lines = body.split("\n")
    chunks: list[str] = []
    for title, start, end in _find_section_boundaries(body):
        if title in PROVENANCE_RATIO_CORE_SECTIONS:
            section = "\n".join(lines[start:end])
            if title == "论证与解释":
                section = section.split("### 操作步骤", 1)[0]
            chunks.append(section)
    return "\n\n".join(chunks)


def _how_section_text(text: str) -> str:
    section = _get_section_text(_body_without_frontmatter(text), "论证与解释")
    if "### 操作步骤" not in section:
        return ""
    return section.split("### 操作步骤", 1)[1]


def _how_executable_commands(text: str) -> list[tuple[str, str]]:
    return [
        (match.group(1), match.group(2).strip())
        for match in HOW_STEP_COMMAND_RE.finditer(_how_section_text(text))
        if match.group(2).strip()
    ]


def _has_forbidden_hardcoded_novelty(body: str, rel_path: str) -> bool:
    """Detect fixed novelty baselines without rejecting dynamic comparison rows."""
    idx = body.find(rel_path)
    if idx == -1:
        return False
    line_start = body.rfind("\n", 0, idx) + 1
    line_end = body.find("\n", idx)
    if line_end == -1:
        line_end = len(body)
    line = body[line_start:line_end].strip()
    if line.startswith("|") and " | " in line:
        return False
    forbidden_markers = (
        "固定基线",
        "默认基线",
        "写死",
        "hardcoded",
        "hard-coded",
    )
    return any(marker in line.lower() for marker in forbidden_markers) or line == rel_path


def _normalize_must_cover_text(s: str) -> str:
    """SC-4：归一化 must_cover 匹配文本——去 [raw §X] 锚点/markdown 标记/列表前缀/压空白。

    旧 gate `literal in section_text` 逐字匹配：compose 渲染同源内容时插 [raw §X]
    锚点、保留 **bold**/`code` 标记、"N. " 前缀，导致同源内容匹配不上 → 假 fail
    （满仓 simonwillison "1. **A computation graph**..." / code.claude 两条挂此）。
    归一化后只比"实质内容是否渲染"，tolerant 格式差异、仍 fail-closed 真缺失
    （内容根本没渲染时归一化后仍不匹配 → 仍报缺陷）。
    """
    s = re.sub(rf"\[raw\s*{ANCHOR_TOKEN_PAT}\]", " ", s)  # 去 [raw §X] 锚点
    # P2-C：单引号 ' 与反引号 ` 同去——渲染侧 _sanitize 把 literal 反引号换成 '，
    # 校验侧也消掉 '，两侧同口径（修 `Conv`→'Conv' 后 must_cover 失配，满仓 #6/#16）。
    s = re.sub(r"[`'*#>_]", " ", s)  # 去 markdown 标记 + 单引号
    s = re.sub(r"(?m)^\s*\d+\.\s*", " ", s)  # 去列表前缀 N.
    s = re.sub(r"\s+", " ", s)  # 压空白
    return s.strip().lower()


def run_candidate_self_gate(
    text: str,
    raw_content: str | None = None,
    pending_queue: list[str] | None = None,
    known_concepts: list[str] | None = None,
    content_lane: str = "",
) -> list[Defect]:
    """Run maker-side candidate gate before grader/promote.

    This gate intentionally composes the existing A-layer checks with stricter
    content-contract checks. It is deterministic and must fail closed for fake
    anchors, excessive AKB interpretation, generic AKB commands, hardcoded
    novelty baselines, and template residue.

    step-3 SC-3a：content_lane（concept/procedural）让 SG-how-density 按车道选 gate_min——
    concept 车道核心价值是命名机制组件+因果边（F4 由 integrity_floor 二值硬卡），不是命令
    密度，故 concept gate_min=0（不卡可执行 How 步数）；procedural 保留 ≥3（零回归）。
    SG-provenance-ratio 在 concept 改为逐条 traces_to 溯源（见 Step.3 verify_interpretation_*）。
    """
    defects = run_structural_checks(
        text, raw_content, pending_queue, known_concepts, content_lane
    )
    body = _body_without_frontmatter(text)

    if "[raw §" not in body:
        defects.append(
            Defect(
                dimension="SG-raw-anchor",
                location="正文",
                defect="候选页正文没有任何 [raw §X] 锚点",
                suggestion="所有关键论点、边界、图谱 evidence 必须补充真实 raw anchor",
            )
        )

    # §0.2/D7/C1 + §0.2/F5：SG-mechanism-edge-evidence — 机制组件与因果边证据表完整性
    # F5：与 F4 统一口径——eligible edge 的 evidence_anchor 须 ∈ raw 声明全集
    # （复用 _extract_raw_anchors，认 #{1,6} 多# + [raw §X] + _CONTENT_START + 表格）。
    _sg_raw_anchors: set[str] = set()
    if raw_content:
        _sg_raw_anchors = _extract_raw_anchors(raw_content)
    defects.extend(
        _check_sg_mechanism_edge_evidence(body, content_lane, raw_anchors=_sg_raw_anchors)
    )

    provenance_body = _core_provenance_body(text)
    source_count, akb_count = _provenance_counts(provenance_body)
    total_provenance = source_count + akb_count
    # Finding 6: 消费三态 provenance_split 摘要（compose_candidate 持久化到 How 节末尾）。
    # 核心比例阈值仍按二态 marker（操作步骤故意排除，见不变量
    # test_how_interpretation_steps_do_not_count_against_core_ratio）；三态在此只读报告，
    # 让 SG 能区分 source_operation / source_fact / akb_interpretation。
    split_summary = _provenance_split_summary(text)
    # step-3 SC-3c/Decision-1：concept 车道删 ≤40% 比例上限，改逐条 traces_to 溯源
    # （由 integrity_floor._interpretation_traceability_failed 硬判，层① 确定性地板）；
    # procedural 保留原 ≤40% cap（零回归）。
    if (
        content_lane != "concept"
        and total_provenance > 0
        and akb_count / total_provenance > MAX_AKB_INTERPRETATION_RATIO
    ):
        split_note = ""
        if split_summary is not None:
            split_note = (
                f"（三态细分：source_operation={split_summary['source_operation']} "
                f"source_fact={split_summary['source_fact']} "
                f"akb_interpretation={split_summary['akb_interpretation']}）"
            )
        defects.append(
            Defect(
                dimension="SG-provenance-ratio",
                location="核心知识区 provenance",
                defect=(
                    f"akb_interpretation 占比 {akb_count}/{total_provenance} "
                    f"> {MAX_AKB_INTERPRETATION_RATIO:.0%}{split_note}"
                ),
                suggestion=(
                    "减少编辑解释，优先改写为 source_grounded 来源事实"
                    "或删除无原文支撑的判断"
                ),
            )
        )

    lowered = body.lower()
    for marker in FORBIDDEN_GENERIC_COMMANDS:
        if marker.lower() in lowered:
            defects.append(
                Defect(
                    dimension="SG-generic-command",
                    location="操作步骤",
                    defect=f"候选页包含通用 AKB 内部命令: {marker}",
                    suggestion=(
                        "How 步骤只能使用来源明确给出的操作、协议、API 或配置，"
                        "不能用 AKB 内部检查命令补齐"
                    ),
                )
            )

    how_section = _how_section_text(text)
    commands = _how_executable_commands(text)
    # step-3 SC-3a：SG-how-density 按车道选 gate_min。concept 核心价值是命名机制组件+
    # 因果边（F4 由 integrity_floor 二值硬卡），不卡可执行命令密度 → gate_min=0；
    # procedural 保留 ≥3（零回归）。concept 仍要求有「操作步骤」小节（A1 结构完整性）。
    how_density_min = 0 if _is_concept_lane(content_lane) else MIN_HOW_EXECUTABLE_STEPS
    if not how_section:
        defects.append(
            Defect(
                dimension="SG-how-density",
                location="### 操作步骤",
                defect="候选页缺少操作步骤小节",
                suggestion=(
                    "compose 阶段必须生成至少 3 条带 raw anchor/provenance/"
                    "code command 的可执行 How 步骤"
                ),
            )
        )
    elif how_density_min and len(commands) < how_density_min:
        defects.append(
            Defect(
                dimension="SG-how-density",
                location="### 操作步骤",
                defect=(
                    f"可执行 How 步骤 {len(commands)} < {how_density_min}"
                ),
                suggestion=(
                    "补足至少 3 条带反引号命令、"
                    "raw anchor 和 provenance 的可复制步骤"
                ),
            )
        )
    for marker in FORBIDDEN_HOW_FALLBACK_MARKERS:
        if marker in how_section:
            defects.append(
                Defect(
                    dimension="SG-how-executability",
                    location="### 操作步骤",
                    defect=f"操作步骤含放弃式占位: {marker}",
                    suggestion=(
                        "来源有协议/流程/配置操作时必须锚定并渲染；"
                        "无足够 How 时 compose 应 fail-closed"
                    ),
                )
            )

    for rel_path in FORBIDDEN_HARDCODED_COMPARISONS:
        if _has_forbidden_hardcoded_novelty(body, rel_path):
            defects.append(
                Defect(
                    dimension="SG-novelty-hardcoded",
                    location="新颖增量",
                    defect=f"新颖性对照包含硬编码基线: {rel_path}",
                    suggestion=(
                        "改为按当前 vault 的 slug/source_url/concepts "
                        "动态选择相邻条目；无邻居时明确暂无本地基线"
                    ),
                )
            )

    lowered_full = text.lower()
    for marker in TEMPLATE_RESIDUE_MARKERS:
        if marker.lower() in lowered_full:
            defects.append(
                Defect(
                    dimension="SG-template-residue",
                    location="全文",
                    defect=f"候选页包含模板残留: {marker}",
                    suggestion="删除占位文本；无法生成真实内容时 fail-closed，不产出候选",
                )
            )

    # SG-must-cover: 确定性证据覆盖（fail-closed）
    # scan_all(raw_content) 产出 must_cover 证据清单（CLI/编号步骤/边界句/结论句）；
    # 任一字面量未在对应节逐字渲染即 Defect。raw_content=None 本身即 Defect——
    # 绝不静默跳过覆盖校验（宪法 P3 fail-closed + P6 来源锚定）。
    if raw_content is None:
        defects.append(
            Defect(
                dimension="SG-must-cover",
                location="raw_content",
                defect="raw_content 缺失，无法验证 must_cover 覆盖",
                suggestion=(
                    "run_candidate_self_gate 必须传入与候选页同源的真实 raw 文本；"
                    "缺失即 fail-closed，不静默跳过覆盖校验"
                ),
            )
        )
    else:
        must_cover_items = _scan_source_evidence(raw_content)
        body_text = _body_without_frontmatter(text)
        how_text = _how_section_text(text)
        boundary_text = _get_section_text(body_text, "适用边界")
        conclusion_text = (
            _get_section_text(body_text, "TLDR")
            + "\n"
            + _get_section_text(body_text, "关键发现")
            + "\n"
            + _get_section_text(body_text, "核心论点")
        )
        must_cover_section_text = {
            "source_operation": how_text,
            "source_boundary": boundary_text,
            "source_conclusion": conclusion_text,
        }
        must_cover_section_name = {
            "source_operation": "### 操作步骤",
            "source_boundary": "## 适用边界",
            "source_conclusion": "TLDR/关键发现/核心论点",
        }
        for item in must_cover_items:
            section_text = must_cover_section_text.get(item.must_cover_class, "")
            # SC-4：归一化后比实质内容，tolerant compose 重格式化（[raw §X]/**/`/N. 前缀），
            # 仍 fail-closed 真缺失（内容没渲染时归一化后也不匹配）。
            if _normalize_must_cover_text(item.literal) not in _normalize_must_cover_text(
                section_text
            ):
                section_name = must_cover_section_name.get(
                    item.must_cover_class, "正文"
                )
                defects.append(
                    Defect(
                        dimension="SG-must-cover",
                        location=section_name,
                        defect=f"must_cover 字面量未在对应节渲染: {item.literal}",
                        suggestion=(
                            f"在 {section_name} 中补上来源字面量并锚定 raw，"
                            "不得意译或省略；无对应操作时 compose 应 fail-closed"
                        ),
                    )
                )

    # SG-command-recheck: 全文回查 + 危险黑名单附加（SC-4 原意恢复）
    # 全文字面量回查: 正文每个反引号命令（非 reserved token）必须字面出现在 raw。
    #   不出现 = 编造 = Defect。不区分 source_grounded/akb_interpretation——
    #   之前的"代码直出 only"收窄让安全但 raw 不存在的编造命令漏过（审查 Finding 1）。
    # 危险黑名单附加: 命中危险命令即使 raw 有也算 Defect（双重拦截）。
    # raw_content=None 已被 SG-must-cover 块 fail-closed（保持现状）；空字符串 raw
    # 仍必须执行 recheck，让 raw_verify 把每条非 reserved 命令判为 violation。
    if raw_content is not None:
        command_violations = _verify_commands_in_raw(body, raw_content or "")
        for v in command_violations:
            if "危险" in v.reason:
                defect_text = f"派生命令含危险操作: `{v.command}`"
                suggestion = (
                    "删除该危险命令或替换为安全的等价操作示例；"
                    "How 步骤中的派生命令不得含 rm -rf / fork bomb / 管道执行远程脚本 / "
                    "凭据外泄等破坏性操作"
                )
            else:
                defect_text = f"命令原文不存在（疑似编造）: `{v.command}`"
                suggestion = (
                    "删除该命令或改为 raw 中真实存在的字面量；"
                    "若 raw 确无对应操作，compose 必须 fail-closed，不得编造命令补齐 How"
                )
            defects.append(
                Defect(
                    dimension="SG-command-recheck",
                    location="### 操作步骤",
                    defect=defect_text,
                    suggestion=suggestion,
                )
            )

    return defects
