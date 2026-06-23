"""B 层评分器 — 10 维量规 LLM 评分，输出可定位缺陷清单。

裁决模型: GPT-5.4（经 llm_router），兜底 gemini-flash。
量规文本: 从 skillpack/quality/rubric/ 读取，版本化。

核心约束:
    - FR-005: A 全过才调 B（调用方保证，本模块不自我检查）
    - FR-006: 缺 location 的缺陷条目整批拒收
    - FR-008: GPT-5.4 不可用切 gemini-flash；两者皆不可用挂起不放行
    - P7: Maker/Checker 异族分离 — 提炼用 DeepSeek，评分用 GPT-5.4

输出: Defect 清单（与 checks_structural.py 的 Defect 同构）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A 层 Defect 类型引用（同构）
from src.akb2.quality.checks_structural import Defect

# 导入只读依赖（step-3 产物）
from src.akb2.quality.llm_router import (
    RouterConfigError,
    route_llm,
)

# ---------------------------------------------------------------------------
# 具名常量（宪法 P4: 预算显式）
# ---------------------------------------------------------------------------

# 量规文件路径（相对本文件）
RUBRIC_DIR = Path(__file__).resolve().parents[3] / "skillpack" / "quality" / "rubric"
RUBRIC_FILE = "10-dimensions.md"
REQUIRED_RUBRIC_VERSION = "1.0.0"

# 评分模型阵容
PRIMARY_GRADER_MODEL = "gpt-5.4"
FALLBACK_GRADER_MODEL = "gemini-flash"

# 预算
MAX_GRADER_RETRIES = 2  # 评分重试次数（解析失败时）
GRADER_TIMEOUT_S = 120

# 10 维量规名称（用于解析输出）
RUBRIC_DIMENSIONS: list[str] = [
    "论断锚定率",
    "How 密度",
    "How 可执行性",
    "证据诚实",
    "解读分离",
    "TLDR 密度",
    "新颖增量",
    "边强度匹配",
    "Future Questions 质量",
    "适用边界",
]


@dataclass
class GraderResult:
    """评分器详细结果，保留 fallback 原因供 canary/runner 审计。"""

    defects: list[Defect]
    model_used: str
    suspended: bool
    primary_model: str = PRIMARY_GRADER_MODEL
    fallback_model: str = FALLBACK_GRADER_MODEL
    primary_failed: bool = False
    primary_failure_reason: str = ""
    fallback_failed: bool = False
    fallback_failure_reason: str = ""
    fallback_used: bool = False
    parse_warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _load_rubric_text() -> str:
    """加载量规文本并验证版本。

    Raises:
        RuntimeError: 量规文件不存在或版本不匹配。
    """
    rubric_path = RUBRIC_DIR / RUBRIC_FILE
    if not rubric_path.exists():
        raise RuntimeError(
            f"量规文件不存在: {rubric_path}。"
            f" 请确认 skillpack/quality/rubric/ 目录已部署。"
        )

    text = rubric_path.read_text(encoding="utf-8")

    # 验证版本
    version_match = re.search(r"版本:\s*([\d.]+)", text)
    if not version_match:
        raise RuntimeError(
            f"量规文件缺少版本标记: {rubric_path}。"
            f" 请在文件头部添加 '版本: X.Y.Z'。"
        )

    found_version = version_match.group(1)
    if found_version != REQUIRED_RUBRIC_VERSION:
        raise RuntimeError(
            f"量规版本不匹配: 需要 {REQUIRED_RUBRIC_VERSION}，"
            f" 实际 {found_version}。请更新量规文件或 grader 版本常量。"
        )

    return text


def _build_grader_prompt(candidate_text: str, rubric_text: str) -> list[dict[str, str]]:
    """构建评分 prompt（system + user）。"""
    system_prompt = f"""你是一个知识库内容质量评审专家。请根据以下 10 维量规对候选页进行评分。

量规:
{rubric_text}

评审规则:
1. 每个维度给出 pass 或 fail 判定
2. 如果 fail，必须给出具体的 defect 描述和修复建议
3. 每条缺陷必须包含 location 字段（指明稿件中的具体位置，如节标题/段落/字段名）
4. 输出必须是严格的 JSON 格式"""
    user_prompt = f"""请评审以下候选页:

{candidate_text}

请输出 JSON 格式的评审结果。"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _build_grader_schema() -> dict[str, Any]:
    """构建评分输出的 JSON Schema。"""
    return {
        "type": "object",
        "properties": {
            "dimensions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "dimension": {
                            "type": "string",
                            "description": "量规维度名称",
                            "enum": RUBRIC_DIMENSIONS,
                        },
                        "pass": {"type": "boolean"},
                        "defects": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "location": {
                                        "type": "string",
                                        "description": "稿件中缺陷的具体位置",
                                    },
                                    "defect": {
                                        "type": "string",
                                        "description": "缺陷描述",
                                    },
                                    "suggestion": {
                                        "type": "string",
                                        "description": "修复建议",
                                    },
                                },
                                "required": ["location", "defect", "suggestion"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["dimension", "pass", "defects"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["dimensions"],
        "additionalProperties": False,
    }


def _canonical_dimension_name(raw_name: Any) -> str | None:
    """将允许的维度名变体归一到 canonical 中文名。

    模型偶尔会返回 "论断锚定率 (Claim Anchor Rate)"。这仍是同一维度；
    但未知维度、id 数字、错别字仍必须拒收。
    """
    if not isinstance(raw_name, str):
        return None
    name = raw_name.strip()
    if name in RUBRIC_DIMENSIONS:
        return name
    for dim in RUBRIC_DIMENSIONS:
        if name.startswith(f"{dim} (") and name.endswith(")"):
            return dim
    return None


def _parse_grader_output(
    raw_content: str | dict,
) -> tuple[list[Defect], bool]:
    """解析 LLM 评分输出为 Defect 清单。

    FR-006: 缺 location 的条目整批拒收。

    Returns:
        (defects, parse_success): parse_success=False 表示解析失败
        （含缺 location 条目），此时应重试或挂起。
    """
    if isinstance(raw_content, dict):
        data = raw_content
    elif isinstance(raw_content, str):
        try:
            # 尝试提取 JSON 块
            json_match = re.search(r"\{[\s\S]*\}", raw_content)
            if json_match:
                data = json.loads(json_match.group(0))
            else:
                return [], False
        except json.JSONDecodeError:
            return [], False
    else:
        return [], False

    dimensions = data.get("dimensions", [])
    if not isinstance(dimensions, list):
        return [], False
    if not dimensions:
        return [], False

    defects: list[Defect] = []
    seen_dimensions: set[str] = set()
    for dim in dimensions:
        if not isinstance(dim, dict):
            return [], False
        if set(dim.keys()) - {"dimension", "pass", "defects"}:
            return [], False
        dim_name = _canonical_dimension_name(dim.get("dimension"))
        if dim_name is None:
            return [], False
        seen_dimensions.add(dim_name)
        passed = dim.get("pass", True)
        if not isinstance(passed, bool):
            return [], False
        if passed:
            if dim.get("defects", []) != []:
                return [], False
            continue

        dim_defects = dim.get("defects", [])
        if not isinstance(dim_defects, list):
            return [], False
        if not dim_defects:
            return [], False

        for d in dim_defects:
            if not isinstance(d, dict):
                return [], False
            if set(d.keys()) - {"location", "defect", "suggestion"}:
                return [], False
            location = d.get("location", "")
            defect_desc = d.get("defect", "")
            suggestion = d.get("suggestion", "")

            # FR-006: 缺 location 的条目整批拒收
            if not location or not isinstance(location, str) or not location.strip():
                return [], False
            if (
                not isinstance(defect_desc, str)
                or not defect_desc.strip()
                or not isinstance(suggestion, str)
                or not suggestion.strip()
            ):
                return [], False

            defects.append(
                Defect(
                    dimension=dim_name,
                    location=location,
                    defect=defect_desc,
                    suggestion=suggestion,
                )
            )

    if seen_dimensions != set(RUBRIC_DIMENSIONS):
        return [], False

    return defects, True


def _parse_failure_reason(model_family: str, content: Any) -> str:
    """生成短失败原因，避免静默 fallback。"""
    if isinstance(content, dict):
        keys = ",".join(sorted(str(k) for k in content.keys()))
        return f"{model_family} 返回不符合 grader schema 的 JSON(keys={keys})"
    if isinstance(content, str):
        return f"{model_family} 返回不可解析/不符合 grader schema 的文本"
    return f"{model_family} 返回类型不符合 grader schema: {type(content).__name__}"


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------


def run_grader_detailed(candidate_text: str) -> GraderResult:
    """运行 B 层 10 维量规评分。

    调用 GPT-5.4 评分；GPT-5.4 不可用时切 gemini-flash 兜底（FR-008）。
    解析输出为 Defect 清单。

    Args:
        candidate_text: 候选页全文（含 frontmatter + 正文）

    Returns:
        tuple[list[Defect], str, bool]:
            - GraderResult.defects: 缺陷清单（空列表 = 全 pass）
            - GraderResult.model_used: 实际使用的模型 ID
            - GraderResult.suspended: True 表示评分器双不可用，稿件挂起

    Raises:
        RuntimeError: 量规文件加载失败。
    """
    rubric_text = _load_rubric_text()
    messages = _build_grader_prompt(candidate_text, rubric_text)
    response_schema = _build_grader_schema()

    result = GraderResult(defects=[], model_used="none", suspended=True)

    # 尝试 GPT-5.4（解析失败时按 MAX_GRADER_RETRIES 重试，耗尽再切兜底）
    for _ in range(MAX_GRADER_RETRIES):
        try:
            response = route_llm(
                model_family=PRIMARY_GRADER_MODEL,
                messages=messages,
                response_schema=response_schema,
            )
            defects, parse_ok = _parse_grader_output(response.content)
            if parse_ok:
                return GraderResult(
                    defects=defects,
                    model_used=response.model_used,
                    suspended=False,
                )
            # 解析失败：记录原因，继续重试（循环耗尽后退出走兜底）
            result.primary_failed = True
            result.primary_failure_reason = _parse_failure_reason(
                PRIMARY_GRADER_MODEL, response.content
            )
        except (RouterConfigError, RuntimeError) as exc:
            result.primary_failed = True
            result.primary_failure_reason = f"{PRIMARY_GRADER_MODEL} 调用失败: {exc}"
            break  # 配置/运行时错误非解析失败，不重试，直接走兜底

    # 兜底: gemini-flash
    if result.primary_failed:
        try:
            response = route_llm(
                model_family=FALLBACK_GRADER_MODEL,
                messages=messages,
                response_schema=response_schema,
            )
            defects, parse_ok = _parse_grader_output(response.content)
            if parse_ok:
                result.defects = defects
                result.model_used = response.model_used
                result.suspended = False
                result.fallback_used = True
                return result
            result.fallback_failed = True
            result.fallback_failure_reason = _parse_failure_reason(
                FALLBACK_GRADER_MODEL, response.content
            )
        except (RouterConfigError, RuntimeError) as exc:
            result.fallback_failed = True
            result.fallback_failure_reason = f"{FALLBACK_GRADER_MODEL} 调用失败: {exc}"

    # FR-008: 双不可用 → 挂起
    return result


def run_grader(candidate_text: str) -> tuple[list[Defect], str, bool]:
    """运行 B 层评分器，保留旧三元组 API。"""
    result = run_grader_detailed(candidate_text)
    return result.defects, result.model_used, result.suspended


def run_grader_with_fixture_detailed(
    candidate_text: str,
    mock_route_llm: Any,
) -> GraderResult:
    """使用 fixture LLM 响应运行评分器（测试用）。

    Args:
        candidate_text: 候选页全文
        mock_route_llm: mock route_llm 函数（由 conftest.make_mock_route_llm 创建）

    Returns:
        tuple[list[Defect], str, bool]: 同 run_grader。
    """
    rubric_text = _load_rubric_text()
    messages = _build_grader_prompt(candidate_text, rubric_text)
    response_schema = _build_grader_schema()

    result = GraderResult(defects=[], model_used="none", suspended=True)

    # 尝试 GPT-5.4
    try:
        response = mock_route_llm(
            model_family=PRIMARY_GRADER_MODEL,
            messages=messages,
            response_schema=response_schema,
        )
        if response is not None:
            defects, parse_ok = _parse_grader_output(response.content)
            if parse_ok:
                return GraderResult(
                    defects=defects,
                    model_used=response.model_used,
                    suspended=False,
                )
            result.primary_failure_reason = _parse_failure_reason(
                PRIMARY_GRADER_MODEL, response.content
            )
        else:
            result.primary_failure_reason = f"{PRIMARY_GRADER_MODEL} 返回 None"
        result.primary_failed = True
    except Exception as exc:
        result.primary_failed = True
        result.primary_failure_reason = f"{PRIMARY_GRADER_MODEL} 调用失败: {exc}"

    # 兜底: gemini-flash
    if result.primary_failed:
        try:
            response = mock_route_llm(
                model_family=FALLBACK_GRADER_MODEL,
                messages=messages,
                response_schema=response_schema,
            )
            if response is not None:
                defects, parse_ok = _parse_grader_output(response.content)
                if parse_ok:
                    result.defects = defects
                    result.model_used = response.model_used
                    result.suspended = False
                    result.fallback_used = True
                    return result
                result.fallback_failure_reason = _parse_failure_reason(
                    FALLBACK_GRADER_MODEL, response.content
                )
            else:
                result.fallback_failure_reason = f"{FALLBACK_GRADER_MODEL} 返回 None"
            result.fallback_failed = True
        except Exception as exc:
            result.fallback_failed = True
            result.fallback_failure_reason = f"{FALLBACK_GRADER_MODEL} 调用失败: {exc}"

    # FR-008: 双不可用 → 挂起
    return result


def run_grader_with_fixture(
    candidate_text: str,
    mock_route_llm: Any,
) -> tuple[list[Defect], str, bool]:
    """使用 fixture LLM 响应运行评分器，保留旧三元组 API。"""
    result = run_grader_with_fixture_detailed(candidate_text, mock_route_llm)
    return result.defects, result.model_used, result.suspended
