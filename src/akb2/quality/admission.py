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

from src.akb2.quality.raw_anchors import ANCHOR_TOKEN_PAT

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
    # §0.2/D11：lane reject 二次确认标记——how_tendency PASS + strong mechanism 时
    # 降级 reject → concept（不直接拒），原 lane_reason 记此。仅观测，不参与判定。
    lane_reject_recheck: bool = False
    original_lane_reason: str = ""


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
    lane_reject_recheck = False
    original_lane_reason = ""

    # §0.2/D11：lane reject 二次确认——how_tendency PASS + strong mechanism（strong
    # signal≥1 或 strong_component≥3）时不直接拒，降级 reject → concept（继续走机制
    # 信号门，由 mechanism_signal 决定，不放观点稿）。weak-only 不覆盖 reject（保持拒）。
    # 修 idx6 how_tendency PASS 但 lane=reject 抖动误拒。
    if lane_rejected:
        original_lane_reason = lane_reason
        mech_signals = _scan_mechanism_signals(body)
        strong_signal = (
            mech_signals["causal"] >= 1
            or mech_signals["code_blocks"] >= 1
            or mech_signals["source_operations"] >= 1
        )
        strong_component = mech_signals["components"] >= MECHANISM_COMPONENT_MIN
        if how_check.passed and (strong_signal or strong_component):
            lane_reject_recheck = True
            content_lane = LANE_CONCEPT
            lane_rejected = False
            lane_reason = (
                f"lane_reject_recheck: how_tendency PASS + strong mechanism"
                f"（components={mech_signals['components']}, causal={mech_signals['causal']}, "
                f"code={mech_signals['code_blocks']}, src_ops={mech_signals['source_operations']}）"
                f" → 降级 concept（原 reject: {original_lane_reason}）"
            )
        # weak-only: 不覆盖 reject，保持 lane_rejected=True（LLM 窄仲裁或 fail-closed）

    # 5. 机制信号准入门（WP-2/D2）：仅 concept 候选，确定性为主 + LLM 窄仲裁。
    # 无机制散文（openai 政策/新闻/纯观点）在 admission 即拒（no_mechanism_signal），
    # 不下传 distill 硬填模板（D8 maker fallback 造假机制源头）。procedural 不受此门
    # （F4 H1 判）；lane_failed/reject 时已拒收，不跑。
    if content_lane == LANE_CONCEPT and not lane_failed and not lane_rejected:
        # F13-fix2（reviewer 两轮 blocking 定论）：经 D11 reject-recheck 降级回 concept
        # 的样本,禁用**全部**确定性 PASS 分支(force_llm_adjudication)——lane LLM 已判过
        # 一次"非知识",撤销该判定 ≠ 确定性准入,必须过 mechanism LLM 仲裁(`## 1. X`
        # 编号标题会被计成命名组件,任何确定性分支都是后门——reviewer 反例实证)。
        checks.append(
            _check_mechanism_signal(
                body, route_llm_fn, force_llm_adjudication=lane_reject_recheck
            )
        )

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
        # WP-2：mechanism_signal 失败映射 no_mechanism_signal（runner 据此设 error_class）
        failed: list[str] = []
        for c in checks:
            if not _check_counts_for_admission(c):
                failed.append(
                    "no_mechanism_signal" if c.name == "mechanism_signal" else c.name
                )
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
        # §0.2/D11：lane reject 二次确认观测字段（ledger/calibration 透出）
        lane_reject_recheck=lane_reject_recheck,
        original_lane_reason=original_lane_reason,
    )


# ---------------------------------------------------------------------------
# raw 样板剥离
# ---------------------------------------------------------------------------

# 正文起始锚点：raw_archiver 写入 §A_CONTENT_START（或 §X_CONTENT_START）标记正文开端
# P1a: token pattern 走 raw_anchors.ANCHOR_TOKEN_PAT（含下划线,认业务锚 §I_LIMITATIONS）
_ANCHOR_DECL_RE = ANCHOR_TOKEN_PAT
_CONTENT_START_RE = re.compile(rf"{_ANCHOR_DECL_RE}_CONTENT_START")

_ANCHOR_HEADING_RE = re.compile(rf"(?m)^##\s+{_ANCHOR_DECL_RE}\.?\s+.+$")
# F11-C: 任意 H2 标题切节(scraped blog 普通 ## 标题,非 ## §X 锚点标题)。
# 旧 _split_heading_sections 只按 ## §X 切 → scraped blog 整篇 1 节 →
# _build_keyword_aware_snippet 取前 8000(intro),机制段在末尾被截 → LLM 误判
# no_mechanism_signal(idx0 long-running-agents)。改任意 H2 切,机制节单独成节被选入。
_H2_HEADING_RE = re.compile(r"(?m)^##\s+\S.*$")
_HOW_HEADING_RE = re.compile(
    r"(?im)^#{1,4}\s+.*(?:"
    r"how|step|workflow|configuration|template|example|code|"
    r"步骤|流程|工作流|配置|模板|示例|代码|架构|模式|组件|构建块|实践|实现"
    r").*$"
)
_LEGACY_HOW_ANCHOR_HEADING_RE = re.compile(r"(?im)^##\s+§[B-H]\b")
_TOC_BLOCK_RE = re.compile(
    # F11-C: 加 ^##\s+\S(任意 H2 标题)作停边界——idx8 lilian-weng 等 scraped blog
    # 有 intake 加的 ## 锚点目录 但无 --- 分隔、body 是普通 ## H2 → 旧正则只停 ---
    # 或 ## §X. → 从 ## 锚点目录 吃到 \Z,只剩 frontmatter → mechanism snippet=325
    # 字符 → LLM 收空 body 误判 no_mechanism_signal。加 H2 停边界正确界定 TOC 块。
    r"(?ms)^##\s*锚点目录\s*\n.*?(?=^---\s*$|^##\s+\S|\Z)"
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


def _build_keyword_aware_snippet(
    raw_content: str,
    section_filter: Any,
    context_chars: int,
    max_chars: int,
) -> str:
    """P2 通用关键词感知窗口:去 TOC + 短导语 + 命中 section_filter 的章节,限 max_chars。

    `_build_how_tendency_snippet` 的重构通用版(行为逐字节不变,测试锁);
    `_build_mechanism_snippet` 复用同结构,换 section_filter(how ∪ mechanism 谓词)。

    真实 raw 开头常含抓取说明/传播背景/锚点目录/概念导语,机械前 N 字让后段章节不可见
    → 误拒。本函数保留短导语作上下文,再优先拼入命中 filter 的章节,限 max_chars。
    """
    text = _remove_anchor_toc(raw_content).strip()
    if not text:
        text = raw_content.strip()
    if len(text) <= MAX_ADMISSION_CONTENT_CHARS:
        return text[:MAX_ADMISSION_CONTENT_CHARS]

    sections = _split_heading_sections(text)
    selected = [section.strip() for section in sections if section_filter(section)]

    if not selected:
        return text[:MAX_ADMISSION_CONTENT_CHARS]

    parts = [text[:context_chars].strip()]
    seen: set[str] = {parts[0]} if parts[0] else set()
    for section in selected:
        if not section or section in seen:
            continue
        parts.append(section)
        seen.add(section)
        if len("\n\n".join(parts)) >= max_chars:
            break

    snippet = "\n\n".join(part for part in parts if part).strip()
    return snippet[:max_chars]


def _build_how_tendency_snippet(raw_content: str) -> str:
    """构建 How 倾向专用窗口(P2 重构为 _build_keyword_aware_snippet 委托,行为逐字节不变)。"""
    return _build_keyword_aware_snippet(
        raw_content,
        _section_is_how_candidate,
        HOW_TENDENCY_CONTEXT_CHARS,
        MAX_HOW_TENDENCY_CONTENT_CHARS,
    )


# P2: mechanism 仲裁窗口(how ∪ mechanism 谓词,cap 8000)
MAX_MECHANISM_CONTENT_CHARS: int = 8000
MECHANISM_CONTEXT_CHARS: int = 1200
# mechanism 谓词关键词(标题/正文出现即算机制章节候选)
_MECHANISM_HEADING_RE = re.compile(
    r"(?im)^#{1,4}\s+.*(?:"
    r"mechanism|component|architecture|pipeline|flow|graph|layer|runtime|"
    r"scheduler|executor|coordinator|dispatcher|protocol|"
    r"机制|组件|架构|管线|流程|图谱|层|运行时|调度|协调|协议"
    r").*$"
)


def _section_is_mechanism_candidate(section: str) -> bool:
    """P2: 章节是否值得送入 mechanism 仲裁窗口(how ∪ mechanism 谓词)。"""
    if _section_is_how_candidate(section):
        return True
    if _MECHANISM_HEADING_RE.search(section):
        return True
    lower = section.lower()
    return any(
        kw in lower
        for kw in (
            "mechanism", "component", "architecture", "pipeline",
            "机制", "组件", "架构", "管线",
        )
    )


def _build_mechanism_snippet(raw_content: str) -> str:
    """P2: mechanism 仲裁专用窗口(how ∪ mechanism 谓词,cap 8000)。

    长文机制在后段(body[:4000] 截断漏判 idx0),改用关键词感知窗口拼入机制章节。
    """
    return _build_keyword_aware_snippet(
        raw_content,
        _section_is_mechanism_candidate,
        MECHANISM_CONTEXT_CHARS,
        MAX_MECHANISM_CONTENT_CHARS,
    )


def _remove_anchor_toc(raw_content: str) -> str:
    """移除 raw 归档锚点目录；格式不匹配时原样返回。"""
    return _TOC_BLOCK_RE.sub("", raw_content)


def _split_heading_sections(text: str) -> list[str]:
    """按 H2 章节切分(任意 ## 标题,含 ## §X 锚点 + scraped blog 普通 ##)。

    F11-C: 旧版只按 ## §X 锚点切,scraped blog(无锚点,普通 ## 标题)整篇 1 节 →
    snippet 取前 8000(intro),机制段在末尾被截 → LLM 误判 no_mechanism(idx0)。
    改任意 H2 切,机制节单独成节被 _build_keyword_aware_snippet 选入。
    """
    matches = list(_H2_HEADING_RE.finditer(text))
    if not matches:
        return [text]

    sections: list[str] = []
    # H2 之前的内容(frontmatter/导语)作为首节(若非空)
    pre = text[: matches[0].start()].strip()
    if pre:
        sections.append(pre)
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


def _normalize_wiki_references(
    wiki_references: list[str] | list[dict] | None,
) -> list[dict[str, str]]:
    """§0.2/D10/G1：wiki_references 归一为结构化 list[dict]。

    兼容旧 list[str]（TLDR 字符串 → {tldr: ref, slug/path: ""}）与新 list[dict]。
    """
    if not wiki_references:
        return []
    out: list[dict[str, str]] = []
    for ref in wiki_references:
        if isinstance(ref, dict):
            out.append(
                {
                    "path": str(ref.get("path", "")),
                    "slug": str(ref.get("slug", "")),
                    "title": str(ref.get("title", "")),
                    "tldr": str(ref.get("tldr", "")),
                    "source_url": str(ref.get("source_url", "")),
                    "taxonomy": str(ref.get("taxonomy", "")),
                }
            )
        elif isinstance(ref, str):
            out.append(
                {"path": "", "slug": "", "title": "", "tldr": ref,
                 "source_url": "", "taxonomy": ""}
            )
    return out


def _raw_novelty_meta(raw_content: str) -> dict[str, str]:
    """§0.2/D10/G1：从 raw frontmatter + 首个 H1 提取 slug/url/title 供 exact 匹配。"""
    meta: dict[str, str] = {"slug": "", "source_url": "", "title": ""}
    if not raw_content:
        return meta
    # frontmatter
    if raw_content.lstrip().startswith("---"):
        end = raw_content.find("\n---", 3)
        if end != -1:
            fm = raw_content[3:end]
            for key in ("source_slug", "source_url", "source_title"):
                m = re.search(rf"^({key})\s*:\s*(.+?)\s*$", fm, re.M)
                if m:
                    val = m.group(2).strip().strip("\"'")
                    if key == "source_slug":
                        meta["slug"] = val
                    elif key == "source_url":
                        meta["source_url"] = val
                    elif key == "source_title":
                        meta["title"] = val
    # 首个 H1 作 title fallback
    if not meta["title"]:
        m = re.search(r"(?m)^#\s+(.+?)\s*$", raw_content)
        if m:
            meta["title"] = m.group(1).strip()
    return meta


def _tokenize_for_overlap(text: str) -> set[str]:
    """轻量分词（小写 + 去标点 + 停用词），供 lexical/topic top-k 近邻选择。"""
    if not text:
        return set()
    lower = re.sub(r"[^\w\s一-鿿]", " ", text.lower())
    stop = {
        "the", "a", "an", "to", "of", "and", "in", "for", "is", "are", "with",
        "on", "by", "or", "as", "at", "it", "this", "that", "from", "be",
    }
    return {w for w in lower.split() if w and w not in stop and len(w) > 1}


def _select_novelty_neighbors(
    raw_content: str,
    references: list[dict[str, str]],
    k: int = 3,
) -> tuple[list[dict[str, str]], str]:
    """§0.2/D10/G1：选主题相关 top-k 近邻（修 idx6/idx10 全库前 5 条无关引用误拒）。

    先 exact slug/source_url/near-title，再 lexical/topic top-k（tldr+title token overlap）。
    无近邻 → 返回 ([], "no_relevant_baseline")。
    命中 exact slug/url → "duplicate_same_claim" 候选（LLM 判重复 vs 新增）。
    其余近邻 → "unrelated_baseline"（相关但不同源，LLM 判增量）。
    """
    if not references:
        return [], "no_relevant_baseline"
    raw_meta = _raw_novelty_meta(raw_content)
    raw_tokens = _tokenize_for_overlap(
        raw_content[:MAX_NOVELTY_CONTENT_CHARS] + " " + raw_meta.get("title", "")
    )
    # 1. exact slug / source_url 匹配（duplicate_same_claim 候选）
    exact: list[dict[str, str]] = []
    for ref in references:
        rs = ref.get("slug", "")
        ru = ref.get("source_url", "")
        if (rs and rs == raw_meta["slug"]) or (ru and ru and ru == raw_meta["source_url"]):
            ref = {**ref, "_match_type": "duplicate_same_claim"}
            exact.append(ref)
    if exact:
        return exact[:k], "duplicate_same_claim"
    # 2. near-title + lexical/topic top-k（token overlap）
    scored: list[tuple[int, dict[str, str]]] = []
    for ref in references:
        ref_text = (ref.get("title", "") + " " + ref.get("tldr", "")).strip()
        ref_tokens = _tokenize_for_overlap(ref_text)
        overlap = len(raw_tokens & ref_tokens)
        if overlap > 0:
            scored.append((overlap, ref))
    if not scored:
        return [], "no_relevant_baseline"
    scored.sort(key=lambda x: x[0], reverse=True)
    neighbors = [{**r, "_match_type": "unrelated_baseline"} for _, r in scored[:k]]
    return neighbors, "unrelated_baseline"


def _check_novelty(
    raw_content: str,
    wiki_references: list[str] | list[dict] | None,
    route_llm_fn: Any,
) -> AdmissionCheck:
    """§0.2/D10/G1：新颖度判定——top-k 近邻选择 + LLM 判增量。

    无既有 wiki 引用 → 默认 pass。有引用时 _select_novelty_neighbors 选相关 top-k
    （exact slug/url → duplicate_same_claim；lexical overlap → unrelated_baseline；
    无近邻 → no_relevant_baseline，默认 pass 低置信）。reason 记比较对象 path/slug +
    判定类型。洗稿（duplicate_same_claim）仍拒。
    """
    references = _normalize_wiki_references(wiki_references)
    if not references:
        return AdmissionCheck(
            name="novelty",
            passed=True,
            reason="无既有 wiki 引用，默认通过",
        )

    from src.akb2.quality.llm_router import RouterConfigError

    neighbors, match_type = _select_novelty_neighbors(raw_content, references)
    if not neighbors:
        return AdmissionCheck(
            name="novelty",
            passed=True,
            reason="no_relevant_baseline: 无相关既有 wiki 近邻，默认增量通过（低置信）",
        )

    refs_str = "\n---\n".join(
        f"[{n.get('slug') or n.get('path')}] {n.get('tldr', '')[:600]}" for n in neighbors
    )
    content_snippet = raw_content[:MAX_NOVELTY_CONTENT_CHARS]
    compare_targets = ", ".join(
        n.get("slug") or n.get("path") or "?" for n in neighbors
    )

    messages = [
        {
            "role": "system",
            "content": (
                "你判断新内容是否对既有知识库提供增量信息（新颖度）。"
                "如果新内容基本重复既有知识（洗稿），输出 pass=false。"
                "如果新内容提供新视角、新方法、新代码、新数据，输出 pass=true。"
                "注意：相关主题但核心概念不同（如同一作者的相邻主题、上下游概念、"
                "同领域不同机制）属于增量，输出 pass=true——相关≠重复；"
                "仅当核心论点与既有条目基本重合（洗稿/转述）才 pass=false。"
                "输出 JSON: {\"pass\": true/false, \"reason\": \"...\"}。"
                "reason 最多 150 字符。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"既有知识摘要（比较近邻）:\n{refs_str}\n\n"
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
            reason=f"判定调用失败（比较 {compare_targets}, {match_type}）: {exc}",
        )

    result = _validate_and_normalize(response.content, "novelty")
    reason = f"[{match_type}, compare={compare_targets}] {result['reason']}"
    return AdmissionCheck(
        name="novelty",
        passed=result["pass"],
        reason=reason,
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


# ---------------------------------------------------------------------------
# 机制信号准入门（WP-2 / D2）—— 仅 concept 候选，确定性为主 + LLM 窄仲裁
# ---------------------------------------------------------------------------

# concept 候选在 admission 段预筛「机制存在性」：无机制散文（openai 政策 / 新闻 /
# 纯观点）在 admission 即拒（error_class=no_mechanism_signal），不下传 distill 硬填
# 模板组件（D8 maker fallback 造假机制的源头）。procedural 不受此门（F4 H1 判）。
MECHANISM_COMPONENT_MIN: int = 3
# §0.2/F8：长正文阈值——total==0 但 body_length ≥ 此值时走 LLM 窄仲裁（不 deterministic fail），
# 救 long-running-agents 等长机制散文（无标题/连接词/code 但有机制内容）。短正文 total==0 仍 fail。
MECHANISM_LONG_PROSE_THRESHOLD: int = 500
# F13-fix2（reviewer 两轮 blocking 定论）：词面/结构计数在确定性层面分不开"机制文"与
# "营销编号清单"（`## 1. Faster onboarding` 会被计成命名组件;普通 H2 分节营销文轻松凑
# weak_comp≥3）。故 **不设任何密度类确定性 PASS 短路**;密度信号降级为仲裁证据——
# `_check_mechanism_signal` 把确定性信号计数注入 mechanism LLM 仲裁 prompt,由 LLM
# 拿证据判（idx8 lilian 类推理机制文靠证据增强争取,不开确定性后门）。

# 命名组件候选（strong）：编号 H2/H3/H4 标题（### 1. Captain / ## 2. Executor Pool / ### Step 1）
_MECHANISM_COMPONENT_RE = re.compile(r"(?m)^#{2,4}\s+\d+[\.\)]\s+\S+")
# §0.2/G2/C4：普通（非编号）H2/H3/H4 标题 → weak 机制候选（## Captain / ### Executor Pool）。
# weak 信号不触发 deterministic pass（C4：3 泛技术标题观点稿不绕过 LLM 仲裁），只让
# 普通 H2/H3 机制文不被 no_mechanism_signal 强反信号误拒（idx0/idx8）→ 走 LLM 窄仲裁。
_PLAIN_MECHANISM_HEADING_RE = re.compile(r"(?m)^#{2,4}\s+(?!\d+[\.\)]\s*)\S+")
# 因果连接词（机制组件间的因果关系）
_CAUSAL_CONNECTOR_RE = re.compile(
    r"(?i)\b(?:leads?\s+to|triggers?|causes?|because\s+of|therefore|"
    r"results?\s+in|implies|produces?|enables?|prevents?|propagates?)\b"
)
# 编号步骤行（≥3 构成 how 信号，concept 机制也常编号列点）
_NUMBERED_STEP_RE = re.compile(r"(?m)^\s*\d+\.\s+\S")


def _scan_mechanism_signals(body: str) -> dict[str, int]:
    """确定性扫描机制信号强度（命名组件 / 因果连接词 / 编号步 / code / source_operation）。

    纯代码无 LLM：供 ``_check_mechanism_signal`` 确定性短路用。

    §0.2/G2/C4：components = strong（编号标题）；weak_components = 普通 H2/H3 标题。
    weak 不触发 deterministic pass，只计入 total 避免 no_mechanism_signal 误拒普通机制文。
    """
    components = len(_MECHANISM_COMPONENT_RE.findall(body))
    weak_components = len(_PLAIN_MECHANISM_HEADING_RE.findall(body))
    causal = len(_CAUSAL_CONNECTOR_RE.findall(body))
    numbered_steps = len(_NUMBERED_STEP_RE.findall(body))
    code_blocks = body.count("```") // 2
    try:
        from src.akb2.quality.source_ops_scanners import operations

        src_ops = len(operations.scan(body))
    except Exception:
        src_ops = 0
    return {
        "components": components,
        "weak_components": weak_components,
        "causal": causal,
        "numbered_steps": numbered_steps,
        "code_blocks": code_blocks,
        "source_operations": src_ops,
    }


def _check_mechanism_signal(
    body: str, route_llm_fn: Any, force_llm_adjudication: bool = False
) -> AdmissionCheck:
    """WP-2/D2：concept 候选机制信号准入门（确定性为主 + LLM 窄仲裁）。

    - 确定性 **强信号 PASS**：≥3 命名组件候选 / (≥1 组件 + ≥1 因果边) / (≥3 编号步 + code)
    - 确定性 **强反信号 FAIL**：全信号 = 0（纯散文，无组件/因果/编号/code/ops）
      → ``no_mechanism_signal``，**LLM 不得绕过**（确定性反信号优先）。
    - 模糊（部分信号未达强信号）→ LLM 窄仲裁；LLM 不可用 → fail-closed 拒收。
    - F13-fix2：仲裁 prompt 附确定性信号计数（密度是证据不是通行证,由 LLM 判）。

    Args:
        force_llm_adjudication: F13-fix2（reviewer 两轮 blocking 定论）——True 时禁用
            **全部**确定性 PASS 分支（强反信号 FAIL 保留），一律进 LLM 仲裁。用于
            D11 reject-recheck 降级样本：lane 判过"非知识"的样本必须保留 mechanism
            LLM 否决权,任何确定性放行都是后门（`## 1. Faster onboarding` 编号标题
            会被计成命名组件——reviewer 反例实证）。

    返回 AdmissionCheck(name="mechanism_signal")；失败时 reason 以
    ``no_mechanism_signal`` 开头，runner 据此映射 error_class。
    """
    signals = _scan_mechanism_signals(body)
    comp = signals["components"]
    weak_comp = signals["weak_components"]
    causal = signals["causal"]
    steps = signals["numbered_steps"]
    code = signals["code_blocks"]
    src_ops = signals["source_operations"]

    # 确定性 PASS：强机制信号（§0.2/C4：仅 strong 信号触发 deterministic pass；
    # weak_components 不触发，3 泛技术标题观点稿不绕过 LLM 仲裁）。
    # F13-fix2：force_llm_adjudication=True（D11 recheck 样本）禁用全部确定性 PASS。
    if not force_llm_adjudication:
        if comp >= MECHANISM_COMPONENT_MIN:
            return AdmissionCheck(
                name="mechanism_signal", passed=True,
                reason=f"确定性机制信号: {comp} 命名组件候选",
            )
        if comp >= 1 and causal >= 1:
            return AdmissionCheck(
                name="mechanism_signal", passed=True,
                reason=f"确定性机制信号: {comp} 组件 + {causal} 因果边",
            )
        if steps >= MECHANISM_COMPONENT_MIN and code >= 1:
            return AdmissionCheck(
                name="mechanism_signal", passed=True,
                reason=f"确定性机制信号: {steps} 编号步 + code 块",
            )

    # 确定性 FAIL：全无信号 + 正文短（纯短散文/新闻 recap）—— LLM 不可绕过此强反信号
    # §0.2/G2：weak_components（普通 H2/H3）计入 total，避免普通标题机制文被误拒
    # （idx0/idx8：普通 H2/H3 机制文 → total>0 → 走 LLM 窄仲裁，不 deterministic fail）
    # §0.2/F8：长正文（body_length ≥ MECHANISM_LONG_PROSE_THRESHOLD）即使 total==0 也走 LLM
    # 窄仲裁（long-running-agents 等长机制散文无标题/连接词/code 但有机制内容）；
    # 短正文 total==0 → 仍 deterministic fail（短新闻/营销 recap）。
    total = comp + weak_comp + causal + steps + code + src_ops
    body_length = len(body or "")
    if total == 0 and body_length < MECHANISM_LONG_PROSE_THRESHOLD:
        return AdmissionCheck(
            name="mechanism_signal", passed=False,
            reason="no_mechanism_signal: 无命名组件/因果边/编号步/code/source_operation 且正文短",
        )

    # 模糊（部分信号未达强信号）→ LLM 窄仲裁
    from src.akb2.quality.llm_router import RouterConfigError

    # P2: mechanism 仲裁用关键词感知窗口(how ∪ mechanism 谓词,cap 8000),非 body[:4000]
    # (长文机制在后段,截断漏判 idx0)
    content_snippet = _build_mechanism_snippet(body)
    # F13-fix2：确定性信号计数注入仲裁 prompt——密度是证据不是通行证。idx8 lilian 类
    # 推理机制文（causal/steps/小节标题三高）靠证据增强争取；同时明示"营销收益清单的
    # 因果词不算机制因果",防计数被词面营销文借用。
    signal_line = (
        f"确定性信号计数（辅助证据，非判定依据）: 命名组件={comp}, "
        f"小节标题={weak_comp}, 因果连接词={causal}, 编号步={steps}, "
        f"code块={code}, source_ops={src_ops}"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你判断内容是否阐述了一个可复用的「机制」（≥3 命名组件 + 因果关系），"
                "而非纯政策/新闻/观点散文。机制组件 = 命名架构部件/流程阶段/算法步骤/协议层；"
                "推理/验证/自我修正等认知过程机制（如 CoT、self-consistency、verification）"
                "也属于机制，只要文中给出组件化的过程与因果关系。"
                "注意：信号计数高不等于机制——营销/发布文的收益清单（faster/better 因果词堆叠）"
                "不构成机制因果，应判 false。"
                "输出 JSON: {\"pass\": true/false, \"reason\": \"...\"}。reason 最多 150 字符。"
            ),
        },
        {"role": "user", "content": f"{signal_line}\n\n内容:\n{content_snippet}"},
    ]
    try:
        response = route_llm_fn(
            ADMISSION_MODEL, messages, response_schema=_ADMISSION_CHECK_SCHEMA,
        )
    except (RouterConfigError, RuntimeError) as exc:
        # 信号识别不可用 → fail-closed 拒收
        return AdmissionCheck(
            name="mechanism_signal", passed=False,
            reason=f"no_mechanism_signal: 机制信号识别不可用 fail-closed: {exc}",
        )
    result = _validate_and_normalize(response.content, "mechanism_signal")
    return AdmissionCheck(
        name="mechanism_signal", passed=result["pass"], reason=result["reason"],
    )



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
    # P2: lane 仲裁用 mechanism 窗口(how ∪ mechanism 谓词,cap 8000),非 body[:4000]
    content_snippet = _build_mechanism_snippet(body)
    messages = [
        {
            "role": "system",
            "content": (
                "你判断内容的「主要意图」属于「概念型」「操作型」还是「非知识」。"
                "概念型(concept): 主旨是阐述机制/架构/设计原则/模型，讲清「为什么/是什么」；"
                "即便夹有示例代码、API 名或编号机制点，只要它们是为例证概念、"
                "不是教操作，仍判 concept。"
                "操作型(procedural): 主旨是教「怎么做」——可执行步骤、命令、配置、工作流是内容主线；"
                "实操案例/项目移植记录/工具使用 walkthrough（叙述「我用 X 工具做成了 Y」"
                "的过程、决策与步骤）即使夹叙夹议、无显式编号步骤，也判 procedural。"
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
