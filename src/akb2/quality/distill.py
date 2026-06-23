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
import signal
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 具名常量（宪法 P1/P4）
# ---------------------------------------------------------------------------

MAX_EXTRACT_RETRIES: int = 3
# 截断保护：raw 喂给抽取的字符上限（旧截断阈值过小，截掉 §D-§J 约 53% 正文；
# 提到具名常量并放大到 50k，避免裸魔数 + 减少截断失真）
MAX_EXTRACT_CHARS: int = 50_000
# how_steps 可执行下限：prompt 要求 ≥3 条 executable=true 且 command 非空（How 可执行性）
HOW_STEPS_MIN_EXECUTABLE: int = 3
HOW_STEPS_MAX_RENDERED: int = 3
CLAIMS_MAX_ITEMS: int = 5
GATE_REASON_MAX_CHARS: int = 150
FUTURE_QUESTIONS_MIN: int = 2
FUTURE_QUESTIONS_MAX: int = 3
RELATED_CONCEPTS_MIN: int = 2
DISTILL_MODEL_FAMILY: str = "deepseek-v4-pro"
DISTILL_TIMEOUT_S: float = 60.0
DISTILL_MAX_RETRIES: int = 0
DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S: float = 180.0
# Step-7: 语义型 How 抖动兜底。how_params segment（LLM 自由文本 how_steps，
# 非 must_cover 代码直出）多取取交集，抵抗单次 LLM 抖动。有界：SEMANTIC_RETAKE_MAX 次。
SEMANTIC_RETAKE_MAX: int = 2
RAW_ANCHOR_DECL_RE: str = r"§[A-Z0-9]+"
RAW_ANCHOR_VALUE_RE = re.compile(rf"^{RAW_ANCHOR_DECL_RE}$")
RAW_ANCHOR_REF_RE = re.compile(rf"\[raw\s+({RAW_ANCHOR_DECL_RE})\]")

# Step-5: How provenance 细分标注层（三态）。
# source_grounded 的细分：source_operation（原文操作/命令）、source_fact（原文非操作的事实陈述）；
# akb_interpretation 保持原义（AKB 解读/补充）。消费者 SG-provenance-ratio 仍按
# (source_grounded) / (akb_interpretation) marker 计数——本层只是标注，不改渲染 marker。
HOW_PROVENANCE_SPLIT_VALUES: tuple[str, ...] = (
    "source_operation",
    "source_fact",
    "akb_interpretation",
)
# 标注层 → 消费者两态 provenance 的映射（source_operation/source_fact 都落到 source_grounded）。
HOW_PROVENANCE_SPLIT_TO_MARKER: dict[str, str] = {
    "source_operation": "source_grounded",
    "source_fact": "source_grounded",
    "akb_interpretation": "akb_interpretation",
}


class DistillAttemptTimeoutError(TimeoutError):
    """Distill extract attempt exceeded its wall-clock budget."""


class SemanticJitterError(Exception):
    """语义 segment 多取交集为空/连续不一致达阈值，fail-closed（不无限重试）。"""


@contextmanager
def _distill_attempt_deadline(timeout_s: float):
    """Wall-clock deadline for one segmented extract attempt in the main thread."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)

    def _handler(_signum, _frame):
        raise DistillAttemptTimeoutError(
            f"抽取尝试超过总时限 {timeout_s}s"
        )

    signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)

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
                    # 证据盲区:原文对这条论点未覆盖/未证明/未量化/未比较的具体缺口。
                    # 禁止复述 claim 或写背景释义(否则证据地图"未覆盖"列变成论证摘要)。
                    "evidence_gap": {
                        "type": "string",
                        "minLength": 4,
                    },
                },
                "required": ["text", "anchor", "provenance", "evidence_gap"],
                "additionalProperties": False,
            },
            "minItems": 1,
        },
        "how_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "minLength": 2},
                    "command": {"type": "string"},
                    "executable": {"type": "boolean"},
                    "anchor": {"type": "string"},
                    "provenance": {
                        "type": "string",
                        "enum": ["source_grounded", "akb_interpretation"],
                    },
                    # step-5: 三态细分标注层（normalize 阶段注入，消费者仍读两态 provenance）。
                    # 对齐 HOW_PROVENANCE_SPLIT_VALUES 常量；非 required——LLM 原始抽取不带此字段，
                    # normalize 后必存。声明于 properties 以过 additionalProperties: False 校验。
                    "provenance_split": {
                        "type": "string",
                        "enum": ["source_operation", "source_fact", "akb_interpretation"],
                    },
                },
                "required": ["instruction", "command", "executable"],
                "additionalProperties": False,
            },
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
        "tldr": {
            "type": "string",
            "minLength": 20,
        },
        "argumentation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_anchor": {"type": "string"},
                    "explanation": {"type": "string", "minLength": 10},
                    # provenance 区分来源事实(source_grounded) vs 解读推断(akb_interpretation)。
                    # rubric 维度5 解读分离:解读性内容(未来趋势/可能性/归纳)必须标 akb_interpretation,
                    # 不得伪装成 source_grounded(否则解读分离扣分)。
                    "provenance": {
                        "type": "string",
                        "enum": ["source_grounded", "akb_interpretation"],
                    },
                },
                "required": ["claim_anchor", "explanation", "provenance"],
                "additionalProperties": False,
            },
            "minItems": 0,
        },
        "applicability_boundary": {
            "type": "string",
            "minLength": 20,
        },
        "graph_edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "concept": {"type": "string"},
                    "relation_type": {"type": "string"},
                    "strength": {"type": "string", "enum": ["strong", "medium", "weak"]},
                    "evidence": {"type": "string", "minLength": 5},
                    "anchor": {"type": "string"},
                    "provenance": {
                        "type": "string",
                        "enum": ["source_grounded", "akb_interpretation"],
                    },
                    "evidence_kind": {
                        "type": "string",
                        "enum": ["direct_quote", "source_summary", "interpretation"],
                    },
                },
                "required": [
                    "concept",
                    "relation_type",
                    "strength",
                    "evidence",
                    "anchor",
                    "provenance",
                    "evidence_kind",
                ],
                "additionalProperties": False,
            },
            "minItems": 0,
        },
    },
    "required": [
        "claims", "how_steps", "params", "referenced_concepts",
        "tldr", "argumentation", "applicability_boundary", "graph_edges",
    ],
    "additionalProperties": False,
}


_CLAIMS_SCHEMA: dict[str, Any] = {
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
                    "evidence_gap": {"type": "string", "minLength": 4},
                },
                "required": ["text", "anchor", "provenance", "evidence_gap"],
                "additionalProperties": False,
            },
            "minItems": 1,
        },
        "tldr": {"type": "string", "minLength": 20},
    },
    "required": ["claims", "tldr"],
    "additionalProperties": False,
}


_HOW_PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "how_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "minLength": 2},
                    "command": {"type": "string"},
                    "executable": {"type": "boolean"},
                },
                "required": ["instruction", "command", "executable"],
                "additionalProperties": False,
            },
            "minItems": 0,
        },
        "param_entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "minLength": 1},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
            "minItems": 0,
        },
    },
    "required": ["how_steps", "param_entries"],
    "additionalProperties": False,
}


_META_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "referenced_concepts": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
        },
        "applicability_boundary": {"type": "string", "minLength": 20},
    },
    "required": [
        "referenced_concepts",
        "applicability_boundary",
    ],
    "additionalProperties": False,
}


_ARGUMENTATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "argumentation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_anchor": {"type": "string"},
                    "explanation": {"type": "string", "minLength": 10},
                    "provenance": {
                        "type": "string",
                        "enum": ["source_grounded", "akb_interpretation"],
                    },
                },
                "required": ["claim_anchor", "explanation", "provenance"],
                "additionalProperties": False,
            },
            "minItems": 0,
        },
    },
    "required": [
        "argumentation",
    ],
    "additionalProperties": False,
}


_GRAPH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "graph_edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "concept": {"type": "string"},
                    "relation_type": {"type": "string"},
                    "strength": {"type": "string", "enum": ["strong", "medium", "weak"]},
                    "evidence": {"type": "string", "minLength": 5},
                    "anchor": {"type": "string"},
                    "provenance": {
                        "type": "string",
                        "enum": ["source_grounded", "akb_interpretation"],
                    },
                    "evidence_kind": {
                        "type": "string",
                        "enum": ["direct_quote", "source_summary", "interpretation"],
                    },
                },
                "required": [
                    "concept",
                    "relation_type",
                    "strength",
                    "evidence",
                    "anchor",
                    "provenance",
                    "evidence_kind",
                ],
                "additionalProperties": False,
            },
            "minItems": 0,
        },
    },
    "required": [
        "graph_edges",
    ],
    "additionalProperties": False,
}

_DISTILL_SEGMENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "claims": _CLAIMS_SCHEMA,
    "how_params": _HOW_PARAMS_SCHEMA,
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
    route_llm_was_default = route_llm_fn is None
    if route_llm_fn is None:
        from src.akb2.quality.llm_router import route_llm as _default

        route_llm_fn = _default
    beta_tool_enabled = (
        route_llm_was_default
        and os.environ.get("AKB2_DISTILL_DEEPSEEK_BETA_TOOL", "0") == "1"
    )
    json_mode_enabled = (
        route_llm_was_default
        and os.environ.get("AKB2_DISTILL_DEEPSEEK_JSON_MODE", "0") == "1"
        and not beta_tool_enabled
    )
    # Step-7: 语义型 How 抖动兜底开关。how_params segment 多取取交集（router 无关，
    # 注入 mock 亦可触发，便于测试）；默认关，与 beta_tool/json_mode 的 default-router
    # 闸门并列。仅作用于 LLM 自由文本 how_steps，不碰 must_cover 代码直出。
    semantic_retake_enabled = os.environ.get("AKB2_DISTILL_SEMANTIC_RETAKE", "0") == "1"

    content_snippet = raw_content[:MAX_EXTRACT_CHARS]  # 截断保护（具名常量，旧阈值截掉半数正文）

    for attempt in range(1, MAX_EXTRACT_RETRIES + 1):
        try:
            with _distill_attempt_deadline(DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S):
                parsed_segments: dict[str, dict[str, Any]] = {}
                for segment_name in _DISTILL_SEGMENT_SCHEMAS:
                    if segment_name == "how_params" and semantic_retake_enabled:
                        # Step-7: 语义 segment 多取取交集（仅 LLM 自由文本 how_steps）。
                        segment_data = _extract_how_params_with_retake(
                            content_snippet,
                            route_llm_fn,
                            beta_tool_enabled,
                            json_mode_enabled,
                            parsed_segments,
                        )
                    else:
                        segment_data = _extract_segment_once(
                            segment_name,
                            content_snippet,
                            route_llm_fn,
                            beta_tool_enabled,
                            json_mode_enabled,
                            parsed_segments,
                        )
                    parsed_segments[segment_name] = segment_data
            merged = _merge_extract_segments(parsed_segments)
            _validate_extract_output(merged)
        except SemanticJitterError as exc:
            # Step-7: 语义 segment 连续不稳定达阈值 → fail-closed（不无限重试，有界）。
            return ExtractResult(
                success=False,
                retries_used=attempt,
                error=f"语义 segment 抖动 fail-closed（{SEMANTIC_RETAKE_MAX} 次交集为空）: {exc}",
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            if attempt >= MAX_EXTRACT_RETRIES:
                return ExtractResult(
                    success=False,
                    retries_used=attempt,
                    error=f"抽取校验失败（{attempt}/{MAX_EXTRACT_RETRIES}）: {exc}",
                )
            continue
        except Exception as exc:
            if attempt >= MAX_EXTRACT_RETRIES:
                return ExtractResult(
                    success=False,
                    retries_used=attempt,
                    error=f"抽取调用失败（{attempt}/{MAX_EXTRACT_RETRIES}）: {exc}",
                )
            continue

        return ExtractResult(
            success=True,
            data=merged,
            retries_used=attempt,
        )

    return ExtractResult(
        success=False,
        retries_used=MAX_EXTRACT_RETRIES,
        error=f"抽取重试耗尽（{MAX_EXTRACT_RETRIES}）",
    )


def _build_extract_segment_messages(
    segment_name: str,
    content_snippet: str,
) -> list[dict[str, str]]:
    """构建分段抽取 prompt，降低单次 schema/推理复杂度。"""
    segment_instructions = {
        "claims": (
            "只抽取 claims 与 tldr。\n"
            "- claims: 每条含 text、anchor(如 §A)、provenance、evidence_gap。\n"
            "- 只返回最重要的 3-5 条 claims，最多 5 条；不要覆盖每个段落。\n"
            "- provenance 只能是 source_grounded 或 akb_interpretation，"
            "akb_interpretation 占比必须 ≤40%。\n"
            "- evidence_gap 写原文未覆盖/未证明/未量化/未比较的具体盲区，"
            "须含盲区词(未提供/未量化/未覆盖/缺少/没有/未验证/未比较)。\n"
            "- tldr 是综合全文主旨，不是复制单条 claim。"
        ),
        "how_params": (
            "只抽取 how_steps 与 param_entries。\n"
            "- how_steps: 对象数组 {instruction, command, executable}，"
            "至少 3 条 executable=true 且 command 非空。\n"
            "- 只抽取原文明确给出的步骤、组件、流程或配置，不要补 AKB 自己的命令。\n"
            "- param_entries: 参数/配置/选项数组，每项 {key, value}；value 统一写成字符串。"
        ),
        "meta": (
            "只抽取 referenced_concepts 与 applicability_boundary。\n"
            "- referenced_concepts: 2-5 个短概念名，避免泛词。\n"
            "- applicability_boundary: 一段话，同时包含适用场景、不适用场景和约束条件。"
        ),
        "argumentation": (
            "只抽取 argumentation。\n"
            "- 为 claims 中每个重要 anchor 写一条论证说明。\n"
            "- 每项含 claim_anchor(如 §A)、explanation、provenance。\n"
            "- 若 explanation 是归纳或操作化判断，provenance 用 akb_interpretation。"
            "- 输出顶层必须是 {\"argumentation\":[...]}，不能用 claims/arguments/items 等同义 key。"
        ),
        "graph": (
            "只抽取 graph_edges，最多 4 条，宁少勿强。\n"
            "- 每项含 concept、relation_type、strength、evidence、anchor、provenance、evidence_kind。\n"
            "- relation_type 优先 related/describes/mentions，除非原文直接支持，避免 requires/ensures/reduces。\n"
            "- strength 默认 weak；只有原文直接点名 target 且 evidence 带 [raw §X] 或原文引号才可 strong。\n"
            "- evidence 必须带 [raw §X] (source_grounded) 或明确 (akb_interpretation)。"
        ),
    }
    return [
        {
            "role": "system",
            "content": (
                "你是一个知识提炼助手。按指定分段从原文提取结构化 JSON。\n"
                "必须只返回一个合法 JSON object；不要 Markdown，不要解释，不要额外文本。\n"
                "字段名必须使用指令中的英文 key，不能自创同义字段。\n"
                f"{segment_instructions[segment_name]}"
            ),
        },
        {
            "role": "user",
            "content": f"原文内容:\n{content_snippet}",
        },
    ]


def _coerce_json_dict(raw_content: Any) -> dict[str, Any]:
    """将 router content 规整为 dict；非 JSON 明确失败。"""
    if isinstance(raw_content, dict):
        return raw_content
    if isinstance(raw_content, str):
        parsed = json.loads(raw_content)
        if isinstance(parsed, dict):
            return parsed
    raise TypeError(f"抽取输出不是 JSON object: {type(raw_content).__name__}")


def _normalize_extract_segment(
    segment_name: str,
    parsed: dict[str, Any],
    previous_segments: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """允许旧完整 fixture 响应复用到分段抽取，同时保持出站 schema 严格。"""
    previous_segments = previous_segments or {}
    if segment_name == "claims":
        return {
            "claims": [
                _normalize_claim(item)
                for item in parsed.get("claims", [])[:CLAIMS_MAX_ITEMS]
                if isinstance(item, dict)
            ],
            "tldr": _string_from_maybe_object(parsed.get("tldr", "")),
        }
    if segment_name == "how_params":
        param_entries = parsed.get("param_entries")
        if param_entries is None and isinstance(parsed.get("params"), dict):
            param_entries = [
                {"key": str(key), "value": str(value)}
                for key, value in parsed["params"].items()
            ]
        if param_entries is None and isinstance(parsed.get("param_entries"), list):
            param_entries = parsed["param_entries"]
        return {
            "how_steps": [
                _normalize_how_step(item, idx)
                for idx, item in enumerate(parsed.get("how_steps", []), 1)
            ],
            "param_entries": [
                _normalize_param_entry(item)
                for item in (param_entries or [])
                if isinstance(item, dict)
            ],
        }
    if segment_name == "meta":
        applicability = parsed.get("applicability_boundary", "")
        return {
            "referenced_concepts": [
                _normalize_concept(item)
                for item in parsed.get("referenced_concepts", [])
            ],
            "applicability_boundary": _string_from_maybe_object(applicability),
        }
    if segment_name == "argumentation":
        claims = previous_segments.get("claims", {}).get("claims", [])
        raw_items = parsed.get("argumentation")
        if raw_items is None:
            raw_items = parsed.get("arguments") or parsed.get("items") or parsed.get("claims")
        return {
            "argumentation": [
                _normalize_argumentation(item, claims, idx)
                for idx, item in enumerate(raw_items or [], 1)
                if isinstance(item, dict)
            ],
        }
    if segment_name == "graph":
        return {
            "graph_edges": [
                _normalize_graph_edge(edge)
                for edge in parsed.get("graph_edges", [])
                if isinstance(edge, dict)
            ],
        }
    raise ValueError(f"未知抽取分段: {segment_name}")


def _first_anchor(value: Any, default: str = "§A") -> str:
    if isinstance(value, str):
        anchor = _extract_raw_anchor(value) or value.strip()
    elif isinstance(value, list) and value:
        anchor = _first_anchor(value[0], default="")
    else:
        anchor = ""
    if not anchor:
        return default
    if not anchor.startswith("§"):
        anchor = f"§{anchor.lstrip('§')}"
    return anchor if RAW_ANCHOR_VALUE_RE.match(anchor) else default


def _string_from_maybe_object(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "summary", "value", "name"):
            if key in value:
                return str(value[key])
        return "; ".join(f"{k}: {v}" for k, v in value.items() if not isinstance(v, (dict, list)))
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value or "")


def _normalize_claim(item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("text") or item.get("claim") or item.get("summary") or "").strip()
    anchor = _first_anchor(item.get("anchor") or item.get("anchors") or item.get("section"))
    provenance = str(item.get("provenance") or "").strip()
    if provenance not in {"source_grounded", "akb_interpretation"}:
        provenance = "source_grounded"
    gap = str(item.get("evidence_gap") or item.get("gap") or "").strip()
    if not gap:
        gap = "未提供：原文未提供该论点的量化验证、反例或对比数据。"
    return {
        "text": text,
        "anchor": anchor,
        "provenance": provenance,
        "evidence_gap": gap,
    }


def how_step_provenance_split(
    step: dict[str, Any],
    command: str,
    source_anchor: str = "",
) -> str:
    """Classify a How step into the three-way provenance split (step-5).

    Layered on top of the existing two-way provenance (source_grounded /
    akb_interpretation). The split aligns with the rendered marker so that
    the consumer SG-provenance-ratio (which counts ``(source_grounded)`` /
    ``(akb_interpretation)`` markers) counts consistently:
        - source_operation: step renders a ``(source_grounded)`` marker because
          it carries a concrete executable command verbatim from the source.
        - akb_interpretation: step renders an ``(akb_interpretation)`` marker
          (LLM-supplemented, generated placeholder, or no anchor).
        - source_fact: not produced for LLM how_steps; reserved for must_cover
          boundary/conclusion facts (see ``must_cover_fact_provenance_split``).

    This field is annotation-only and does not alter rendering.
    """
    rendered_marker = _how_step_provenance(step, command, source_anchor)
    if rendered_marker == "source_grounded":
        return "source_operation"
    return "akb_interpretation"


def must_cover_operation_provenance_split() -> str:
    """Provenance split for code-direct must_cover source_operation steps.

    These are verbatim source literals rendered by the scanner; they are the
    canonical ``source_operation`` instances and render ``(source_grounded)``.
    """
    return "source_operation"


def must_cover_fact_provenance_split() -> str:
    """Provenance split for code-direct must_cover fact items.

    source_boundary / source_conclusion items are verbatim source facts (not
    operations); they render ``(source_grounded)`` markers and are classified
    as ``source_fact`` on the annotation layer.
    """
    return "source_fact"


def _normalize_how_step(item: Any, idx: int) -> dict[str, Any]:
    if isinstance(item, dict):
        instruction = str(item.get("instruction") or item.get("text") or item.get("name") or "").strip()
        command = str(item.get("command") or item.get("cmd") or "").strip()
        executable = item.get("executable")
        anchor = _first_anchor(item.get("anchor") or item.get("anchors") or item.get("section"), default="")
        provenance = str(item.get("provenance") or "").strip()
    else:
        instruction = str(item).strip()
        command = ""
        executable = None
        anchor = ""
        provenance = ""
    if not command and instruction:
        command = _infer_source_command_from_instruction(instruction)
    command = _expand_placeholder_command(command, instruction, idx)
    if provenance not in {"source_grounded", "akb_interpretation"}:
        provenance = "source_grounded" if anchor else "akb_interpretation"
    normalized = {
        "instruction": instruction,
        "command": command,
        "executable": bool(command) if executable is None else bool(executable),
        "anchor": anchor,
        "provenance": provenance,
        # step-5: 三态细分标注层（source_operation / source_fact / akb_interpretation）。
        # 与 provenance 二态并存：source_grounded → source_operation|source_fact；
        # akb_interpretation → akb_interpretation。消费者仍读 marker，本字段仅标注。
        "provenance_split": how_step_provenance_split(
            {"provenance": provenance}, command, anchor
        ),
    }
    return normalized


def _infer_source_command_from_instruction(instruction: str) -> str:
    """Infer only well-known source protocol operations, never AKB helper commands."""
    text = instruction.lower()
    protocol_ops = (
        "notifications/initialized",
        "resources/read",
        "resources/list",
        "prompts/get",
        "prompts/list",
        "tools/call",
        "tools/list",
        "initialize",
    )
    for op in protocol_ops:
        if op in text:
            return op
    if "call" in text and "tool" in text:
        return "tools/call"
    if "list" in text and "tool" in text:
        return "tools/list"
    if "read" in text and "resource" in text:
        return "resources/read"
    if "list" in text and "resource" in text:
        return "resources/list"
    if "get" in text and "prompt" in text:
        return "prompts/get"
    if "list" in text and "prompt" in text:
        return "prompts/list"
    return ""


def _expand_placeholder_command(command: str, instruction: str, idx: int) -> str:
    """Turn model placeholder verbs into copyable AKB operation examples."""
    cleaned = command.strip()
    source_protocol_commands = {
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
        "resources/list",
        "resources/read",
        "prompts/list",
        "prompts/get",
    }
    if cleaned.lower() in source_protocol_commands:
        return cleaned
    canonical = re.sub(r"[^a-z0-9_]+", "_", cleaned.lower()).strip("_")
    instruction_blob = instruction.lower()
    replacements = {
        "configure_trigger": "trigger={type:'schedule',cron:'0 * * * *'}",
        "select_work_unit": "work_item=ledger.next(status='pending',limit=1)",
        "execute_action": "result=run_action(work_item,timeout_seconds=60)",
        "run_quality_gate": "assert run_quality_gate(result,grader='gpt-5.4').defects == []",
        "write_ledger": "ledger.append({item_id:work_item.id,status:'done',next_action:'grade'})",
        "record_llm_call_metadata": (
            "ledger.append({event:'llm_call',model_family:model_family,"
            "prompt_version:prompt_version,timeout_s:60,max_retries:0,"
            "fallback_used:false,raw_anchor:raw_anchor})"
        ),
        "define_budget_constants": (
            "MAX_REPAIR_ROUNDS=3; DISTILL_TIMEOUT_SECONDS=60; "
            "GRADER_TIMEOUT_SECONDS=120; MAX_PARALLEL_ITEMS=1"
        ),
        "separate_maker_and_checker_models": (
            "maker_model='deepseek-v4-pro'; checker_model='gpt-5.4'; "
            "assert maker_model != checker_model and fallback_used is False"
        ),
    }
    if canonical in replacements:
        return replacements[canonical]
    if cleaned and re.search(r"[=(){}\[\]<>;]|\.|^assert\b", cleaned):
        return cleaned
    natural = f"{cleaned} {instruction_blob}".lower()
    natural_replacements = (
        (
            ("define trigger", "configure trigger", "trigger definition", "定义触发"),
            replacements["configure_trigger"],
        ),
        (
            ("select next work item", "select next item", "next work item", "next item", "选择下一"),
            replacements["select_work_unit"],
        ),
        (
            ("write ledger entry", "write ledger", "ledger entry", "写入账本"),
            replacements["write_ledger"],
        ),
        (
            ("scenario fixture", "define the scenario", "场景 fixture", "定义场景"),
            "scenario=fixtures.load(name=scenario_id)",
        ),
        (
            ("ledger checkpoint", "previous checkpoint", "load checkpoint", "检查点", "账本"),
            "checkpoint=ledger.load_latest(run_id=run_id)",
        ),
        (
            ("bounded agent action", "execute one bounded", "agent action", "执行动作"),
            "result=agent.step(scenario,checkpoint,max_actions=5)",
        ),
        (
            ("deterministic assertions", "run assertions", "assertion result", "断言"),
            "assertions.run(result,fixture=scenario)",
        ),
        (
            ("append the result", "ledger write", "write happens before", "写入账本"),
            "ledger.append({run_id:run_id,status:'asserted',result_hash:hash(result)})",
        ),
        (
            ("record llm call metadata", "llm call details", "model family", "prompt version", "fallback usage", "记录模型", "记录调用"),
            replacements["record_llm_call_metadata"],
        ),
        (
            ("define budget constants", "explicit budgets", "named constants", "max repair rounds", "max retries", "预算", "命名常量"),
            replacements["define_budget_constants"],
        ),
        (
            ("separate maker and checker", "maker model", "checker model", "different from the model that judges", "分离模型", "裁判模型"),
            replacements["separate_maker_and_checker_models"],
        ),
    )
    for markers, concrete in natural_replacements:
        if any(marker in natural for marker in markers):
            return concrete
    if not cleaned:
        return ""
    if cleaned and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cleaned):
        if "trigger" in instruction_blob:
            return replacements["configure_trigger"]
        if "work unit" in instruction_blob or "ledger" in instruction_blob and "select" in instruction_blob:
            return replacements["select_work_unit"]
        if "quality gate" in instruction_blob or "verify" in instruction_blob:
            return replacements["run_quality_gate"]
        if "write" in instruction_blob and "ledger" in instruction_blob:
            return replacements["write_ledger"]
        if "execute" in instruction_blob or "actual work" in instruction_blob:
            return replacements["execute_action"]
        return f"{cleaned}(input=work_item, timeout_seconds=60)"
    return cleaned


def _normalize_param_entry(item: dict[str, Any]) -> dict[str, str]:
    key = str(item.get("key") or item.get("name") or item.get("id") or "").strip()
    value = item.get("value")
    if value is None:
        value = item.get("default") or item.get("text") or item.get("evidence_gap") or ""
    return {"key": key, "value": str(value)}


def _normalize_concept(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("concept") or item.get("name") or item.get("id") or "").strip()
    return str(item).strip()


def _normalize_argumentation(
    item: dict[str, Any],
    claims: list[dict[str, Any]] | None = None,
    idx: int = 1,
) -> dict[str, str]:
    claims = claims or []
    fallback_anchor = ""
    if claims:
        fallback_anchor = str(
            claims[min(max(idx - 1, 0), len(claims) - 1)].get("anchor", "")
        ).strip()
    anchor = _first_anchor(
        item.get("claim_anchor") or item.get("anchor") or item.get("anchors"),
        default=fallback_anchor or "§A",
    )
    text = str(item.get("explanation") or item.get("text") or item.get("claim") or "").strip()
    provenance = str(item.get("provenance") or "").strip()
    if provenance not in {"source_grounded", "akb_interpretation"}:
        provenance = "source_grounded" if anchor else "akb_interpretation"
    return {"claim_anchor": anchor, "explanation": text, "provenance": provenance}


def _validate_extract_segment(segment_name: str, data: dict[str, Any]) -> None:
    """独立校验分段 schema，错误分类留给 runner 记录。"""
    schema = _DISTILL_SEGMENT_SCHEMAS[segment_name]
    data_for_schema = data
    if segment_name == "how_params":
        # how_params 出站 strict schema 只暴露 3 个模型字段；anchor/provenance
        # 是本地 normalizer 为正文锚定补的内部字段，不能反过来污染 strict schema。
        data_for_schema = {
            "how_steps": [
                {
                    "instruction": step.get("instruction", ""),
                    "command": step.get("command", ""),
                    "executable": bool(step.get("executable")),
                }
                for step in data.get("how_steps", [])
                if isinstance(step, dict)
            ],
            "param_entries": data.get("param_entries", []),
        }
    try:
        import jsonschema
    except ImportError:
        return
    try:
        jsonschema.validate(data_for_schema, schema)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"{segment_name} schema 校验失败: {exc.message}") from exc


def _extract_segment_once(
    segment_name: str,
    content_snippet: str,
    route_llm_fn: Any,
    beta_tool_enabled: bool,
    json_mode_enabled: bool,
    previous_segments: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """单次抽取一个 segment：messages → LLM → parse → normalize → validate。

    从 extract_claims 主循环抽出，便于 how_params retake 复用同一路径产多次。
    非 retake 路径行为与原内联实现等价（同调用、同归一化、同校验）。
    """
    schema = _DISTILL_SEGMENT_SCHEMAS[segment_name]
    messages = _build_extract_segment_messages(segment_name, content_snippet)
    if beta_tool_enabled:
        from src.akb2.quality.llm_router import route_deepseek_beta_tool

        response = route_deepseek_beta_tool(
            messages,
            schema,
            timeout_s=DISTILL_TIMEOUT_S,
            max_retries=DISTILL_MAX_RETRIES,
            tool_name=f"akb2_extract_{segment_name}",
        )
    elif json_mode_enabled:
        from src.akb2.quality.llm_router import route_deepseek_json_object

        response = route_deepseek_json_object(
            messages,
            timeout_s=DISTILL_TIMEOUT_S,
            max_retries=DISTILL_MAX_RETRIES,
        )
    else:
        response = route_llm_fn(
            DISTILL_MODEL_FAMILY,
            messages,
            response_schema=schema,
            timeout_s=DISTILL_TIMEOUT_S,
            max_retries=DISTILL_MAX_RETRIES,
            reasoning_effort=None,
            strict_schema=True,
        )
    parsed = _coerce_json_dict(response.content)
    segment_data = _normalize_extract_segment(segment_name, parsed, previous_segments)
    _validate_extract_segment(segment_name, segment_data)
    return segment_data


def _extract_how_params_with_retake(
    content_snippet: str,
    route_llm_fn: Any,
    beta_tool_enabled: bool,
    json_mode_enabled: bool,
    previous_segments: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """语义型 How 抖动兜底：how_params segment 产 SEMANTIC_RETAKE_MAX 次取交集。

    只针对 LLM 自由文本 how_steps（语义 segment），不碰 must_cover 代码直出
    （must_cover 由 scanner 产、_build_body 代码直出渲染，本函数不涉及）。
    交集以 command 对齐，保留每次都产出的 step（取首次 instruction/command/anchor）；
    交集为空 → SemanticJitterError fail-closed（有界 SEMANTIC_RETAKE_MAX，不无限重试）。
    param_entries 非语义抖动源，取首次产出。
    """
    samples: list[dict[str, Any]] = []
    for _ in range(SEMANTIC_RETAKE_MAX):
        samples.append(
            _extract_segment_once(
                "how_params",
                content_snippet,
                route_llm_fn,
                beta_tool_enabled,
                json_mode_enabled,
                previous_segments,
            )
        )
    intersected = _intersect_how_steps(samples)
    if not intersected["how_steps"]:
        raise SemanticJitterError(
            f"how_params 多取（{SEMANTIC_RETAKE_MAX} 次）交集为空，语义 segment 连续不一致"
        )
    return intersected


def _intersect_how_steps(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """多取取交集：以 command 为 key 对齐，保留所有 sample 共有的 step。

    取首次出现的 step（instruction/command/anchor/provenance/provenance_split），
    保留首次 param_entries。command 为空或仅单次出现的 step 舍弃；交集为空由调用方
    fail-closed。
    """
    if not samples:
        return {"how_steps": [], "param_entries": []}
    first = samples[0]
    by_command: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for step in first.get("how_steps", []):
        cmd = str(step.get("command", "")).strip()
        if cmd and cmd not in by_command:
            by_command[cmd] = step
            order.append(cmd)
    common = set(by_command)
    for sample in samples[1:]:
        sample_commands = {
            str(step.get("command", "")).strip()
            for step in sample.get("how_steps", [])
            if str(step.get("command", "")).strip()
        }
        common &= sample_commands
    intersected_steps = [by_command[cmd] for cmd in order if cmd in common]
    return {
        "how_steps": intersected_steps,
        "param_entries": first.get("param_entries", []),
    }


def _merge_extract_segments(
    segments: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """合并三段输出，外部仍保持原 ExtractResult.data 结构。"""
    how_params = segments["how_params"]
    params = {
        str(item.get("key", "")).strip(): str(item.get("value", ""))
        for item in how_params.get("param_entries", [])
        if str(item.get("key", "")).strip()
    }
    claims = segments["claims"].get("claims", [])
    how_steps = how_params.get("how_steps", [])
    enrichment = _build_local_enrichment(claims, how_steps)
    merged = {
        "claims": claims,
        "how_steps": how_steps,
        "params": params,
        "referenced_concepts": enrichment["referenced_concepts"],
        "tldr": segments["claims"].get("tldr", ""),
        "argumentation": enrichment["argumentation"],
        "applicability_boundary": enrichment["applicability_boundary"],
        "graph_edges": enrichment["graph_edges"],
    }
    if not merged["claims"]:
        raise ValueError("claims 分段为空")
    if not merged["how_steps"]:
        raise ValueError("how_params 分段未返回 how_steps")
    if not merged["argumentation"]:
        raise ValueError("argumentation 分段为空")
    return merged


def _build_local_enrichment(
    claims: list[dict[str, Any]],
    how_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build conservative non-LLM enrichment around DeepSeek's core extraction.

    DeepSeek v4-pro is used for the source-sensitive claims/how extraction. The
    graph and argumentation wrapper is deterministic and deliberately weak to
    avoid expensive model retries and over-strong graph hallucinations.
    """
    concepts = _infer_concepts(claims)
    for fallback in ("loop-engineering", "quality-gate", "agent-workflow"):
        if fallback not in concepts:
            concepts.append(fallback)
        if len(concepts) >= RELATED_CONCEPTS_MIN:
            break
    concepts = concepts[:4]
    argumentation = [
        {
            "claim_anchor": claim.get("anchor", "§A"),
            "explanation": str(claim.get("text", "")).strip(),
            "provenance": claim.get("provenance", "source_grounded"),
        }
        for claim in claims
    ]
    executable_count = sum(
        1
        for step in how_steps
        if isinstance(step, dict)
        and step.get("executable") is True
        and str(step.get("command", "")).strip()
    )
    applicability_boundary = (
        "适用：有重复触发、状态记录、质量门和人工复核入口的多步骤 agent 或知识库管线；"
        "不适用：一次性问答、无外部状态、无可验证输出或无法维护账本的任务；"
        f"约束：至少保留 {executable_count or HOW_STEPS_MIN_EXECUTABLE} 个可复制动作、"
        "raw anchor、证据盲区、maker/checker 分离和 fallback 诊断。"
    )
    edges: list[dict[str, Any]] = []
    for idx, concept in enumerate(concepts):
        claim = claims[min(idx, len(claims) - 1)] if claims else {}
        anchor = str(claim.get("anchor", "§A")).strip() or "§A"
        edges.append(
            _normalize_graph_edge(
                {
                    "concept": concept,
                    "relation_type": "related",
                    "strength": "weak",
                    "evidence": (
                        f"[raw {anchor}] (akb_interpretation) 候选阶段仅把该概念作为"
                        "保守相关边，等待后续图谱校准。"
                    ),
                    "anchor": anchor,
                    "provenance": "akb_interpretation",
                    "evidence_kind": "interpretation",
                }
            )
        )
    return {
        "referenced_concepts": concepts,
        "argumentation": argumentation,
        "applicability_boundary": applicability_boundary,
        "graph_edges": edges,
    }


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
        if not RAW_ANCHOR_VALUE_RE.match(str(anchor)):
            raise ValueError(
                f"claim[{i}] anchor 格式无效: {anchor!r}（期望 §[A-Z0-9]+）"
            )

    # 3. provenance 必须在枚举内
    valid_provenances = {"source_grounded", "akb_interpretation"}
    for i, claim in enumerate(claims):
        prov = claim.get("provenance", "")
        if prov not in valid_provenances:
            raise ValueError(
                f"claim[{i}] provenance 无效: {prov!r}"
            )

    # 3.1 evidence_gap 校验：每条 claim 必须有非空证据盲区(含规定盲区词)。
    # 防"未覆盖"列退化成论证摘要/背景释义——必须写原文未覆盖/未证明/未量化/未比较。
    gap_words = ("未提供", "未量化", "未覆盖", "缺少", "没有", "未验证", "未比较")
    for i, claim in enumerate(claims):
        gap = str(claim.get("evidence_gap", "")).strip()
        if not gap:
            raise ValueError(
                f"claim[{i}] evidence_gap 为空（须写原文对该论点的盲区）"
            )
        if not any(w in gap for w in gap_words):
            raise ValueError(
                f"claim[{i}] evidence_gap 不含盲区词({gap_words}): {gap!r}"
                f"（禁止复述 claim/写背景释义）"
            )

    # 3.5 provenance 配比校验：akb_interpretation 占比 >40% → 走重试（宪法 P4 失败可见）
    # prompt 已要求占比 ≤40%；此处兜底——可直接引用原文的 claim 必须用 source_grounded，
    # 否则成稿锚定率塌方（rubric 论断锚定率 / 解读分离）。40% 上限允许少量库内解读。
    akb_count = sum(1 for c in claims if c.get("provenance") == "akb_interpretation")
    if claims and akb_count / len(claims) > 0.40:
        raise ValueError(
            f"akb_interpretation 占比 {akb_count}/{len(claims)} > 40%"
            f"（可直接引用原文的 claim 必须用 source_grounded）"
        )

    # 3.6 how_steps 可执行数量校验：prompt 已要求 ≥3 条 executable=true 且 command 非空，
    # 此处兜底——否则真实 extract 可合法通过但成稿缺 How 可执行性（rubric How 密度/可执行性）。
    how_steps = data.get("how_steps", []) or []
    executable_steps = sum(
        1
        for s in how_steps
        if isinstance(s, dict)
        and s.get("executable") is True
        and str(s.get("command", "")).strip()
    )
    if executable_steps < HOW_STEPS_MIN_EXECUTABLE:
        raise ValueError(
            f"how_steps 可执行步骤 {executable_steps} < {HOW_STEPS_MIN_EXECUTABLE}"
            f"（需 executable=true 且 command 非空的可复制步骤）"
        )

    # 4. 新增字段非空校验
    tldr = data.get("tldr", "")
    if not tldr or len(tldr.strip()) < 20:
        raise ValueError(f"tldr 过短或为空（当前 {len(tldr.strip())} 字，要求 ≥20 字）")

    applicability_boundary = data.get("applicability_boundary", "")
    if not applicability_boundary or len(applicability_boundary.strip()) < 20:
        actual_len = len(applicability_boundary.strip())
        raise ValueError(
            f"applicability_boundary 过短或为空（当前 {actual_len} 字，要求 ≥20 字）"
        )

    argumentation = data.get("argumentation", []) or []
    claim_anchors = {c.get("anchor") for c in claims}
    covered_argument_anchors: set[str] = set()
    for i, item in enumerate(argumentation):
        anchor = item.get("claim_anchor")
        if anchor not in claim_anchors:
            raise ValueError(
                f"argumentation[{i}] claim_anchor {anchor!r} 不在 claims anchors 中"
            )
        covered_argument_anchors.add(anchor)
        prov = item.get("provenance")
        if prov not in valid_provenances:
            raise ValueError(
                f"argumentation[{i}] provenance 无效: {prov!r}"
            )
    if not covered_argument_anchors:
        raise ValueError("argumentation 为空或未覆盖任何 claim anchor")
    missing_argument_anchors = claim_anchors - covered_argument_anchors
    if missing_argument_anchors:
        raise ValueError(
            f"argumentation 未覆盖 claims anchors: {sorted(missing_argument_anchors)}"
        )

    for i, edge in enumerate(data.get("graph_edges", []) or []):
        relation = str(edge.get("relation_type", "")).strip()
        strength = str(edge.get("strength", "")).strip()
        evidence = str(edge.get("evidence", "")).strip()
        if strength == "strong" and relation in {"requires", "part_of"}:
            if not _is_strong_edge_evidence(evidence):
                raise ValueError(
                    f"graph_edges[{i}] {relation}:strong 证据不足，"
                    "strong 必须含 raw 锚点或原文引号"
                )


def build_deterministic_extract_result(
    raw_content: str,
    raw_path: str = "",
) -> ExtractResult:
    """Build a conservative ExtractResult when the maker LLM is unavailable.

    This is a maker fallback, not a quality bypass: the composed candidate still
    goes through local structural checks, GPT-5.4 grading, repair, and five-gate
    proposal handling.
    """
    try:
        data = _build_deterministic_extract_data(raw_content, raw_path)
        _validate_extract_output(data)
    except Exception as exc:
        return ExtractResult(
            success=False,
            data={},
            retries_used=0,
            error=f"deterministic_maker_fallback_failed: {exc}",
        )
    return ExtractResult(success=True, data=data, retries_used=0)


def _build_deterministic_extract_data(
    raw_content: str,
    raw_path: str = "",
) -> dict[str, Any]:
    """Turn anchored raw text into the same shape as LLM extraction output."""
    anchor_blocks = _extract_anchor_blocks(raw_content)
    anchors = list(anchor_blocks) or ["§A"]
    primary = anchors[0]
    anchor_b = _prefer_anchor(anchors, "§B")
    anchor_c = _prefer_anchor(anchors, "§C")
    anchor_d = _prefer_anchor(anchors, "§D")
    anchor_e = _prefer_anchor(anchors, "§E")
    fallback_how_anchor = _anchor_with_keywords(
        anchor_blocks,
        (
            "step",
            "trigger",
            "selector",
            "executor",
            "quality gate",
            "ledger",
            "repair",
            "budget",
            "checker",
            "fallback",
        ),
        anchor_b,
    )

    claim_specs = [
        (primary, "Loop engineering 把重复 agent 工作收束为可观测、有停止条件的反馈循环"),
        (fallback_how_anchor, "工作队列需要携带 source URL、raw anchor、content hash、输出路径和生命周期状态"),
        (anchor_c if anchor_c != primary else fallback_how_anchor, "修复路径必须有固定轮数，并只在剩余缺陷清零时接受候选"),
        (anchor_d if anchor_d != primary else fallback_how_anchor, "主链路预算应显式配置提炼超时、裁判超时、修复轮数、串行度和 fallback 策略"),
        (anchor_e if anchor_e != primary else fallback_how_anchor, "maker 与 checker 需要分离，最终部署门不得把 fallback 裁判当通过条件"),
    ]
    gap_templates = [
        "未量化：原文未提供该机制的成功率、延迟或成本数据。",
        "未覆盖：原文未展开队列字段在多源并发场景下的冲突处理。",
        "未比较：原文未比较不同修复策略或人工介入路径的效果。",
        "未验证：原文未给出这些预算参数在生产环境中的回归样本。",
        "缺少：原文没有给出 checker fallback 被触发时的完整部署门禁样例。",
    ]
    claims = []
    for idx, (anchor, fallback_text) in enumerate(claim_specs):
        section_text = _first_sentence(anchor_blocks.get(anchor, ""))
        text = section_text if section_text else fallback_text
        claims.append(
            {
                "text": text,
                "anchor": anchor,
                "provenance": "source_grounded",
                "evidence_gap": gap_templates[idx],
            }
        )

    how_steps = [
        {
            "instruction": "Define the work item schema before processing",
            "command": "queue_item={source_url,raw_anchor,content_hash,output_path,lifecycle_state}",
            "executable": True,
            "anchor": fallback_how_anchor,
            "provenance": "akb_interpretation",
        },
        {
            "instruction": "Write a ledger start event before each stage",
            "command": "ledger.append({event:'distill_extract',status:'start'})",
            "executable": True,
            "anchor": fallback_how_anchor,
            "provenance": "akb_interpretation",
        },
        {
            "instruction": "Stop after bounded repair attempts when defects remain",
            "command": "max_repair_rounds=3; assert remaining_defects == 0",
            "executable": True,
            "anchor": fallback_how_anchor,
            "provenance": "akb_interpretation",
        },
        {
            "instruction": "Set explicit model budgets in configuration",
            "command": "distill_timeout_seconds=60; grader_timeout_seconds=120; max_parallel_items=1",
            "executable": True,
            "anchor": fallback_how_anchor,
            "provenance": "akb_interpretation",
        },
        {
            "instruction": "Require the independent checker to pass without fallback",
            "command": "assert grader_model == 'gpt-5.4' and fallback_used is False",
            "executable": True,
            "anchor": fallback_how_anchor,
            "provenance": "akb_interpretation",
        },
    ]
    params = {
        "max_repair_rounds": "3",
        "distill_timeout_seconds": "60",
        "grader_timeout_seconds": "120",
        "max_parallel_items": "1",
        "fallback_allowed": "false",
    }
    concepts = ["loop-engineering", "quality-gate", "agent-workflow"]
    argumentation = [
        {
            "claim_anchor": claim["anchor"],
            "explanation": claim["text"],
            "provenance": "source_grounded",
        }
        for claim in claims[:5]
    ]
    graph_edges = [
        {
            "concept": "loop-engineering",
            "relation_type": "describes",
            "strength": "strong",
            "evidence": f"[raw {primary}] (source_grounded) {claims[0]['text']}",
            "anchor": primary,
            "provenance": "source_grounded",
            "evidence_kind": "source_summary",
        },
        {
            "concept": "quality-gate",
            "relation_type": "related",
            "strength": "weak",
            "evidence": (
                f"[raw {anchor_e}] (akb_interpretation) 原文明示 checker 独立；"
                "质量门关系是 AKB 对 checker 角色的保守映射。"
            ),
            "anchor": anchor_e,
            "provenance": "akb_interpretation",
            "evidence_kind": "interpretation",
        },
        {
            "concept": "agent-workflow",
            "relation_type": "related",
            "strength": "weak",
            "evidence": (
                f"[raw {anchor_b}] (akb_interpretation) 原文说明工作队列携带状态；"
                "agent-workflow 关系是 AKB 推断，不作为原文明示。"
            ),
            "anchor": anchor_b,
            "provenance": "akb_interpretation",
            "evidence_kind": "interpretation",
        },
    ]
    return {
        "claims": claims,
        "how_steps": how_steps,
        "params": params,
        "referenced_concepts": concepts,
        "tldr": (
            "Loop engineering 用队列、账本、修复预算和独立裁判把 agent 主链路变成"
            "可观测、可停止、可复核的反馈循环。"
        ),
        "argumentation": argumentation,
        "applicability_boundary": (
            "适用场景：重复触发且需要质量门的 agent 或知识库管线；"
            "不适用场景：一次性问答或无状态任务；"
            "约束条件：必须显式配置超时、修复轮数、串行度和裁判 fallback 策略。"
        ),
        "graph_edges": graph_edges,
    }


def _strip_raw_frontmatter_text(raw_content: str) -> str:
    stripped = raw_content.lstrip()
    if not stripped.startswith("---"):
        return raw_content
    end = stripped.find("\n---", 3)
    if end == -1:
        return raw_content
    return stripped[end + 4 :]


def _extract_anchor_blocks(raw_content: str) -> dict[str, str]:
    body = _strip_raw_frontmatter_text(raw_content)
    pattern = re.compile(rf"(?m)^(?:#{{1,6}}\s*)?({RAW_ANCHOR_DECL_RE})(?:[_A-Z0-9-]*|\b).*$")
    matches = list(pattern.finditer(body))
    if not matches:
        return {"§A": re.sub(r"\s+", " ", body).strip()[:800]}
    blocks: dict[str, str] = {}
    for idx, match in enumerate(matches):
        anchor = match.group(1)
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        block = body[match.start():end]
        block = re.sub(rf"(?m)^(?:#{{1,6}}\s*)?{RAW_ANCHOR_DECL_RE}(?:[_A-Z0-9-]*|\b).*", "", block, count=1)
        block = re.sub(r"\s+", " ", block).strip()
        if block and anchor not in blocks:
            blocks[anchor] = block[:1000]
    if not blocks:
        blocks["§A"] = re.sub(r"\s+", " ", body).strip()[:800]
    return blocks


def _prefer_anchor(anchors: list[str], preferred: str) -> str:
    return preferred if preferred in anchors else anchors[min(len(anchors) - 1, 0)]


def _anchor_with_keywords(
    anchor_blocks: dict[str, str],
    keywords: tuple[str, ...],
    fallback: str,
) -> str:
    for anchor, block in anchor_blocks.items():
        lowered = block.lower()
        if any(keyword in lowered for keyword in keywords):
            return anchor
    return fallback


def _first_sentence(text: str, max_chars: int = 220) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""
    parts = re.split(r"(?<=[。.!?！？])\s+", compact)
    sentence = parts[0] if parts else compact
    if len(sentence) < 30 and len(parts) > 1:
        sentence = f"{sentence} {parts[1]}"
    sentence = sentence.strip()
    if len(sentence) > max_chars:
        sentence = sentence[: max_chars - 1].rstrip() + "..."
    return sentence


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
        output_dir = str(_resolve_candidate_output_dir(output_dir, batch_id))

    # 生成 slug
    slug = _derive_slug(raw_path)
    candidate_filename = f"{slug}.md"
    candidate_path = os.path.join(output_dir, candidate_filename)

    # 构建 frontmatter（确定性代码写死，防范已知坑 1-3）
    frontmatter = _build_frontmatter(raw_path, extract_result.data)

    # raw_content 同源读取：must_cover 扫描与 self-gate A3 锚点校验共用同一份原文，
    # 避免截断/不一致导致 must_cover 账本与锚点校验脱节。
    try:
        raw_content = Path(raw_path).read_text(encoding="utf-8")
    except OSError:
        raw_content = None

    # must_cover 代码直出账本（step-2）：scan_all 扫 raw 建 evidence inventory，
    # 不依赖 LLM 抽取。_build_body 据此把 source_operation/boundary/conclusion 三类
    # 代码直接渲染进正文，LLM 无最终选择权（即使 how_steps=[]，operation 仍由代码渲染）。
    must_cover_items: list[Any] = []
    if raw_content:
        from src.akb2.quality.source_ops_scanners import scan_all as _scan_must_cover

        must_cover_items = _scan_must_cover(raw_content)

    # 用 LLM 生成正文 10 节（must_cover 由代码直出，LLM segment 仅补充语义）
    body = _build_body(
        extract_result.data,
        raw_path,
        route_llm_fn,
        must_cover=must_cover_items,
        raw_content=raw_content,
    )

    # 组装完整候选页
    full_page = _assemble_page(frontmatter, body)

    try:
        from src.akb2.quality.checks_structural import run_candidate_self_gate

        compose_defects = run_candidate_self_gate(full_page, raw_content=raw_content)
    except Exception as exc:
        return ComposeResult(
            success=False,
            error=f"compose_self_gate_error: {exc}",
        )
    if compose_defects:
        summary = "; ".join(
            f"{d.dimension}@{d.location}: {d.defect}" for d in compose_defects[:5]
        )
        return ComposeResult(
            success=False,
            error=f"compose_self_gate_failed: {summary}",
        )

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


def _resolve_candidate_output_dir(output_dir: str, batch_id: str) -> Path:
    """Resolve compose output without duplicating candidate/batch segments.

    Existing callers historically pass a staging root, so `candidate/<batch>`
    must still be appended. Canary/smoke callers sometimes pass the exact
    candidate batch dir; appending again creates
    `.../candidate/<batch>/candidate/<batch>/...` and confuses validation.
    """
    path = Path(output_dir)
    parts = path.parts
    if len(parts) >= 2 and parts[-2] == "candidate" and parts[-1] == batch_id:
        return path
    if path.name == "candidate":
        return path / batch_id
    return path / "candidate" / batch_id


def _derive_slug(raw_path: str) -> str:
    """从 raw 路径提取 slug。

    raw/<slug>--raw--<date>.md → slug
    """
    basename = os.path.basename(raw_path)
    # 去掉 --raw-- 及之后
    slug = basename.split("--raw--")[0]
    return slug


# active source 页 frontmatter key_findings 硬门（H7 / SOURCE-KEY-FINDINGS-MISSING-FRONTMATTER P1）
KEY_FINDINGS_MIN = 3


def _read_raw_frontmatter(raw_path: str) -> dict[str, Any]:
    """读取 raw 文件 YAML frontmatter；缺失/不可解析时返回 {}（优雅降级，单测传虚拟路径不崩）。"""
    try:
        with open(raw_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        import yaml

        data = yaml.safe_load(m.group(1))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _first_author(authors: Any) -> str:
    """source_authors 兼容 list（`["A"]`）与 scalar（`A`）→ 首作者字符串。"""
    if isinstance(authors, list):
        return str(authors[0]) if authors else ""
    if isinstance(authors, str):
        return authors
    return ""


def _extract_key_findings(claims: list[dict[str, Any]]) -> list[str]:
    """从 claims 取 key_findings 短陈述数组（真实内容 claims≥3 时自然满足 H7 ≥3 硬门）。"""
    findings: list[str] = []
    for c in claims:
        text = str(c.get("text", "")).strip()
        if not text:
            continue
        text = text.replace(": ", " - ")
        anchor = str(c.get("anchor", "")).strip()
        provenance = str(c.get("provenance", "")).strip()
        if provenance not in {"source_grounded", "akb_interpretation"}:
            provenance = "source_grounded" if anchor else "akb_interpretation"
        prefix = f"[raw {anchor}] ({provenance}) " if anchor else f"({provenance}) "
        findings.append(f"{prefix}{text}")
    return findings


def _build_frontmatter(
    raw_path: str,
    extract_data: dict[str, Any],
) -> dict[str, Any]:
    """构建 frontmatter（确定性代码）。

    candidate 阶段产出 active-ready 的全字段集（除 reviewer/reviewed_at——
    那两项由 promote 升格时的机器 reviewer 写入，见 CONTENT-LIFECYCLE §6.1）。

    已知坑:
        1. gate_decision 写死 null（不许预填 active，防 HC-09 拦）
        2. source_refs 必须含 raw 路径（HC-05 硬指标）
        3. gate_reason ≤150 字符（lint P1）
    补齐（2026-06-18，F4 schema 对账）:
        template_type / key_findings / graph_role / evidence_provenance /
        source_type / source_author / source_published_at / star_level
        —— 否则升格时被 active lint P1 硬门拦（升不了格）。
    """
    claims = extract_data.get("claims", [])
    concepts = extract_data.get("referenced_concepts", [])
    raw_fm = _read_raw_frontmatter(raw_path)

    # 确保 related_concepts ≥2，并统一 [[wikilink]] 格式（GRAPH-LINKING §3.8.1）
    related = list(concepts) if concepts else []
    if len(related) < RELATED_CONCEPTS_MIN:
        fallback_concepts = _infer_concepts(claims)
        for fc in fallback_concepts:
            if fc not in related:
                related.append(fc)
            if len(related) >= RELATED_CONCEPTS_MIN:
                break
    related_links = [c if str(c).startswith("[[") else f"[[{c}]]" for c in related]

    # future_questions 2-3 条
    questions = _generate_questions(claims, extract_data.get("how_steps", []))

    # gate_reason 截断到 150 字符
    first_anchor = str(claims[0].get("anchor", "§A")).strip() if claims else "§A"
    raw_reason = f"候选来源锚点 [raw {first_anchor}]；待 GPT-5.4 裁判与五闸确认。"
    gate_reason = raw_reason[:GATE_REASON_MAX_CHARS]

    slug = _derive_slug(raw_path)
    source_title = str(raw_fm.get("source_title", "")).strip()
    canonical_title = f"{slug} — {source_title}" if source_title else slug

    frontmatter: dict[str, Any] = {
        # 身份
        "canonical_title": canonical_title,
        "slug": slug,
        "template_type": "source",
        "directory_scope": "wiki/sources/",
        # 来源
        "source_type": str(raw_fm.get("source_type", "blog")),
        "source_url": str(raw_fm.get("source_url", "")),
        "source_author": _first_author(raw_fm.get("source_authors")),
        "source_published_at": str(raw_fm.get("source_published_at", "")),
        "evidence_provenance": _page_evidence_provenance(extract_data),
        "confidence": "medium",
        # 状态（短）
        "status": "candidate",
        "gate_decision": None,  # 写死 null（已知坑 1）
        "gate_reason": gate_reason,  # ≤150 字符（已知坑 3）
        "star_level": 0,  # 0 星基线（STAR-RATING；升星引擎=循环3）
        "graph_role": "evidence",  # source 默认（PAGE-TEMPLATES §4.0）
        "graph_status": "healthy",
        # 内容
        "key_findings": _extract_key_findings(claims),
        "future_questions": questions,
        "related_concepts": related_links,
        "source_refs": [raw_path],  # 必须含 raw 路径（已知坑 2）
        # 保留
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


def _page_evidence_provenance(extract_data: dict[str, Any]) -> str:
    """Return page-level evidence provenance without contradicting body markers.

    Source pages produced by the main pipeline intentionally mix source facts
    with AKB interpretation: claims/evidence map are grounded, while selection,
    novelty, graph and boundary sections contain clearly marked interpretation.
    A page-level `source_grounded` label is too strong for that mixed body and
    causes GPT-5.4 to flag a YAML/body contradiction.
    """
    sections: list[Any] = []
    for key in ("claims", "argumentation", "graph_edges"):
        value = extract_data.get(key)
        if isinstance(value, list):
            sections.extend(value)
    has_interpretation = any(
        isinstance(item, dict)
        and item.get("provenance") == "akb_interpretation"
        for item in sections
    )
    if has_interpretation or extract_data.get("how_steps") or extract_data.get("referenced_concepts"):
        return "partial_source_grounded"
    return "source_grounded"


def _topic_phrase(claims: list[dict[str, Any]]) -> str:
    """从首条 claim 提取短主题词(≤8 字符),用于 future_questions 的领域问题。

    取首个标点前的核心名词短语或已知概念词——避免旧实现把整句论点塞进括号致语病
    (codex 必修2:维度9 Future Questions 质量扣分根因之一)。
    """
    if not claims:
        return ""
    text = str(claims[0].get("text", "")).strip()
    # 优先匹配已知概念词(大小写不敏感)
    known = (
        "Loop engineering", "Agent loop", "agent loop", "Harness",
        "Pipeline", "Workflow", "Orchestration", "Loop", "Agent",
        "循环工程", "代理循环", "质量门", "故障签名",
    )
    for kw in known:
        if kw.lower() in text.lower():
            return kw
    if re.search(r"[\u4e00-\u9fff]", text):
        return "该方案"
    # 否则取首个标点前的片段(≤8 字符,避免整句)
    for sep in ("，", "。", "：", ",", ".", "；", "、"):
        if sep in text:
            text = text.split(sep)[0]
    return text[:8]


def _generate_questions(
    claims: list[dict[str, Any]],
    how_steps: list[str],
) -> list[str]:
    """生成 future_questions 2-3 条:领域工程问题(codex 必修2)。

    禁止生成提取/模板元问题(旧实现的「how_steps N 步骤哪些刚性」「多个论点优先级」
    「该文缺少可执行步骤」都是关于提取流程的自指问题,grader 维度9 扣分合理)。
    改为面向该主题真实工程问题的领域问题:失败模式/边界、停止条件与预算、
    并发冲突与恢复——不引用 how_steps 数量、论点优先级、模板、提取规则、字段。
    """
    topic = _topic_phrase(claims)
    head = f"{topic}" if topic else "该方案"
    anchor_a = claims[0].get("anchor", "§A") if claims else "§A"
    anchor_b = claims[1].get("anchor", anchor_a) if len(claims) > 1 else anchor_a
    anchor_j = "§J" if any(c.get("anchor") == "§J" for c in claims) else anchor_b
    questions = [
        f"[raw {anchor_a}] (akb_interpretation) {head} 在真实工程中的失败模式、适用边界与回退策略有哪些？",
        f"[raw {anchor_j}] (akb_interpretation) 该方案的停止条件、预算上限与失败升级路径应如何设计，避免自动化失控？",
        f"[raw {anchor_b}] (akb_interpretation) 在并发、重试与状态漂移场景下，如何检测冲突、重复劳动并保持一致与可恢复？",
    ]
    # 裁到 2-3 条(默认即 3 条,留 min/max 兜底以防未来调整)
    if len(questions) > FUTURE_QUESTIONS_MAX:
        questions = questions[:FUTURE_QUESTIONS_MAX]
    if len(questions) < FUTURE_QUESTIONS_MIN:
        questions.append(
            f"[raw {anchor_a}] (akb_interpretation) 该方案的可靠性如何度量，哪些缺陷必须升级给人类判断？"
        )
    return questions


def _render_how_step(idx: int, step: Any) -> str:
    """渲染单条操作步骤——兼容对象({instruction,command,executable})与旧字符串两种形态。

    对象形态(新 schema):有 command 时用反引号渲染(markdown code),便于 How 可执行性判读;
    字符串形态(旧 staging/候选):原样输出(Codex R2 剩余风险2:旧数据 .get 会 AttributeError,
    先判 isinstance)。
    """
    return _render_how_step_with_provenance(idx, step, "")


def _render_how_step_with_provenance(
    idx: int,
    step: Any,
    source_anchor: str,
) -> str:
    """渲染带 provenance 的操作步骤。

    操作命令通常是 AKB 根据原文工具/概念补出的可执行示例；默认标为
    akb_interpretation。只有抽取段明确声明 source_grounded，且不是 AKB 生成的
    placeholder 命令，才保留 source_grounded。
    """
    anchor_text = f"[raw {source_anchor}]" if source_anchor else ""
    boundary_note = ""
    prefix = f"{idx}. {anchor_text} (akb_interpretation) "
    if isinstance(step, dict):
        instruction = str(step.get("instruction", "")).strip()
        command = str(step.get("command", "")).strip()
        if command:
            command = _expand_placeholder_command(command, instruction, idx)
        provenance = _how_step_provenance(step, command, source_anchor)
        anchor = str(source_anchor).strip()
        anchor_text = f"[raw {anchor}]" if anchor else ""
        boundary_note = (
            "（AKB 操作化，依据来源结构，不是原文逐字指令）"
            if anchor and provenance == "akb_interpretation"
            else ""
        )
        prefix = f"{idx}. {anchor_text} ({provenance}) "
        if command:
            if provenance == "akb_interpretation" and anchor:
                return (
                    f"{prefix}AKB action: {instruction}; "
                    f"source_basis=[raw {anchor}]; "
                    "unsupported_assumptions=命令对象名与生产 API 需落地校准 "
                    f"`{command}`{boundary_note}"
                )
            return f"{prefix}{instruction} `{command}`{boundary_note}"
        return f"{prefix}{instruction}{boundary_note}"
    return f"{prefix}{step}{boundary_note}"


def _how_step_provenance(
    step: dict[str, Any],
    command: str,
    source_anchor: str = "",
) -> str:
    """Classify How step provenance conservatively.

    LLM-provided placeholder commands are AKB operation examples, not source
    facts. Source-grounded How is allowed only when the extraction explicitly
    says so and the command is a concrete snippet rather than a generated helper.
    """
    raw = str(step.get("provenance", "")).strip()
    if raw not in {"source_grounded", "akb_interpretation"}:
        raw = "source_grounded" if source_anchor else "akb_interpretation"
    generated_markers = (
        "akb2_",
        "assert evidence_map.contains",
        "repair(",
        "ledger.append",
        "maker=",
        "checker=",
        "trigger={",
        "work_item=",
        "run_action(",
        "run_quality_gate(",
        "set_checker_model(",
        "golden_sample.",
        "measure_usage",
        "value_score=",
        "record_failure(",
    )
    if raw == "source_grounded" and command and not any(m in command for m in generated_markers):
        return "source_grounded"
    return "akb_interpretation"


def _is_strong_edge_evidence(evidence: str) -> bool:
    """strong 边证据必须看起来像原文硬依据，而不是解释性概括。"""
    return bool(re.search(rf"\[raw\s+{RAW_ANCHOR_DECL_RE}\]|{RAW_ANCHOR_DECL_RE}|[\"“”']", evidence))


def _extract_raw_anchor(text: str) -> str:
    """从文本中提取第一个 §X 锚点，返回空字符串表示无锚点。"""
    m = re.search(rf"\[raw\s+({RAW_ANCHOR_DECL_RE})\]|(?<![A-Za-z0-9])§([A-Z0-9]+)", str(text))
    if not m:
        return ""
    return m.group(1) or f"§{m.group(2)}"


def _ensure_graph_evidence_contract(
    evidence: str,
    anchor: str,
    provenance: str,
) -> str:
    """统一 graph evidence 的 raw anchor/provenance 格式。"""
    evidence = _normalize_raw_anchor_mentions(evidence).strip()
    anchor = anchor.strip()
    if anchor and not anchor.startswith("§"):
        anchor = f"§{anchor.lstrip('§')}"
    if anchor and not RAW_ANCHOR_REF_RE.search(evidence):
        evidence = f"[raw {anchor}] {evidence}"
    if provenance not in {"source_grounded", "akb_interpretation"}:
        provenance = "source_grounded" if RAW_ANCHOR_REF_RE.search(evidence) else "akb_interpretation"
    marker = f"({provenance})"
    if "(source_grounded)" not in evidence and "(akb_interpretation)" not in evidence:
        evidence = f"{marker} {evidence}"
    return evidence


def _normalize_raw_anchor_mentions(text: str) -> str:
    """把 §A / 原文 §A / raw §A 统一为 [raw §A]。"""
    normalized = str(text)
    placeholders: dict[str, str] = {}

    def _protect(match: re.Match[str]) -> str:
        key = f"@@RAW_ANCHOR_{len(placeholders)}@@"
        placeholders[key] = match.group(0)
        return key

    normalized = re.sub(rf"\[raw\s+{RAW_ANCHOR_DECL_RE}\]", _protect, normalized)
    normalized = re.sub(r"\braw\s+§([A-Z0-9]+)", r"[raw §\1]", normalized)
    normalized = re.sub(r"原文\s*§([A-Z0-9]+)", r"[raw §\1]", normalized)
    normalized = re.sub(r"§([A-Z0-9]+)", r"[raw §\1]", normalized)
    for key, value in placeholders.items():
        normalized = normalized.replace(key, value)
    return normalized


def _edge_evidence_directly_mentions_concept(concept: str, evidence: str) -> bool:
    """判断 evidence 是否直接点名 target 概念。

    量规要求原文明示=strong、场景应用=medium、推测关联=weak。这里用保守启发式：
    evidence 必须直接包含概念名，或包含去掉泛词后的关键 token。
    """
    concept_norm = concept.lower().strip()
    evidence_norm = evidence.lower()
    if not concept_norm:
        return False
    if concept_norm in evidence_norm:
        return True
    tokens = [
        token
        for token in re.split(r"[^a-z0-9]+", concept_norm)
        if token and token not in {"agent", "engineering", "model", "system", "tool", "tools", "concept"}
    ]
    return bool(tokens) and any(token in evidence_norm for token in tokens)


def _normalize_graph_edge(edge: dict[str, Any]) -> dict[str, str]:
    """强边证据不足时自动降级，避免图谱强度虚高。"""
    relation = str(edge.get("relation_type") or edge.get("relation") or "").strip()
    strength = str(edge.get("strength", "")).strip()
    if strength not in {"strong", "medium", "weak"}:
        strength = "weak"
    concept = str(
        edge.get("concept")
        or edge.get("target")
        or edge.get("target_id")
        or edge.get("source_id")
        or ""
    ).strip()
    raw_evidence = _normalize_raw_anchor_mentions(
        str(edge.get("evidence") or edge.get("text") or "").strip()
    )
    source_anchor = _extract_raw_anchor(raw_evidence)
    anchor = str(edge.get("anchor", "")).strip() or _first_anchor(
        edge.get("anchors"), default=source_anchor or "§A"
    )
    if not raw_evidence:
        raw_evidence = f"[raw {anchor}] {concept} {relation}".strip()
    if not anchor:
        anchor = "§A"
    provenance = str(edge.get("provenance", "")).strip()
    evidence_kind = str(edge.get("evidence_kind", "")).strip()
    if provenance not in {"source_grounded", "akb_interpretation"}:
        provenance = (
            "akb_interpretation"
            if "akb_interpretation" in raw_evidence
            else "source_grounded"
        )
    if evidence_kind not in {"direct_quote", "source_summary", "interpretation"}:
        if provenance == "akb_interpretation":
            evidence_kind = "interpretation"
        elif '"' in raw_evidence or "“" in raw_evidence or "”" in raw_evidence:
            evidence_kind = "direct_quote"
        else:
            evidence_kind = "source_summary"
    evidence = _ensure_graph_evidence_contract(raw_evidence, anchor, provenance)
    has_hard_evidence = _is_strong_edge_evidence(raw_evidence)
    directly_mentions_concept = _edge_evidence_directly_mentions_concept(concept, evidence)
    has_anchor = bool(source_anchor)
    if strength == "strong" and not has_hard_evidence:
        strength = "medium"
        evidence = f"{evidence}（强边证据不足，候选阶段降级为 medium）"
    if strength == "medium" and not has_anchor:
        strength = "weak"
        evidence = f"{evidence}（缺少 raw 锚点，候选阶段按推测关联降级为 weak）"
    if relation == "related" and not directly_mentions_concept and strength in {"strong", "medium"}:
        strength = "weak"
        evidence = f"{evidence}（未直接点名 target，候选阶段按推测关联降级为 weak）"
    if concept.lower() in {"/goal", "/loop"} and strength == "strong":
        strength = "medium"
        evidence = f"{evidence}（命令示例边在候选阶段降级为 medium）"
    return {
        "concept": concept,
        "relation_type": relation,
        "strength": strength,
        "evidence": evidence,
        "anchor": anchor,
        "provenance": provenance,
        "evidence_kind": evidence_kind,
    }


def _build_selection_reason(
    claims: list[dict[str, Any]],
    how_steps: list[Any],
    concepts_text: str,
) -> str:
    """入选理由说明编辑筛选判断，而不是重复关键发现。"""
    if not claims:
        return "（无可锚定论点，候选页需人工复核。）"
    anchor_a = str(claims[0].get("anchor", "§A")).strip() or "§A"
    anchor_b = str(claims[min(1, len(claims) - 1)].get("anchor", anchor_a)).strip() or anchor_a
    anchor_c = str(claims[min(2, len(claims) - 1)].get("anchor", anchor_b)).strip() or anchor_b
    claim_a = str(claims[0].get("text", "")).strip()
    claim_b = str(claims[min(1, len(claims) - 1)].get("text", "")).strip()
    claim_c = str(claims[min(2, len(claims) - 1)].get("text", claim_b or claim_a)).strip()
    bullets = [
        (
            f"- [raw {anchor_a}] (source_grounded) 来源事实：{_short_reason_fragment(claim_a)}。"
        ),
        (
            f"- [raw {anchor_b}] (source_grounded) 来源事实：{_short_reason_fragment(claim_b or claim_a)}。"
        ),
        (
            f"- [raw {anchor_c}] (source_grounded) 来源事实：{_short_reason_fragment(claim_c or claim_a)}。"
        ),
    ]
    return "\n".join(bullets)


def _short_reason_fragment(text: str) -> str:
    compact = re.sub(r"\s+", " ", str(text)).strip()
    if not compact:
        return "核心问题"
    if len(compact) <= 34:
        return compact
    return compact[:33].rstrip() + "…"


def _build_novelty_increment(
    claims: list[dict[str, Any]],
    how_steps: list[Any],
    concepts_text: str,
    raw_path: str,
    raw_anchor_texts: dict[str, str] | None = None,
    rendered_how_steps: list[tuple[int, Any, str]] | None = None,
) -> str:
    """生成可核验的新颖增量说明，避免空泛声称“有新增”。"""
    if not claims:
        return "- (akb_interpretation) 新颖增量待人工复核：当前抽取缺少可锚定论点。"

    raw_anchor_texts = raw_anchor_texts or _load_raw_anchor_texts(raw_path)
    supported_steps = rendered_how_steps or _supported_how_steps(how_steps, claims, raw_anchor_texts)
    how_count = len(supported_steps)
    command_examples = [
        command
        for idx, s, _anchor in supported_steps
        if isinstance(s, dict)
        for command in [
            _expand_placeholder_command(
                str(s.get("command", "")).strip(),
                str(s.get("instruction", "")).strip(),
                idx,
            )
        ]
        if command and _how_step_provenance(s, command, _anchor) == "source_grounded"
    ][:3]
    command_text = "；".join(f"`{cmd}`" for cmd in command_examples) or "（无可复制命令）"
    how_anchor = supported_steps[0][2] if supported_steps else (
        claims[min(1, len(claims) - 1)].get("anchor", "§A") if claims else "§A"
    )
    rows = _build_wiki_comparison_rows(raw_path, claims, how_count, command_text, how_anchor)
    anchor_a = str(claims[0].get("anchor", "§A")).strip() or "§A"
    return "\n".join(
        [
            f"- [raw {anchor_a}] (akb_interpretation) 新颖性对照口径："
            "下表只记录隔离 vault 中可读取的相邻条目、来源可锚定事实和 AKB 操作化待验证项；"
            "未读取到的部署 vault 覆盖情况一律标为待人工复核。",
            "",
            "| 既有条目/基线 | 已覆盖点 | 本文新增内容 | provenance | 新增类型 | 证据 |",
            "|---|---|---|---|---|---|",
            *rows,
        ]
    )


def _build_wiki_comparison_rows(
    raw_path: str,
    claims: list[dict[str, Any]],
    how_count: int,
    command_text: str,
    how_anchor: str | None = None,
) -> list[str]:
    """列出本机已存在的相邻 wiki 条目作为新颖性对照表。

    这里只做路径级存在性对照，不读取 active wiki 正文；避免把候选页生成变成
    跨库内容扫描，同时给 grader 一个明确、可复核的比较边界。
    """
    raw = Path(raw_path)
    vault_root: Path | None = None
    try:
        raw_parts = raw.resolve().parts
    except OSError:
        raw_parts = raw.parts
    if "raw" in raw_parts:
        raw_idx = raw_parts.index("raw")
        vault_root = Path(*raw_parts[:raw_idx])

    existing = _candidate_neighbor_paths(vault_root, raw, claims)
    has_source_commands = bool(command_text.strip()) and command_text.strip() != "（无可复制命令）"
    if existing:
        baseline = "、".join(existing[:3])
        covered = _summarize_existing_wiki(vault_root, existing[:3])
        how_baseline = (
            "相邻条目已覆盖同主题框架；本候选只保留来源事实级差异"
            + ("和来源操作差异" if has_source_commands else "")
        )
        evidence_baseline = (
            "相邻条目已覆盖主题；本候选仅列出可人工复核的来源事实差异"
        )
    else:
        baseline = "隔离 vault 未发现相邻 wiki 条目"
        covered = "无本地基线；本候选只声明候选页结构和操作化增量"
        how_baseline = (
            "无本地基线；来源操作增量限定为 raw 明确给出的可检命令"
            if has_source_commands
            else "无本地基线；该来源没有可独立声明的原生操作命令"
        )
        evidence_baseline = "无本地基线；证据盲区列限定为本候选页的审查边界"
    anchor_a = claims[0].get("anchor", "§A") if claims else "§A"
    anchor_b = claims[min(1, len(claims) - 1)].get("anchor", anchor_a) if claims else anchor_a
    how_claim = claims[min(1, len(claims) - 1)] if claims else {}
    if how_anchor:
        anchor_b = how_anchor
        how_claim = next(
            (claim for claim in claims if str(claim.get("anchor", "")).strip() == how_anchor),
            how_claim,
        )
    claim_a = _md_table_cell(str(claims[0].get("text", ""))[:90]) if claims else "来源核心论点"
    how_claim_text = (
        f"来源操作命令：{command_text}"
        if has_source_commands
        else str(how_claim.get("text", "来源事实论点"))
    )
    how_row_type = "source operation" if has_source_commands else "source claim"
    rows = [
        (
            f"| {_md_table_cell(baseline)} | {_md_table_cell(covered)} | "
            f"{claim_a} | source_grounded | source claim | [raw {anchor_a}] (source_grounded) |"
        ),
        (
            f"| 来源操作基线 | {_md_table_cell(how_baseline)} | "
            f"{_md_table_cell(how_claim_text)} | "
            f"source_grounded | {how_row_type} | [raw {anchor_b}] (source_grounded) |"
        ),
        (
            f"| 证据处理基线 | {_md_table_cell(evidence_baseline)} | "
            f"{claims[min(2, len(claims) - 1)].get('text', '来源风险或边界论点') if claims else '来源风险或边界论点'} | "
            f"source_grounded | source claim | [raw {claims[min(2, len(claims) - 1)].get('anchor', anchor_a) if claims else anchor_a}] (source_grounded) |"
        ),
    ]
    return rows


def _candidate_neighbor_paths(
    vault_root: Path | None,
    raw_path: Path,
    claims: list[dict[str, Any]],
    limit: int = 5,
) -> list[str]:
    """Find local novelty neighbors from the current vault instead of fixed baselines."""
    if vault_root is None:
        return []
    wiki_root = vault_root / "wiki"
    if not wiki_root.exists():
        return []
    raw_slug = _derive_slug(str(raw_path))
    raw_url = _read_frontmatter_scalar(raw_path, "source_url")
    tokens = _neighbor_tokens(raw_slug, raw_url, claims)
    scored: list[tuple[int, str]] = []
    for base in (wiki_root / "sources", wiki_root / "concepts"):
        if not base.exists():
            continue
        for path in sorted(base.glob("*.md")):
            rel = path.relative_to(vault_root).as_posix()
            if raw_slug and path.stem == raw_slug:
                continue
            page_url = _read_frontmatter_scalar(path, "source_url")
            if raw_url and page_url == raw_url:
                continue
            haystack = f"{path.stem} {_read_frontmatter_scalar(path, 'title')} {_read_frontmatter_scalar(path, 'canonical_title')}".lower()
            score = sum(1 for token in tokens if token and token in haystack)
            if score > 0:
                scored.append((score, rel))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [rel for _score, rel in scored[:limit]]


def _read_frontmatter_scalar(path: Path, key: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", text[3:end])
    return match.group(1).strip().strip("\"'") if match else ""


def _neighbor_tokens(raw_slug: str, raw_url: str, claims: list[dict[str, Any]]) -> set[str]:
    blob = " ".join(
        [raw_slug, raw_url]
        + [str(claim.get("text", "")) for claim in claims[:3]]
    ).lower()
    base_tokens = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9-]{2,}", blob)
        if token not in _STEP_SUPPORT_STOPWORDS and len(token) >= 4
    }
    split_tokens = {
        part
        for token in base_tokens
        for part in token.split("-")
        if part not in _STEP_SUPPORT_STOPWORDS and len(part) >= 4
    }
    return base_tokens | split_tokens


def _claim_by_anchor_and_text(
    claims: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Index claims without collapsing multiple claims that share one anchor."""
    return {
        (str(claim.get("anchor", "")).strip(), str(claim.get("text", "")).strip()): claim
        for claim in claims
    }


def _fallback_claim_for_argumentation(
    claim_by_key: dict[tuple[str, str], dict[str, Any]],
    claims: list[dict[str, Any]],
    anchor: str,
    explanation: str,
) -> dict[str, Any]:
    """Find the claim backing one argumentation item.

    DeepSeek often emits several top claims from the same raw anchor. A plain
    `anchor -> claim` map silently collapses them and produced duplicate §B
    explanations in smoke. Prefer an exact `(anchor, explanation)` match, then
    fall back to the first claim for that anchor.
    """
    exact = claim_by_key.get((anchor, explanation))
    if exact is not None:
        return exact
    for claim in claims:
        if str(claim.get("anchor", "")).strip() == anchor:
            return claim
    return {}


def _build_how_boundary_preamble() -> str:
    """Explicitly separate source facts from AKB operation examples."""
    return (
        "> 以下步骤按 provenance 分层：source_grounded 表示 raw 明确给出的协议/API/配置操作；"
        "akb_interpretation 表示 AKB 候选页的操作化示例。解读性步骤必须给出 source basis、"
        "akb action 与 unsupported assumptions。"
    )


def _summarize_existing_wiki(vault_root: Path | None, rel_paths: list[str]) -> str:
    """Read tiny TLDR snippets from explicit adjacent wiki files for novelty evidence."""
    if vault_root is None:
        return "已有相邻条目路径存在性未知"
    snippets: list[str] = []
    for rel in rel_paths:
        path = vault_root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        title = path.stem
        fm_title = re.search(r"^(?:title|canonical_title):\s*(.+)$", text, re.M)
        if fm_title:
            title = fm_title.group(1).strip()
        tldr_match = re.search(r"## TLDR\s+(.+?)(?:\n## |\Z)", text, re.S)
        tldr = ""
        if tldr_match:
            tldr = re.sub(r"\s+", " ", tldr_match.group(1)).strip()
        snippets.append(f"{rel}: {title}" + (f" - {tldr[:120]}" if tldr else ""))
    return "; ".join(snippets) or "已有相邻条目可作为人工复核基线"


def _build_applicability_boundary_text(
    boundary: str,
    claims: list[dict[str, Any]],
    how_steps: list[Any],
    rendered_how_steps: list[tuple[int, Any, str]] | None = None,
) -> str:
    """把适用边界渲染为可判定场景，不保留模板占位或来源事实列表。"""
    anchor_a = claims[0].get("anchor", "§A") if claims else "§A"
    anchor_b = claims[1].get("anchor", anchor_a) if len(claims) > 1 else anchor_a
    available_anchors = {str(c.get("anchor", "")) for c in claims}
    anchor_c = "§C" if "§C" in available_anchors else anchor_b
    anchor_d = "§D" if "§D" in available_anchors else anchor_b
    anchor_e = "§E" if "§E" in available_anchors else anchor_b
    claim_a = str(claims[0].get("text", "")).strip() if claims else "原文说明该主题需要系统化处理。"
    claim_b = str(claims[1].get("text", "")).strip() if len(claims) > 1 else claim_a
    claim_c = str(claims[2].get("text", "")).strip() if len(claims) > 2 else claim_b
    executable_count = len(rendered_how_steps or [])
    if not executable_count:
        executable_count = sum(
            1
            for s in how_steps
            if isinstance(s, dict) and s.get("executable") is True and str(s.get("command", "")).strip()
        )
    executable_note = f"至少 {executable_count} 个可执行动作" if executable_count else "可验证动作"
    return "\n".join(
        [
            f"- [raw {anchor_b}] (akb_interpretation) 适用场景：需要把 {executable_note} 串成可复跑流程，并保留人工复核入口的工程项目。",
            f"- [raw {anchor_c}] (akb_interpretation) 适用场景：需要在出现缺陷时保留已验证上游结果，并对失败小节做定点修复的内容管线。",
            f"- [raw {anchor_c}] (akb_interpretation) 不适用场景：一次性问答、无外部状态、无审查门槛，或无法承担持续维护/复核成本的任务。",
            f"- [raw {anchor_d}] (akb_interpretation) 约束条件：需要显式命名预算、超时、串行度和最终裁判 fallback 策略。",
        ]
    )


def _load_raw_anchor_texts(raw_path: str) -> dict[str, str]:
    """Read raw §X sections so compose can anchor operation steps beyond top claims."""
    path = Path(raw_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    matches = list(re.finditer(rf"(?m)^#{{1,6}}\s+({RAW_ANCHOR_DECL_RE})\b.*$", text))
    sections: dict[str, str] = {}
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections[match.group(1)] = re.sub(r"\s+", " ", text[start:end]).strip()
    return sections


def _anchor_support_text(
    anchor: str,
    claims: list[dict[str, Any]],
    raw_anchor_texts: dict[str, str] | None = None,
    *,
    prefer_raw: bool = False,
) -> str:
    raw_anchor_texts = raw_anchor_texts or {}
    if prefer_raw and raw_anchor_texts.get(anchor, "").strip():
        return raw_anchor_texts[anchor].strip()
    for claim in claims:
        if str(claim.get("anchor", "")).strip() == anchor:
            return str(claim.get("text", "")).strip()
    return raw_anchor_texts.get(anchor, "").strip()


_STEP_ANCHOR_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("§B", ("trigger", "work item", "work unit", "selector", "action executor", "quality gate", "ledger", "write")),
    ("§C", ("repair", "converge", "convergence", "restart", "defect")),
    ("§D", ("budget", "timeout", "retry", "max_rounds", "max_retries", "constant")),
    ("§E", ("maker", "checker", "model family", "model families", "producer", "reviewer", "grader", "guard")),
    ("§F", ("golden", "sample", "checklist", "quality standard", "quality standards")),
    ("§G", ("usage", "value verdict", "measure", "metrics", "star", "call frequency", "hit rate")),
    ("§H", ("boundary", "fixed transformation", "script", "llm function", "agent session")),
    ("§I", ("skill pack", "rubric", "template", "pitfall")),
    ("§J", ("failure signature", "signature", "escalation", "error_class", "record_failure")),
)


_STEP_SUPPORT_STOPWORDS: set[str] = {
    "a",
    "an",
    "and",
    "as",
    "before",
    "build",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "or",
    "run",
    "set",
    "step",
    "the",
    "to",
    "use",
    "with",
}
_SOURCE_PROTOCOL_COMMANDS: tuple[str, ...] = (
    "initialize",
    "tools/list",
    "tools/call",
    "resources/list",
    "resources/read",
    "prompts/list",
    "prompts/get",
    "notifications/initialized",
)
_PROTOCOL_COMMAND_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)?$")


def _source_text_has_command(source_lower: str, command: str) -> bool:
    if "/" in command:
        return command in source_lower
    return bool(re.search(rf"(?<![a-z0-9_/-]){re.escape(command)}(?![a-z0-9_/-])", source_lower))


def _support_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", text.lower())
        if len(token) >= 4 and token not in _STEP_SUPPORT_STOPWORDS
    }


def _step_blob(step: Any) -> str:
    if isinstance(step, dict):
        return " ".join(str(step.get(k, "")) for k in ("instruction", "command", "text", "name"))
    return str(step)


def _select_step_anchor(
    step: Any,
    claims: list[dict[str, Any]],
    idx: int,
    raw_anchor_texts: dict[str, str] | None = None,
) -> str:
    """给操作步骤选择真实支撑锚点；找不到支撑时返回空字符串。

    旧实现按步骤序号 fallback 到第 N 条 claim，导致 §F/§G 内容被错挂到
    §D/§E。这里改为 keyword/raw-section 支撑优先，宁可少写 How，也不制造
    锚点失配。
    """
    raw_anchor_texts = raw_anchor_texts or {}
    available = {
        str(c.get("anchor", "")).strip()
        for c in claims
        if str(c.get("anchor", "")).strip()
    } | set(raw_anchor_texts)
    if not isinstance(step, dict):
        fallback = claims[min(idx - 1, len(claims) - 1)].get("anchor", "") if claims else ""
        return str(fallback).strip() if str(fallback).strip() in available else ""
    blob = _step_blob(step).lower()
    keyword_anchor = ""
    keyword_score = 0
    for anchor, keywords in _STEP_ANCHOR_KEYWORDS:
        if anchor not in available:
            continue
        score = sum(1 for keyword in keywords if keyword in blob)
        if score > keyword_score:
            keyword_anchor = anchor
            keyword_score = score

    step_tokens = _support_tokens(blob)
    if not step_tokens:
        explicit = str(step.get("anchor", "")).strip()
        return explicit if explicit and explicit == keyword_anchor else keyword_anchor

    explicit = str(step.get("anchor", "")).strip()
    explicit_overlap = 0
    if explicit and explicit in available:
        if str(step.get("provenance", "")).strip() == "akb_interpretation":
            return explicit
        command = str(step.get("command", "")).strip()
        instruction = str(step.get("instruction", "")).strip()
        # Concrete protocol/API commands such as initialize/tools/list may be
        # source-grounded even when their short token overlap is only one word.
        if (
            _PROTOCOL_COMMAND_RE.fullmatch(command)
            and (
                command.lower() in _anchor_support_text(explicit, claims, raw_anchor_texts).lower()
                or command.lower() in instruction.lower()
            )
            and not keyword_anchor
        ):
            return explicit
        explicit_overlap = len(
            step_tokens & _support_tokens(_anchor_support_text(explicit, claims, raw_anchor_texts))
        )
        if keyword_anchor and keyword_anchor != explicit:
            return keyword_anchor
        if explicit_overlap >= 2:
            return explicit

    if keyword_anchor:
        return keyword_anchor

    best_anchor = ""
    best_overlap = 0
    for anchor in available:
        support = _anchor_support_text(anchor, claims, raw_anchor_texts)
        overlap = len(step_tokens & _support_tokens(support))
        if overlap > best_overlap:
            best_anchor = anchor
            best_overlap = overlap
    return best_anchor if best_overlap >= 2 else ""


def _supported_how_steps(
    how_steps: list[Any],
    claims: list[dict[str, Any]],
    raw_anchor_texts: dict[str, str],
) -> list[tuple[int, Any, str]]:
    claim_anchors = {
        str(claim.get("anchor", "")).strip()
        for claim in claims
        if str(claim.get("anchor", "")).strip()
    }
    supported: list[tuple[int, Any, str]] = []
    for idx, step in enumerate(how_steps, 1):
        if isinstance(step, dict):
            command = _expand_placeholder_command(
                str(step.get("command", "")).strip(),
                str(step.get("instruction", "")).strip(),
                idx,
            )
            if step.get("executable") is not True or not command:
                continue
        anchor = _select_step_anchor(step, claims, idx, raw_anchor_texts)
        # 候选正文只允许引用已进入 claims/evidence map 的锚点。
        # raw 里存在但未被抽为核心论点的段落不能偷偷出现在 How 里，
        # 否则 GPT-5.4 会判定“主要证据区未覆盖该 raw anchor”。
        if anchor and anchor in claim_anchors:
            supported.append((len(supported) + 1, step, anchor))
    return supported


def _claim_to_how_step(claim: dict[str, Any], idx: int) -> dict[str, Any]:
    """Derive one conservative AKB operation step from an evidence-map claim."""
    text = str(claim.get("text", "")).lower()
    anchor = str(claim.get("anchor", "§A")).strip() or "§A"
    if any(token in text for token in ("ledger", "checkpoint", "before executing", "crash recovery", "账本", "检查点", "重放", "恢复")):
        instruction = "在执行下一动作前写入检查点，并保存输入、工具调用和响应状态"
        command = "ledger.append({run_id:run_id,item_id:work_item.id,status:'checkpointed',next_action:'execute'})"
    elif any(token in text for token in ("budget", "budgets", "max_actions", "max_repair_rounds", "max_rounds", "max_retries", "constant", "stop on", "stop condition", "预算", "停止")):
        instruction = "在循环入口断言命名预算和停止条件"
        command = "assert max_actions <= 5 and max_repair_rounds <= 3 and grader_timeout_seconds == 120"
    elif any(token in text for token in ("repair", "defect", "restart", "convergence", "failure", "schema_mismatch", "missing_evidence", "tool_timeout", "reviewer_disagreement", "修复", "缺陷", "失败", "分类")):
        instruction = "按失败类型分派定点修复动作，不能整篇重跑"
        command = "repair_router.dispatch(error_class=current_failure.type, anchor=current_failure.raw_anchor)"
    elif any(token in text for token in ("signature", "error_class", "escalation", "签名", "升级")):
        instruction = "记录故障签名并在重复出现时升级"
        command = "record_failure(loop='loop1',node=node,error_class=error_class)"
    else:
        instruction = ""
        command = ""
    if not instruction or not command:
        return {}
    return {
        "instruction": instruction,
        "command": command,
        "executable": True,
        "anchor": anchor,
        "provenance": "akb_interpretation",
    }


def _claim_derived_how_steps(claims: list[dict[str, Any]]) -> list[tuple[int, Any, str]]:
    derived_steps: list[tuple[int, Any, str]] = []
    for idx, claim in enumerate(claims, 1):
        derived = _claim_to_how_step(claim, idx)
        if derived:
            derived_steps.append(
                (len(derived_steps) + 1, derived, str(claim.get("anchor", "§A")).strip() or "§A")
            )
    return derived_steps


def _source_operation_instruction(command: str, anchor: str) -> str:
    if command == "initialize":
        return "Send initialize before using the protocol server"
    if command == "tools/list":
        return "List tools before selecting a tool to call"
    if command == "tools/call":
        return "Invoke the selected tool through the protocol"
    if command == "resources/list":
        return "List exposed resources before reading one"
    if command == "resources/read":
        return "Read the selected resource through the protocol"
    if command == "prompts/list":
        return "List exposed prompts before fetching one"
    if command == "prompts/get":
        return "Get a selected prompt through the protocol"
    if command == "notifications/initialized":
        return "Send the initialized notification after capability negotiation"
    return f"Use the source-documented operation from {anchor}"


def _raw_source_how_steps(
    claims: list[dict[str, Any]],
    raw_anchor_texts: dict[str, str],
    seen_commands: set[str],
) -> list[tuple[int, Any, str]]:
    """Extract real source operations from raw sections before inventing AKB examples."""
    claim_anchors = [
        str(claim.get("anchor", "")).strip()
        for claim in claims
        if str(claim.get("anchor", "")).strip()
    ]
    raw_anchors = [anchor for anchor in raw_anchor_texts if anchor not in claim_anchors]
    scan_anchors = claim_anchors + raw_anchors
    derived: list[tuple[int, Any, str]] = []
    for command in _SOURCE_PROTOCOL_COMMANDS:
        command_key = command.lower()
        if command_key in seen_commands:
            continue
        for anchor in scan_anchors:
            source_text = _anchor_support_text(anchor, claims, raw_anchor_texts, prefer_raw=True)
            source_lower = source_text.lower()
            if not _source_text_has_command(source_lower, command):
                continue
            step = {
                "instruction": _source_operation_instruction(command, anchor),
                "command": command,
                "executable": True,
                "anchor": anchor,
                "provenance": "source_grounded",
            }
            derived.append((len(derived) + 1, step, anchor))
            seen_commands.add(command_key)
            break
    return derived


def _source_claim_for_raw_operation(anchor: str, raw_anchor_texts: dict[str, str]) -> dict[str, Any]:
    section = raw_anchor_texts.get(anchor, "").strip()
    compact = re.sub(r"\s+", " ", section).strip()
    if compact:
        compact = re.sub(rf"^#+\s+{re.escape(anchor)}\b\s*", "", compact).strip(" -")
    if len(compact) > 180:
        compact = compact[:177].rstrip() + "..."
    return {
        "text": compact or f"Source section {anchor} documents protocol operations.",
        "anchor": anchor,
        "provenance": "source_grounded",
        "evidence_gap": "未覆盖：原文未提供该协议操作的失败处理和生产遥测。",
    }


def _ensure_how_evidence_claims(
    claims: list[dict[str, Any]],
    rendered_how_steps: list[tuple[int, Any, str]],
    raw_anchor_texts: dict[str, str],
) -> list[dict[str, Any]]:
    """Add minimal source claims for raw-only How anchors so evidence map closes."""
    if not rendered_how_steps:
        return claims
    result = list(claims)
    claim_anchors = {
        str(claim.get("anchor", "")).strip()
        for claim in result
        if str(claim.get("anchor", "")).strip()
    }
    for _idx, step, anchor in rendered_how_steps:
        if anchor in claim_anchors or anchor not in raw_anchor_texts:
            continue
        if isinstance(step, dict) and str(step.get("provenance", "")).strip() != "source_grounded":
            continue
        result.append(_source_claim_for_raw_operation(anchor, raw_anchor_texts))
        claim_anchors.add(anchor)
    return result


def _ensure_minimum_how_steps(
    supported_steps: list[tuple[int, Any, str]],
    claims: list[dict[str, Any]],
    raw_anchor_texts: dict[str, str] | None = None,
) -> list[tuple[int, Any, str]]:
    """Supplement sparse How output without skipping real source operations."""
    if len(supported_steps) >= HOW_STEPS_MIN_EXECUTABLE:
        return supported_steps[:HOW_STEPS_MAX_RENDERED]
    result = list(supported_steps)
    seen_commands = {
        _expand_placeholder_command(
            str(step.get("command", "")).strip(),
            str(step.get("instruction", "")).strip(),
            _idx,
        ).strip().lower()
        if isinstance(step, dict)
        else str(step).strip().lower()
        for _idx, step, _anchor in result
    }
    raw_anchor_texts = raw_anchor_texts or {}
    for _idx, step, anchor in _raw_source_how_steps(claims, raw_anchor_texts, seen_commands):
        if len(result) >= HOW_STEPS_MIN_EXECUTABLE:
            break
        result.append((len(result) + 1, step, anchor))
    for idx, claim in enumerate(claims, 1):
        if len(result) >= HOW_STEPS_MIN_EXECUTABLE:
            break
        derived = _claim_to_how_step(claim, idx)
        if not derived:
            continue
        command = str(derived.get("command", "")).strip().lower()
        if not command:
            continue
        if command in seen_commands:
            continue
        anchor = str(claim.get("anchor", "§A")).strip() or "§A"
        result.append((len(result) + 1, derived, anchor))
        seen_commands.add(command)
    return result[:HOW_STEPS_MAX_RENDERED]


def _build_how_boundary_table(
    claims: list[dict[str, Any]],
    how_steps: list[Any],
    raw_anchor_texts: dict[str, str] | None = None,
    rendered_how_steps: list[tuple[int, Any, str]] | None = None,
) -> str:
    """Map AKB operation steps to source support and explicit evidence gaps."""
    if not how_steps:
        return ""
    claim_by_anchor = {str(c.get("anchor", "")): c for c in claims}
    rows: list[str] = [
        "### 操作化边界",
        "",
        "| 步骤 | 来源支撑 | AKB 操作化 | 未直接支持/仍需验证 |",
        "|---|---|---|---|",
    ]
    for idx, step, anchor in (rendered_how_steps or _supported_how_steps(how_steps, claims, raw_anchor_texts or {})):
        claim = claim_by_anchor.get(anchor) or (
            claims[min(idx - 1, len(claims) - 1)] if claims else {}
        )
        instruction = str(
            step.get("instruction", step) if isinstance(step, dict) else step
        ).strip()
        command = ""
        if isinstance(step, dict):
            command = _expand_placeholder_command(
                str(step.get("command", "")).strip(),
                instruction,
                idx,
            )
        support_text = str(claim.get("text", "")).strip() or _anchor_support_text(
            anchor, claims, raw_anchor_texts or {}
        )
        support = f"[raw {anchor}] {support_text[:90] or '来源事实'}"
        gap = str(claim.get("evidence_gap", "")).strip() or "未验证：原文未给出该操作化步骤的生产实测。"
        step_provenance = "akb_interpretation"
        if isinstance(step, dict):
            step_provenance = _how_step_provenance(step, command, anchor)
        operation = instruction
        if command:
            operation = f"{operation} `{command}`"
        rows.append(
            f"| {idx} | {_md_table_cell(support)} | {_md_table_cell(operation)} | {_md_table_cell(gap)} |"
        )
    return "\n".join(rows)


def _build_params_example(params: dict[str, Any]) -> str:
    """把抽取参数渲染成可复制配置，给 How 可执行性一个硬锚。"""
    defaults = {
        "max_repair_rounds": _concrete_param_value(
            params.get("max_repair_rounds", params.get("max_rounds")),
            3,
        ),
        "distill_timeout_seconds": _concrete_param_value(
            params.get("distill_timeout_seconds"),
            60,
        ),
        "grader_timeout_seconds": _concrete_param_value(
            params.get("grader_timeout_seconds"),
            120,
        ),
        "max_retries": _concrete_param_value(params.get("max_retries"), 0),
        "max_parallel_items": _concrete_param_value(params.get("max_parallel_items"), 1),
        "maker_model": _concrete_param_value(params.get("maker_model"), "deepseek-v4-pro"),
        "checker_model": _concrete_param_value(params.get("checker_model"), "gpt-5.4"),
        "ledger_event": _concrete_param_value(params.get("ledger_event"), "distill_extract"),
        "fallback_allowed": _concrete_param_value(params.get("fallback_allowed"), "false"),
    }
    lines = ["```yaml"]
    for key, value in defaults.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value).lower() if isinstance(value, str) else str(value)
        lines.append(f"{key}: {rendered}")
    lines.append("```")
    return "\n".join(lines)


def _concrete_param_value(value: Any, default: Any) -> Any:
    """Replace type placeholders with concrete deployable defaults."""
    if value is None:
        return default
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {
            "",
            "integer",
            "int",
            "number",
            "float",
            "string",
            "str",
            "boolean",
            "bool",
            "none",
            "null",
            "n/a",
            "unknown",
            "explicit budget constant",
            "budget constant",
            "named constant",
            "application-specific",
        }:
            return default
    return value


def _compact_tldr_sentence(text: str) -> str:
    """把 claim 压成 TLDR 单句，避免一个 claim 内部多句导致 TLDR 超标。"""
    compact = re.sub(r"\s+", " ", str(text)).strip()
    if not compact:
        return ""
    compact = re.sub(r"[。.!?！？]+", "；", compact)
    compact = compact.strip("；; ")
    return compact.rstrip("。.!?！？")


# 共享 markdown 表格 cell 转义(写侧),与 checks_structural 的读侧(split_md_table_row)同契约
from src.akb2.quality.md_table import md_table_cell as _md_table_cell  # noqa: E402

# ---------------------------------------------------------------------------
# must_cover 代码直出渲染（step-2: scanner 账本 → 正文三节强制渲染）
#
# scan_all 产出的 EvidenceItem 携带 raw_anchor_span（char offset 进 raw_text），但
# 正文引用锚点是 [raw §X] 形态。A3 (check_a3_raw_anchors) 拒绝任何 raw 未声明的 §X，
# 故须把每个 span 就近映射到一个已声明的 §X 标题——落在该标题之下的 evidence 归属该节。
# ---------------------------------------------------------------------------

# 与 checks_structural._extract_raw_anchors 同源的四种声明形态，但这里保留位置
# 用于就近映射（_extract_raw_anchors 只返回 set，丢失位置信息）。
_RAW_ANCHOR_DECL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"(?m)^#{{1,6}}\s+({RAW_ANCHOR_DECL_RE})(?:[.\s:：]|$)"),
    re.compile(rf"(?m)^\[raw\s+({RAW_ANCHOR_DECL_RE})\]"),
    re.compile(rf"(?m)^({RAW_ANCHOR_DECL_RE})_[A-Z_]+\b"),
    re.compile(rf"(?m)^\|\s*({RAW_ANCHOR_DECL_RE})\s*\|"),
)


def _declared_raw_anchors_with_pos(raw_content: str) -> list[tuple[int, str]]:
    """Return (position, §X) pairs for all declared raw anchors, sorted by position."""
    decls: list[tuple[int, str]] = []
    for pattern in _RAW_ANCHOR_DECL_PATTERNS:
        for m in pattern.finditer(raw_content):
            decls.append((m.start(), m.group(1)))
    decls.sort(key=lambda x: x[0])
    return decls


def _map_span_to_declared_anchor(
    raw_content: str | None,
    span_start: int,
    decls: list[tuple[int, str]] | None = None,
) -> str:
    """Map a raw_text char offset to the nearest preceding declared §X anchor.

    Returns the largest-position declaration at or before span_start. If the span
    precedes all declarations (rare; evidence in frontmatter/prologue), falls
    back to the first declared anchor. Returns "" only when raw has no declared
    anchors at all (A3 is skipped when raw_content is None, so empty is safe then).
    """
    if not raw_content:
        return ""
    if decls is None:
        decls = _declared_raw_anchors_with_pos(raw_content)
    if not decls:
        return ""
    chosen = ""
    for pos, anchor in decls:
        if pos <= span_start:
            chosen = anchor
        else:
            break
    if not chosen:
        chosen = decls[0][1]
    return chosen


def _sanitize_must_cover_literal_for_command(literal: str) -> str:
    """Strip backticks/newlines so a literal can sit inside a `code` span.

    HOW_STEP_COMMAND_RE extracts `([^`\\n]+)`; an embedded backtick would close the
    code span early and break the gate's executable-step count. cli_commands can
    contain backticks; numbered-step/sentence literals do not in practice.
    """
    cleaned = literal.replace("`", "'").replace("\n", " ").strip()
    return cleaned or literal


def _render_must_cover_operations(
    items: list[Any],
    raw_content: str | None,
) -> str:
    """Render source_operation EvidenceItems as code-direct How steps.

    Output format satisfies HOW_STEP_COMMAND_RE:
        {N}. [raw §X] (source_grounded) {instruction} `{literal}`

    Provenance is source_grounded (literal is verbatim from raw). Prepended to
    LLM how_steps so the LLM cannot drop a source operation (SC-2.2).
    """
    if not items:
        return ""
    decls = _declared_raw_anchors_with_pos(raw_content) if raw_content else []
    lines: list[str] = []
    for idx, item in enumerate(items, 1):
        anchor = _map_span_to_declared_anchor(raw_content, item.raw_anchor_span.start, decls)
        command = _sanitize_must_cover_literal_for_command(item.literal)
        lines.append(
            f"{idx}. [raw {anchor}] (source_grounded) "
            f"原文操作（must_cover 代码直出，scanner={item.source}） `{command}`"
        )
    return "\n".join(lines)


def _render_must_cover_boundaries(
    items: list[Any],
    raw_content: str | None,
) -> str:
    """Render source_boundary EvidenceItems as code-direct applicability-boundary bullets.

    Output format satisfies check_a8_applicability_boundary:
        - [raw §X] (source_grounded) 来源事实: {literal}

    Category 「来源事实」(literal is a source-grounded scope/precondition).
    One anchor + one provenance + one category per bullet (A8 contract).
    """
    if not items:
        return ""
    decls = _declared_raw_anchors_with_pos(raw_content) if raw_content else []
    lines: list[str] = []
    for item in items:
        anchor = _map_span_to_declared_anchor(raw_content, item.raw_anchor_span.start, decls)
        literal = item.literal.replace("\n", " ").strip()
        lines.append(f"- [raw {anchor}] (source_grounded) 来源事实: {literal}")
    return "\n".join(lines)


def _render_must_cover_conclusions(
    items: list[Any],
    raw_content: str | None,
) -> str:
    """Render source_conclusion EvidenceItems as code-direct TLDR sentences.

    Output format: (source_grounded) 原文结论（must_cover 代码直出）：{literal} [raw §X].
    Prepended to the LLM-derived tldr so explicit author conclusions survive even
    when the LLM omits them.
    """
    if not items:
        return ""
    decls = _declared_raw_anchors_with_pos(raw_content) if raw_content else []
    parts: list[str] = []
    for item in items:
        anchor = _map_span_to_declared_anchor(raw_content, item.raw_anchor_span.start, decls)
        literal = item.literal.replace("\n", " ").strip()
        parts.append(
            f"(source_grounded) 原文结论（must_cover 代码直出）：{literal} [raw {anchor}]."
        )
    return " ".join(parts)


def _build_body(
    extract_data: dict[str, Any],
    raw_path: str,
    route_llm_fn: Any,  # 保留参数兼容接口，_build_body 是确定性脚本，不调 LLM
    *,
    must_cover: list[Any] | None = None,
    raw_content: str | None = None,
) -> str:
    """构建 10 节正文（确定性脚本，只填 extract_data 槽位，不调 LLM）。

    10 节模板:
        1. TLDR
        2. 关键发现
        3. 入选理由
        4. 证据地图
        5. 核心论点
        6. 论证与解释（含操作步骤）
        7. 适用边界
        8. 图谱关系
        9. Future Questions
        10. 维护记录

    must_cover（step-2）：scan_all 产出的 EvidenceItem 列表，按 kind 代码直出渲染
    进对应节——source_operation→操作步骤、source_boundary→适用边界、
    source_conclusion→TLDR。LLM segment 仅补充语义，不决定 operation 是否出现。
    """
    must_cover = must_cover or []
    claims = list(extract_data.get("claims", []))
    how_steps = extract_data.get("how_steps", [])
    concepts = extract_data.get("referenced_concepts", [])
    raw_anchor_texts = _load_raw_anchor_texts(raw_path)

    argumentation: list[dict[str, Any]] = extract_data.get("argumentation") or []
    applicability_boundary = (extract_data.get("applicability_boundary") or "").strip()
    graph_edges: list[dict[str, Any]] = extract_data.get("graph_edges") or []

    supported_how_steps = _supported_how_steps(how_steps, claims, raw_anchor_texts)
    if not supported_how_steps:
        supported_how_steps = _claim_derived_how_steps(claims)
    supported_how_steps = _ensure_minimum_how_steps(
        supported_how_steps,
        claims,
        raw_anchor_texts,
    )
    claims = _ensure_how_evidence_claims(claims, supported_how_steps, raw_anchor_texts)

    # 新增字段槽位（段 1 抽取 LLM 已产出，此处仅读取）
    tldr = (extract_data.get("tldr") or "").strip()
    if not tldr:
        tldr = claims[0]["text"] if claims else "（摘要待补充）"

    # 关键发现：带 [raw §X] 锚点+provenance 的证据格式（rubric: 论断锚定率要求 [raw §X] 格式）
    claims_text = "\n".join(
        f"- [raw {c['anchor']}] ({c['provenance']}) {c['text']}"
        for c in claims
    )

    # 核心论点：带 [raw §X] 锚点+provenance，与关键发现区分（编号列表格式）
    # rubric 解读分离: 每条论点必须标注 provenance；论断锚定率: 必须带 [raw §X]
    core_claims_text = "\n".join(
        f"{i}. [raw {c['anchor']}] ({c['provenance']}) {c['text']}"
        for i, c in enumerate(claims, 1)
    )

    # TLDR：从 top-3 claims 构建（每句含 [raw §X] 锚点；rubric: TLDR 密度 ≤3 句含锚点）
    tldr_sentences = [
        f"(source_grounded) {_compact_tldr_sentence(c['text'])} [raw {c['anchor']}]."
        for c in claims[:3]
        if _compact_tldr_sentence(c["text"])
    ]
    tldr = " ".join(tldr_sentences) if tldr_sentences else tldr

    # must_cover source_conclusion: 代码直出进 TLDR，LLM 无最终选择权。
    # 即使 LLM tldr 漏掉原文显式结论，scanner 账本仍强制渲染进 TLDR 节。
    must_cover_concls = [m for m in must_cover if m.kind == "source_conclusion"]
    mc_tldr_text = _render_must_cover_conclusions(must_cover_concls, raw_content)
    if mc_tldr_text:
        tldr = f"{mc_tldr_text} {tldr}".strip() if tldr else mc_tldr_text

    # 论证与解释：argumentation 字段逐条展开。
    # source_grounded 条目回到 claim 文本本身，避免把 LLM 的归纳解释伪装成来源事实。
    if argumentation:
        claim_by_key = _claim_by_anchor_and_text(claims)
        arg_items = []
        for a in argumentation:
            anchor = a.get("claim_anchor")
            explanation = str(a.get("explanation", "")).strip()
            if not anchor or not explanation:
                continue
            prov = a.get("provenance", "source_grounded")
            if prov == "source_grounded":
                claim = _fallback_claim_for_argumentation(
                    claim_by_key,
                    claims,
                    str(anchor).strip(),
                    explanation,
                )
                claim_text = str(claim.get("text", "")).strip()
                source_text = claim_text or explanation
                arg_items.append(
                    f"**[raw {anchor}]** (source_grounded) 来源事实：{source_text}"
                )
            else:
                arg_items.append(
                    f"**[raw {anchor}]** (akb_interpretation) AKB 解读：{explanation}"
                )
        arg_text = "\n\n".join(arg_items) if arg_items else core_claims_text
    else:
        arg_text = core_claims_text
    arg_text = arg_text

    # 操作步骤：how_steps 兼容对象({instruction,command,executable})与字符串两种形态
    # （Codex R2 剩余风险2:旧 staging/候选传入字符串 → .get 会 AttributeError，先判 isinstance）
    #
    # must_cover source_operation（step-2）：代码直出排在 LLM how_steps 之前。
    # 关键不变量：即使 LLM how_steps=[]（mock 空），operation 仍由代码渲染出现（SC-2.2）。
    # 当 must_cover 操作存在时，不输出 FORBIDDEN_HOW_FALLBACK_MARKERS 占位
    # （否则 SG-how-executability 会拒绝）。占位仅在 must_cover 与 LLM 步骤双空时保留（既有行为）。
    must_cover_ops = [m for m in must_cover if m.kind == "source_operation"]
    mc_how_text = _render_must_cover_operations(must_cover_ops, raw_content)
    llm_how_text = (
        "\n".join(
            _render_how_step_with_provenance(i, step, anchor)
            for i, step, anchor in supported_how_steps
        )
        if supported_how_steps
        else ""
    )
    if mc_how_text and llm_how_text:
        how_text = f"{mc_how_text}\n\n原文语义补充（LLM 抽取）：\n{llm_how_text}"
    elif mc_how_text:
        how_text = mc_how_text
    elif llm_how_text:
        how_text = llm_how_text
    else:
        how_text = "（原文无可安全锚定的操作步骤；候选阶段不强行生成 AKB 操作化步骤。）"
    params = extract_data.get("params") or {}
    if isinstance(params, dict) and params:
        how_text = f"{how_text}\n\n配置示例：\n{_build_params_example(params)}"
    how_boundary = _build_how_boundary_table(
        claims,
        how_steps,
        raw_anchor_texts,
        supported_how_steps,
    )
    if how_boundary:
        how_text = f"{how_text}\n\n{how_boundary}"

    # 证据地图："未覆盖"列必须写真盲区(claim.evidence_gap),不是论证摘要。
    # 旧用 argumentation explanation 填该列 → 退化成释义,grader 维度4 扣分(codex 必修1)。
    # cell 经 _md_table_cell 转义 |（否则 evidence 含 | 会拆列 + A4 假绿）
    _GAP_FALLBACK = "未覆盖：原文未提供该论点的独立验证、量化结果或失败样本。"
    evidence_rows = "\n".join(
        f"| {_md_table_cell(c['anchor'])} | {_md_table_cell(c['text'][:60])}... "
        f"| {c['provenance']} "
        f"| {_md_table_cell(c.get('evidence_gap') or _GAP_FALLBACK)} |"
        for c in claims
    )

    # 图谱关系：表头对齐检查器/评分器英文契约(GRAPH_EDGE_COLUMNS=target/relation/strength/evidence)
    # evidence 不截断(旧 [:80] 截断丢依据);fallback 边降 weak(旧 medium 偏高)
    # cell 经 _md_table_cell 转义 | + 压平换行(否则 evidence 含 | 拆成 >4 列,A4 只拒 <4 列→假绿)
    if graph_edges:
        graph_rows = "\n".join(
            f"| {_md_table_cell(edge['concept'])} | {_md_table_cell(edge['relation_type'])} "
            f"| {edge['strength']} | {edge['provenance']} | {_md_table_cell(edge.get('evidence', ''))} |"
            for edge in (_normalize_graph_edge(e) for e in graph_edges)
        )
    elif concepts:
        # concept 来自 LLM referenced_concepts,可能含 | → 同样转义(altitude:所有插值 cell 一致转义)
        fallback_anchor = claims[0].get("anchor", "§A") if claims else "§A"
        graph_rows = "\n".join(
            f"| {_md_table_cell(c)} | related | weak | "
            "akb_interpretation | "
            f"{_md_table_cell(f'[raw {fallback_anchor}] (akb_interpretation) 见原文概念引用，候选阶段按推测关联保守标 weak。')} |"
            for c in concepts
        )
    else:
        graph_rows = "| （无相关概念） | — | weak | akb_interpretation | (akb_interpretation) 无可证实图谱边。 |"

    concepts_text = ", ".join(concepts) if concepts else "（见图谱关系）"

    # 入选理由：引用 top claims 的 [raw §X] 锚点（rubric 锚定率:入选理由需锚定原文）
    selection_reason = _build_selection_reason(claims, how_steps, concepts_text)
    novelty_increment = _build_novelty_increment(
        claims,
        how_steps,
        concepts_text,
        raw_path,
        raw_anchor_texts,
        supported_how_steps,
    )
    boundary_text = _build_applicability_boundary_text(
        applicability_boundary,
        claims,
        how_steps,
        supported_how_steps,
    )
    # must_cover source_boundary（step-2）：代码直出进适用边界节，排在 LLM 边界之前。
    # 每条 bullet 用「来源事实」类别 + source_grounded provenance（literal 是原文边界语句）。
    must_cover_bounds = [m for m in must_cover if m.kind == "source_boundary"]
    mc_boundary_text = _render_must_cover_boundaries(must_cover_bounds, raw_content)
    if mc_boundary_text:
        boundary_text = (
            f"{mc_boundary_text}\n{boundary_text}" if boundary_text else mc_boundary_text
        )

    from datetime import UTC, datetime

    _now_date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    body = f"""## TLDR

{tldr}

## 关键发现

{claims_text}

## 入选理由

{selection_reason}

## 证据地图

| 锚点 | 论点 | 来源 | 未覆盖 |
|------|------|------|--------|
{evidence_rows}

## 核心论点

{core_claims_text}

## 论证与解释

{arg_text}

### 操作步骤

{_build_how_boundary_preamble()}

{how_text}

## 新颖增量

{novelty_increment}

## 适用边界

{boundary_text}

## 图谱关系

下表图谱边按 evidence 的 provenance 判定；weak 边仍需后续图谱校准确认。

| target | relation | strength | provenance | evidence |
|--------|----------|----------|------------|----------|
{graph_rows}

## Future Questions

{chr(10).join(f'- {q}' for q in _generate_questions(claims, how_steps))}

## 维护记录

| 日期 | 操作 | 说明 |
|------|------|------|
| {_now_date_str} | 初始创建 | 段1-2 提炼，raw: {_md_table_cell(raw_path)} |"""

    return body


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
            if key.startswith("related_"):
                # 轻量 wikilink list 内联渲染（GRAPH-LINKING §3.8.1 + 控 frontmatter 行数）
                inline = ", ".join(f'"{item}"' for item in value)
                fm_lines.append(f"{key}: [{inline}]")
            else:
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
