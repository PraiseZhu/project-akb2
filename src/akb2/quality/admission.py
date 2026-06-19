"""准入三硬判定 — 二值无连续分数（宪法 P3/P8）。

三个硬判定，每项输出 {pass: bool, reason: str} 经 jsonschema 校验：
    1. 主题相关性 — 领域 taxonomy 锚点命中
    2. How 倾向 — 可执行知识占比
    3. 新颖度 — 对既有 wiki 增量

任一 fail → 整体拒收并落账（调用 step-1 ledger.append_event）。
全部 pass → 返回 AdmissionResult(passed=True) 进入提炼段。

公开接口:
    run_admission(raw_path, raw_content, ledger_path, wiki_references=...) -> AdmissionResult
    AdmissionResult = dataclass(passed, checks, reason)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# 具名常量（宪法 P4）
# ---------------------------------------------------------------------------

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

# 内容截断长度：送入 LLM 判定的最大字符数
# 主题相关性和 How 倾向取 4000 字足以覆盖导语+前几段核心内容
# 新颖度判定需更完整的论点和论证结构，取 3000 字聚焦核心主张
MAX_ADMISSION_CONTENT_CHARS: int = 4000
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
    checks: 逐项判定详情。
    reason: 整体拒收原因（passed=True 时为空字符串）。
    """

    passed: bool
    checks: list[AdmissionCheck] = field(default_factory=list)
    reason: str = ""


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

    all_passed = all(c.passed for c in checks)
    reason = ""
    if not all_passed:
        failed_checks = [c.name for c in checks if not c.passed]
        reason = f"准入未通过: {', '.join(failed_checks)}"

    # 落账（任一 fail 都落账）
    if not all_passed:
        _log_rejection(raw_path, checks, ledger_path)

    return AdmissionResult(passed=all_passed, checks=checks, reason=reason)


# ---------------------------------------------------------------------------
# raw 样板剥离
# ---------------------------------------------------------------------------

# 正文起始锚点：raw_archiver 写入 §A_CONTENT_START（或 §X_CONTENT_START）标记正文开端
_CONTENT_START_RE = re.compile(r"§[A-Z]_CONTENT_START")


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
            "gemini-flash",
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

    content_snippet = raw_content[:MAX_ADMISSION_CONTENT_CHARS]

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
            "gemini-flash",
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
            "gemini-flash",
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
) -> None:
    """落账拒收事件。

    调用 step-1 ledger.append_event 写入 jsonl 账本。
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
            "failed_checks": {
                k: v for k, v in failure_details.items() if not v["passed"]
            },
            "all_checks": failure_details,
        },
    }

    append_event(ledger_path, event)
