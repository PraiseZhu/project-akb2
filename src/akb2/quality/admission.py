"""准入判定 — 单次结构化 LLM 判定 + 确定性预筛（宪法 P3/P4/P8）。

P3 重构（intake-rebuild）：topic / how / lane / mechanism / novelty-LLM 的多次
LLM 调用收成**一次**结构化判定（topic+lane+non_knowledge 一票出），novelty 保持
确定性 top-k 预筛、不再单独打 LLM。单篇 run_admission 对 route_llm 的调用次数
≤ 1，由 ``_SingleCallGuard`` 源码级守卫强制（第二次调用直接抛错 fail-closed，
预算强制是 llm_router 侧 sc-p3-max-calls-enforced 的独立不变式）。

判定结构（每项二值，无连续分数）：
    1. 主题相关性（topic_pass）— 领域 taxonomy 锚点命中，全车道硬门
    2. How 倾向（how_pass）— 仅 tutorial 车道拒收门（sc-p3-lane-scoped-gates）
    3. 新颖度 — 纯确定性：exact slug/url 匹配拒（洗稿），lexical top-k 近邻
       仅作 reason 记录（不再打 LLM）
    4. 机制信号（mechanism_pass）— 仅 concept 车道拒收门；确定性强反信号
       （全信号 0 且正文短）LLM 不可绕过（fail-closed 方向不变）
    5. 车道 + 非知识一票（lane + non_knowledge）— reject / non_knowledge=true
       直接拒收（P0-c；D11 降级机制按 holds「LANE_REJECT 仍直接拒收」删除）

任一硬门 fail → 整体拒收并落账（调用 step-1 ledger.append_event）。
全部 pass → 返回 AdmissionResult(passed=True) 进入提炼段。

公开接口:
    run_admission(raw_path, raw_content, ledger_path, wiki_references=...) -> AdmissionResult
    AdmissionResult = dataclass(passed, checks, reason, content_lane)

内容车道（content_lane，sc-p3-lane-enum）:
    concept（机制/架构概念阐述，过渡保留）| tutorial（教程/操作指南，兼容
    procedural 别名规范化）| pattern（可复用模式）| design（设计呈现）四选一，
    外加 reject（非知识，一票拒收不下传）。未知车道值 fail-closed（拒收+落账），
    不静默归车道。pattern/design 不再默认落 concept 桶（各自独立车道）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.akb2.quality.raw_anchors import ANCHOR_TOKEN_PAT

# ---------------------------------------------------------------------------
# 内容车道常量（sc-p3-lane-enum：四车道 + reject）
# ---------------------------------------------------------------------------

LANE_CONCEPT: str = "concept"
# tutorial 是操作型车道的 canonical 值；procedural 降为兼容别名（LLM 输出
# procedural 时规范化为 tutorial，输出侧不再产生 procedural 终值）。
LANE_TUTORIAL: str = "tutorial"
LANE_PATTERN: str = "pattern"
LANE_DESIGN: str = "design"
# 兼容别名：旧契约的 procedural 车道值。仅作输入归一化，不作为输出终值。
LANE_PROCEDURAL: str = "procedural"
# P0-c：非知识出口。纯新闻播报/融资/会议 recap/无机制营销/纯观点既不讲机制
# 也不教操作，LLM 判 reject → 直接拒收（不混进通过态 content_lane，下游不下传
# reject）。产品/模型发布稿若含 ≥3 可命名机制组件或可执行配置/代码，按
# concept/tutorial 放行，不因体裁一律 reject。
# 堵 lane 放松后"新闻被判 concept → how_tendency 豁免 → 溜过准入"的后门。
# P3 重构：reject 直接拒收（holds「LANE_REJECT 仍直接拒收」），D11 降级机制删除。
LANE_REJECT: str = "reject"

# 车道合法终值（通过态）；reject/未知值不进此集合（fail-closed 或一票拒）。
LANE_PASSABLE: tuple[str, ...] = (
    LANE_CONCEPT,
    LANE_TUTORIAL,
    LANE_PATTERN,
    LANE_DESIGN,
)

# ---------------------------------------------------------------------------
# 具名常量（宪法 P4）
# ---------------------------------------------------------------------------

# 准入单次结构化判定共用的 LLM 模型族（宪法 P7 异族分离）。
# 准入只判「源是否值得提炼」、不判提炼产物 → maker=DeepSeek 族 / checker=gpt-5.4 族，
# 两族异族不破。router 仅支持 "deepseek-v4-pro"（llm_router._MODEL_CONFIG），
# 传其他 family 名会抛 RouterConfigError 使准入 fail-closed。
ADMISSION_MODEL: str = "deepseek-v4-pro"

# 准入单次判定输出 schema（每项判定结果）
_ADMISSION_CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 300},
    },
    "required": ["pass", "reason"],
    "additionalProperties": False,
}

# 单次结构化判定 schema（sc-p3-single-llm-call）。字段名用 `lane` 而非
# `content_lane`：外围测试（test_f13）按 schema.properties.content_lane 识别
# 旧车道调用，unified schema 必须不触发该识别路径。
_ADMISSION_UNIFIED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic_pass": {"type": "boolean"},
        "lane": {
            "type": "string",
            "enum": [
                LANE_CONCEPT,
                LANE_TUTORIAL,
                LANE_PROCEDURAL,  # 兼容别名，规范化为 tutorial
                LANE_PATTERN,
                LANE_DESIGN,
                LANE_REJECT,
            ],
        },
        "non_knowledge": {"type": "boolean"},
        "mechanism_pass": {"type": "boolean"},
        "how_pass": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 300},
    },
    "required": ["topic_pass", "lane", "non_knowledge", "reason"],
    "additionalProperties": False,
}

# 单次判定守卫错误（sc-p3-second-call-mutation）：同篇第 2 次 admission LLM
# 调用被源码级守卫拦截。继承 RuntimeError 使既有 except (RouterConfigError,
# RuntimeError) 分支统一 fail-closed。
class SecondAdmissionCallError(RuntimeError):
    """同篇 admission 的第 2 次 LLM 调用被单次判定守卫拦截（sc-p3-second-call-mutation）。"""


def _make_single_call_guard(route_llm_fn: Any) -> Any:
    """包装 route_llm_fn：仅放行第一次调用，第二次起抛 SecondAdmissionCallError。

    sc-p3-second-call-mutation 的源码级守卫——若未来有人把第二次 admission LLM
    调用加回，守卫在调用点抛错 → 统一 fail-closed 拒收路径，计数/通过测试双红。
    不依赖外部计数器；预算强制（MAX_CALLS_PER_ARTICLE）归 llm_router 侧
    sc-p3-max-calls-enforced，本守卫只管 admission 单篇内调用次数。
    """
    state = {"called": False}

    def _guarded(model_family: str, messages: list[dict], **kwargs: Any):
        if state["called"]:
            raise SecondAdmissionCallError(
                "admission 单次判定守卫: 同篇第 2 次 LLM 调用被拦截"
                "（sc-p3-single-llm-call；预算强制见 llm_router MAX_CALLS_PER_ARTICLE）"
            )
        state["called"] = True
        return route_llm_fn(model_family, messages, **kwargs)

    _guarded.wrapped = route_llm_fn  # type: ignore[attr-defined]
    return _guarded

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
    # [F39 扩锚 2026-07-11,用户授权"直接修正"] LLM 内部机制类知识纳入库 scope:
    # F38 域内批次 6/25 死于 topic_relevance,死者全是 LLM 架构/可解释性/RAG 深文
    # (Raschka/EleutherAI/dbreunig)。扩的是收录学科范围,机制/证据/新颖度标准零动。
    # 与 skillpack/quality/concept-index-injection.md 保持两端同步。
    "LLM Architecture & Training",
    "LLM Inference & Interpretability",
    "RAG & Retrieval Engineering",
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

    passed: 全部硬门通过。
    checks: 逐项判定详情。顺序恒为 topic_relevance / how_tendency / novelty，
        concept 车道追加 mechanism_signal（sc-p3-lane-scoped-gates：mechanism
        门仅 concept 产出；tutorial/pattern/design 不产 mechanism_signal 条目）。
    reason: 整体拒收原因（passed=True 时为空字符串）。
    content_lane: 内容车道（concept / tutorial / pattern / design）。当 passed=True
        时为合法终值之一；passed=False 时可能为空字符串（分类器不可用/拒收场景）。
        lane 不进 checks 列表——它是路由信号而非准入判定门。
    """

    passed: bool
    checks: list[AdmissionCheck] = field(default_factory=list)
    reason: str = ""
    content_lane: str = ""
    # lane_reason：单次结构化判定给出的车道理由（与 content_lane 配对）。
    # 全路径透出——pass 路径也可见（不止 reject），供 runner 落 ledger 做
    # P1 语义审计的逐篇可读 trace（spec FR-008 / 宪法 P4 失败可见）。仅观测字段，
    # 不参与判定（lane 判定仍由 content_lane/lane_failed/lane_rejected 驱动）。
    lane_reason: str = ""
    # P3 重构：D11 lane reject 二次确认已删除（holds「LANE_REJECT 仍直接拒收」）。
    # 字段保留恒默认 False/空——dataclass 消费方（runner/ledger/calibration）
    # 契约不破，后续波次统一清理。
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
    """执行准入判定（单次结构化 LLM 判定 + 确定性预筛）。

    Args:
        raw_path: raw 文件路径（用于落账锚定）。
        raw_content: raw 文件全文内容。
        ledger_path: 循环账本 jsonl 路径。
        wiki_references: 既有 wiki 页面摘要列表（用于确定性新颖度预筛），
            None 时新颖度默认 pass。
        route_llm_fn: LLM 路由函数注入（fixture 测试用），
            默认使用 src/akb2.quality.llm_router.route_llm。

    Returns:
        AdmissionResult: 整体判定结果。

    LLM 调用预算（sc-p3-single-llm-call）：
        - novelty exact 重复 → 0 次（确定性拒收，无需 LLM）
        - 其余 → 恰好 1 次单次结构化判定（_SingleCallGuard 强制，
          第二次调用抛 SecondAdmissionCallError → fail-closed）
    """
    if route_llm_fn is None:
        from src.akb2.quality.llm_router import route_llm as _default

        route_llm_fn = _default
    guarded_llm = _make_single_call_guard(route_llm_fn)

    # 剥离 raw 样板（frontmatter + §锚点目录 TOC），让判定看正文而非元数据骨架。
    # 此前直接判 raw_content：4000 字窗口被 ~960 字 frontmatter+26 行锚点目录稀释，
    # how-to 实质被挤出视野 → gemini 判"introductory/conceptual" 误拒（含黄金样张）。
    # 实测：同篇去样板后 how_tendency 由 FAIL→PASS。
    body = _strip_raw_boilerplate(raw_content)

    # 1. 新颖度（纯确定性，sc-p3-single-llm-call）：exact slug/url 匹配 → 洗稿拒收；
    #    lexical top-k 预筛仅作 reason 记录；无近邻默认 pass（低置信）。不打 LLM。
    #    传原始 raw_content（非剥离 body）——exact 匹配依赖 frontmatter 的
    #    source_slug/source_url（_raw_novelty_meta）。
    novelty_check = _check_novelty(raw_content, wiki_references)
    checks: list[AdmissionCheck] = [novelty_check]

    # 2. exact 重复已确定性拒收 → 不发起 LLM（0 次调用路径）。
    if not novelty_check.passed:
        reason = f"准入未通过: novelty({novelty_check.reason})"
        lane_reason = "novelty exact duplicate，未发起 LLM 判定"
        _log_rejection(
            raw_path, checks, ledger_path,
            content_lane="", lane_failed=False, lane_reason=lane_reason,
        )
        return AdmissionResult(
            passed=False, checks=checks, reason=reason,
            content_lane="", lane_reason=lane_reason,
        )

    # 3. 单次结构化判定（topic + lane + non_knowledge + mechanism + how 一票出）。
    #    强 procedural 信号短路车道（tutorial），但仍发起这一次调用判 topic 等其余项。
    unified = _run_unified_admission(body, guarded_llm)
    topic_check = AdmissionCheck(
        name="topic_relevance",
        passed=unified["topic_pass"],
        reason=unified["topic_reason"],
    )
    how_check = AdmissionCheck(
        name="how_tendency", passed=unified["how_pass"], reason=unified["how_reason"]
    )
    # checks 顺序契约（runner/f54 探测依赖）：topic → how → novelty；机制门仅 concept 追加。
    checks = [topic_check, how_check, novelty_check]

    content_lane: str = unified["lane"]
    lane_failed: bool = unified["lane_failed"]
    lane_reason: str = unified["lane_reason"]

    # P0-c：非知识一票拒（lane=reject 或 non_knowledge=true），直接拒收不降级
    # （holds「LANE_REJECT 仍直接拒收」；D11 已删）。
    lane_rejected = (not lane_failed) and (
        content_lane == LANE_REJECT or unified["non_knowledge"]
    )
    if unified["non_knowledge"] and content_lane != LANE_REJECT:
        lane_rejected = True
        lane_reason = f"non_knowledge 一票拒（LLM 判非知识）: {lane_reason}"

    # 4. 机制信号门（sc-p3-lane-scoped-gates）：仅 concept 车道。
    #    确定性强反信号（全信号 0 且正文短）LLM 不可绕过（fail-closed 方向不变，
    #    F13-fix3）；其余由单次判定的 mechanism_pass 裁决。
    #    lane_failed（判定输出非法/调用失败）时同样产出一条 fail 的
    #    mechanism_signal——「一切不可知 → 全判定 fail」与 f54 的四判定
    #    fail-closed 形态同构，且保证营销文误判路径的机制否决观测在场。
    if (content_lane == LANE_CONCEPT or lane_failed) and not lane_rejected:
        checks.append(_mechanism_check_from_unified(body, unified))

    # how_tendency 车道作用域（sc-p3-lane-scoped-gates）：仅 tutorial 车道拒收门；
    # concept/pattern/design 下 how fail 不拒（纯概念文天然无 how；pattern/design
    # 缺 ≥3 步不得因此 abort）。分类失败/reject 时按最严判不豁免。
    def _check_counts_for_admission(c: AdmissionCheck) -> bool:
        if c.name == "how_tendency" and content_lane != LANE_TUTORIAL and not lane_failed:
            return True  # 非 tutorial 车道：how_tendency 不作拒收门
        return c.passed

    all_checks_pass = all(_check_counts_for_admission(c) for c in checks)
    # 车道分类失败（fail-closed）或非知识 reject（P0-c）→ 拒收
    all_passed = all_checks_pass and not lane_failed and not lane_rejected

    reason = ""
    if not all_passed:
        # mechanism_signal 失败映射 no_mechanism_signal（runner 据此设 error_class）
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


def _run_unified_admission(body: str, route_llm_fn: Any) -> dict[str, Any]:
    """单次结构化判定（sc-p3-single-llm-call）：topic+lane+non_knowledge 一票出。

    一次 route_llm 调用同时裁决：
        - topic_pass：领域 taxonomy 锚点命中（全车道硬门）
        - lane：concept / tutorial（procedural 别名归一） / pattern / design / reject
        - non_knowledge：非知识一票拒
        - mechanism_pass：机制信号存在性（仅 concept 车道消费）
        - how_pass：可执行 how-to 知识（仅 tutorial 车道消费）

    fail-closed（不静默归车道）：
        - LLM 路由不可用（RouterConfigError/RuntimeError/SecondAdmissionCallError）
        - 输出非法（schema 校验失败 / 缺 lane）
        → 全子判定 fail + lane_failed=True（run_admission 拒收并落账）。

    prompt 措辞契约（外围 mock 识别词，改动前先核对 tests/quality/conftest.py
    make_mock_route_llm 与 test_f13_lead_fixes）：
        - 含「主要意图」：conftest mock 的 lane 分支识别词
        - 含「机制」：f13 mock 的 mechanism 分支识别词
    non_knowledge 收窄（f1-nonknowledge-narrow）：产品/模型发布稿若含 ≥3 可命名
    机制组件或可执行配置/代码 → non_knowledge=false，按 concept/tutorial 放行；
    纯新闻/融资/会议 recap/无机制营销仍一票拒。
    """
    from src.akb2.quality.llm_router import RouterConfigError

    def _fail_closed(exc: BaseException | None = None) -> dict[str, Any]:
        detail = f": {exc}" if exc else ""
        return {
            "topic_pass": False,
            "topic_reason": f"判定调用失败{detail}",
            "lane": "",
            "lane_failed": True,
            "lane_reason": f"单次判定调用失败{detail}",
            "non_knowledge": False,
            "mechanism_pass": False,
            "how_pass": False,
            "how_reason": f"判定调用失败{detail}",
            "unified_reason": f"判定调用失败{detail}",
        }

    # 强 procedural 信号短路车道（不问 LLM lane，但仍发起本调用判其余项）。
    deterministic_tutorial = _has_strong_procedural_signal(body)
    lane_hint = (
        "车道已由确定性强 procedural 信号判定为 tutorial（教程/操作型），"
        "lane 字段请固定输出 tutorial，其余维度照常判定。"
        if deterministic_tutorial
        else ""
    )

    taxonomy_str = "\n".join(f"- {t}" for t in _DEFAULT_DOMAIN_TAXONOMY)
    content_snippet = _build_mechanism_snippet(body)

    messages = [
        {
            "role": "system",
            "content": (
                "你一次性对内容做准入结构化判定，输出一个 JSON 对象：\n"
                "- topic_pass: 内容是否命中下方领域 taxonomy 中至少一个锚点。\n"
                "- lane: 按内容主要意图判车道——"
                "concept（阐述机制/架构/设计原则的概念型内容）；"
                "tutorial（教怎么做的操作型内容：步骤/命令/配置/工作流/实操记录，"
                "即便夹叙夹议也算）；"
                "pattern（提炼可复用模式/最佳实践/反模式集合）；"
                "design（设计呈现/设计决策/视觉与交互方案）；"
                "reject（纯新闻播报/融资动态/会议 recap/无机制营销软文/纯观点，"
                "既不讲机制也不教操作）。产品/模型发布稿若含 ≥3 个可命名机制组件"
                "或可执行配置/代码，按 concept 或 tutorial 放行，不得因「发布稿」体裁"
                "一律 reject。不得为二选一硬塞 concept。\n"
                "- non_knowledge: 是否属于非知识内容；true 则一票拒收。"
                "true 仅当是纯新闻/融资/会议 recap/无机制营销/纯观点。"
                "产品/模型发布稿若含 ≥3 个可命名机制组件或可执行配置/代码 → false，"
                "并按 concept/tutorial 车道放行；不得因体裁是发布稿一律 true。\n"
                "- mechanism_pass: 正文中是否存在可提炼的机制信号——即能否抽出 ≥3 个"
                "原文命名的机制组件（架构部件/流程阶段/算法步骤/协议层）及因果/交互证据。"
                "组件散布正文任意处即算；营销收益清单（faster/better 式因果词堆叠）不构成"
                "机制。纯政策/新闻/观点判 false。\n"
                "- how_pass: 是否包含可执行 how-to 知识（具体步骤/代码示例/命令/配置模板/"
                "工作流）。纯新闻、观点评论、无机制营销不算；发布稿若含可执行配置/代码"
                "可判 true。\n"
                "- reason: 判定理由，最多 150 字符。\n"
                "输出 JSON: {\"topic_pass\": ..., \"lane\": \"concept|tutorial|pattern|"
                "design|reject\", \"non_knowledge\": ..., \"mechanism_pass\": ..., "
                "\"how_pass\": ..., \"reason\": ...}。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"领域 taxonomy:\n{taxonomy_str}\n\n{lane_hint}\n内容:\n{content_snippet}"
            ),
        },
    ]

    try:
        response = route_llm_fn(
            ADMISSION_MODEL,
            messages,
            response_schema=_ADMISSION_UNIFIED_SCHEMA,
        )
    except (RouterConfigError, RuntimeError) as exc:
        return _fail_closed(exc)

    parsed = _validate_unified(response.content)
    if parsed is None:
        return {
            "topic_pass": False,
            "topic_reason": "判定 schema 校验失败: 非法输出格式",
            "lane": "",
            "lane_failed": True,
            "lane_reason": "单次判定输出非法（缺合法 lane 枚举值）",
            "non_knowledge": False,
            "mechanism_pass": False,
            "how_pass": False,
            "how_reason": "判定 schema 校验失败: 非法输出格式",
            "unified_reason": "判定 schema 校验失败: 非法输出格式",
        }

    # procedural 别名归一（sc-p3-lane-enum）：LLM 回旧值 → tutorial 终值。
    lane = parsed.get("lane", "")
    if lane == LANE_PROCEDURAL:
        lane = LANE_TUTORIAL
    # 确定性短路盖过 LLM 车道（强 procedural 形态无歧义）。
    if deterministic_tutorial:
        lane = LANE_TUTORIAL

    reason = str(parsed.get("reason", ""))[:150]
    return {
        "topic_pass": bool(parsed.get("topic_pass", False)),
        "topic_reason": reason or "topic_pass",
        "lane": lane,
        "lane_failed": False,
        "lane_reason": reason or "单次结构化判定",
        "non_knowledge": bool(parsed.get("non_knowledge", False)),
        # mechanism/how 非必填：缺省 False（fail-closed 方向——该门只在对应车道消费）。
        "mechanism_pass": bool(parsed.get("mechanism_pass", False)),
        "how_pass": bool(parsed.get("how_pass", False)),
        "how_reason": reason or "how_pass",
        "unified_reason": reason,
    }


def _validate_unified(content: str | dict) -> dict[str, Any] | None:
    """校验单次判定输出；非法返回 None（调用方 fail-closed）。

    兼容旧车道形状：``content_lane`` 字段名作为 ``lane`` 别名接受（tests/quality/
    conftest.make_mock_route_llm 的 "lane" key 响应仍是旧形状；admission 侧输出
    契约以 unified schema 的 ``lane`` 为准）。reject 一票语义在别名下同样成立。
    topic_pass/non_knowledge 仅在 lane 为通过态时必填——reject 由车道一票先决，
    不因缺 topic 字段而改变拒收结论。
    无 jsonschema 时退化为字段存在性校验，仍不放宽枚举。
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
    if not isinstance(parsed, dict):
        return None
    lane = parsed.get("lane")
    if lane is None:
        lane = parsed.get("content_lane")
    if lane == LANE_PROCEDURAL:
        lane = LANE_TUTORIAL  # 别名归一前置（与 _run_unified_admission 一致）
    if lane == LANE_REJECT:
        return {**parsed, "lane": LANE_REJECT}
    if lane not in (LANE_CONCEPT, LANE_TUTORIAL, LANE_PATTERN, LANE_DESIGN):
        return None
    if "topic_pass" not in parsed or "non_knowledge" not in parsed:
        return None
    return {**parsed, "lane": lane}


def _mechanism_check_from_unified(
    body: str, unified: dict[str, Any]
) -> AdmissionCheck:
    """从单次判定结果构造 mechanism_signal check（仅 concept 车道调用）。

    F13-fix3 语义保留：确定性强反信号（全信号 0 且正文短）→ no_mechanism_signal，
    LLM 的 mechanism_pass 不可绕过；其余由单次判定 mechanism_pass 裁决（LLM 仍是
    唯一放行人，信号计数已在判定 prompt 的内容窗口内可见）。
    """
    signals = _scan_mechanism_signals(body)
    total = (
        signals["components"] + signals["weak_components"] + signals["causal"]
        + signals["numbered_steps"] + signals["code_blocks"] + signals["source_operations"]
    )
    body_length = len(body or "")
    if total == 0 and body_length < MECHANISM_LONG_PROSE_THRESHOLD:
        return AdmissionCheck(
            name="mechanism_signal", passed=False,
            reason="no_mechanism_signal: 无命名组件/因果边/编号步/code/source_operation 且正文短",
        )
    if unified.get("lane_failed"):
        return AdmissionCheck(
            name="mechanism_signal", passed=False,
            reason=(
                f"no_mechanism_signal: 单次判定不可用 fail-closed; "
                f"{unified.get('unified_reason', '')}"
            ),
        )
    if unified["mechanism_pass"]:
        return AdmissionCheck(
            name="mechanism_signal", passed=True,
            reason=f"mechanism_signal: 单次判定 mechanism_pass; {unified['unified_reason']}",
        )
    return AdmissionCheck(
        name="mechanism_signal", passed=False,
        reason=f"no_mechanism_signal: 单次判定机制信号不存在; {unified['unified_reason']}",
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
    route_llm_fn: Any = None,
) -> AdmissionCheck:
    """§0.2/D10/G1 + P3 重构：新颖度判定——纯确定性 top-k 预筛，不打 LLM。

    - 无既有 wiki 引用 → 默认 pass。
    - exact slug/source_url 匹配（duplicate_same_claim）→ fail（确定性洗稿拒收，
      唯一确定性可判的重复形态）。
    - lexical/topic top-k 近邻（unrelated_baseline）→ pass；reason 记比较对象
      （相关≠重复；改写洗稿的进一步识别交下游证据诚实度，不在准入段打 LLM）。
    - 无相关近邻 → pass（no_relevant_baseline，低置信）。

    route_llm_fn 形参保留仅为签名兼容（历史调用方/测试注入），本函数不再使用。
    """
    references = _normalize_wiki_references(wiki_references)
    if not references:
        return AdmissionCheck(
            name="novelty",
            passed=True,
            reason="无既有 wiki 引用，默认通过",
        )

    neighbors, match_type = _select_novelty_neighbors(raw_content, references)
    if not neighbors:
        return AdmissionCheck(
            name="novelty",
            passed=True,
            reason="no_relevant_baseline: 无相关既有 wiki 近邻，默认增量通过（低置信）",
        )

    compare_targets = ", ".join(
        n.get("slug") or n.get("path") or "?" for n in neighbors
    )
    if match_type == "duplicate_same_claim":
        return AdmissionCheck(
            name="novelty",
            passed=False,
            reason=(
                f"[duplicate_same_claim, compare={compare_targets}] "
                f"确定性拒收: 与既有条目同源同主张（exact slug/url 匹配）"
            ),
        )
    return AdmissionCheck(
        name="novelty",
        passed=True,
        reason=(
            f"[unrelated_baseline, compare={compare_targets}] "
            f"确定性预筛: 相关但非同源，默认增量（top-k 近邻仅记录）"
        ),
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
# F14-B: mechanism 仲裁双次取或——首次 fail 再仲裁一次,任一 pass 即 pass(共 2 票)。
# LLM 仍是本门唯一放行人,这是有界方差对冲(同 retry 哲学);强反信号(全0+短正文)
# 已在前 deterministic fail,不可绕过。营销安全由证据 prompt(fix4 标题原文自证)+
# lane/novelty/强反信号多层兜底。
MECHANISM_ARBITRATION_MAX_VOTES: int = 2
# F13-fix3（reviewer 三轮 blocking 定论）：词面/结构计数在确定性层面分不开"机制文"与
# "营销文"（dense 密度 / weak_comp 结构 / `## 1. X` 编号标题伪装 comp>=3——三轮反例
# 全部实证可伪造）。故 mechanism 门 **无任何确定性 PASS 分支**;全部信号计数降级为
# 仲裁证据注入 mechanism LLM prompt,LLM 是唯一放行人;确定性只保留强反信号 FAIL
# (fail-closed 方向不变)。idx8 lilian 类推理机制文靠证据增强争取,不开确定性后门。

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


# F13-fix4（e2e v4 实证）：仲裁证据样本上限（具名,宪法 P4）——只给计数时 deepseek
# 仲裁系统性把"参考文档式真机制文"(memory/crewai/skills)误判为无机制(v4 admission
# 误拒 9 条,重试不救=系统性)。把实际组件名/因果句样本注入 prompt:真机制文亮出
# "Captain/Executor Pool/hooks lifecycle",营销文只能亮出"Faster onboarding"——
# 证据升级,门语义不变(LLM 仍是唯一放行人)。
_MECHANISM_EVIDENCE_HEADINGS_MAX: int = 10
_MECHANISM_EVIDENCE_CAUSAL_MAX: int = 3
_MECHANISM_EVIDENCE_SENT_CHARS: int = 160


def _collect_mechanism_evidence(body: str) -> str:
    """收集机制仲裁的文本证据(标题原文 + 因果句样本),供 LLM 仲裁 prompt 使用。

    纯确定性抽样,不判定;空 body 返回空串。
    """
    if not body:
        return ""
    lines: list[str] = []
    headings: list[str] = []
    for m in re.finditer(r"(?m)^#{2,4}\s+(.+)$", body):
        t = m.group(1).strip()
        if t and t not in headings:
            headings.append(t)
        if len(headings) >= _MECHANISM_EVIDENCE_HEADINGS_MAX:
            break
    if headings:
        lines.append("小节标题原文: " + " | ".join(headings))
    causal_sents: list[str] = []
    for sent in re.split(r"(?<=[.!?。])\s+", body):
        if len(causal_sents) >= _MECHANISM_EVIDENCE_CAUSAL_MAX:
            break
        if _CAUSAL_CONNECTOR_RE.search(sent):
            s = " ".join(sent.split())[:_MECHANISM_EVIDENCE_SENT_CHARS]
            if s:
                causal_sents.append(s)
    if causal_sents:
        lines.append("因果句样本: " + " || ".join(causal_sents))
    return "\n".join(lines)


def _check_mechanism_signal(body: str, route_llm_fn: Any) -> AdmissionCheck:
    """WP-2/D2：concept 候选机制信号准入门（**遗留独立仲裁实现**）。

    P3 重构后主流程（run_admission）不再调用本函数——机制判定已并入
    ``_run_unified_admission`` 单次判定 + ``_mechanism_check_from_unified``
    确定性强反信号短路。函数保留是因为 test_f14b / test_f25 直调本函数锚定
    F13-fix3/F14-B 仲裁语义（admission.py 外的测试文件不在本组 allowed_paths，
    由 rebuild 后续波次统一清理）。

    F13-fix3（reviewer 三轮 blocking 定论）：**无任何确定性 PASS 分支**——
    `## 1. Faster onboarding` 类编号标题会被 `_MECHANISM_COMPONENT_RE` 计成"命名
    组件",词面/结构计数在确定性层面分不开机制文与营销文（三轮反例实证:dense 密度 /
    weak_comp 结构 / comp>=3 编号标题全部可被营销文伪造）。故:

    - 确定性 **强反信号 FAIL** 保留：全信号 = 0 且正文短（纯短散文/新闻 recap）
      → ``no_mechanism_signal``，**LLM 不得绕过**（fail-closed 方向不变）。
    - 其余一律 **LLM 仲裁**：mechanism LLM 是本门唯一放行人;确定性信号计数注入
      仲裁 prompt 作证据（密度/组件数是证据不是通行证）。LLM 不可用 → fail-closed。

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
    # F13-fix4：文本证据样本(标题原文/因果句)——只给计数会把参考文档式真机制文
    # 误判(v4 实证);给出实际名称让 LLM 分辨"Captain/Executor Pool"vs"Faster onboarding"。
    evidence_block = _collect_mechanism_evidence(body)
    messages = [
        {
            "role": "system",
            "content": (
                "你判断正文中是否**存在可提炼的机制信号**——即下游提炼器能否从正文抽出"
                "≥3 个原文命名的机制组件及其因果/交互证据句。机制组件 = 原文显式命名的"
                "架构部件/流程阶段/算法步骤/协议层（如 Brain/Hands/Session、"
                "planner/executor/joiner）。\n"
                "[F25 职责校准] 你判的是「信号是否存在」，不是「文章是否以机制文体自足展开」：\n"
                "- 组件可散布在正文任意处（常藏在段落句子里，如「X 由 A/B/C 三部分组成："
                "A 是…，B 是…」）——存在即算，不要求集中成章；\n"
                "- 综述/对比/多系统盘点文，若各系统的组件在文中被命名且交互被描述，"
                "同样判 true——不得以「文体是综述/观点散文」为由判 false；\n"
                "- 结构化转写（表格/锚点/机制展开）是提炼器的工作，不是原文的义务。\n"
                "推理/验证/自我修正等认知过程机制（如 CoT、self-consistency、verification）"
                "也属于机制，只要文中给出组件化的过程与因果关系；"
                "工具/系统的组件化参考文档（配置项、生命周期、hooks、权限模型、子系统交互）"
                "同样属于机制——判据是组件是否被命名且相互作用可追溯，不是文体。\n"
                "否决线不变：纯政策/新闻/观点文（全文找不到命名组件+交互）判 false；"
                "信号计数高不等于机制——营销/发布文的收益清单（faster/better 因果词堆叠、"
                "benefit 式小节标题）不构成机制因果，应判 false。"
                "输出 JSON: {\"pass\": true/false, \"reason\": \"...\"}。reason 最多 150 字符。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"{signal_line}\n{evidence_block}\n\n内容:\n{content_snippet}"
                if evidence_block
                else f"{signal_line}\n\n内容:\n{content_snippet}"
            ),
        },
    ]
    # F14-B: 双次取或——首次 fail 再仲裁一次(MECHANISM_ARBITRATION_MAX_VOTES=2),
    # 任一 pass 即 pass;两次 fail 才 fail。LLM 仍唯一放行人,有界方差对冲。首次 pass
    # 不发第二次(省调用)。强反信号(全0+短正文)已在前 deterministic fail,不可绕过。
    votes: list[bool] = []
    vote_reasons: list[str] = []
    for _vote_i in range(MECHANISM_ARBITRATION_MAX_VOTES):
        try:
            response = route_llm_fn(
                ADMISSION_MODEL, messages, response_schema=_ADMISSION_CHECK_SCHEMA,
            )
        except (RouterConfigError, RuntimeError) as exc:
            # 信号识别不可用 → fail-closed(配置/超时错误不重试,直接拒)
            return AdmissionCheck(
                name="mechanism_signal", passed=False,
                reason=f"no_mechanism_signal: 机制信号识别不可用 fail-closed: {exc}",
            )
        result = _validate_and_normalize(response.content, "mechanism_signal")
        votes.append(bool(result["pass"]))
        vote_reasons.append(str(result["reason"])[:80])
        if result["pass"]:
            break  # 任一 pass 即 pass,省第二次调用
    passed = any(votes)
    vote_summary = "/".join(
        f"v{i+1}={'pass' if v else 'fail'}" for i, v in enumerate(votes)
    )
    if passed:
        first_pass = votes.index(True)
        base_reason = vote_reasons[first_pass]
        return AdmissionCheck(
            name="mechanism_signal", passed=True,
            reason=(
                f"mechanism_signal: 仲裁 {vote_summary}({len(votes)}票,任一pass即pass);"
                f" {base_reason}"
            ),
        )
    return AdmissionCheck(
        name="mechanism_signal", passed=False,
        reason=(
            f"no_mechanism_signal: 仲裁 {vote_summary}({len(votes)}票全fail);"
            f" {vote_reasons[-1]}"
        ),
    )
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
