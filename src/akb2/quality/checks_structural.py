"""A 层结构校验器 — 6 项纯代码检查，零 LLM 调用。

检查项:
    A1: 模板 10 节齐（检查 10 个 section 标题存在）
    A2: frontmatter 硬字段（source_refs≥1, future_questions 2-3,
        related_concepts≥2, gate_decision 必须 null, gate_reason≤150 字符）
    A3: [raw §X] 锚点在 raw 文件中真实存在
    A4: 图谱边四列齐（target/relation/strength/evidence）
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

# A4 图谱边四列
GRAPH_EDGE_COLUMNS: tuple[str, ...] = ("target", "relation", "strength", "evidence")


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict[str, object]:
    """解析 YAML frontmatter（两 --- 之间的内容）。

    返回 dict；解析失败返回空 dict。
    """
    # 匹配开头的 --- ... ---
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}

    raw = m.group(1)
    result: dict[str, object] = {}

    # 简单 YAML 解析（仅处理本检查需要的字段）
    # 支持: key: value, key: [list], key: null, key: "string"
    lines = raw.split("\n")
    current_key: str | None = None
    current_list: list[str] = []

    for line in lines:
        # 跳过空行和注释
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # 列表项:   - item
        list_match = re.match(r"^\s+-\s+(.+)", line)
        if list_match and current_key is not None:
            current_list.append(list_match.group(1).strip().strip("\"'"))
            continue

        # 如果之前在收集列表，先保存
        if current_key is not None and current_list:
            result[current_key] = list(current_list)
            current_list = []
            current_key = None

        # key: value
        kv_match = re.match(r"^(\w[\w_]*)\s*:\s*(.+)", line)
        if kv_match:
            key = kv_match.group(1)
            value = kv_match.group(2).strip()

            if value == "null" or value == "~":
                result[key] = None
            elif value.startswith("[") and value.endswith("]"):
                # 内联列表: [a, b, c]
                inner = value[1:-1]
                items = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
                result[key] = items
            elif value.startswith('"') and value.endswith('"'):
                result[key] = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                result[key] = value[1:-1]
            else:
                # 尝试解析数字
                try:
                    if "." in value:
                        result[key] = float(value)
                    else:
                        result[key] = int(value)
                except ValueError:
                    result[key] = value
            continue

        # 可能是列表开始: key:
        list_key_match = re.match(r"^(\w[\w_]*)\s*:\s*$", line)
        if list_key_match:
            current_key = list_key_match.group(1)
            current_list = []
            continue

    # 保存最后一个列表
    if current_key is not None and current_list:
        result[current_key] = list(current_list)

    return result


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


def check_a2_frontmatter(text: str) -> list[Defect]:
    """检查 frontmatter 硬字段约束。

    A2: source_refs≥1, future_questions 2-3, related_concepts≥2,
        gate_decision 必须 null, gate_reason≤150 字符
    """
    defects: list[Defect] = []
    fm = _parse_frontmatter(text)

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

    # related_concepts >= 2
    rc = fm.get("related_concepts", [])
    if isinstance(rc, list):
        if len(rc) < MIN_RELATED_CONCEPTS:
            defects.append(
                Defect(
                    dimension="A2-frontmatter",
                    location="frontmatter.related_concepts",
                    defect=f"related_concepts 数量不足: {len(rc)} < {MIN_RELATED_CONCEPTS}",
                    suggestion="添加至少 2 个相关概念",
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

    # 提取所有 [raw §X] 引用
    anchor_pattern = re.compile(r"\[raw\s+(§[A-Z])\]")
    referenced_anchors = set(anchor_pattern.findall(body))

    # 提取 raw 文件中所有 §X 锚点
    # 兼容两种格式: markdown heading ("# §A") 和 defuddle 纯文本锚点 ("§A_CONTENT_START")
    raw_anchor_pattern = re.compile(r"(?:^#\s+|^)(§[A-Z])(?:[_A-Z]|\s|$)", re.MULTILINE)
    raw_anchors = set(raw_anchor_pattern.findall(raw_content))

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


def check_a4_graph_edges(text: str) -> list[Defect]:
    """检查图谱关系表中每条边是否四列齐。

    A4: 图谱边四列齐（target/relation/strength/evidence）
    """
    defects: list[Defect] = []
    section_text = _get_section_text(text, "图谱关系")

    if not section_text:
        # 图谱关系节不存在由 A1 报告，这里不重复
        return defects

    # 检测表格行: | target | relation | strength | evidence |
    # 在 图谱关系 节中查找表格
    lines = section_text.split("\n")
    in_table = False
    header_seen = False
    row_index = 0

    for _i, line in enumerate(lines):
        stripped = line.strip()

        # 表格行以 | 开头
        if stripped.startswith("|") and "|" in stripped[1:]:
            cells = [c.strip() for c in stripped.split("|")[1:-1]]

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

                # 检查是否四列齐全
                if all(
                    col in header_lower
                    for col in ["target", "relation", "strength", "evidence"]
                ):
                    header_seen = True
                    in_table = True
                    continue
                else:
                    # 表头存在但不全（如缺 evidence 列）→ 报告缺陷
                    header_seen = True
                    in_table = True
                    missing_cols = [
                        col for col in GRAPH_EDGE_COLUMNS
                        if col not in header_lower
                    ]
                    defects.append(
                        Defect(
                            dimension="A4-图谱边",
                            location="图谱关系表 表头",
                            defect=f"图谱关系表缺少列: {', '.join(missing_cols)}",
                            suggestion="补齐四列: target | relation | strength | evidence",
                        )
                    )
                    continue

            if in_table and len(cells) >= 1 and cells[0]:
                row_index += 1
                if len(cells) < 4:
                    defects.append(
                        Defect(
                            dimension="A4-图谱边",
                            location=f"图谱关系表第 {row_index} 行",
                            defect=(
                                f"图谱边缺少列: 需要 4 列"
                                f"(target/relation/strength/evidence)，"
                                f"实际 {len(cells)} 列"
                            ),
                            suggestion="补齐四列: target | relation | strength | evidence",
                        )
                    )
                else:
                    # 检查每列非空
                    for j, col_name in enumerate(GRAPH_EDGE_COLUMNS):
                        if not cells[j]:
                            defects.append(
                                Defect(
                                    dimension="A4-图谱边",
                                    location=f"图谱关系表第 {row_index} 行 {col_name} 列",
                                    defect=f"图谱边 {col_name} 列为空",
                                    suggestion=f"填写 {col_name} 列",
                                )
                            )
        elif in_table and not stripped.startswith("|"):
            # 表格结束
            in_table = False

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
    allowed = set(pending_queue or []) | set(known_concepts or [])

    if not allowed:
        # 没有已知概念列表时跳过检查
        return defects

    body = _extract_body_text(text)

    # 匹配 [[概念名]] 或 [[概念名|显示文本]]
    wikilink_pattern = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
    links = wikilink_pattern.findall(body)

    for link in links:
        link_clean = link.strip()
        if link_clean not in allowed:
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
) -> list[Defect]:
    """运行全部 A 层 6 项检查，返回缺陷清单。

    Args:
        text: 候选页全文（含 frontmatter）
        raw_content: raw 文件内容（A3 检查需要，可选）
        pending_queue: pending 队列概念列表（A5 检查需要，可选）
        known_concepts: 已知概念列表（A5 检查需要，可选）

    Returns:
        list[Defect]: 缺陷清单，空列表表示全部通过。
    """
    defects: list[Defect] = []

    defects.extend(check_a1_sections(text))
    defects.extend(check_a2_frontmatter(text))
    defects.extend(check_a3_raw_anchors(text, raw_content))
    defects.extend(check_a4_graph_edges(text))
    defects.extend(check_a5_wikilinks(text, pending_queue, known_concepts))
    defects.extend(check_a6_maintenance_records(text))

    return defects
