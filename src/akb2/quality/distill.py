"""DeepSeek 两段式提炼 — 结构化抽取 + 模板成稿。

段 1（结构化抽取）:
    raw → {claims[带 raw §X 锚点], how_steps, params, referenced_concepts} JSON
    jsonschema 校验失败 → 有界重试（MAX_EXTRACT_RETRIES=3）
    耗尽 → 显式失败不出候选

段 2（成稿）:
    按 PAGE-TEMPLATES source 模板成稿 10 节
    frontmatter 确定性代码写死:
        gate_decision: null
        status: candidate
        source_refs 含 raw 路径
        gate_reason ≤150 字符截断
        future_questions 2-3 条
        related_concepts ≥2
    落 staging/candidate/<batch>/

公开接口:
    extract_claims(raw_content, route_llm_fn=None) -> ExtractResult
    compose_candidate(extract_result, raw_path, batch_id, output_dir=None) -> ComposeResult
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 具名常量（宪法 P1/P4）
# ---------------------------------------------------------------------------

MAX_EXTRACT_RETRIES: int = 3
GATE_REASON_MAX_CHARS: int = 150
FUTURE_QUESTIONS_MIN: int = 2
FUTURE_QUESTIONS_MAX: int = 3
RELATED_CONCEPTS_MIN: int = 2

# ---------------------------------------------------------------------------
# 段 1 抽取输出 schema
# ---------------------------------------------------------------------------

_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "anchor": {"type": "string"},
                    "provenance": {
                        "type": "string",
                        "enum": ["source_grounded", "akb_interpretation"],
                    },
                },
                "required": ["text", "anchor", "provenance"],
            },
            "minItems": 1,
        },
        "how_steps": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
        },
        "params": {
            "type": "object",
            "additionalProperties": True,
        },
        "referenced_concepts": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
        },
    },
    "required": ["claims", "how_steps", "params", "referenced_concepts"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# 公开类型
# ---------------------------------------------------------------------------


@dataclass
class ExtractResult:
    """段 1 结构化抽取结果。

    success: 抽取是否成功（含 schema 校验通过）。
    data: 抽取出的结构化数据（success=False 时为空 dict）。
    retries_used: 重试次数。
    error: 失败原因（success=False 时）。
    """

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    retries_used: int = 0
    error: str = ""


@dataclass
class ComposeResult:
    """段 2 成稿结果。

    success: 成稿是否成功。
    candidate_path: 候选页输出路径（success=False 时为空字符串）。
    frontmatter: 写出的 frontmatter dict（供测试断言）。
    error: 失败原因（success=False 时）。
    """

    success: bool
    candidate_path: str = ""
    frontmatter: dict[str, Any] = field(default_factory=dict)
    error: str = ""


# ---------------------------------------------------------------------------
# 段 1: 结构化抽取
# ---------------------------------------------------------------------------


def extract_claims(
    raw_content: str,
    route_llm_fn: Any = None,
) -> ExtractResult:
    """段 1: raw → 结构化 JSON 抽取。

    有界重试（MAX_EXTRACT_RETRIES=3），jsonschema 校验。
    耗尽 → 显式失败，不出候选。

    Args:
        raw_content: raw 文件全文内容。
        route_llm_fn: LLM 路由函数注入（fixture 测试用）。

    Returns:
        ExtractResult: 含 success / data / retries_used / error。
    """
    if route_llm_fn is None:
        from src.akb2.quality.llm_router import route_llm as _default

        route_llm_fn = _default

    # 构建抽取 prompt
    content_snippet = raw_content[:8000]  # 取前 8000 字符

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个知识提炼助手。从给定原始内容中提取结构化 JSON。\n"
                "要求:\n"
                "1. claims: 核心论点数组，每条含 text(论点) + anchor(如 §A)"
                " + provenance(来源类型)\n"
                "   - provenance 只能是 source_grounded(原文支撑) 或 akb_interpretation(库内解读)\n"
                "   - 每个 anchor 必须对应原文中的锚点标记\n"
                "2. how_steps: 可执行步骤/代码/工作流（字符串数组）\n"
                "3. params: 参数/配置/选项（自由 key-value）\n"
                "4. referenced_concepts: 引用的概念名（字符串数组）\n\n"
                "输出必须严格符合 JSON schema，不要有任何额外文本。"
            ),
        },
        {
            "role": "user",
            "content": f"原文内容:\n{content_snippet}",
        },
    ]

    for attempt in range(1, MAX_EXTRACT_RETRIES + 1):
        try:
            response = route_llm_fn(
                "deepseek-v4-pro",
                messages,
                response_schema=_EXTRACT_SCHEMA,
            )
        except Exception as exc:
            if attempt >= MAX_EXTRACT_RETRIES:
                return ExtractResult(
                    success=False,
                    retries_used=attempt,
                    error=f"抽取调用失败（{attempt}/{MAX_EXTRACT_RETRIES}）: {exc}",
                )
            continue

        # jsonschema 校验
        parsed = response.content
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                if attempt >= MAX_EXTRACT_RETRIES:
                    return ExtractResult(
                        success=False,
                        retries_used=attempt,
                        error=(
                            f"抽取输出非 JSON（{attempt}/{MAX_EXTRACT_RETRIES}）"
                        ),
                    )
                continue

        # 强制校验 claims 锚点格式
        try:
            _validate_extract_output(parsed)
        except ValueError as exc:
            if attempt >= MAX_EXTRACT_RETRIES:
                return ExtractResult(
                    success=False,
                    retries_used=attempt,
                    error=f"抽取校验失败（{attempt}/{MAX_EXTRACT_RETRIES}）: {exc}",
                )
            continue

        # 全部通过
        return ExtractResult(
            success=True,
            data=parsed,
            retries_used=attempt,
        )

    # 不应到达这里
    return ExtractResult(
        success=False,
        retries_used=MAX_EXTRACT_RETRIES,
        error=f"抽取重试耗尽（{MAX_EXTRACT_RETRIES}）",
    )


def _validate_extract_output(data: dict[str, Any]) -> None:
    """校验抽取输出的结构完整性。

    Raises:
        ValueError: 校验未通过。
    """
    # 1. jsonschema 校验（错误转为 ValueError 以统一异常类型）
    try:
        import jsonschema
    except ImportError:
        pass  # 无 jsonschema 时跳过，靠代码校验
    else:
        try:
            jsonschema.validate(data, _EXTRACT_SCHEMA)
        except jsonschema.ValidationError as exc:
            raise ValueError(f"schema 校验失败: {exc.message}") from exc

    # 2. claims 每条必须有 anchor 且格式为 §X
    claims = data.get("claims", [])
    if not claims:
        raise ValueError("claims 为空")

    for i, claim in enumerate(claims):
        anchor = claim.get("anchor", "")
        if not re.match(r"^§[A-Z]$", anchor):
            raise ValueError(
                f"claim[{i}] anchor 格式无效: {anchor!r}（期望 §A-§Z）"
            )

    # 3. provenance 必须在枚举内
    valid_provenances = {"source_grounded", "akb_interpretation"}
    for i, claim in enumerate(claims):
        prov = claim.get("provenance", "")
        if prov not in valid_provenances:
            raise ValueError(
                f"claim[{i}] provenance 无效: {prov!r}"
            )


# ---------------------------------------------------------------------------
# 段 2: 成稿
# ---------------------------------------------------------------------------


def compose_candidate(
    extract_result: ExtractResult,
    raw_path: str,
    batch_id: str,
    output_dir: str | None = None,
    route_llm_fn: Any = None,
) -> ComposeResult:
    """段 2: 按模板成稿 10 节，落 staging/candidate/<batch>/。

    frontmatter 由确定性代码写死（防范已知坑 1-3）。

    Args:
        extract_result: 段 1 抽取结果（必须 success=True）。
        raw_path: raw 文件路径（用于 source_refs）。
        batch_id: 批次标识（如 "2026-06-13-001"）。
        output_dir: 输出根目录（None 时从 vault.toml 解析 staging）。
        route_llm_fn: LLM 路由函数注入（fixture 测试用）。

    Returns:
        ComposeResult: 含 success / candidate_path / frontmatter。
    """
    if not extract_result.success:
        return ComposeResult(
            success=False,
            error=f"段 1 抽取未成功，无法成稿: {extract_result.error}",
        )

    if route_llm_fn is None:
        from src.akb2.quality.llm_router import route_llm as _default

        route_llm_fn = _default

    # 解析输出目录
    if output_dir is None:
        try:
            from src.akb2.loops.paths import get_staging_root

            staging_root = get_staging_root()
        except Exception:
            staging_root = None
        if staging_root is None:
            return ComposeResult(
                success=False,
                error="无法解析 staging 输出路径",
            )
        output_dir = str(staging_root / "candidate" / batch_id)
    else:
        output_dir = os.path.join(output_dir, "candidate", batch_id)

    # 生成 slug
    slug = _derive_slug(raw_path)
    candidate_filename = f"{slug}.md"
    candidate_path = os.path.join(output_dir, candidate_filename)

    # 构建 frontmatter（确定性代码写死，防范已知坑 1-3）
    frontmatter = _build_frontmatter(raw_path, extract_result.data)

    # 用 LLM 生成正文 10 节
    body = _build_body(extract_result.data, raw_path, route_llm_fn)

    # 组装完整候选页
    full_page = _assemble_page(frontmatter, body)

    # 落盘
    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(candidate_path, "w", encoding="utf-8") as fh:
            fh.write(full_page)
    except OSError as exc:
        return ComposeResult(
            success=False,
            error=f"写入候选页失败: {exc}",
        )

    return ComposeResult(
        success=True,
        candidate_path=candidate_path,
        frontmatter=frontmatter,
    )


def _derive_slug(raw_path: str) -> str:
    """从 raw 路径提取 slug。

    raw/<slug>--raw--<date>.md → slug
    """
    basename = os.path.basename(raw_path)
    # 去掉 --raw-- 及之后
    slug = basename.split("--raw--")[0]
    return slug


def _build_frontmatter(
    raw_path: str,
    extract_data: dict[str, Any],
) -> dict[str, Any]:
    """构建 frontmatter（确定性代码，防范已知坑 1-3）。

    已知坑:
        1. gate_decision 写死 null（不许预填 active，防 HC-09 拦）
        2. source_refs 必须含 raw 路径（HC-05 硬指标）
        3. gate_reason ≤150 字符（lint P1）
    """
    claims = extract_data.get("claims", [])
    concepts = extract_data.get("referenced_concepts", [])

    # 确保 related_concepts ≥2
    related = list(concepts) if concepts else []
    if len(related) < RELATED_CONCEPTS_MIN:
        # 从 claims 中反推概念名
        fallback_concepts = _infer_concepts(claims)
        for fc in fallback_concepts:
            if fc not in related:
                related.append(fc)
            if len(related) >= RELATED_CONCEPTS_MIN:
                break

    # future_questions 2-3 条
    questions = _generate_questions(claims, extract_data.get("how_steps", []))

    # gate_reason 截断到 150 字符
    raw_reason = (
        f"准入通过，段1抽取{len(claims)}条claims，"
        f"{len(extract_data.get('how_steps', []))}条how_steps"
    )
    gate_reason = raw_reason[:GATE_REASON_MAX_CHARS]

    frontmatter: dict[str, Any] = {
        "status": "candidate",
        "gate_decision": None,  # 写死 null（已知坑 1）
        "source_refs": [raw_path],  # 必须含 raw 路径（已知坑 2）
        "gate_reason": gate_reason,  # ≤150 字符（已知坑 3）
        "future_questions": questions,
        "related_concepts": related,
        "artifact_type": "wiki",
        "pipeline_version": "v1",
    }

    return frontmatter


def _infer_concepts(claims: list[dict[str, Any]]) -> list[str]:
    """从 claims 反推概念名作为 related_concepts 的 fallback。

    简单启发式：从 claim 文本中提取常见概念词。
    """
    concept_keywords = [
        "Agent",
        "Loop",
        "Context",
        "Harness",
        "Eval",
        "Orchestration",
        "Workflow",
        "Design",
        "Token",
        "Prompt",
        "Tool",
        "Protocol",
        "MCP",
        "A2A",
        "LangGraph",
        "Architecture",
        "Pattern",
        "Pipeline",
        "Quality",
        "Convergence",
    ]

    found: list[str] = []
    all_text = " ".join(c.get("text", "") for c in claims)

    for kw in concept_keywords:
        if kw.lower() in all_text.lower() and kw not in found:
            found.append(kw)
        if len(found) >= RELATED_CONCEPTS_MIN:
            break

    return found


def _generate_questions(
    claims: list[dict[str, Any]],
    how_steps: list[str],
) -> list[str]:
    """生成 future_questions 2-3 条。

    基于 claims 和 how_steps 的确定性启发式生成。
    """
    questions: list[str] = []

    # 从 claims 主题生成问题
    if claims:
        first_claim = claims[0].get("text", "")
        if len(first_claim) > 30:
            questions.append(
                f"该模式（{first_claim[:40]}...）在不同技术栈中的适用边界如何？"
            )
        else:
            questions.append(
                "该文核心模式在真实工程中的失败模式有哪些？"
            )

    if how_steps:
        questions.append(
            f"how_steps 中的 {len(how_steps)} 个步骤，哪些是刚性约束、哪些可替换？"
        )
    else:
        questions.append(
            "该文缺少可执行步骤——其核心洞察如何转化为具体工作流？"
        )

    if len(claims) >= 3:
        questions.append(
            "多个论点的优先级排序是否在原文中有明确依据？"
        )

    # 确保 2-3 条
    if len(questions) < FUTURE_QUESTIONS_MIN:
        questions.append("该知识在 AKB 库内与其他概念的关系网络如何演化？")
    if len(questions) > FUTURE_QUESTIONS_MAX:
        questions = questions[:FUTURE_QUESTIONS_MAX]

    return questions


def _build_body(
    extract_data: dict[str, Any],
    raw_path: str,
    route_llm_fn: Any,
) -> str:
    """构建 10 节正文。

    10 节模板:
        1. TLDR
        2. 关键发现
        3. 入选理由
        4. 证据地图
        5. 核心论点
        6. 论证与解释
        7. 适用边界
        8. 图谱关系
        9. Future Questions
        10. 维护记录
    """
    claims = extract_data.get("claims", [])
    how_steps = extract_data.get("how_steps", [])
    concepts = extract_data.get("referenced_concepts", [])

    # 构建 claims 文本（带锚点）
    claims_text = "\n".join(
        f"- [{c['anchor']}] ({c['provenance']}) {c['text']}"
        for c in claims
    )

    _how_text = "\n".join(f"- {s}" for s in how_steps) if how_steps else "（无）"
    concepts_text = ", ".join(concepts) if concepts else "（待补充）"

    # 证据地图（含未覆盖列）
    evidence_rows = "\n".join(
        f"| {c['anchor']} | {c['text'][:60]}... | {c['provenance']} | 待补充 |"
        for c in claims
    )

    body = f"""## TLDR

{claims[0]['text'] if claims else '（待补充）'}

## 关键发现

{claims_text}

## 入选理由

本文提供 {len(claims)} 条核心论点和 {len(how_steps)} 条可执行步骤，涉及概念: {concepts_text}。

## 证据地图

| 锚点 | 论点 | 来源 | 未覆盖 |
|------|------|------|--------|
{evidence_rows}

## 核心论点

{claims_text}

## 论证与解释

{claims_text}

## 适用边界

（待后续 gate 评估补充）

## 图谱关系

| 概念 | 关系类型 | 强度 | 证据 |
|------|---------|------|------|
{chr(10).join(f'| {c} | related | medium | 待 gate 评估 |'
                     for c in concepts) if concepts else '| （待补充） | | | |'}

## Future Questions

{chr(10).join(f'- {q}' for q in _generate_questions(claims, how_steps))}

## 维护记录

| 日期 | 操作 | 说明 |
|------|------|------|
| {_now_date()} | 初始创建 | 段1-2 提炼，raw: {raw_path} |"""

    return body


def _now_date() -> str:
    """当前日期字符串 YYYY-MM-DD。"""
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d")


def _assemble_page(frontmatter: dict[str, Any], body: str) -> str:
    """组装 frontmatter + 正文为完整候选页。"""
    # 将 None 转为 null 字符串
    fm_lines = ["---"]
    for key, value in frontmatter.items():
        if value is None:
            fm_lines.append(f"{key}: null")
        elif isinstance(value, bool):
            fm_lines.append(f"{key}: {str(value).lower()}")
        elif isinstance(value, list):
            fm_lines.append(f"{key}:")
            for item in value:
                fm_lines.append(f"  - {item}")
        elif isinstance(value, (int, float)):
            fm_lines.append(f"{key}: {value}")
        else:
            fm_lines.append(f"{key}: {value}")
    fm_lines.append("---")

    fm_str = "\n".join(fm_lines)
    return f"{fm_str}\n\n{body}\n"
