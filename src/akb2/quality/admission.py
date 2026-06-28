"""准入三硬判定 — 二值无连续分数（宪法 P3/P8）。

三个硬判定，每项输出 {pass: bool, reason: str} 经 jsonschema 校验：
    1. 主题相关性 — 领域 taxonomy 锚点命中
    2. How 倾向 — 可执行知识占比
    3. 新颖度 — 对既有 wiki 增量

任一 fail → 整体拒收并落账（调用 step-1 ledger.append_event）。
全部 pass → 返回 AdmissionResult(passed=True) 进入提炼段。

公开接口:
    run_admission(raw_path, raw_content, ledger_path, wiki_references=...) -> AdmissionResult
    AdmissionResult = dataclass(passed, checks, reason, content_lane)

内容车道（content_lane，spec FR-001/FR-002）:
    每篇内容在准入阶段判为 concept（概念型）或 procedural（操作型）二选一。
    含可执行操作（命令/代码块/编号步骤）即 procedural；混合归 procedural（FR-002 从严）。
    本字段只被「记录与传递」，不在准入段被消费用于改判定（lane-aware 判定属 step-3）。
    分类器不可用且无确定性证据时 fail-closed（拒收+落账），不静默归车道（SC-4）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# 内容车道常量（spec FR-001：concept/procedural 二值）
# ---------------------------------------------------------------------------

LANE_CONCEPT: str = "concept"
LANE_PROCEDURAL: str = "procedural"
# P0-c：非知识出口。新闻播报/产品发布/营销/纯观点既不讲机制也不教操作，
# LLM 判 reject → 直接拒收（不混进通过态 content_lane，下游不下传 reject）。
# 堵 lane 放松后"新闻被判 concept → how_tendency 豁免 → 溜过准入"的后门。
LANE_REJECT: str = "reject"

# ---------------------------------------------------------------------------
# 具名常量（宪法 P4）
# ---------------------------------------------------------------------------

# 准入四判定（topic / how / novelty / lane）共用的 LLM 模型族（宪法 P7 异族分离）。
# 准入只判「源是否值得提炼」、不判提炼产物 → maker=DeepSeek 族 / checker=gpt-5.4 族，
# 两族异族不破。router 仅支持 "deepseek-v4-pro"（llm_router._MODEL_CONFIG），
# 传其他 family 名会抛 RouterConfigError 使准入四路全 fail-closed。
ADMISSION_MODEL: str = "deepseek-v4-pro"

# 准入判定输出 schema（每项判定结果）
_ADMISSION_CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 300},
    },
    "required": ["pass", "reason"],
    "additionalProperties": False,
}

# 车道分类器输出 schema（content_lane 二值枚举 + reason）
_LANE_CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_lane": {
            "type": "string",
            "enum": [LANE_CONCEPT, LANE_PROCEDURAL, LANE_REJECT],
        },
        "reason": {"type": "string", "maxLength": 300},
    },
    "required": ["content_lane"],
    "additionalProperties": False,
}

# procedural 确定性证据阈值：raw 含 ≥ 该数量可执行操作（命令/代码块/编号步骤）
# 即判 procedural（混合归 procedural，spec FR-002）。
# SC-1：阈值=2——单个示例代码块（count=1）不构成操作型，交 LLM 轻判 concept/procedural；
# 含 ≥2 个可执行操作才视为客观无歧义 procedural（教程/工作流式），不依赖 LLM。
# 实测锚点：_MIXED_BODY(2 命令)→procedural（mixed 测试锁死）；ONE_BLOCK(1 命令)→交 LLM。
LANE_PROCEDURAL_MIN_EVIDENCE: int = 2

# 内容截断长度：送入 LLM 判定的最大字符数
# 主题相关性取 4000 字足以覆盖导语+前几段核心内容。
# How 倾向另用 how-aware 窗口，避免真实归档的目录/背景说明把后段 how 章节挤出。
# 新颖度判定需更完整的论点和论证结构，取 3000 字聚焦核心主张。
MAX_ADMISSION_CONTENT_CHARS: int = 4000
MAX_HOW_TENDENCY_CONTENT_CHARS: int = 8000
HOW_TENDENCY_CONTEXT_CHARS: int = 1200
MAX_NOVELTY_CONTENT_CHARS: int = 3000

# 领域 taxonomy 锚点（从 skillpack/quality/concept-index-injection.md 运行时注入）
# 初版硬编码 fallback，后续从概念索引注入文件读取
_DEFAULT_DOMAIN_TAXONOMY: list[str] = [
    "AI Agent",
    "Loop Engineering",
    "Context Engineering",
    "Harness Engineering",
    "Eval Engineering",
    "Multi-Agent Orchestration",
    "AI Coding Workflow",
    "Design System & Tokens",
    "Generative UI",
    "UI/UX Design",
    "Frontend Architecture",
    "Game Design",
    "AI Animation",
    "AI UI Animation",
    "Knowledge Engineering",
    "Tool Design",
    "Protocol Design",
    "MCP",
    "A2A",
    "LangGraph",
]

# ---------------------------------------------------------------------------
# 公开类型
# ---------------------------------------------------------------------------


@dataclass
class AdmissionCheck:
    """单条准入判定结果。"""

    name: str  # 判定名称（topic_relevance / how_tendency / novelty）
    passed: bool
    reason: str


@dataclass
class AdmissionResult:
    """准入整体结果。

    passed: 三判定全通过。
    checks: 逐项判定详情（恒为 3 条：topic_relevance / how_tendency / novelty）。
    reason: 整体拒收原因（passed=True 时为空字符串）。
    content_lane: 内容车道（concept / procedural）。当 passed=True 时为合法终值
        之一；passed=False 时可能为空字符串（分类器不可用而拒收的场景）。
        lane 不进 checks 列表——它是路由信号而非准入判定门。
    """

    passed: bool
    checks: list[AdmissionCheck] = field(default_factory=list)
    reason: str = ""
    content_lane: str = ""
    # lane_reason：车道分类器给出的理由（与 content_lane 配对）。
    # 全路径透出——pass 路径也可见（不止 reject），供 runner 落 ledger 做
    # P1 语义审计的逐篇可读 trace（spec FR-008 / 宪法 P4 失败可见）。仅观测字段，
    # 不参与判定（lane 判定仍由 content_lane/lane_failed/lane_rejected 驱动）。
    lane_reason: str = ""


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------


def run_admission(
    raw_path: str,
    raw_content: str,
    ledger_path: str,
    wiki_references: list[str] | None = None,
    route_llm_fn: Any = None,
) -> AdmissionResult:
    """执行三硬判定准入。

    Args:
        raw_path: raw 文件路径（用于落账锚定）。
        raw_content: raw 文件全文内容。
        ledger_path: 循环账本 jsonl 路径。
        wiki_references: 既有 wiki 页面摘要列表（用于新颖度判定），
            None 时新颖度判定使用内置启发式（无既有知识时默认 pass）。
        route_llm_fn: LLM 路由函数注入（fixture 测试用），
            默认使用 src/akb2/quality/llm_router.route_llm。

    Returns:
        AdmissionResult: 整体判定结果。
    """
    if route_llm_fn is None:
        from src.akb2.quality.llm_router import route_llm as _default

        route_llm_fn = _default

    # 剥离 raw 样板（frontmatter + §锚点目录 TOC），让三判定看正文而非元数据骨架。
    # 此前直接判 raw_content：4000 字窗口被 ~960 字 frontmatter+26 行锚点目录稀释，
    # how-to 实质被挤出视野 → gemini 判"introductory/conceptual" 误拒（含黄金样张）。
    # 实测：同篇去样板后 how_tendency 由 FAIL→PASS。
    body = _strip_raw_boilerplate(raw_content)

    checks: list[AdmissionCheck] = []

    # 1. 主题相关性
    topic_check = _check_topic_relevance(body, route_llm_fn)
    checks.append(topic_check)

    # 2. How 倾向
    how_check = _check_how_tendency(body, route_llm_fn)
    checks.append(how_check)

    # 3. 新颖度
    novelty_check = _check_novelty(body, wiki_references, route_llm_fn)
    checks.append(novelty_check)

    # 4. 车道分类（concept / procedural）。lane 不进 checks 列表——它是路由信号，
    #    不是准入判定门。分类器不可用且无确定性证据 → fail-closed（lane_failed=True）。
    content_lane, lane_failed, lane_reason = _classify_content_lane(body, route_llm_fn)

    # P0-c：非知识内容（新闻/营销/会议recap/纯观点）显式拒收门——不依赖 how_tendency。
    # 堵后门：lane 放松后新闻可能被判 concept → how_tendency 豁免 → 溜过准入（W5/w7 实证）。
    lane_rejected = content_lane == LANE_REJECT

    # how_tendency 路由化（spec FR-003）：concept 车道下其 fail 不使 passed=False。
    # 纯概念内容（无命令）天然 how_tendency=fail，旧逻辑会误拒——本改动让 how_tendency
    # 在 concept 车道仅作路由信号、不计入 all_passed。procedural 车道 how fail 仍拒收。
    # lane 分类失败/reject 时不豁免（按最严判，how fail 仍计入）。
    def _check_counts_for_admission(c: AdmissionCheck) -> bool:
        if c.name == "how_tendency" and content_lane == LANE_CONCEPT and not lane_failed:
            return True  # concept 车道：how_tendency 不作拒收门
        return c.passed

    all_checks_pass = all(_check_counts_for_admission(c) for c in checks)
    # 车道分类失败（SC-4 fail-closed）或非知识 reject（P0-c）→ 拒收
    all_passed = all_checks_pass and not lane_failed and not lane_rejected

    reason = ""
    if not all_passed:
        failed = [
            c.name for c in checks if not _check_counts_for_admission(c)
        ]
        if lane_rejected:
            failed.append(f"non_knowledge({lane_reason})")
        elif lane_failed:
            failed.append("content_lane")
        reason = f"准入未通过: {', '.join(failed)}"

    # 落账（任一 fail 都落账）
    if not all_passed:
        _log_rejection(
            raw_path, checks, ledger_path,
            content_lane=content_lane,
            lane_failed=lane_failed,
            lane_reason=lane_reason,
        )

    return AdmissionResult(
        passed=all_passed,
        checks=checks,
        reason=reason,
        # reject/分类失败都不下传 content_lane（reject 不混进通过态，w9）。
        content_lane="" if (lane_failed or lane_rejected) else content_lane,
        # lane_reason 全路径透出（pass/reject/fail 均可见，不止 reject），
        # 供 runner 落 ledger 做 P1 语义审计逐篇可读 trace。仅观测，不参与判定。
        lane_reason=lane_reason,
    )


# ---------------------------------------------------------------------------
# raw 样板剥离
# ---------------------------------------------------------------------------

# 正文起始锚点：raw_archiver 写入 §A_CONTENT_START（或 §X_CONTENT_START）标记正文开端
_ANCHOR_DECL_RE = r"§[A-Z0-9]+"
_CONTENT_START_RE = re.compile(rf"{_ANCHOR_DECL_RE}_CONTENT_START")

_ANCHOR_HEADING_RE = re.compile(rf"(?m)^##\s+{_ANCHOR_DECL_RE}\.?\s+.+$")
_HOW_HEADING_RE = re.compile(
    r"(?im)^#{1,4}\s+.*(?:"
    r"how|step|workflow|configuration|template|example|code|"
    r"步骤|流程|工作流|配置|模板|示例|代码|架构|模式|组件|构建块|实践|实现"
    r").*$"
)
_LEGACY_HOW_ANCHOR_HEADING_RE = re.compile(r"(?im)^##\s+§[B-H]\b")
_TOC_BLOCK_RE = re.compile(
    rf"(?ms)^##\s*锚点目录\s*\n.*?(?=^---\s*$|^##\s+{_ANCHOR_DECL_RE}\.|\Z)"
)


def _strip_raw_boilerplate(raw_content: str) -> str:
    """剥离 raw 文件的 frontmatter + 锚点目录样板，返回正文供准入判定。

    raw 文件结构：`---frontmatter---` + `## 锚点目录`(26 行 §A-§Z TOC) + `§A_CONTENT_START` + 正文。
    准入三判定应基于正文；直接判全文会让 4000 字窗口被元数据+TOC 稀释，
    how-to 实质被挤出 → 误拒（实测黄金样张同篇去样板后 how_tendency FAIL→PASS）。

    剥离策略（fail-safe：剥空时回退原文，不让格式异常的 raw 直接判空）：
        1. 去开头 `---...---` frontmatter 块
        2. 从首个 `§X_CONTENT_START` 锚点起截取正文（跳过 ## 锚点目录 TOC）
    """
    text = raw_content

    # 1. 剥 frontmatter
    lstripped = text.lstrip()
    if lstripped.startswith("---"):
        end = lstripped.find("\n---", 3)
        if end != -1:
            text = lstripped[end + 4 :]

    # 2. 从正文起始锚点截取（跳过锚点目录 TOC）
    m = _CONTENT_START_RE.search(text)
    if m:
        text = text[m.start() :]

    stripped = text.strip()
    return stripped if stripped else raw_content


def _build_how_tendency_snippet(raw_content: str) -> str:
    """构建 How 倾向专用窗口。

    真实 raw 归档可能没有 `§X_CONTENT_START`，且开头常含抓取说明、传播背景、
    锚点目录和概念导语。机械前 4000 字会让 §B/§H 等 how 章节不可见，造成
    `how_tendency` 误拒。这里保留短导语作为上下文，再优先拼入命中 how 关键词
    的章节，仍限制总长度，避免把整篇文章无界送入轻量准入模型。
    """
    text = _remove_anchor_toc(raw_content).strip()
    if not text:
        text = raw_content.strip()
    if len(text) <= MAX_ADMISSION_CONTENT_CHARS:
        return text[:MAX_ADMISSION_CONTENT_CHARS]

    sections = _split_heading_sections(text)
    selected = [
        section.strip()
        for section in sections
        if _section_is_how_candidate(section)
    ]

    if not selected:
        return text[:MAX_ADMISSION_CONTENT_CHARS]

    parts = [text[:HOW_TENDENCY_CONTEXT_CHARS].strip()]
    seen: set[str] = {parts[0]} if parts[0] else set()
    for section in selected:
        if not section or section in seen:
            continue
        parts.append(section)
        seen.add(section)
        if len("\n\n".join(parts)) >= MAX_HOW_TENDENCY_CONTENT_CHARS:
            break

    snippet = "\n\n".join(part for part in parts if part).strip()
    return snippet[:MAX_HOW_TENDENCY_CONTENT_CHARS]


def _remove_anchor_toc(raw_content: str) -> str:
    """移除 raw 归档锚点目录；格式不匹配时原样返回。"""
    return _TOC_BLOCK_RE.sub("", raw_content)


def _split_heading_sections(text: str) -> list[str]:
    """按 `## §X` 章节切分；没有锚点标题时返回整篇。"""
    matches = list(_ANCHOR_HEADING_RE.finditer(text))
    if not matches:
        return [text]

    sections: list[str] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        section = text[start:end].strip()
        if section:
            sections.append(section)
    return sections


def _section_is_how_candidate(section: str) -> bool:
    """判断章节是否值得送入 How 倾向窗口。"""
    if _HOW_HEADING_RE.search(section):
        return True
    if _LEGACY_HOW_ANCHOR_HEADING_RE.search(section):
        return True
    lower = section.lower()
    return any(
        marker in lower
        for marker in (
            "1.",
            "2.",
            "3.",
            "- ",
            "| primitive |",
            "code",
            "config",
            "workflow",
            "step",
        )
    )


# ---------------------------------------------------------------------------
# 内部判定函数
# ---------------------------------------------------------------------------


def _check_topic_relevance(
    raw_content: str,
    route_llm_fn: Any,
) -> AdmissionCheck:
    """主题相关性判定：领域 taxonomy 锚点命中。

    轻量 LLM 判定内容是否命中领域 taxonomy 中至少一个锚点。
    输出经 jsonschema 校验，非法输出按 fail 处理（fail-closed）。
    """
    from src.akb2.quality.llm_router import RouterConfigError

    # 构建包含 taxonomy 的 prompt
    taxonomy_str = "\n".join(f"- {t}" for t in _DEFAULT_DOMAIN_TAXONOMY)
    # 只取前 4000 字符避免 token 浪费
    content_snippet = raw_content[:MAX_ADMISSION_CONTENT_CHARS]

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个内容分类器。判断给定内容是否命中以下领域 taxonomy 中的至少一个锚点。"
                "输出 JSON: {\"pass\": true/false, \"reason\": \"...\"}。"
                "reason 最多 150 字符。"
            ),
        },
        {
            "role": "user",
            "content": f"领域 taxonomy:\n{taxonomy_str}\n\n内容:\n{content_snippet}",
        },
    ]

    try:
        response = route_llm_fn(
            ADMISSION_MODEL,
            messages,
            response_schema=_ADMISSION_CHECK_SCHEMA,
        )
    except (RouterConfigError, RuntimeError) as exc:
        # 路由不可用时 fail-closed
        return AdmissionCheck(
            name="topic_relevance",
            passed=False,
            reason=f"判定调用失败: {exc}",
        )

    result = _validate_and_normalize(response.content, "topic_relevance")
    return AdmissionCheck(
        name="topic_relevance",
        passed=result["pass"],
        reason=result["reason"],
    )


def _check_how_tendency(
    raw_content: str,
    route_llm_fn: Any,
) -> AdmissionCheck:
    """How 倾向判定：可执行知识占比。

    判定内容是否包含可执行步骤、代码示例、工作流、配置片段等
    "how" 型知识，而非纯新闻/观点/综述。
    """
    from src.akb2.quality.llm_router import RouterConfigError

    content_snippet = _build_how_tendency_snippet(raw_content)

    messages = [
        {
            "role": "system",
            "content": (
                "你判断内容是否包含可执行知识（how-to）。"
                "可执行知识包括：具体步骤、代码示例、工作流、配置模板、架构模式、设计决策理由。"
                "纯新闻、观点评论、benchmark 刷分、融资动态、产品发布公告等不算。"
                "输出 JSON: {\"pass\": true/false, \"reason\": \"...\"}。"
                "reason 最多 150 字符。"
            ),
        },
        {
            "role": "user",
            "content": f"内容:\n{content_snippet}",
        },
    ]

    try:
        response = route_llm_fn(
            ADMISSION_MODEL,
            messages,
            response_schema=_ADMISSION_CHECK_SCHEMA,
        )
    except (RouterConfigError, RuntimeError) as exc:
        return AdmissionCheck(
            name="how_tendency",
            passed=False,
            reason=f"判定调用失败: {exc}",
        )

    result = _validate_and_normalize(response.content, "how_tendency")
    return AdmissionCheck(
        name="how_tendency",
        passed=result["pass"],
        reason=result["reason"],
    )


def _check_novelty(
    raw_content: str,
    wiki_references: list[str] | None,
    route_llm_fn: Any,
) -> AdmissionCheck:
    """新颖度判定：对既有 wiki 增量。

    无既有 wiki 引用时默认 pass（首个入库内容）。
    有引用时轻量 LLM 判定是否提供增量信息。
    """
    if not wiki_references:
        return AdmissionCheck(
            name="novelty",
            passed=True,
            reason="无既有 wiki 引用，默认通过",
        )

    from src.akb2.quality.llm_router import RouterConfigError

    refs_str = "\n---\n".join(ref[:800] for ref in wiki_references[:5])
    content_snippet = raw_content[:MAX_NOVELTY_CONTENT_CHARS]

    messages = [
        {
            "role": "system",
            "content": (
                "你判断新内容是否对既有知识库提供增量信息（新颖度）。"
                "如果新内容基本重复既有知识（洗稿），输出 pass=false。"
                "如果新内容提供新视角、新方法、新代码、新数据，输出 pass=true。"
                "输出 JSON: {\"pass\": true/false, \"reason\": \"...\"}。"
                "reason 最多 150 字符。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"既有知识摘要:\n{refs_str}\n\n"
                f"新内容:\n{content_snippet}"
            ),
        },
    ]

    try:
        response = route_llm_fn(
            ADMISSION_MODEL,
            messages,
            response_schema=_ADMISSION_CHECK_SCHEMA,
        )
    except (RouterConfigError, RuntimeError) as exc:
        return AdmissionCheck(
            name="novelty",
            passed=False,
            reason=f"判定调用失败: {exc}",
        )

    result = _validate_and_normalize(response.content, "novelty")
    return AdmissionCheck(
        name="novelty",
        passed=result["pass"],
        reason=result["reason"],
    )


def _scan_procedural_evidence_count(body: str) -> int:
    """确定性扫描 raw 正文中的**可执行操作**证据数（命令/代码块/编号步骤/API 签名）。

    只用 source_ops_scanners.operations 扫描器（kind=source_operation，纯代码无 LLM）。
    **不数** source_boundary / source_conclusion——那两类语义边界纯概念内容也有
    （如"适用于…""关键洞察是…"），不构成可执行操作；若计入会把纯概念内容误判
    procedural。含 ≥LANE_PROCEDURAL_MIN_EVIDENCE 个可执行操作即客观无歧义 procedural
    （spec FR-002 混合从严归 procedural）；单个示例代码块或无则交 LLM 轻判
    concept/procedural（SC-1：概念文章夹单个示例代码块不应被确定性判 procedural）。
    扫描器不可用（导入失败）时返回 0，把车道判定交给 LLM。
    """
    try:
        from src.akb2.quality.source_ops_scanners import operations
    except ImportError:
        return 0
    try:
        return len(operations.scan(body))
    except Exception:
        return 0


# SC-1（worker round-4 修法）：强 procedural 信号阈值。只有明确 tutorial/command recipe
# 形态才确定性短路，避免把"概念文章夹示例代码/API/编号机制点"误判 procedural。真根因是
# source_operation scanner 双角色复用（must-cover inventory 高召回 + lane intent 短路）。
STRONG_PROCEDURAL_CMD_MIN = 3  # ≥3 shell 命令 → command recipe 形态
_HOWTO_TITLE_RE = re.compile(
    r"(?im)^#{1,3}\s+.*(?:how\s+to|how\s+do\s+i|tutorial|教程|如何|"
    r"配置指南|安装指南|setup\s+guide|step[- ]by[- ]step)"
)
# NUMBERED 修复（w8/w9）：旧 `.*`[^`]+`` 把编号行里任意反引号（API 名 agent.plan()、
# 模型名 DeepSeek-V3、概念术语）都当命令，导致概念文编号机制点误判 procedural。改为反引号内
# 须以真 CLI 命令/shell 关键字开头才算 command recipe；其余交 LLM 主判 intent（宁漏判不误判）。
_NUMBERED_WITH_CMD_RE = re.compile(
    r"(?m)^\s*\d+\.\s+.*`\s*(?:\$\s*)?(?:sudo\s+)?"
    r"(?:npm|pnpm|yarn|pip3?|python3?|node|git|cd|mkdir|rm|cp|mv|"
    r"echo|export|curl|wget|make|docker|kubectl|bash|sh|brew|apt|go|cargo|"
    r"pytest|ruff|mypy)\b"
)


def _has_strong_procedural_signal(body: str) -> bool:
    """SC-1：只有强 procedural 形态才确定性短路（不调 LLM）。

    旧逻辑 `_scan_procedural_evidence_count >= 2` 把任何 2+ 可执行操作（含 illustrative
    code/API/编号机制点）短路成 procedural，导致概念博客带示例代码就被误判 procedural
    → how_tendency 不豁免 + procedural F4 H1<1 拒（满仓 12/18 失败的根因）。本函数只对
    **强 procedural 形态**短路：shell command density（≥3 命令）/ how-to 标题 /
    ≥3 含反引号命令的编号步。其余（含混合、概念+示例代码）交 LLM 主判 intent。
    """
    # 1. shell 命令密度（$ cmd 或 bash/sh block 命令行）
    try:
        from src.akb2.quality.source_ops_scanners import operations

        if len(operations.scan_cli_commands(body)) >= STRONG_PROCEDURAL_CMD_MIN:
            return True
    except ImportError:
        pass
    # 2. how-to 标题
    if _HOWTO_TITLE_RE.search(body):
        return True
    # 3. ≥3 含反引号命令的编号步（imperative + command）
    if len(_NUMBERED_WITH_CMD_RE.findall(body)) >= STRONG_PROCEDURAL_CMD_MIN:
        return True
    return False


def _classify_content_lane(
    body: str,
    route_llm_fn: Any,
) -> tuple[str, bool, str]:
    """车道分类：concept / procedural 二值（spec FR-001/FR-002）。

    SC-1（worker round-4 修法）：**LLM 主判 intent + deterministic 仅作强 procedural 信号**。
        1. 确定性短路：仅强 procedural 形态（≥3 shell 命令 / how-to 标题 / ≥3 含命令编号步）
           → procedural（无歧义 command recipe/tutorial，不调 LLM）。
        2. 其余（含混合、概念+示例代码/API/编号机制点）→ LLM 主判 intent。
           illustrative code/API/编号机制点不再直接短路——避免概念博客误判 procedural。
        schema 校验：仅接受 content_lane ∈ {concept, procedural}。

    fail-closed（SC-4，不静默归车道）:
        - LLM 路由不可用（RouterConfigError/RuntimeError）→ (("", True, reason))
        - LLM 输出非法（无合法 content_lane）→ (("", True, reason))

    Returns:
        (content_lane, lane_failed, reason)
        lane_failed=True 表示分类失败需 fail-closed 拒收；此时 content_lane 为空。
    """
    from src.akb2.quality.llm_router import RouterConfigError

    # 1. 强 procedural 形态才确定性短路（command recipe / how-to tutorial）
    if _has_strong_procedural_signal(body):
        return (
            LANE_PROCEDURAL,
            False,
            "强 procedural 信号（shell 命令密度/how-to 标题/含命令编号步），判操作型车道",
        )

    # 2. 其余 → LLM 主判 intent（concept vs procedural）
    content_snippet = body[:MAX_ADMISSION_CONTENT_CHARS]
    messages = [
        {
            "role": "system",
            "content": (
                "你判断内容的「主要意图」属于「概念型」「操作型」还是「非知识」。"
                "概念型(concept): 主旨是阐述机制/架构/设计原则/模型，讲清「为什么/是什么」；"
                "即便夹有示例代码、API 名或编号机制点，只要它们是为例证概念、"
                "不是教操作，仍判 concept。"
                "操作型(procedural): 主旨是教「怎么做」——可执行步骤、命令、配置、工作流是内容主线。"
                "非知识(reject): 新闻播报/产品发布公告/会议 recap/融资动态/营销软文/纯观点抒发，"
                "既不阐述可复用机制、也不教可执行操作 → 输出 reject；不得为二选一硬塞 concept。"
                "按主要意图判，不要因出现代码就判 procedural。"
                "输出 JSON: {content_lane: concept|procedural|reject, reason: ...}。"
                "reason 最多 150 字符。"
            ),
        },
        {"role": "user", "content": f"内容:\n{content_snippet}"},
    ]

    try:
        response = route_llm_fn(
            ADMISSION_MODEL,
            messages,
            response_schema=_LANE_CLASSIFY_SCHEMA,
        )
    except (RouterConfigError, RuntimeError) as exc:
        # LLM 不可用 → fail-closed（不静默归车道）
        return "", True, f"车道分类调用失败: {exc}"

    lane = _validate_and_normalize_lane(response.content)
    if lane is None:
        # 输出非法（无合法 content_lane）→ fail-closed（不静默归车道）
        return "", True, "车道分类输出非法（缺合法 content_lane 枚举值）"
    if lane == LANE_REJECT:
        # P0-c：非知识内容显式拒收。lane_failed=False 以区分"分类器失败"（SC-4）。
        return LANE_REJECT, False, "非知识内容（新闻/营销/会议recap/纯观点），拒收"
    return lane, False, "LLM 主判 intent 车道"


def _validate_and_normalize_lane(content: str | dict) -> str | None:
    """校验车道分类输出，返回合法 content_lane 或 None（非法）。

    仅接受 content_lane ∈ {concept, procedural}。非法/缺字段返回 None（调用方 fail-closed）。
    无 jsonschema 时退化为枚举值直接校验，仍不放宽（不接受未知值）。
    """
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
    elif isinstance(content, dict):
        parsed = content
    else:
        return None

    lane = parsed.get("content_lane") if isinstance(parsed, dict) else None
    if lane in (LANE_CONCEPT, LANE_PROCEDURAL, LANE_REJECT):
        return lane
    return None


def _validate_and_normalize(
    content: str | dict,
    check_name: str,
) -> dict[str, Any]:
    """jsonschema 校验并归一化判定输出。

    非法输出按 fail 处理（fail-closed）。
    """
    try:
        import jsonschema
    except ImportError:
        # 无 jsonschema 时 fallback 到简单校验
        if isinstance(content, dict):
            if "pass" in content:
                return {
                    "pass": bool(content["pass"]),
                    "reason": str(content.get("reason", "no reason"))[:150],
                }
        return {"pass": False, "reason": "schema 校验失败: 非法输出格式"}

    # 如果 content 是字符串，尝试解析 JSON
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return {
                "pass": False,
                "reason": f"判定 {check_name} 返回非 JSON 格式",
            }
    else:
        parsed = content

    try:
        jsonschema.validate(parsed, _ADMISSION_CHECK_SCHEMA)
        return {"pass": bool(parsed["pass"]), "reason": str(parsed["reason"])[:150]}
    except jsonschema.ValidationError as exc:
        return {
            "pass": False,
            "reason": f"判定 {check_name} schema 校验失败: {exc.message}",
        }


def _log_rejection(
    raw_path: str,
    checks: list[AdmissionCheck],
    ledger_path: str,
    content_lane: str = "",
    lane_failed: bool = False,
    lane_reason: str = "",
) -> None:
    """落账拒收事件。

    调用 step-1 ledger.append_event 写入 jsonl 账本。
    details 携 content_lane 键（Step.9：lane 贯穿到 ledger）。lane 分类失败时
    failed_checks 含 content_lane 条目，使「车道不可用而拒收」在账本可见（SC-4）。
    """
    import hashlib

    from src.akb2.loops.ledger import append_event

    content_hash = hashlib.sha256(
        raw_path.encode("utf-8")
    ).hexdigest()[:16]
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    failure_details = {
        c.name: {"passed": c.passed, "reason": c.reason}
        for c in checks
    }
    if lane_failed:
        failure_details["content_lane"] = {"passed": False, "reason": lane_reason}

    event: dict[str, Any] = {
        "loop_id": "loop1",
        "node": "admission",
        "event": "admission",
        "content_hash": content_hash,
        "ts": ts,
        "budget": {
            "token_count": 0,
            "elapsed_sec": 0,
            "retry_count": 0,
        },
        "details": {
            "raw_path": raw_path,
            "admission_result": "rejected",
            "content_lane": content_lane,
            "failed_checks": {
                k: v for k, v in failure_details.items() if not v["passed"]
            },
            "all_checks": failure_details,
        },
    }

    append_event(ledger_path, event)
