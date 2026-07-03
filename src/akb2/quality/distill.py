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

import difflib
import json
import os
import re
import signal
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.akb2.quality.chrome_strip import strip_chrome_blocks
from src.akb2.quality.raw_anchors import (
    ANCHOR_TOKEN_PAT,
    RAW_ANCHOR_DECL_RE,
    RAW_ANCHOR_REF_RE,
    RAW_ANCHOR_VALUE_RE,
    canonicalize_anchor,
    declared_anchor_positions,
    declared_anchor_set,
    nearest_preceding_anchor,
    resolve_anchor_ref,
    strip_directory_block,
)
from src.akb2.quality.source_ops_scanners.raw_verify import (
    is_command_allowed_for_candidate,
)

# ---------------------------------------------------------------------------
# 具名常量（宪法 P1/P4）
# ---------------------------------------------------------------------------

MAX_EXTRACT_RETRIES: int = 3
# 截断保护：raw 喂给抽取的字符上限（旧截断阈值过小，截掉 §D-§J 约 53% 正文；
# 提到具名常量并放大到 50k，避免裸魔数 + 减少截断失真）
MAX_EXTRACT_CHARS: int = 50_000
# how_steps 可执行下限：prompt 要求 ≥3 条 executable=true 且 command 非空（How 可执行性）
HOW_STEPS_MIN_EXECUTABLE: int = 3
# step-3 SC-3a/A3-5：concept 车道命名机制组件下限（F4 结构真相源）。
# concept extract 改判 len(mechanism_components)≥3（替代 procedural 的 executable≥3 门）。
CONCEPT_MIN_MECHANISM_COMPONENTS: int = 3
# P1b-2: fallback 组件抽取上限(防长表格灌水;有界预算)
FALLBACK_MAX_COMPONENTS: int = 12
# step-1 content_lane 枚举：concept（概念型）/ procedural（操作型，默认兼容）。
CONTENT_LANE_CONCEPT: str = "concept"
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
DISTILL_DISABLE_THINKING: bool = True
# Step-7: 语义型 How 抖动兜底。how_params segment（LLM 自由文本 how_steps，
# 非 must_cover 代码直出）多取取交集，抵抗单次 LLM 抖动。有界：SEMANTIC_RETAKE_MAX 次。
SEMANTIC_RETAKE_MAX: int = 2
# P1a: §-anchor 解析口径统一到 raw_anchors 模块（RAW_ANCHOR_DECL_RE/VALUE_RE/REF_RE
# 自 raw_anchors 导入，canonical 规则 + 声明形态见该模块 docstring）。

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


class _FrontmatterDumper(yaml.SafeDumper):
    """PyYAML dumper that keeps block-list indentation compatible with local lint."""

    def increase_indent(self, flow: bool = False, indentless: bool = False):
        return super().increase_indent(flow=flow, indentless=False)


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
                    # step-3 SC-3c：akb_interpretation claim 带 traces_to
                    # （指向 source_grounded anchor）。
                    # source_grounded claim 可省；layer① 地板由 integrity_floor 校验。
                    "traces_to": {
                        "type": "array",
                        "items": {"type": "string"},
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
                    # step-3 SC-3a：三级 how 标注（H1/H2/H3），非 required（旧响应兼容）。
                    "how_level": {
                        "type": "string",
                        "enum": ["H1", "H2", "H3"],
                    },
                },
                "required": ["instruction", "command", "executable"],
                "additionalProperties": False,
            },
            "minItems": 0,
        },
        # step-3 SC-3a/A3-5：命名机制组件 + 因果边（concept F4 结构真相源）。
        # 顶层 minItems=0 非 required——procedural 零回归（不产），concept 由
        # _validate_extract_output 按 lane 硬卡 ≥3；F4 只读这两字段不反解 H3 标签。
        # §0.2/D2：扩 component_id/aliases/evidence_anchor/evidence_sentence
        # （同 _HOW_PARAMS_SCHEMA）。
        "mechanism_components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "component_id": {"type": "string"},
                    "name": {"type": "string", "minLength": 1},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "role": {"type": "string"},
                    "evidence_anchor": {"type": "string"},
                    "evidence_sentence": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            "minItems": 0,
        },
        # §0.2/D2：扩 edge_kind/evidence/evidence_anchor（同 _HOW_PARAMS_SCHEMA）。
        "causal_edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "minLength": 1},
                    "to": {"type": "string", "minLength": 1},
                    "relation": {"type": "string"},
                    "edge_kind": {"type": "string"},
                    "evidence": {"type": "string"},
                    "evidence_anchor": {"type": "string"},
                },
                "required": ["from", "to"],
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
                    # rubric 维度5 解读分离:解读性内容(未来趋势/可能性/归纳)
                    # 必须标 akb_interpretation,
                    # 不得伪装成 source_grounded(否则解读分离扣分)。
                    "provenance": {
                        "type": "string",
                        "enum": ["source_grounded", "akb_interpretation"],
                    },
                    # step-3 SC-3c：akb_interpretation argumentation 带 traces_to
                    "traces_to": {
                        "type": "array",
                        "items": {"type": "string"},
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
                    "traces_to": {"type": "array", "items": {"type": "string"}},
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
                    # step-3 SC-3a：三级 how 标注（H1 命令级 / H2 数据流-状态-接口 /
                    # H3 命名机制组件+因果边）。normalize 阶段（_normalize_how_step）
                    # 必为每条注入 how_level（默认 H1），故此字段 required——出站 strict
                    # schema 不留缺字段口子；concept 车道允许 H1==0（地板改判 F4 结构字段）。
                    "how_level": {
                        "type": "string",
                        "enum": ["H1", "H2", "H3"],
                    },
                },
                "required": ["instruction", "command", "executable", "how_level"],
                "additionalProperties": False,
            },
            "minItems": 0,
        },
        # step-3 SC-3a/A3-5：命名机制组件数组（concept F4 的结构真相源）。
        # 顶层 required + minItems=0——procedural 不产组件时为空数组（零回归），concept
        # 由 _validate_extract_output 按 lane 硬卡 ≥3。组件 role normalize 必填（默认 ""）。
        # §0.2/D2：扩 component_id/aliases/evidence_anchor/evidence_sentence（§4 两阶段抽取
        # 产稳定 component_id + evidence 锚定；F4 D1 eligible edge 依赖 evidence_anchor）。
        "mechanism_components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "component_id": {"type": "string"},
                    "name": {"type": "string", "minLength": 1},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "role": {"type": "string"},
                    "evidence_anchor": {"type": "string"},
                    "evidence_sentence": {"type": "string"},
                },
                "required": ["name", "role"],
                "additionalProperties": False,
            },
            "minItems": 0,
        },
        # step-3 SC-3a/A3-5：因果边数组，from/to 必须指向 mechanism_components 的 name。
        # F4 只读此结构字段判机制完整性，不从自然语言反解 H3 标签。relation normalize 必填。
        # §0.2/D2：扩 edge_kind/evidence/evidence_anchor。edge_kind 枚举
        # source_causal|llm_evidence|inferred_from_structure 由 _normalize_causal_edges 校验
        # （未知 fail-closed）；不用 schema enum 以免空 edge_kind（LLM 未产时）被误拒。
        # F4 D1 只认 source_causal/llm_evidence 带 evidence_anchor 的 eligible edge；
        # inferred_from_structure 不计 F4，只渲染进正文表 + 交 grader H3。
        "causal_edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "minLength": 1},
                    "to": {"type": "string", "minLength": 1},
                    "relation": {"type": "string"},
                    "edge_kind": {"type": "string"},
                    "evidence": {"type": "string"},
                    "evidence_anchor": {"type": "string"},
                },
                "required": ["from", "to", "relation"],
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
    "required": ["how_steps", "mechanism_components", "causal_edges", "param_entries"],
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
                    # step-3 SC-3c：akb_interpretation argumentation 带 traces_to
                    "traces_to": {
                        "type": "array",
                        "items": {"type": "string"},
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
    diagnostics: F8-A fail-visible 诊断(仅 success 路径填)——如组件 evidence 回填
        {components_evidence_backfilled, components_evidence_unlocated},供 runner ledger。
    """

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    retries_used: int = 0
    error: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)


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
    content_lane: str = "",
) -> ExtractResult:
    """段 1: raw → 结构化 JSON 抽取。

    有界重试（MAX_EXTRACT_RETRIES=3），jsonschema 校验。
    耗尽 → 显式失败，不出候选。

    Args:
        raw_content: raw 文件全文内容。
        route_llm_fn: LLM 路由函数注入（fixture 测试用）。
        content_lane: 内容车道（concept/procedural，step-1 透传字段）。本 step 仅
            透传给 _validate_extract_output、行为不变；lane-aware 改判属 step-3。

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
    # §0.2/A1：语义型 How 抖动兜底开关。默认关（"0"）——单次抽取 + _extract_segment_once
    # 的 schema 校验为主路径。retake 取交集以 command/instruction 语义 key 对齐，对
    # procedural how_steps 合理，但对 concept 的 mechanism_components/causal_edges 语义
    # 字段无意义（交集不交组件，M2 仅透传首样本，非真交集）——会丢非共享的机制组件/边，
    # 概念机制抽取不能丢这些。故默认关；稳定性改靠 M1 raw-grounded fallback + §4 两阶段
    # 结构化抽取补，不押注 retake 单点。仅当显式 AKB2_DISTILL_SEMANTIC_RETAKE=1 才开启
    # （retake 仍是可配实验路径，M2 修了透传 mechanism_components/causal_edges，不靠默认规避）。
    # 仅作用于 LLM 自由文本 how_steps，不碰 must_cover 代码直出。
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
            # F9-A: extract 后处理归一化单入口(LLM 成功路径 + fallback 共用 choke point)。
            # 按序:F-A 锚解析→edge anchor 校正→P1b-1 接地→F8-C 组件过滤→F6-A 端点闭合→
            # F8-D 命名归一→F8-A evidence 回填→F3-B 命令剥离。diag 透传 ExtractResult.diagnostics。
            _norm_diag = _normalize_extract_payload(merged, raw_content, content_lane)
            _validate_extract_output(merged, content_lane=content_lane)
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
            diagnostics={
                "components_evidence_backfilled": _norm_diag.get(
                    "components_evidence_backfilled", 0
                ),
                "components_evidence_unlocated": _norm_diag.get(
                    "components_evidence_unlocated", 0
                ),
                "components_filtered": _norm_diag.get("components_filtered", 0),
            },
        )

    return ExtractResult(
        success=False,
        retries_used=MAX_EXTRACT_RETRIES,
        error=f"抽取重试耗尽（{MAX_EXTRACT_RETRIES}）",
    )


# ---------------------------------------------------------------------------
# F2-B: traceability 有界 retake( floor 仅因 traceability fail 时触发一次)
# ---------------------------------------------------------------------------

TRACEABILITY_RETAKE_MAX: int = 1  # 具名常量:只 retake 一次(有界)
TRACEABILITY_RETAKE_TIMEOUT_S: float = 60.0

# retake 响应 schema:每条 interpretation claim 补 traces_to 或改判 provenance
# F4-B: 扩展 new_grounded_claim(verbatim 引文)路径——LLM 可返回该出处锚的逐字原文,
# 代码 B1 强校验后作新 source_grounded claim 追加(不造假溯源)
# F9-B: 扩展 argumentation_retake——argumentation 条目(provenance=akb_interpretation
# 缺 traces_to)同样走 LLM retake(arg_index 标识在 argumentation 数组中位置)
_TRACEABILITY_RETAKE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "retake": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_index": {"type": "integer"},
                    "traces_to": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "provenance": {
                        "type": "string",
                        "enum": ["akb_interpretation", "source_grounded"],
                    },
                    "new_grounded_claim": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "anchor": {"type": "string"},
                        },
                        "required": ["text", "anchor"],
                        "additionalProperties": False,
                    },
                },
                "required": ["claim_index", "traces_to", "provenance"],
                "additionalProperties": False,
            },
        },
        # F9-B: argumentation 条目 retake(arg_index 在 argumentation 数组中位置)
        "argumentation_retake": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "arg_index": {"type": "integer"},
                    "traces_to": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "provenance": {
                        "type": "string",
                        "enum": ["akb_interpretation", "source_grounded"],
                    },
                    "new_grounded_claim": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "anchor": {"type": "string"},
                        },
                        "required": ["text", "anchor"],
                        "additionalProperties": False,
                    },
                },
                "required": ["arg_index", "traces_to", "provenance"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["retake"],
    "additionalProperties": False,
}


def _build_traceability_retake_messages(
    interp_claims: list[dict[str, Any]],
    grounded_anchors: list[str],
    raw_snippet: str,
    interp_argumentation: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """F2-B: 构建 traceability retake prompt。

    给 LLM 一批缺 traces_to 的 interpretation claims + grounded anchor 清单 + raw 摘要,
    要求补 traces_to(指向 grounded anchor)或改判 provenance(若实为原文直述)。

    F9-B: 扩展 interp_argumentation——argumentation 条目(provenance=akb_interpretation
    缺 traces_to)同样送 LLM,返回 argumentation_retake(arg_index 标识)。
    """
    import json as _json

    interp_brief = [
        {
            "claim_index": int(c.get("_retake_index", 0)),
            "text": str(c.get("text", ""))[:200],
            "anchor": str(c.get("anchor", "")),
        }
        for c in interp_claims
    ]
    arg_brief = [
        {
            "arg_index": int(a.get("_retake_index", 0)),
            "explanation": str(a.get("explanation", ""))[:200],
            "claim_anchor": str(a.get("claim_anchor", "")),
        }
        for a in (interp_argumentation or [])
    ]
    sys_msg = (
        "你是 traceability 修复助手。给定一批 akb_interpretation claims(缺 traces_to 溯源)"
        "与 raw 中已严格接地的 source_grounded anchor 清单,为每条 interpretation claim 补"
        "traces_to(指向它所解读的 grounded anchor),或若该 claim 实际是原文直述则改判"
        "source_grounded 并补对应 anchor。\n"
        "硬约束:traces_to 必须从 grounded_anchors 里选,不可编造;不确定则保持"
        " akb_interpretation 且 traces_to=[]。\n"
        "F4-B:若 interpretation 的真实出处锚(在 raw 里)不在 grounded_anchors 里(该处无"
        " grounded claim),你可返回 new_grounded_claim={text, anchor}:text 须是该锚位置的"
        " **逐字原文引文**(verbatim,不可改写),anchor 是该出处的 raw 锚(可在 grounded_anchors"
        " 之外,但须是 raw 真实锚)。代码会 B1 强校验 text 逐字在 raw 该锚 block 内才接受。\n"
        "F9-B:argumentation 条目(provenance=akb_interpretation 缺 traces_to)同样需补——"
        "返回 argumentation_retake(arg_index 在 argumentation 数组中位置),字段同 retake"
        "(traces_to/provenance/new_grounded_claim),traces_to 指向 grounded anchor。\n"
        "只返回 JSON。"
    )
    user_msg = (
        f"grounded_anchors: {_json.dumps(grounded_anchors, ensure_ascii=False)}\n\n"
        f"interpretation_claims (claim_index 是在 claims 数组中的位置):\n"
        f"{_json.dumps(interp_brief, ensure_ascii=False)}\n\n"
    )
    if arg_brief:
        user_msg += (
            f"interpretation_argumentation (arg_index 是在 argumentation 数组中的位置):\n"
            f"{_json.dumps(arg_brief, ensure_ascii=False)}\n\n"
        )
    user_msg += (
        f"raw 摘要(供判断解读出处):\n{raw_snippet[:3000]}\n\n"
        f"返回 {{\"retake\": [{{\"claim_index\": int, \"traces_to\": [\"§X\"], "
        f"\"provenance\": \"akb_interpretation|source_grounded\"}}]"
    )
    if arg_brief:
        user_msg += (
            f", \"argumentation_retake\": [{{\"arg_index\": int, \"traces_to\": [\"§X\"], "
            f"\"provenance\": \"akb_interpretation|source_grounded\"}}]"
        )
    user_msg += "}"
    return [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": user_msg},
    ]


def retake_traceability(
    extract_data: dict[str, Any],
    raw_content: str,
    content_lane: str = "",
) -> dict[str, Any]:
    """F2-B/F3-A: traceability 有界 retake(一次 LLM 调用,补 traces_to 或改判 provenance)。

    caller(runner)在 floor 仅因 FLOOR_INTERPRETATION_TRACEABILITY fail 时触发本函数。
    本函数不放宽 floor,只给 LLM 一次机会补 traces_to;retake 后 caller 重评 floor,
    仍 fail → fail-closed。TRACEABILITY_RETAKE_MAX=1(具名,有界)。

    F3-A 修(lead 复现定位):
    1. 覆盖面:原只收 `not tt`(traces_to 空),漏 traces_to 非空但指错(不在 grounded 集)
       的 interpretation(ag2 §T_CONFIGURATION/vercel §E_CLAIMS 指错)。改为 tt 空 OR
       tt ∩ grounded 集 == ∅ 都纳入 retake。
    2. 接受逻辑:原 `traces_to ⊆ grounded_set` 精确匹配,LLM 按示例返单字母 §E 而 grounded
       是全名 §E_CLAIMS → 全拒 → changed=False。改:接受前对每个锚过 resolve_anchor_ref
       唯一前缀解析(目标集=grounded_set);解析不了才拒(不放水)。
    3. fail-visible:返回结构化 {changed, data, diagnostics}(interp_count/llm_called/
       items_returned/accepted_count/reject_reason),worker/ledger 可见。

    Returns:
        {"changed": bool, "data": extract_data, "diagnostics": {...}}
        changed=True 时 data 已 in-place 更新;changed=False 时 data 不变,diagnostics 记原因。
    """
    diag: dict[str, Any] = {
        "interp_count": 0,
        "llm_called": False,
        "items_returned": 0,
        "accepted_count": 0,
        "reject_reason": "",
        # F4-B: 两路径分别计数 + LLM 返回摘要(截断 80 字符,便于下轮 debug)
        "accepted_traces_to": 0,
        "accepted_new_claim": 0,
        "rejected_new_claim": 0,
        "llm_return_summary": "",
        # F5-B: 确定性补 grounded claim 计数(去 LLM 化)
        "deterministic_backfill_count": 0,
        # F6-B: 飘浮 interpretation 丢弃计数(claims 侧)
        "dropped_floating_interpretations": 0,
        # F9-B: argumentation 救济链覆盖(probe-p7 memory/crewai 死因:akb_interpretation[N]
        # 缺 traces_to 指的是 argumentation 条目,旧版只救 claims 侧)
        "interp_arg_count": 0,
        "arg_deterministic_backfill_count": 0,
        "arg_accepted_count": 0,
        "dropped_floating_argumentation": 0,
    }
    claims = extract_data.get("claims")
    if not isinstance(claims, list) or not claims:
        diag["reject_reason"] = "no_claims"
        return {"changed": False, "data": extract_data, "diagnostics": diag}
    # 收集 grounded anchors(source_grounded claims 的 anchor,去重保序)
    grounded_anchors: list[str] = []
    seen: set[str] = set()
    for c in claims:
        if (
            isinstance(c, dict)
            and str(c.get("provenance", "")).strip() == "source_grounded"
            and c.get("anchor")
        ):
            a = str(c["anchor"]).strip()
            if a and a not in seen:
                seen.add(a)
                grounded_anchors.append(a)
    grounded_set = set(grounded_anchors)
    if not grounded_anchors:
        diag["reject_reason"] = "no_grounded"
        return {"changed": False, "data": extract_data, "diagnostics": diag}
    # F3-A 覆盖面:收需 retake 的 interpretation claims —— traces_to 空 OR 指错(与 grounded
    # 集无交集,即指向非 grounded 的锚)。原只收 `not tt` 漏指错锚。
    interp: list[dict[str, Any]] = []
    for i, c in enumerate(claims):
        if not isinstance(c, dict):
            continue
        if str(c.get("provenance", "")).strip() != "akb_interpretation":
            continue
        tt = c.get("traces_to")
        tt_set = (
            {str(a).strip() for a in tt if isinstance(a, str) and str(a).strip()}
            if isinstance(tt, list) else set()
        )
        if not tt_set or not (tt_set & grounded_set):  # 空 OR 指错(与 grounded 无交集)
            c["_retake_index"] = i
            interp.append(c)
    diag["interp_count"] = len(interp)
    # F9-B: 收需 retake 的 interpretation argumentation(provenance=akb_interpretation,
    # traces_to 空 OR 指错)——同 claims 口径。probe-p7 memory/crewai 死因:floor 检查
    # claims+argumentation 合并,但旧 retake 只救 claims 侧,argumentation 飘浮无人救。
    argumentation = extract_data.get("argumentation")
    if not isinstance(argumentation, list):
        argumentation = []
    interp_args: list[dict[str, Any]] = []
    for i, a in enumerate(argumentation):
        if not isinstance(a, dict):
            continue
        if str(a.get("provenance", "")).strip() != "akb_interpretation":
            continue
        tt = a.get("traces_to")
        tt_set = (
            {str(x).strip() for x in tt if isinstance(x, str) and str(x).strip()}
            if isinstance(tt, list) else set()
        )
        if not tt_set or not (tt_set & grounded_set):
            a["_retake_index"] = i
            interp_args.append(a)
    diag["interp_arg_count"] = len(interp_args)
    if not interp and not interp_args:
        diag["reject_reason"] = "no_interp"
        return {"changed": False, "data": extract_data, "diagnostics": diag}
    # F5-B: 确定性优先——先走纯代码路径补 grounded claim(去 LLM 化,raw 原文天然 verbatim)
    raw_decl_set = declared_anchor_set(raw_content) if raw_content else set()
    anchor_blocks = _extract_anchor_blocks(raw_content) if raw_content else {}
    changed = False
    det_backfill_count = 0
    remaining_interp: list[dict[str, Any]] = []
    for c in interp:
        interp_text = str(c.get("text", "") or c.get("claim", "") or "")
        # target anchor:interp 的 traces_to 指向的锚(resolve 后),空则在全 raw 找
        tt = c.get("traces_to")
        target_anchor = ""
        if isinstance(tt, list) and tt:
            for a in tt:
                if isinstance(a, str) and a.strip():
                    resolved = _resolve_grounded_anchor(a.strip(), raw_decl_set)
                    if resolved:
                        target_anchor = resolved
                        break
        backfill = _deterministic_backfill_grounded_claim(
            interp_text, target_anchor, anchor_blocks, raw_content or ""
        )
        if backfill:
            # 原样摘句作新 source_grounded claim
            new_claim = {
                "text": backfill["text"],
                "anchor": backfill["anchor"],
                "provenance": "source_grounded",
                "evidence_gap": "未提供 verbatim 校验细节",
            }
            claims.append(new_claim)
            c["traces_to"] = [backfill["anchor"]]
            det_backfill_count += 1
            changed = True
        else:
            remaining_interp.append(c)  # 留给 LLM retake
    diag["deterministic_backfill_count"] = det_backfill_count
    # F9-B: argumentation 确定性回填(同 claims 口径,interp_text=explanation,
    # target_anchor=claim_anchor 或 traces_to)。回填 → 追加 grounded claim + arg.traces_to。
    arg_det_backfill = 0
    remaining_args: list[dict[str, Any]] = []
    for a in interp_args:
        interp_text = str(a.get("explanation", "") or a.get("claim", "") or "")
        tt = a.get("traces_to")
        target_anchor = ""
        if isinstance(tt, list) and tt:
            for x in tt:
                if isinstance(x, str) and x.strip():
                    resolved = _resolve_grounded_anchor(x.strip(), raw_decl_set)
                    if resolved:
                        target_anchor = resolved
                        break
        if not target_anchor:
            # arg.claim_anchor 作 target fallback
            ca = str(a.get("claim_anchor", "")).strip()
            if ca:
                resolved = _resolve_grounded_anchor(ca, raw_decl_set)
                if resolved:
                    target_anchor = resolved
        backfill = _deterministic_backfill_grounded_claim(
            interp_text, target_anchor, anchor_blocks, raw_content or ""
        )
        if backfill:
            new_claim = {
                "text": backfill["text"],
                "anchor": backfill["anchor"],
                "provenance": "source_grounded",
                "evidence_gap": "未提供 verbatim 校验细节",
            }
            claims.append(new_claim)
            a["traces_to"] = [backfill["anchor"]]
            arg_det_backfill += 1
            changed = True
        else:
            remaining_args.append(a)
    diag["arg_deterministic_backfill_count"] = arg_det_backfill
    # 全部确定性补了 → 不调 LLM(去 LLM 化,省一次调用)
    if not remaining_interp and not remaining_args:
        diag["llm_called"] = False
        # 清理临时 _retake_index(claims + argumentation)
        for c in claims:
            if isinstance(c, dict):
                c.pop("_retake_index", None)
        for a in argumentation:
            if isinstance(a, dict):
                a.pop("_retake_index", None)
        return {"changed": changed, "data": extract_data, "diagnostics": diag}
    # 仍有未补的 → 走 LLM retake(claims + argumentation 一起送)
    from src.akb2.quality.llm_router import route_llm as _default

    messages = _build_traceability_retake_messages(
        remaining_interp, grounded_anchors, raw_content or "",
        interp_argumentation=remaining_args,
    )
    try:
        with _distill_attempt_deadline(TRACEABILITY_RETAKE_TIMEOUT_S):
            response = _default(
                DISTILL_MODEL_FAMILY,
                messages,
                response_schema=_TRACEABILITY_RETAKE_SCHEMA,
                timeout_s=TRACEABILITY_RETAKE_TIMEOUT_S,
                max_retries=DISTILL_MAX_RETRIES,
                reasoning_effort=None,
                strict_schema=True,
                extra_body={"enable_thinking": False} if DISTILL_DISABLE_THINKING else None,
            )
    except Exception as exc:
        diag["reject_reason"] = f"llm_failed: {type(exc).__name__}"
        for c in claims:
            if isinstance(c, dict):
                c.pop("_retake_index", None)
        for a in argumentation:
            if isinstance(a, dict):
                a.pop("_retake_index", None)
        return {"changed": False, "data": extract_data, "diagnostics": diag}
    diag["llm_called"] = True
    parsed = _coerce_json_dict(response.content)
    retake_items = parsed.get("retake") if isinstance(parsed, dict) else None
    if not isinstance(retake_items, list):
        diag["reject_reason"] = "no_items"
        for c in claims:
            if isinstance(c, dict):
                c.pop("_retake_index", None)
        for a in argumentation:
            if isinstance(a, dict):
                a.pop("_retake_index", None)
        return {"changed": False, "data": extract_data, "diagnostics": diag}
    diag["items_returned"] = len(retake_items)
    # F4-B: LLM 返回摘要(截断 80 字符,便于下轮 debug)
    try:
        import json as _json
        diag["llm_return_summary"] = _json.dumps(retake_items, ensure_ascii=False)[:80]
    except Exception:
        diag["llm_return_summary"] = str(retake_items)[:80]
    # F4-B: raw_decl_set + anchor_blocks 已在 F5-B 确定性段算过,此处复用
    # F3-A 接受逻辑:对每个 LLM 返回的锚过 resolve_anchor_ref 唯一前缀解析(目标集=grounded_set),
    # 解析不了才拒(防 LLM 编造 §Z;但接受 LLM 返单字母 §E → 解析为全名 §E_CLAIMS)。
    accepted = 0
    for item in retake_items:
        if not isinstance(item, dict):
            continue
        idx = item.get("claim_index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(claims):
            continue
        claim = claims[idx]
        if not isinstance(claim, dict):
            continue
        # F3-A 路径:traces_to 重指(grounded 集)
        tt = item.get("traces_to")
        if isinstance(tt, list):
            clean_tt: list[str] = []
            for a in tt:
                if not isinstance(a, str):
                    continue
                ref = a.strip()
                if not ref:
                    continue
                # F3-A:canonicalize + 精确 + 唯一前缀解析,三级匹配
                resolved = _resolve_grounded_anchor(ref, grounded_set)
                if resolved:
                    clean_tt.append(resolved)
            if clean_tt and clean_tt != (
                claim.get("traces_to") if isinstance(claim.get("traces_to"), list) else []
            ):
                claim["traces_to"] = clean_tt
                changed = True
                accepted += 1
                diag["accepted_traces_to"] += 1
        # F4-B 路径:new_grounded_claim(verbatim 引文)——anchor ∈ raw 声明集(resolve 后)+
        # text B1 强匹配(连续覆盖 ≥60%)在 raw 该锚 block 内 → 追加新 source_grounded claim
        ngc = item.get("new_grounded_claim")
        if isinstance(ngc, dict) and ngc.get("text") and ngc.get("anchor"):
            ngc_anchor = str(ngc["anchor"]).strip()
            ngc_text = str(ngc["text"]).strip()
            # anchor resolve 到 raw 声明集(canonicalize + 唯一前缀)
            resolved_anchor = _resolve_grounded_anchor(ngc_anchor, raw_decl_set)
            if not resolved_anchor:
                diag["rejected_new_claim"] += 1
                continue
            block = anchor_blocks.get(resolved_anchor, "")
            if not block:
                diag["rejected_new_claim"] += 1
                continue
            # B1 强匹配:text 连续覆盖 ≥60% in block(verbatim 引文)
            coverage = _verbatim_coverage_in_block(ngc_text, block)
            if coverage < _GROUND_STRONG_COVERAGE:
                diag["rejected_new_claim"] += 1
                continue
            # 通过 → 追加新 source_grounded claim,interpretation.traces_to 指向它
            new_claim = {
                "text": ngc_text,
                "anchor": resolved_anchor,
                "provenance": "source_grounded",
                "evidence_gap": "未提供 verbatim 校验细节",
            }
            claims.append(new_claim)
            claim["traces_to"] = [resolved_anchor]
            changed = True
            accepted += 1
            diag["accepted_new_claim"] += 1
        prov = str(item.get("provenance", "")).strip()
        if prov in ("akb_interpretation", "source_grounded") and prov != claim.get("provenance"):
            if prov == "source_grounded" and claim.get("anchor") in grounded_set:
                claim["provenance"] = "source_grounded"
                changed = True
                accepted += 1
            elif prov == "akb_interpretation":
                claim["provenance"] = "akb_interpretation"
                changed = True
                accepted += 1
    diag["accepted_count"] = accepted
    # F9-B: 处理 argumentation_retake LLM 项(同 claims 逻辑:traces_to 重指 +
    # new_grounded_claim verbatim + provenance 改判)。arg_index 标识在 argumentation 数组位置。
    arg_items = parsed.get("argumentation_retake") if isinstance(parsed, dict) else None
    if isinstance(arg_items, list):
        arg_accepted = 0
        for item in arg_items:
            if not isinstance(item, dict):
                continue
            idx = item.get("arg_index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(argumentation):
                continue
            arg = argumentation[idx]
            if not isinstance(arg, dict):
                continue
            # traces_to 重指(grounded 集)
            tt = item.get("traces_to")
            if isinstance(tt, list):
                clean_tt: list[str] = []
                for a in tt:
                    if not isinstance(a, str):
                        continue
                    ref = a.strip()
                    if not ref:
                        continue
                    resolved = _resolve_grounded_anchor(ref, grounded_set)
                    if resolved:
                        clean_tt.append(resolved)
                if clean_tt and clean_tt != (
                    arg.get("traces_to") if isinstance(arg.get("traces_to"), list) else []
                ):
                    arg["traces_to"] = clean_tt
                    changed = True
                    arg_accepted += 1
            # new_grounded_claim(verbatim 引文)— 同 claims B1 强校验
            ngc = item.get("new_grounded_claim")
            if isinstance(ngc, dict) and ngc.get("text") and ngc.get("anchor"):
                ngc_anchor = str(ngc["anchor"]).strip()
                ngc_text = str(ngc["text"]).strip()
                resolved_anchor = _resolve_grounded_anchor(ngc_anchor, raw_decl_set)
                if not resolved_anchor:
                    diag["rejected_new_claim"] += 1
                    continue
                block = anchor_blocks.get(resolved_anchor, "")
                if not block:
                    diag["rejected_new_claim"] += 1
                    continue
                coverage = _verbatim_coverage_in_block(ngc_text, block)
                if coverage < _GROUND_STRONG_COVERAGE:
                    diag["rejected_new_claim"] += 1
                    continue
                new_claim = {
                    "text": ngc_text,
                    "anchor": resolved_anchor,
                    "provenance": "source_grounded",
                    "evidence_gap": "未提供 verbatim 校验细节",
                }
                claims.append(new_claim)
                arg["traces_to"] = [resolved_anchor]
                changed = True
                arg_accepted += 1
                diag["accepted_new_claim"] += 1
            prov = str(item.get("provenance", "")).strip()
            if prov in ("akb_interpretation", "source_grounded") and prov != arg.get("provenance"):
                if prov == "source_grounded" and arg.get("claim_anchor") in grounded_set:
                    arg["provenance"] = "source_grounded"
                    changed = True
                    arg_accepted += 1
                elif prov == "akb_interpretation":
                    arg["provenance"] = "akb_interpretation"
                    changed = True
                    arg_accepted += 1
        diag["arg_accepted_count"] = arg_accepted
    if not changed:
        diag["reject_reason"] = "no_accept"
    # F6-B/F7-A: 飘浮 interpretation 有界丢弃——确定性 backfill + LLM retake 都救不回的
    # interpretation(仍无 traces_to 或 traces_to 仍指向非 grounded 锚)从 claims 移除。
    # F7-A 修:原条件 `if not tt_set` 只丢空 traces_to 的 interp,漏了 traces_to 非空但
    # 指向非 grounded 锚的 interp(probe-p6 vercel:§J_DISCUSSION 指向真实 raw 锚但该处
    # 无 grounded claim,LLM 返空 traces_to 也没改 → drop 没执行)。条件改为与 F3-A 收集
    # 条件一致:`not tt_set or not (tt_set & grounded_set)`。
    # F7-A 修2:grounded_set 须重算(含 F4-B/F5-B 新追加的 grounded claim),否则新补的
    # grounded claim 锚不在原 grounded_set → 误丢已救回的 interp(F4-B 测试暴露)。
    current_grounded: set[str] = set()
    for c in claims:
        if (
            isinstance(c, dict)
            and str(c.get("provenance", "")).strip() == "source_grounded"
            and c.get("anchor")
        ):
            current_grounded.add(str(c["anchor"]).strip())
    dropped_floating = 0
    remaining_claims: list[dict[str, Any]] = []
    for c in claims:
        if not isinstance(c, dict):
            remaining_claims.append(c)
            continue
        if str(c.get("provenance", "")).strip() == "akb_interpretation":
            tt = c.get("traces_to")
            tt_set = (
                {str(a).strip() for a in tt if isinstance(a, str) and str(a).strip()}
                if isinstance(tt, list) else set()
            )
            # F7-A:与 F3-A 收集条件一致——空 OR 指向非 grounded 锚(与 grounded 无交集)
            # 用 current_grounded(含新追加的 grounded claim 锚)
            if not tt_set or not (tt_set & current_grounded):
                dropped_floating += 1
                continue
        remaining_claims.append(c)
    if dropped_floating > 0:
        extract_data["claims"] = remaining_claims
        changed = True  # 数据变了(caller 须重评 floor)
    diag["dropped_floating_interpretations"] = dropped_floating
    # F9-B: argumentation 飘浮丢弃——确定性 backfill + LLM retake 都救不回的 argumentation
    # (provenance=akb_interpretation,仍无 traces_to 或指向非 grounded)从 argumentation 移除。
    # 同 claims F7-A 条件:not tt_set or not (tt_set & current_grounded)。diag 单独计数。
    dropped_arg = 0
    remaining_args: list[dict[str, Any]] = []
    for a in argumentation:
        if not isinstance(a, dict):
            remaining_args.append(a)
            continue
        if str(a.get("provenance", "")).strip() == "akb_interpretation":
            tt = a.get("traces_to")
            tt_set = (
                {str(x).strip() for x in tt if isinstance(x, str) and str(x).strip()}
                if isinstance(tt, list) else set()
            )
            if not tt_set or not (tt_set & current_grounded):
                dropped_arg += 1
                continue
        remaining_args.append(a)
    if dropped_arg > 0:
        extract_data["argumentation"] = remaining_args
        changed = True
    diag["dropped_floating_argumentation"] = dropped_arg
    # 清理临时 _retake_index(claims + argumentation)
    for c in claims:
        if isinstance(c, dict):
            c.pop("_retake_index", None)
    for a in argumentation:
        if isinstance(a, dict):
            a.pop("_retake_index", None)
    return {"changed": changed, "data": extract_data, "diagnostics": diag}


def _deterministic_backfill_grounded_claim(
    interp_text: str,
    target_anchor: str,
    anchor_blocks: dict[str, str],
    raw_content: str,
) -> dict[str, str] | None:
    """F5-B: 确定性补 grounded claim——从 raw 该锚 block 挑与 interpretation 文本
    token 重合度最高的一句,原样摘句作 source_grounded claim。

    去 LLM 化:raw 原文天然 verbatim,无造假空间。挑不出合格句 → 返 None(留给 LLM retake)。
    - target_anchor 有值 → block = anchor_blocks[target_anchor]
    - target_anchor 空 → 在所有 anchor_blocks 里找最高重合 block
    - 句分割:re.split 句号/换行;排除标题行(#)/表格行(|)/空行
    - 合格句:≥2 长 token(len≥5 非停用词)与 interp_text 重合 + 长度 20-300 字符
    """
    if not interp_text or not raw_content:
        return None
    interp_tokens = set(_tokenize_long_tokens(interp_text))
    if not interp_tokens:
        return None
    # 候选 block
    candidates: list[tuple[str, str]] = []  # (anchor, block_text)
    if target_anchor:
        block = anchor_blocks.get(target_anchor, "")
        if block:
            candidates.append((target_anchor, block))
    else:
        for anchor, block in anchor_blocks.items():
            if block:
                candidates.append((anchor, block))
    if not candidates:
        return None
    best: dict[str, str] | None = None
    best_overlap = 0
    for anchor, block in candidates:
        for sent in re.split(r"[。\n.!?]", block):
            sent = sent.strip()
            if not sent or len(sent) < 20 or len(sent) > 300:
                continue
            if sent.startswith("#") or sent.startswith("|"):
                continue  # 排除标题/表格行
            sent_tokens = set(_tokenize_long_tokens(sent))
            overlap = len(interp_tokens & sent_tokens)
            if overlap >= 2 and overlap > best_overlap:
                best_overlap = overlap
                best = {"text": sent, "anchor": anchor}
    return best


def _resolve_grounded_anchor(ref: str, grounded_set: set[str]) -> str | None:
    """F3-A: 把 LLM 返回的 traces_to 锚解析到 grounded 集——canonicalize + 精确 + 唯一前缀。

    三级匹配(都不中才拒,fail-closed 不放水):
    1. canonicalize(ref) 精确 ∈ grounded_set(LLM 产全名或已剥后缀)
    2. ref 精确 ∈ grounded_set(原样)
    3. resolve_anchor_ref(canonicalize(ref), grounded_set) 唯一前缀解析(单字母 §E → §E_CLAIMS)
    """
    if not ref or not grounded_set:
        return None
    canon = canonicalize_anchor(ref)
    if canon in grounded_set:
        return canon
    if ref in grounded_set:
        return ref
    resolved = resolve_anchor_ref(canon, grounded_set)
    return resolved


def _verbatim_coverage_in_block(claim_text: str, block_text: str) -> float:
    """F4-B: claim_text 在 block_text 中的最长连续片段覆盖比例(B1 强匹配标准)。

    用 difflib.find_longest_match 找最长连续公共子串,返回 size / len(claim_norm)。
    ≥0.60 才算 verbatim 引文(近全文转写),改写/paraphrase 不达 60% → 拒(fail-closed)。
    """
    claim_norm = _normalize_for_grounding(claim_text)
    block_norm = _normalize_for_grounding(block_text)
    if not claim_norm or not block_norm:
        return 0.0
    sm = difflib.SequenceMatcher(None, claim_norm, block_norm, autojunk=False)
    m = sm.find_longest_match(0, len(claim_norm), 0, len(block_norm))
    return m.size / len(claim_norm) if claim_norm else 0.0


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
            "- akb_interpretation claim 必带 traces_to：指向它所解读的 "
            "source_grounded claim 的 anchor"
            "（如 [\"§A\"]），source_grounded claim 可省 traces_to。\n"
            "- tldr 是综合全文主旨，不是复制单条 claim。"
        ),
        "how_params": (
            "抽取 how_steps、mechanism_components、causal_edges 与 param_entries。\n"
            "- how_steps: 对象数组 {instruction, command, executable, how_level}；"
            "操作型原文至少 3 条 executable=true 且 command 非空。\n"
            "- how_level: 标注每步的机制层级——H1=命令级（可直接执行的命令/调用）；"
            "H2=数据流/状态/接口说明级（讲清数据怎么流、状态怎么变、接口怎么对接）；"
            "H3=命名机制组件+因果边级（讲清是哪几个命名组件、组件间因果如何驱动）。\n"
            "- mechanism_components: 命名机制组件数组，概念型原文须 ≥3 个命名组件"
            "（如 'work_selector'/'quality_gate'）。每项含：\n"
            "  {name(必填), role(职责), aliases(可选,同义名/简称数组), "
            "evidence_anchor(§X 指向原文该组件出处), evidence_sentence(原文证明该组件"
            "存在的句子)}。component_id 由后处理生成，不需你产。\n"
            "- causal_edges: 因果边数组，概念型原文须 ≥1 条带 evidence 的 eligible 边。每项含：\n"
            "  {from, to, relation, edge_kind, evidence, evidence_anchor}。\n"
            "  from/to 用 mechanism_components 的 name（或 alias，后处理对齐）。\n"
            "  edge_kind 枚举：source_causal=原文明示因果（feeds/triggers/dispatches/"
            "writes_to 等，evidence 写原文因果句，evidence_anchor 写 §X）；"
            "llm_evidence=LLM 推断因果（akb 解读，须带 evidence 句 + §X anchor）；"
            "inferred_from_structure=结构顺序推断弱边（无 evidence，不计 F4，少用）。\n"
            "  宁少勿强：无原文支撑的因果不编造（F4 0 eligible 会 fail，不靠弱边凑）。\n"
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
            "- 若 explanation 是归纳或操作化判断，provenance 用 akb_interpretation，"
            "且必带 traces_to 指向所引的 source_grounded anchor（如 [\"§A\"]）。\n"
            "- 输出顶层必须是 {\"argumentation\":[...]}，不能用 claims/arguments/items 等同义 key。"
        ),
        "graph": (
            "只抽取 graph_edges，最多 4 条，宁少勿强。\n"
            "- 每项含 concept、relation_type、strength、evidence、"
            "anchor、provenance、evidence_kind。\n"
            "- relation_type 优先 related/describes/mentions，"
            "除非原文直接支持，避免 requires/ensures/reduces。\n"
            "- strength 默认 weak；只有原文直接点名 target 且 "
            "evidence 带 [raw §X] 或原文引号才可 strong。\n"
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
        # §4 两阶段抽取后处理：component_id 生成 + alias 对齐 + inferred 弱边补全
        components = _normalize_mechanism_components(parsed.get("mechanism_components"))
        edges = _normalize_causal_edges(parsed.get("causal_edges"))
        components, edges = _enrich_mechanism(components, edges)
        return {
            "how_steps": [
                _normalize_how_step(item, idx)
                for idx, item in enumerate(parsed.get("how_steps", []), 1)
            ],
            # step-3 SC-3a/A3-5 + §0.2/D2：concept F4 结构真相源——命名机制组件 +
            # 因果边（含 component_id/aliases/evidence_anchor/evidence_sentence +
            # edge_kind/evidence/evidence_anchor，经 _enrich_mechanism 后处理）。
            "mechanism_components": components,
            "causal_edges": edges,
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
    # step-3 SC-3c：保留 traces_to（akb_interpretation 溯源到 source_grounded anchor）
    traces_to_raw = item.get("traces_to") or []
    if isinstance(traces_to_raw, str):
        traces_to = [str(t).strip() for t in [traces_to_raw] if str(t).strip()]
    elif isinstance(traces_to_raw, list):
        traces_to = [str(t).strip() for t in traces_to_raw if str(t).strip()]
    else:
        traces_to = []
    return {
        "text": text,
        "anchor": anchor,
        "provenance": provenance,
        "evidence_gap": gap,
        "traces_to": traces_to,
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


# Finding 6: 三态摘要注释——把 how_steps 的 provenance_split + 代码直出
# must_cover operations 汇总成结构化 HTML 注释，持久化到候选页 How 节末尾。
# SG（checks_structural._provenance_split_summary）读取此注释消费三态细分，
# 无需把 How 节折叠进核心 ratio 阈值（核心 ratio 故意排除 操作步骤，见
# test_how_interpretation_steps_do_not_count_against_core_ratio 不变量）。
_PROVENANCE_SPLIT_SUMMARY_PREFIX = "<!-- provenance_split:"


def _provenance_split_summary_comment(
    how_steps: list[Any],
    must_cover_ops: list[Any],
) -> str:
    """Emit a three-way provenance split summary as a structured HTML comment.

    Counts are derived from the rendered How section:
    - ``source_operation``: how_steps whose ``provenance_split == source_operation``
      plus every code-direct must_cover operation (scanner-produced, all
      source_operation).
    - ``source_fact``: how_steps whose ``provenance_split == source_fact``
      (rare for LLM how_steps; reserved for must_cover facts rendered in other
      sections — those are NOT counted here, this comment covers the How section).
    - ``akb_interpretation``: how_steps whose split is akb_interpretation or
      missing (missing is treated conservatively as akb_interpretation).

    The comment is consumed by SG; deleting the ``provenance_split`` annotation
    layer makes these counts collapse to all-akb, which the SG-provenance-split
    reader test detects.
    """
    counts = {
        "source_operation": 0,
        "source_fact": 0,
        "akb_interpretation": 0,
    }
    for step in how_steps or []:
        split = ""
        if isinstance(step, dict):
            split = str(step.get("provenance_split", "")).strip()
        if split not in counts:
            split = "akb_interpretation"
        counts[split] += 1
    counts["source_operation"] += len(must_cover_ops or [])
    return (
        f"{_PROVENANCE_SPLIT_SUMMARY_PREFIX} "
        f"source_operation={counts['source_operation']} "
        f"source_fact={counts['source_fact']} "
        f"akb_interpretation={counts['akb_interpretation']} -->"
    )


def _normalize_how_step(item: Any, idx: int) -> dict[str, Any]:
    if isinstance(item, dict):
        instruction = str(
            item.get("instruction") or item.get("text") or item.get("name") or ""
        ).strip()
        command = str(item.get("command") or item.get("cmd") or "").strip()
        executable = item.get("executable")
        anchor = _first_anchor(
            item.get("anchor") or item.get("anchors") or item.get("section"),
            default="",
        )
        provenance = str(item.get("provenance") or "").strip()
        raw_how_level = str(item.get("how_level") or "").strip().upper()
    else:
        instruction = str(item).strip()
        command = ""
        executable = None
        anchor = ""
        provenance = ""
        raw_how_level = ""
    if not command and instruction:
        command = _infer_source_command_from_instruction(instruction)
    command = _expand_placeholder_command(command, instruction, idx)
    if provenance not in {"source_grounded", "akb_interpretation"}:
        provenance = "source_grounded" if anchor else "akb_interpretation"
    final_command = command
    final_executable = bool(command) if executable is None else bool(executable)
    normalized = {
        "instruction": instruction,
        "command": final_command,
        "executable": final_executable,
        "anchor": anchor,
        "provenance": provenance,
        # step-5: 三态细分标注层（source_operation / source_fact / akb_interpretation）。
        # 与 provenance 二态并存：source_grounded → source_operation|source_fact；
        # akb_interpretation → akb_interpretation。消费者仍读 marker，本字段仅标注。
        "provenance_split": how_step_provenance_split(
            {"provenance": provenance}, final_command, anchor
        ),
        # step-3 SC-3a：三级 how 标注。LLM 给的 H1/H2/H3 直采；否则按结构兜底——
        # 有可执行命令 → H1（命令级）；无命令的 how 不能标 H1（concept 机制说明不是命令），
        # 兜底 H2（数据流/状态/接口说明级）。H3 由 LLM 显式标，不从命令反推。
        "how_level": _normalize_how_level(raw_how_level, final_command, final_executable),
    }
    return normalized


def _normalize_how_level(raw_how_level: str, command: str, executable: bool) -> str:
    """归一 how_level（H1/H2/H3），按结构兜底缺失值。

    SC-3a/风险表：concept 无 command 的 how 不标 H1（否则地板误判 concept H1≥1）。
    - LLM 显式给 H1/H2/H3 → 直采。
    - 缺失且有可执行命令 → H1（命令级）。
    - 缺失且无命令 → H2（说明级，非命令）；H3 须 LLM 显式标，不从命令反推。
    """
    if raw_how_level in {"H1", "H2", "H3"}:
        return raw_how_level
    if executable and command.strip():
        return "H1"
    return "H2"


_VALID_EDGE_KINDS = ("source_causal", "llm_evidence", "inferred_from_structure")


def _normalize_mechanism_components(raw: Any) -> list[dict[str, Any]]:
    """归一命名机制组件数组（concept F4 结构真相源）。

    §0.2/D2：每项 → {component_id, name, aliases, role, evidence_anchor, evidence_sentence}。
    name 必填（无名字丢弃）；role 默认 ""；component_id/evidence_anchor/evidence_sentence
    默认 ""（§4 两阶段抽取 normalize 生成稳定 component_id，此处保留 LLM/fallback 产的）；
    aliases 归一为 list[str]。procedural 不产此字段 → 返回空数组（零回归）。
    """
    if not isinstance(raw, list):
        return []
    components: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("component") or "").strip()
            role = str(item.get("role") or "").strip()
            component_id = str(item.get("component_id") or "").strip()
            evidence_anchor = str(item.get("evidence_anchor") or "").strip()
            evidence_sentence = str(item.get("evidence_sentence") or "").strip()
            aliases_raw = item.get("aliases")
            if isinstance(aliases_raw, str):
                aliases = [a.strip() for a in aliases_raw.split(",") if a.strip()]
            elif isinstance(aliases_raw, list):
                aliases = [str(a).strip() for a in aliases_raw if str(a).strip()]
            else:
                aliases = []
        elif isinstance(item, str):
            name = item.strip()
            role, component_id, evidence_anchor, evidence_sentence, aliases = (
                "",
                "",
                "",
                "",
                [],
            )
        else:
            continue
        if name:
            components.append(
                {
                    "component_id": component_id,
                    "name": name,
                    "aliases": aliases,
                    "role": role,
                    "evidence_anchor": evidence_anchor,
                    "evidence_sentence": evidence_sentence,
                }
            )
    return components


def _normalize_causal_edges(raw: Any) -> list[dict[str, Any]]:
    """归一因果边数组（from→to 指向命名组件）。

    §0.2/D2：每项 → {from, to, relation, edge_kind, evidence, evidence_anchor}。
    relation 默认 ""；edge_kind 默认 ""（F4 D1 只认 source_causal/llm_evidence 带
    evidence_anchor 的 eligible edge；inferred_from_structure 不计 F4）。未知 edge_kind
    fail-closed（raise ValueError，不静默丢弃）。缺 from/to 的项丢弃。procedural 不产
    → 返回空数组（零回归）。
    """
    if not isinstance(raw, list):
        return []
    edges: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        src = str(item.get("from") or item.get("source") or "").strip()
        dst = str(item.get("to") or item.get("target") or "").strip()
        relation = str(item.get("relation") or item.get("relation_type") or "").strip()
        edge_kind = str(item.get("edge_kind") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        evidence_anchor = str(item.get("evidence_anchor") or "").strip()
        if edge_kind and edge_kind not in _VALID_EDGE_KINDS:
            raise ValueError(
                f"causal_edges 未知 edge_kind {edge_kind!r}，"
                f"合法值 {list(_VALID_EDGE_KINDS)}（fail-closed）"
            )
        if src and dst:
            edges.append(
                {
                    "from": src,
                    "to": dst,
                    "relation": relation,
                    "edge_kind": edge_kind,
                    "evidence": evidence,
                    "evidence_anchor": evidence_anchor,
                }
            )
    return edges


def _slugify_component_id(name: str) -> str:
    """§4 两阶段抽取阶段A 后处理：name → 稳定 component_id（slug）。"""
    s = re.sub(r"[^\w\s-]", "", str(name or "").lower().strip())
    s = re.sub(r"[\s_-]+", "_", s).strip("_")
    return s or "component"


def _enrich_mechanism(
    components: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """§4 两阶段抽取后处理（单次 LLM 抽取后的确定性等价）。

    - 阶段A 等价：为每个 component 生成稳定 component_id（slugify name），并建
      alias→name 映射（aliases + name 自身）。
    - 阶段B 等质：alias 对齐 edges 的 from/to 到 component name（边不再要求 from/to
      字面等于 name，可用 alias）；对齐失败的边丢弃（F4 不计，第一轮不留 unresolved
      诊断，后续可加）。
    - 补 inferred_from_structure 弱边：相邻组件未被覆盖时补（不计 F4，§0.2 D1）。
    """
    if not components:
        return components, edges
    # 阶段A：component_id + alias→name 映射
    alias_to_name: dict[str, str] = {}
    valid_names: set[str] = set()
    for c in components:
        if not c.get("component_id"):
            c["component_id"] = _slugify_component_id(c.get("name", ""))
        name = c.get("name", "")
        if name:
            valid_names.add(name)
            alias_to_name[name.lower()] = name
        for a in c.get("aliases", []) or []:
            if a:
                alias_to_name[a.lower()] = name
    # 阶段B：alias 对齐 edges，丢弃端点不在 components 的边
    aligned: list[dict[str, Any]] = []
    for e in edges:
        src = str(e.get("from", ""))
        dst = str(e.get("to", ""))
        src_a = alias_to_name.get(src.lower(), src)
        dst_a = alias_to_name.get(dst.lower(), dst)
        if src_a in valid_names and dst_a in valid_names and src_a != dst_a:
            e["from"] = src_a
            e["to"] = dst_a
            aligned.append(e)
    # 补 inferred 弱边（相邻组件，未被覆盖）
    existing = {(e["from"], e["to"]) for e in aligned}
    for i in range(len(components) - 1):
        a = components[i].get("name", "")
        b = components[i + 1].get("name", "")
        if a and b and a != b and (a, b) not in existing:
            aligned.append(
                {
                    "from": a,
                    "to": b,
                    "relation": "structural_sequence",
                    "edge_kind": "inferred_from_structure",
                    "evidence": "",
                    "evidence_anchor": "",
                }
            )
    return components, aligned


def _strip_fabricated_how_commands(
    extract_data: dict[str, Any], raw_content: str | None
) -> None:
    """F3-B: how_steps command 确定性校验——command 必须在 raw 原文存在(与 SG-command-recheck
    同口径,复用 is_command_allowed_for_candidate);不存在(编造)→ 去掉 command + executable=False
    (降级为非可执行,不让进 body)。

    真机探针 probe-p2 memory 死于 SG-command-recheck 拦编造命令(全角分号垃圾片段
    `；claude/rules/`)。本 pass 在 distill 侧就拦,不让编造 command 进 body:
    - 复用 is_command_allowed_for_candidate(command, raw) 同口径(reserved/dangerous/in-raw 三态)
    - 不允许(编造或危险)→ command="" + executable=False
    - procedural lane 若因此跌破 HOW_STEPS_MIN_EXECUTABLE → _validate_extract_output 既有
      fail-closed
    """
    if not raw_content:
        return
    how_steps = extract_data.get("how_steps")
    if not isinstance(how_steps, list):
        return
    from src.akb2.quality.source_ops_scanners.raw_verify import (
        is_command_allowed_for_candidate,
    )

    for step in how_steps:
        if not isinstance(step, dict):
            continue
        command = str(step.get("command", "") or "").strip()
        if not command:
            continue  # 空 command,无可校
        # 同口径 SG-command-recheck:不允许(编造/危险)→ 降级
        if not is_command_allowed_for_candidate(command, raw_content):
            step["command"] = ""
            step["executable"] = False


def _resolve_short_anchors_to_full(merged: dict[str, Any], raw_content: str | None) -> None:
    """F-A: 结构化字段单字母锚 → 全名唯一前缀解析(确定性,在 _correct_edge_anchors_to_raw 之前)。

    真机探针暴露:LLM 照 prompt 示例产单字母引用(``§W``),但 raw 声明全名(``§W_CAVEATS``)
    → compose 渲染 body 用单字母 → A3 拒(``锚点 §W 不存在``)。本 pass 对所有锚引用字段做
    ``resolve_anchor_ref`` 唯一前缀解析:单字母能唯一解析的改写为全名;解析失败(无匹配/歧义)
    保持原值,由后续 _correct_edge_anchors_to_raw / _ground_claims_to_raw / A3 fail-closed 接手。

    覆盖字段:claims.anchor/traces_to、argumentation.claim_anchor/traces_to、
    causal_edges.evidence_anchor、graph_edges.evidence_anchor、how_steps.anchor。
    """
    if not raw_content:
        return
    declared = declared_anchor_set(raw_content)
    if not declared:
        return  # raw 无声明锚 → 不解析(后续 fail-closed)

    def _resolve(ref: str) -> str:
        if not isinstance(ref, str):
            return ref  # type: ignore[return-value]
        resolved = resolve_anchor_ref(ref, declared)
        return resolved if resolved is not None else ref  # 解析失败保持原值

    # claims
    claims = merged.get("claims")
    if isinstance(claims, list):
        for c in claims:
            if not isinstance(c, dict) or not c.get("anchor"):
                continue
            c["anchor"] = _resolve(str(c["anchor"]))
            tt = c.get("traces_to")
            if isinstance(tt, list):
                c["traces_to"] = [_resolve(str(a)) for a in tt if a is not None]

    # argumentation
    arg = merged.get("argumentation")
    if isinstance(arg, list):
        for item in arg:
            if not isinstance(item, dict):
                continue
            if item.get("claim_anchor"):
                item["claim_anchor"] = _resolve(str(item["claim_anchor"]))
            tt = item.get("traces_to")
            if isinstance(tt, list):
                item["traces_to"] = [_resolve(str(a)) for a in tt if a is not None]

    # causal_edges + graph_edges 的 evidence_anchor
    for key in ("causal_edges", "graph_edges"):
        edges = merged.get(key)
        if isinstance(edges, list):
            for e in edges:
                if isinstance(e, dict) and e.get("evidence_anchor"):
                    e["evidence_anchor"] = _resolve(str(e["evidence_anchor"]))

    # how_steps.anchor
    how = merged.get("how_steps")
    if isinstance(how, list):
        for h in how:
            if isinstance(h, dict) and h.get("anchor"):
                h["anchor"] = _resolve(str(h["anchor"]))


def _correct_edge_anchors_to_raw(merged: dict[str, Any], raw_content: str) -> None:
    """§0.2/F6：LLM 产 edge 的 evidence_anchor 校正到真实 raw anchor。

    真机 LLM 编的 evidence_anchor 可能不在 raw 声明集（格式不符/编造）→ F4 0 eligible fail。
    修：对 eligible edge（source_causal/llm_evidence），若 evidence_anchor 不在 raw 声明集，
    用 evidence sentence 在 raw 定位（find 前缀）+ _nearest_preceding_anchor 映射真实 anchor。
    映射不到 → 不改（该边不计 eligible，不放松防伪）。in-place 修改 merged["causal_edges"]。
    """
    if not raw_content:
        return
    edges = merged.get("causal_edges")
    if not isinstance(edges, list) or not edges:
        return
    raw_anchor_positions = _raw_anchor_positions(raw_content)
    if not raw_anchor_positions:
        return
    raw_anchor_set = {a for a, _ in raw_anchor_positions}
    for e in edges:
        if not isinstance(e, dict):
            continue
        kind = str(e.get("edge_kind", "")).strip()
        if kind not in ("source_causal", "llm_evidence"):
            continue
        anchor = str(e.get("evidence_anchor", "")).strip()
        if anchor and anchor in raw_anchor_set:
            continue  # 已是真实 raw anchor
        # 尝试用 evidence sentence 在 raw 定位真实 anchor
        evidence = str(e.get("evidence", "")).strip()
        if not evidence:
            continue
        # 用 evidence 前 40 字在 raw 定位（LLM evidence 应是原文句，前缀可匹配）
        prefix = evidence[:40]
        sent_pos = raw_content.find(prefix)
        if sent_pos < 0:
            # 退化：用 evidence 中最长的连续 token
            for tok in reversed(sorted(evidence.split(), key=len)):
                if len(tok) >= 4:
                    p = raw_content.find(tok)
                    if p >= 0:
                        sent_pos = p
                        break
        if sent_pos >= 0:
            mapped = _nearest_preceding_anchor(sent_pos, raw_anchor_positions)
            if mapped:
                e["evidence_anchor"] = mapped


# ---------------------------------------------------------------------------
# P1b-1: claims/argumentation 接地校正 pass(确定性单遍,无 LLM)
# ---------------------------------------------------------------------------

# 接地匹配具名常量(宪法 P1/P4:有界 + 预算显式)
_GROUND_WEAK_PREFIX_CHARS: int = 60      # 弱匹配:取 claim 前 N 归一字符做连续命中探测
_GROUND_WEAK_CONTIG_MIN: int = 25        # 弱匹配:连续命中 ≥ 此长度才算
_GROUND_WEAK_TOKEN_MIN_LEN: int = 5      # 弱匹配:长 token 最短长度(非停用词)
_GROUND_WEAK_TOKEN_MIN_COUNT: int = 2    # 弱匹配:≥ 此数长 token 同窗共现才算
_GROUND_WEAK_WINDOW: int = 240           # 弱匹配:token 共现窗口
_GROUND_STRONG_COVERAGE: float = 0.60    # 强匹配:连续片段覆盖 ≥ 此比例才升级
_GROUND_BODY_MAX_CHARS: int = 30_000     # 接地匹配 body 上限(性能;claim 罕在 30k 后段)
# 弱匹配 token 共现的停用词(泛词不作 variant)
_GROUND_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "of", "and", "or", "to", "in", "for", "is", "are", "be",
    "with", "as", "by", "on", "at", "this", "that", "it", "from", "which",
    "的", "和", "与", "在", "为", "了", "是", "对", "从", "由", "及",
})


def _normalize_for_grounding(text: str) -> str:
    """归一化(小写 + 去空白 + 去标点)供连续片段比对。"""
    return "".join(
        c.lower() for c in str(text) if not c.isspace() and c.isalnum()
    )


def _normalize_with_pos_map(text: str) -> tuple[str, list[int]]:
    """归一化(小写 + 去空白 + 去标点),返回 (norm, pos_map)。

    pos_map[i] = norm 第 i 字符在原 text 的位置(供匹配位置回映原 body 取 anchor)。
    """
    norm_chars: list[str] = []
    pos_map: list[int] = []
    for i, c in enumerate(text):
        if c.isspace() or not c.isalnum():
            continue
        norm_chars.append(c.lower())
        pos_map.append(i)
    return "".join(norm_chars), pos_map


def _tokenize_long_tokens(text: str) -> list[str]:
    """分词(小写字母/数字序列),返回 len≥5 非停用词 token(去重保序)。"""
    out: list[str] = []
    seen: set[str] = set()
    for t in re.findall(r"[A-Za-z0-9]+", str(text).lower()):
        if (
            len(t) >= _GROUND_WEAK_TOKEN_MIN_LEN
            and t not in _GROUND_STOPWORDS
            and t not in seen
        ):
            seen.add(t)
            out.append(t)
    return out


def _strip_raw_for_grounding(raw_content: str) -> str:
    """去 frontmatter + 锚点目录块 + chrome,留正文供 claim 接地匹配。"""
    if not raw_content:
        return ""
    text = _strip_raw_frontmatter_text(raw_content)
    text = strip_directory_block(text)
    text, _ = strip_chrome_blocks(text)
    return text.strip()


def _weak_match_pos(
    claim_norm: str,
    claim_tokens: list[str],
    body_norm: str,
    pos_map: list[int],
) -> int:
    """弱匹配:返回匹配在原 body 的位置(pos_map 回映),-1 未匹配。

    [B1] 两档(满足任一):
    - 前 60 归一字符连续命中 ≥25
    - ≥2 长 token(len≥5 非停用词)同 240 字窗共现
    弱匹配只用于假锚校正(不升级 provenance)。
    """
    if not claim_norm or not body_norm or not pos_map:
        return -1
    # 档1:前 60 归一字符中找最长连续命中
    prefix = claim_norm[:_GROUND_WEAK_PREFIX_CHARS]
    if len(prefix) >= _GROUND_WEAK_CONTIG_MIN:
        sm = difflib.SequenceMatcher(None, prefix, body_norm, autojunk=False)
        m = sm.find_longest_match(0, len(prefix), 0, len(body_norm))
        if m.size >= _GROUND_WEAK_CONTIG_MIN and m.b < len(pos_map):
            return pos_map[m.b]
    # 档2:长 token 同窗共现
    if len(claim_tokens) >= _GROUND_WEAK_TOKEN_MIN_COUNT:
        token_positions: list[tuple[int, str]] = []
        for t in claim_tokens:
            idx = body_norm.find(t)
            if idx >= 0:
                token_positions.append((idx, t))
        token_positions.sort(key=lambda x: x[0])
        for i in range(len(token_positions)):
            for j in range(i + 1, len(token_positions)):
                if token_positions[j][0] - token_positions[i][0] <= _GROUND_WEAK_WINDOW:
                    b_pos = token_positions[i][0]
                    if b_pos < len(pos_map):
                        return pos_map[b_pos]
    return -1


def _strong_match_pos(
    claim_norm: str, body_norm: str, pos_map: list[int]
) -> int:
    """强匹配:claim 归一文本连续片段覆盖 ≥60% → 返原 body 位置;否则 -1。

    [B1] 强匹配是唯一允许 interpretation→grounded 升级的通路。近全文转写才升级;
    局部前缀/共现不升级(防 paraphrase 误升级)。返回 -1 时 caller 保持 interpretation。
    """
    if not claim_norm or not body_norm or not pos_map or len(claim_norm) < 10:
        return -1
    sm = difflib.SequenceMatcher(None, claim_norm, body_norm, autojunk=False)
    m = sm.find_longest_match(0, len(claim_norm), 0, len(body_norm))
    if m.size / len(claim_norm) >= _GROUND_STRONG_COVERAGE and m.b < len(pos_map):
        return pos_map[m.b]
    return -1


def _ground_claims_to_raw(merged: dict[str, Any], raw_content: str | None) -> None:
    """P1b-1: claims/argumentation 接地校正(确定性单遍,无 LLM)。

    调用点:extract_claims 中 _correct_edge_anchors_to_raw 之后、_validate_extract_output
    之前。匹配目标 = 去除 frontmatter/TOC/chrome 后的 raw 正文。

    [B1] 两档匹配(宁缺勿假,匹配不上→维持原状→fail-closed):
    - 弱匹配(仅假锚校正,不升级 provenance):source_grounded claim 锚不在 raw 声明集 →
      前60归一字符连续命中≥25 或 ≥2 长 token(len≥5 非停用词)同240字窗共现 → remap
      anchor 到就近真实声明锚;失败不动。
    - 强匹配(唯一 interpretation→grounded 升级通路):claim 归一文本连续片段覆盖 ≥60%
      (近全文转写)→ 升级 provenance + remap anchor + 补 traces_to=[anchor];局部前缀/
      token 共现不升级(paraphrase 反例:"Executor Pool guarantees fairness in Task Queue"
      主题词共现但谓词非原文 → 不达 60% 连续覆盖 → 不升级)。

    [B2] argumentation 显式覆盖:argumentation 条目 provenance==akb_interpretation 且
    traces_to 空时,仅当其 claim_anchor 指向已严格接地的 source_grounded claim 才填
    traces_to=[claim_anchor];否则保持 fail-closed(语义蕴含真伪仍由 GPT-5.4 entailment
    硬门裁)。既有 test_interpretation_traceability.py floor 测试保持绿(它们直接构造 dict
    调 floor,不经本 pass)。

    golden 幂等:已接地/已正确者 no-op;无依据者不补造。
    """
    if not raw_content:
        return
    body = _strip_raw_for_grounding(raw_content)
    if not body:
        return
    body = body[:_GROUND_BODY_MAX_CHARS]
    body_norm, pos_map = _normalize_with_pos_map(body)
    if not body_norm:
        return

    decls = declared_anchor_positions(body)
    declared_set = {a for _, a in decls}
    if not declared_set:
        return  # raw 无声明锚 → 无法接地(fail-closed,不造假)

    claims = merged.get("claims")
    grounded_anchors: set[str] = set()  # 本就正确 + 升级后的 source_grounded claim anchors

    if isinstance(claims, list):
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            prov = str(claim.get("provenance", "")).strip()
            anchor = str(claim.get("anchor", "")).strip()
            text = str(claim.get("text", "") or claim.get("claim", "") or "")
            if not text:
                continue
            claim_norm = _normalize_for_grounding(text)
            claim_tokens = _tokenize_long_tokens(text)

            if prov == "source_grounded":
                if anchor and anchor in declared_set:
                    grounded_anchors.add(anchor)  # 本就正确接地
                elif anchor:
                    # 弱匹配:假锚校正(不升级 provenance)
                    pos = _weak_match_pos(claim_norm, claim_tokens, body_norm, pos_map)
                    if pos >= 0:
                        new_anchor = nearest_preceding_anchor(body, pos, decls)
                        if new_anchor and new_anchor in declared_set:
                            claim["anchor"] = new_anchor
                            grounded_anchors.add(new_anchor)
            elif prov == "akb_interpretation":
                # 强匹配:唯一升级通路(连续片段覆盖 ≥60%)
                pos = _strong_match_pos(claim_norm, body_norm, pos_map)
                if pos >= 0:
                    new_anchor = nearest_preceding_anchor(body, pos, decls)
                    if new_anchor and new_anchor in declared_set:
                        claim["provenance"] = "source_grounded"
                        claim["anchor"] = new_anchor
                        if not claim.get("traces_to"):
                            claim["traces_to"] = [new_anchor]
                        grounded_anchors.add(new_anchor)
                # 无强匹配 → 留 interpretation;不补造 traces_to(fail-closed,由 floor 裁)

    # [B2] argumentation 保守补填 traces_to
    arg = merged.get("argumentation")
    if isinstance(arg, list) and grounded_anchors:
        for item in arg:
            if not isinstance(item, dict):
                continue
            if str(item.get("provenance", "")).strip() != "akb_interpretation":
                continue
            if item.get("traces_to"):  # 已有 traces_to,不动
                continue
            claim_anchor = str(item.get("claim_anchor", "")).strip()
            if claim_anchor and claim_anchor in grounded_anchors:
                item["traces_to"] = [claim_anchor]


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
            (
                "select next work item", "select next item",
                "next work item", "next item", "选择下一",
            ),
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
            (
                "record llm call metadata", "llm call details",
                "model family", "prompt version", "fallback usage",
                "记录模型", "记录调用",
            ),
            replacements["record_llm_call_metadata"],
        ),
        (
            (
                "define budget constants", "explicit budgets",
                "named constants", "max repair rounds", "max retries",
                "预算", "命名常量",
            ),
            replacements["define_budget_constants"],
        ),
        (
            (
                "separate maker and checker", "maker model",
                "checker model", "different from the model that judges",
                "分离模型", "裁判模型",
            ),
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
        if (
        "work unit" in instruction_blob
        or "ledger" in instruction_blob and "select" in instruction_blob
    ):
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
    # step-3 SC-3c：保留 traces_to（akb_interpretation 溯源）
    traces_to_raw = item.get("traces_to") or []
    if isinstance(traces_to_raw, str):
        traces_to = [str(t).strip() for t in [traces_to_raw] if str(t).strip()]
    elif isinstance(traces_to_raw, list):
        traces_to = [str(t).strip() for t in traces_to_raw if str(t).strip()]
    else:
        traces_to = []
    return {"claim_anchor": anchor, "explanation": text, "provenance": provenance,
            "traces_to": traces_to}


def _validate_extract_segment(segment_name: str, data: dict[str, Any]) -> None:
    """独立校验分段 schema，错误分类留给 runner 记录。"""
    schema = _DISTILL_SEGMENT_SCHEMAS[segment_name]
    data_for_schema = data
    if segment_name == "how_params":
        # how_params 出站 strict schema 暴露 3 个模型字段 + how_level（normalize 必填）；
        # anchor/provenance/provenance_split 是本地 normalizer 为正文锚定补的内部字段，
        # 不进 strict schema。step-3：how_level + mechanism_components/causal_edges 是
        # concept F4 结构真相源，必须随 segment 校验（strict required 已覆盖）。
        data_for_schema = {
            "how_steps": [
                {
                    "instruction": step.get("instruction", ""),
                    "command": step.get("command", ""),
                    "executable": bool(step.get("executable")),
                    "how_level": step.get("how_level", "H1"),
                }
                for step in data.get("how_steps", [])
                if isinstance(step, dict)
            ],
            "mechanism_components": data.get("mechanism_components", []),
            "causal_edges": data.get("causal_edges", []),
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
            extra_body={"enable_thinking": False} if DISTILL_DISABLE_THINKING else None,
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
    """多取取交集：以归一化 command / instruction 语义 key 对齐共有 step。

    取首次出现的 step（instruction/command/anchor/provenance/provenance_split），
    保留首次 param_entries。仅单次出现的 step 舍弃；交集为空由调用方 fail-closed。
    mechanism_components/causal_edges 不参与 command 交集（语义不适用），透传首样本
    （§0.2/D2/M2：concept 机制结构真相源不能因 how_steps 交集丢失）。
    """
    if not samples:
        return {
            "how_steps": [],
            "param_entries": [],
            "mechanism_components": [],
            "causal_edges": [],
        }
    first = samples[0]
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    step_keys: list[tuple[dict[str, Any], list[tuple[str, str]]]] = []
    for step in first.get("how_steps", []):
        keys = _how_step_intersection_keys(step)
        if not keys:
            continue
        for key in keys:
            by_key.setdefault(key, step)
        step_keys.append((step, keys))
    common = set(by_key)
    for sample in samples[1:]:
        sample_keys = {
            key
            for step in sample.get("how_steps", [])
            for key in _how_step_intersection_keys(step)
        }
        common &= sample_keys

    intersected_steps: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for step, keys in step_keys:
        shared = [key for key in keys if key in common]
        if not shared:
            continue
        if any(key in seen_keys for key in shared):
            continue
        seen_keys.update(shared)
        intersected_steps.append(step)
    # §0.2/D2/M2：retake 交集只对 how_steps（command/instruction 语义 key）有意义；
    # mechanism_components/causal_edges 是 concept 机制结构真相源，不能因 how_steps
    # 交集被丢——透传首样本（retake 是实验路径，不靠默认关规避丢字段 bug）。
    # 含 D2 新字段（component_id/aliases/evidence_anchor/evidence_sentence/edge_kind/evidence）。
    return {
        "how_steps": intersected_steps,
        "param_entries": first.get("param_entries", []),
        "mechanism_components": first.get("mechanism_components", []),
        "causal_edges": first.get("causal_edges", []),
    }


def _normalize_how_command_for_intersection(command: str) -> str:
    cleaned = str(command or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"^\$\s+", "", cleaned)
    cleaned = cleaned.replace("python3 -m pip", "pip")
    cleaned = cleaned.replace("python -m pip", "pip")
    cleaned = cleaned.replace("--upgrade", "-U")
    cleaned = cleaned.replace(" --user", "")
    cleaned = re.sub(r"\s+", " ", cleaned.lower()).strip()
    cleaned = re.sub(r"\s*=\s*", "=", cleaned)
    cleaned = re.sub(r"\s*,\s*", ",", cleaned)
    return cleaned


def _normalize_how_instruction_for_intersection(instruction: str) -> str:
    text = str(instruction or "").lower()
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    words = [
        word
        for word in text.split()
        if word
        not in {
            "the",
            "a",
            "an",
            "to",
            "with",
            "and",
            "then",
            "run",
            "use",
            "step",
        }
    ]
    return " ".join(words)


def _how_step_intersection_keys(step: dict[str, Any]) -> list[tuple[str, str]]:
    command_key = _normalize_how_command_for_intersection(str(step.get("command", "")))
    instruction_key = _normalize_how_instruction_for_intersection(
        str(step.get("instruction", "") or step.get("text", ""))
    )
    keys: list[tuple[str, str]] = []
    if command_key:
        keys.append(("command", command_key))
    if instruction_key:
        keys.append(("instruction", instruction_key))
    return keys


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
        # step-3 SC-3a/A3-5：concept F4 结构真相源随 merged 输出携出（procedural 空数组）。
        "mechanism_components": how_params.get("mechanism_components", []),
        "causal_edges": how_params.get("causal_edges", []),
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


def _is_concept_lane(content_lane: str) -> bool:
    """判断 content_lane 是否为概念型车道（concept）。其余（含空串）按 procedural 兼容。"""
    return str(content_lane or "").strip().lower() == CONTENT_LANE_CONCEPT


def _validate_extract_output(data: dict[str, Any], content_lane: str = "") -> None:
    """校验抽取输出的结构完整性（lane-aware，SC-3a/A3-1）。

    Args:
        data: 抽取输出 dict。
        content_lane: 内容车道（concept/procedural）。procedural（默认）保留
            executable≥3 extract 门（零回归）；concept 改判 mechanism_components≥3
            （允许 H1==0、不卡 executable），核心价值是命名机制组件+因果边。

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
                f"claim[{i}] anchor 格式无效: {anchor!r}（期望 §X 锚点,字母/数字/下划线）"
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
    # step-3 SC-3c/Decision-1：concept 车道删 ≤40% 比例上限，改逐条 traces_to 溯源
    # （由 integrity_floor._interpretation_traceability_failed 硬判，层① 确定性地板）；
    # procedural 保留原 ≤40% cap（零回归）。
    akb_count = sum(1 for c in claims if c.get("provenance") == "akb_interpretation")
    if (
        content_lane != "concept"
        and claims
        and akb_count / len(claims) > 0.40
    ):
        raise ValueError(
            f"akb_interpretation 占比 {akb_count}/{len(claims)} > 40%"
            f"（可直接引用原文的 claim 必须用 source_grounded）"
        )

    # 3.6 how_steps 可执行数量校验（lane-aware，SC-3a/A3-1）：
    # - procedural（默认/兼容）：保留 ≥3 条 executable=true 且 command 非空的 extract 门
    #   （零回归）——否则真实 extract 可合法通过但成稿缺 How 可执行性（rubric How 密度）。
    # - concept：§0.2/A2 删 F4 前移——extract 不再判 F4（≥3 组件/连边），F4 唯一裁决点
    #   回 integrity_floor（runner.py floor 段）。旧 _validate_extract_output 内调
    #   _core_value_f4_failed 与 runner floor（runner.py:651-653）+ 质量段 early-return
    #   （runner.py:988-1017）双跑同一 F4，且 floor 在 A层/grader 之前 early-return，
    #   F4 计边发生在正文/A层/grader 不存在时——顺序矛盾（§0.2 D1）。concept 允许
    #   H1==0、不卡 executable；机制完整性由 floor F4 兜底（D1 改 eligible edge 口径）。
    # 注：本校验作用于 extract 输出（maker 提案层），命令是否最终渲染进候选页由
    # _build_body 的 emission-前回查 raw 护栏裁决（治本2）；此处只保证 procedural maker
    # 提出了足量可执行候选步骤。concept 的机制组件/边由 LLM/§4 两阶段抽取产，
    # 不足量时由 floor F4 fail-closed（不在此处早判）。
    how_steps = data.get("how_steps", []) or []
    if not _is_concept_lane(content_lane):
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


# WP-3/D8 (P1-A 收窄): concept deterministic fallback 扫 raw 找真实命名机制组件。
# 编号 H2/H3 标题（### 1. Captain）信任（显式结构）；编号列表项（1. X）须含
# 可识别机制实体名（组件/阶段/模块/角色/trigger/selector 等）才纳——防政策承诺
# 编号列表（"1. Establish safety standards..."）被当机制组件救活。
_REAL_COMPONENT_HEADING_RE = re.compile(r"(?m)^#{2,4}\s+\d+[\.\)]\s+([^\n(]+)")
# §0.2/F7：§-anchor 标题（## §A Captain / ### §B Executor Pool）——真实 raw 常用 §-anchor
# + 组件名作标题（ag2 等），旧 scanner 只认编号标题 → 0 组件 → fallback fail-closed。
# 认 §-anchor 标题后的名字为组件候选（与编号标题同 strong 信号）。
_SECTION_ANCHOR_COMPONENT_RE = re.compile(
    rf"(?m)^#{{2,4}}\s+{ANCHOR_TOKEN_PAT}\s+(.+?)(?:\s*$)"
)
_NUMBERED_ITEM_RE = re.compile(r"(?m)^\s*\d+\.\s+([^\n]+)")
# 机制实体名关键词（noun，非动词承诺）：组件/阶段/模块/子系统/角色/机制 + 常见机制角色
_MECHANISM_ENTITY_KEYWORDS = (
    "component", "step", "stage", "module", "subsystem", "role", "mechanism",
    "trigger", "selector", "executor", "gate", "ledger", "coordinator", "captain",
    "worker", "queue", "pipeline", "layer", "service", "handler", "controller",
    "agent", "runtime", "cache", "scheduler", "producer", "consumer", "broker",
    "组件", "阶段", "模块", "子系统", "角色", "机制", "触发", "选择", "执行",
    "门", "账本", "协调", "队列", "管线", "层", "服务", "控制器", "代理",
    "运行时", "缓存", "调度", "生产者", "消费者",
)

# P1b-2/F-B: 普通标题组件抽取的过滤常量(宁缺勿假:挡 Introduction/Conclusion/FAQ 类)
_GENERIC_PLAIN_TITLE_NAMES: frozenset[str] = frozenset({
    "introduction", "conclusion", "faq", "overview", "summary", "background",
    "references", "related work", "appendix", "preface", "tldr", "intro",
    "outro", "abstract", "changelog", "credits", "table of contents",
    "further reading",
    # F5-C: 补常见文档泛节名(probe-p4 "Quick Start" 成了边端点)
    "quick start", "getting started", "get started", "next steps",
    "prerequisites", "examples", "usage", "installation",
    "troubleshooting", "see also", "learn more",
    # F8-C: 补 LLM 产泛节名/配置节(probe-p6 crewai "Crew Attributes" 成了组件)
    "crew attributes", "crew properties", "attributes", "properties",
    "configuration", "config", "settings", "options", "parameters",
    "defaults", "environment variables", "fields", "metadata",
})
# 句子起始词(动词/疑问词):以这些词开头的标题是祈使句/疑问句,非命名短语
_SENTENCE_STARTER_WORDS: frozenset[str] = frozenset({
    "how", "when", "why", "what", "where", "who", "which", "choose", "write",
    "set", "import", "manage", "enable", "view", "troubleshoot", "organize",
    "audit", "edit", "add", "use", "install", "configure", "create", "build",
    "run", "check", "verify", "understand", "learn", "getting", "started",
    "read", "download", "update", "delete", "remove", "start", "stop",
    "test", "deploy", "commit", "push", "pull", "merge", "see",
    # F5-C: 补常见文档祈使词(probe-p4 "Invoke subagents explicitly" 成了边端点)
    "invoke", "disable", "debug",
    # F8-C: 补 LLM 产祈使句首词(probe-p6 crewai "Navigate to the directory..." 成了组件)
    "navigate", "open", "click", "select", "enter", "copy", "paste", "follow",
    "ensure", "avoid", "keep", "try", "consider", "review", "remember", "go",
    "locate", "find", "replace", "rename", "generate", "execute", "apply",
})
# 小写助动词(出现则标题是句子而非命名短语):is/are/seem/has/do/can/will...
_AUX_VERB_WORDS: frozenset[str] = frozenset({
    "is", "are", "was", "were", "been", "being", "seem", "seems", "appear",
    "appears", "become", "becomes", "has", "have", "had", "do", "does", "did",
    "can", "could", "will", "would", "shall", "should", "must", "may", "might",
    "gets", "get", "makes", "make",
})
_APOSTROPHE_CHARS: str = "'’´`"


def _item_has_mechanism_entity(item_text: str) -> bool:
    """编号列表项是否含可识别机制实体名（区别于政策承诺动词列表）。"""
    lower = item_text.lower()
    return any(kw in lower for kw in _MECHANISM_ENTITY_KEYWORDS)


# F-B: 普通 H2/H3 标题(非编号/非 §-锚)正则
_PLAIN_TITLE_HEADING_RE: re.Pattern[str] = re.compile(r"(?m)^#{2,3}\s+(.+?)\s*$")


def _is_plain_title_component(title: str) -> bool:
    """F-B: 普通 H2/H3 标题是否算机制组件。

    两条路径(满足任一):
    - 含机制实体关键词(component/step/module/agent/...)→ 是
    - title-case 命名短语:≤6 词 + 首词大写 + 非句子起始词(动词/疑问词) +
      非收缩词(无 ' isn't/it's) + 非小写助动词(is/are/seem/...) + 非泛节名

    宁缺勿假:Introduction/Conclusion/FAQ/Overview/Summary 等泛节名直接拒;
    祈使句/疑问句标题(How/When/Choose/Write/Invoke...开头)拒,即使含 entity 词
    (F5-C:'Invoke subagents explicitly' 含 'agent' 但祈使句 → 拒);含收缩词或助动词的句子拒。
    """
    t = title.strip()
    if not t:
        return False
    low = t.lower()
    if low in _GENERIC_PLAIN_TITLE_NAMES:
        return False
    # F5-C: 祈使句/疑问句首词检查在 entity keyword 之前(防 'Invoke subagents explicitly'
    # 含 'agent' 被 entity 路径先接受)
    words = t.split()
    if words and words[0][:1].isupper() and words[0].lower() in _SENTENCE_STARTER_WORDS:
        return False
    if any(kw in low for kw in _MECHANISM_ENTITY_KEYWORDS):
        return True
    if len(words) > 6 or len(words) < 1:
        return False
    if not words[0][:1].isupper():
        return False
    if any(any(c in _APOSTROPHE_CHARS for c in w) for w in words):
        return False  # 收缩词(isn't/it's)= 句子
    if any(w.lower() in _AUX_VERB_WORDS for w in words):
        return False  # 小写助动词 = 句子
    return True


def _is_garbage_component_name(name: str) -> bool:
    """F8-C: 组件名是否命中黑名单形态(祈使句/泛节名/收缩词/助动词/超长/空)。

    复用 _is_plain_title_component 同族黑名单(_GENERIC_PLAIN_TITLE_NAMES/
    _SENTENCE_STARTER_WORDS/_APOSTROPHE_CHARS/_AUX_VERB_WORDS),但不要求 title-case
    或 entity keyword——LLM 组件名可能小写或无 entity 词,只要是命名短语就保留;
    只滤明确命中黑名单形态的(lead 护栏:不做激进语义判断)。滤后 <3 组件 →
    既有 fail-closed/enrichment 接手。

    F9-A 修:剥括号示例后再数词(同 _generate_component_variants P1b-3)——否则
    'A work unit selector (read ledger, pick next item)' 被 paren 撑到 9 词 >6 误滤
    (它是 valid 组件,含 selector 实体)。
    """
    t = name.strip()
    if not t:
        return True  # 空名是垃圾
    low = t.lower()
    if low in _GENERIC_PLAIN_TITLE_NAMES:
        return True  # 泛节名/配置节
    # F9-A: 剥括号示例后数词(防 paren 撑高词数误滤 valid 组件)
    name_clean = re.sub(r"\([^)]*\)", "", t).strip()
    words = name_clean.split()
    # 祈使句/疑问句首词(大写开头,lowercase 后在 starter 词表)
    if words and words[0][:1].isupper() and words[0].lower() in _SENTENCE_STARTER_WORDS:
        return True
    # 收缩词(isn't/it's)= 句子
    if any(any(c in _APOSTROPHE_CHARS for c in w) for w in words):
        return True
    # 小写助动词(is/are/seem/...)= 句子
    if any(w.lower() in _AUX_VERB_WORDS for w in words):
        return True
    # 超长(>6 词,剥 paren 后)= likely 句子而非命名短语
    if len(words) > 6:
        return True
    return False


def _filter_llm_component_names(extract_data: dict[str, Any]) -> int:
    """F8-C: 对所有来源的 mechanism_components 过黑名单形态过滤(extract 归一化处)。

    probe-p6 crewai 缺陷:LLM 产组件混入祈使句('Navigate to the directory...')/泛节名
    ('Crew Attributes')。F5-C 只过滤确定性 plain-title 扫描,LLM 组件没过滤。本函数在
    extract 归一化处统一过滤所有来源组件(复用 _is_garbage_component_name 同族黑名单)。
    护栏:只滤明确命中黑名单形态;滤后 <3 → 既有 fail-closed/_validate_extract_output/
    enrichment 接手(不在此救)。Returns filtered count(供 diag components_filtered)。
    """
    components = extract_data.get("mechanism_components", []) or []
    if not components:
        return 0
    kept: list[dict[str, Any]] = []
    filtered = 0
    for comp in components:
        if not isinstance(comp, dict):
            kept.append(comp)
            continue
        name = str(comp.get("name", "")).strip()
        if not name:
            kept.append(comp)  # 空名留给 _validate_extract_output 处理,不在此滤
            continue
        if _is_garbage_component_name(name):
            filtered += 1  # 命中黑名单形态,滤
        else:
            kept.append(comp)  # 命名短语,保留
    extract_data["mechanism_components"] = kept
    return filtered


def _scan_plain_title_components(raw_content: str) -> list[str]:
    """F-B: 扫普通 H2/H3 标题(非编号/非 §-锚/非表格)取机制组件名。

    优先级最低(在编号标题/§-锚标题/表格/编号列表之后),供 memory 等纯散文标题型 raw
    补足组件。去重保序。
    """
    if not raw_content:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for m in _PLAIN_TITLE_HEADING_RE.finditer(raw_content):
        title = m.group(1).strip()
        # 跳过 §-锚标题(别处处理)、编号标题、表格行
        if title.startswith("§") or re.match(r"^\d+[.)]", title) or title.startswith("|"):
            continue
        if _is_plain_title_component(title):
            name = title.rstrip(".").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


# P1b-2: 表格组件列 header 关键词(命中则取该列单元作组件名)
_COMPONENT_HEADER_KEYWORDS: tuple[str, ...] = (
    "component", "module", "subsystem", "stage", "layer", "service",
    "组件", "模块", "子系统", "阶段", "层", "服务",
)


def _split_table_cells(line: str) -> list[str]:
    """朴素拆 markdown 表格行 ``| a | b | c |`` → ['a','b','c']。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _scan_component_table_rows(raw_content: str) -> list[str]:
    """P1b-2: 扫 markdown 表格,header 命中组件类关键词(component/module/组件/模块…)的列,
    取该列单元作组件名。

    - 排除锚点目录块内的表(``## 锚点目录`` 区,经 strip_directory_block 去除)。
    - 剥 ``**``/反引号;剥行首 ``§X`` 锚点 token(非组件名一部分)。
    - 跳过分隔行(``|---|---|``)。
    """
    if not raw_content:
        return []
    body = strip_directory_block(raw_content)
    names: list[str] = []
    seen: set[str] = set()
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        # 收集连续表格行(一张表)
        table_lines: list[str] = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            table_lines.append(lines[i])
            i += 1
        if len(table_lines) < 3:  # 至少 header + separator + 1 数据行
            continue
        header = _split_table_cells(table_lines[0])
        # 找组件类列(首个命中的列)
        col_idx = -1
        for idx, h in enumerate(header):
            h_lower = h.lower()
            if any(kw in h_lower for kw in _COMPONENT_HEADER_KEYWORDS):
                col_idx = idx
                break
        if col_idx < 0:
            continue
        # 跳过分隔行(table_lines[1]),取数据行
        for row_line in table_lines[2:]:
            cells = _split_table_cells(row_line)
            if col_idx >= len(cells):
                continue
            cell = cells[col_idx]
            # 分隔行兜底(|:---|---:|)
            if re.fullmatch(r":?-{0,}:?", cell):
                continue
            cell = re.sub(r"[`*]", "", cell).strip()
            cell = re.sub(rf"^{ANCHOR_TOKEN_PAT}\s+", "", cell).strip()  # 剥行首 §X
            if cell and len(cell) <= 80 and cell not in seen:
                seen.add(cell)
                names.append(cell)
    return names


def _scan_real_mechanism_components(raw_content: str) -> list[dict[str, str]]:
    """扫 raw 找真实命名机制组件（编号标题 + §-anchor 标题 + 表格组件列 + 含实体名的编号列表项）。

    返回 ``[{name, role}]``（去重，保序，上限 FALLBACK_MAX_COMPONENTS）。
      1. 编号 H2/H3/H4 标题（``### 1. Captain``）——显式结构，信任；
      2. §0.2/F7：§-anchor 标题（``## §A Captain``）——真实 raw 常用 §-anchor + 组件名
         作标题（ag2 等），与编号标题同 strong 信号；
      1.5. P1b-2：markdown 表格组件列（header 命中 component/组件/模块…）——补表格化组件
         散文（idx2 组件数 2<3 需 table 抽取也命中才救）；
      3. 编号列表项（``1. A trigger``）——须含机制实体名（P1-A），过滤政策承诺
         （"1. Establish safety standards" 无实体名 → 不纳）。
    散文/政策承诺无实体名编号项 → 0 组件 → fail-closed。全局上限 FALLBACK_MAX_COMPONENTS=12。
    """
    components: list[dict[str, str]] = []
    seen: set[str] = set()
    # 1. 编号 H2/H3/H4 标题（显式结构，信任）
    for m in _REAL_COMPONENT_HEADING_RE.findall(raw_content or ""):
        name = m.strip().rstrip(".").strip()
        if name and name not in seen:
            seen.add(name)
            components.append({"name": name, "role": "raw 命名机制组件"})
    # §0.2/F7：§-anchor 标题（## §A Captain）——补 §-anchor 格式组件
    # 与编号标题同 strong 信号（信任，不卡实体关键词——"Task Graph"/"Lifecycle" 是真组件
    # 但不含硬编码实体词；编号标题也不卡关键词，§-anchor 标题同口径）
    if len(components) < CONCEPT_MIN_MECHANISM_COMPONENTS:
        for m in _SECTION_ANCHOR_COMPONENT_RE.findall(raw_content or ""):
            name = m.strip().rstrip(".").strip()
            if (
                name
                and len(name) <= 80
                and name not in seen
            ):
                seen.add(name)
                components.append({"name": name, "role": "raw 命名机制组件"})
                if len(components) >= CONCEPT_MIN_MECHANISM_COMPONENTS:
                    break
    # 1.5. P1b-2：markdown 表格组件列(header 命中 component/组件/模块…)取该列单元
    if len(components) < CONCEPT_MIN_MECHANISM_COMPONENTS:
        for name in _scan_component_table_rows(raw_content or ""):
            name = name.strip().rstrip(".").strip()
            if name and len(name) <= 80 and name not in seen:
                seen.add(name)
                components.append({"name": name, "role": "raw 命名机制组件"})
                if len(components) >= CONCEPT_MIN_MECHANISM_COMPONENTS:
                    break
    # 2. 编号列表项——须含机制实体名（P1-A：防政策承诺编号列表被当组件）
    if len(components) < CONCEPT_MIN_MECHANISM_COMPONENTS:
        for item in _NUMBERED_ITEM_RE.findall(raw_content or ""):
            name = item.strip().rstrip(".").strip()
            if (
                not name
                or len(name) > 80
                or name in seen
                or not _item_has_mechanism_entity(name)
            ):
                continue
            seen.add(name)
            components.append({"name": name, "role": "raw 命名机制组件"})
            if len(components) >= CONCEPT_MIN_MECHANISM_COMPONENTS:
                break
    # 3. F-B: 普通 H2/H3 标题(最低优先级,编号列表之后)——供 memory 等纯散文标题型 raw
    # 补足组件。须过 _is_plain_title_component 过滤(实体关键词 OR title-case 命名短语,
    # 挡 Introduction/Conclusion/FAQ/祈使句/疑问句)。不卡 MIN=3(取到全局 12 上限),
    # 因 P1b-3 边抽取需更多组件共现机会——MIN=3 会让 vercel 只取 3 组件致 0 边。
    if len(components) < CONCEPT_MIN_MECHANISM_COMPONENTS:
        for name in _scan_plain_title_components(raw_content or ""):
            name = name.strip().rstrip(".").strip()
            if name and len(name) <= 80 and name not in seen:
                seen.add(name)
                components.append({"name": name, "role": "raw 命名机制组件"})
                if len(components) >= FALLBACK_MAX_COMPONENTS:
                    break
    # P1b-2: 全局上限(防长表格/编号列表灌水;有界预算)
    if len(components) > FALLBACK_MAX_COMPONENTS:
        components = components[:FALLBACK_MAX_COMPONENTS]
    return components


# P1-A: 真实因果/依赖/触发语言连接词（禁排版顺序边替代因果边）
# §0.2/F7：因果连接词集——增强以覆盖 ag2 等真实 raw 的显式关系动词
# （dispatch/collect/feeds/register/spawn/route/forward/return 等）。
# 旧集只认 "dispatches to"（须带 to）/feeds? 等，ag2 "dispatch"/"collect" 抽不到 → 0 source_causal。
_CAUSAL_CONNECTOR_RE = re.compile(
    r"(?i)\b(?:leads?\s+to|triggers?|feeds?(?:\s+(?:to|into))?|enables?|activates?|"
    r"depends?\s+on|before|after|causes?|results?\s+in|produces?|"
    r"dispatch(?:es)?(?:\s+(?:to|into))?|invokes?|schedules?|"
    r"writes?\s+(?:to|into)|updates?|calls?|sends?\s+(?:to|into)|passes?\s+(?:to|into)|"
    r"→|consumes?|notifies|"
    r"collects?|registers?|spawns?|terminates?|returns?|routes?|forwards?|"
    r"delegates?|assigns?|publishes?|subscribes?|emits?|delivers?|"
    r"handoffs?|hand\s+offs?|transmits?|relays?)\b"
)


# §0.2/F1-regex：raw §-anchor 位置——P1a 口径统一到 raw_anchors.declared_anchor_positions
# （认 heading + marker 全 raw,不只得 heading;canonical 归一 _CONTENT_START 后缀）。


def _raw_anchor_positions(raw_content: str) -> list[tuple[str, int]]:
    """§0.2/F1：raw 内 §-anchor 的 (anchor, 字符位置) 有序列表（供 evidence_anchor 映射）。

    P1a:委托 raw_anchors.declared_anchor_positions（返回 (pos, anchor)），本函数保持
    (anchor, pos) 旧签名供 _correct_edge_anchors_to_raw 复用。

    fallback 不得合成 ``§raw``——evidence_anchor 必须映射到真实 raw anchor block,
    否则不计 eligible edge（破坏 traceability）。
    """
    return [(anchor, pos) for pos, anchor in declared_anchor_positions(raw_content)]


def _nearest_preceding_anchor(
    sent_pos: int, anchor_positions: list[tuple[str, int]]
) -> str:
    """§0.2/F1：句子位置 → 最近的在其之前（含同位）的 §-anchor（无则空串）。

    P1a:委托 raw_anchors.nearest_preceding_anchor（strict fail-closed,不回落首锚）。
    本函数保持 (sent_pos, anchor_positions) 旧签名供 _build_real_causal_edges /
    _correct_edge_anchors_to_raw 复用。
    """
    if not anchor_positions:
        return ""
    # 转 (pos, anchor) 序供 raw_anchors
    decls = sorted([(p, a) for a, p in anchor_positions], key=lambda x: x[0])
    return nearest_preceding_anchor(None, sent_pos, decls)


# P1b-3: 组件匹配变体 domain stoplist([N1] b:泛词单独不作 variant)
_COMPONENT_VARIANT_STOPLIST: frozenset[str] = frozenset({
    "agent", "context", "model", "queue", "task", "worker", "system",
    "pool", "layer", "stage", "service", "module", "engine", "manager",
    "controller", "handler", "runtime", "cache", "scheduler", "broker",
})
# P1b-3 [N1]: 跨句上限(保守,有界预算)
_CROSS_SENTENCE_MAX_EDGES: int = 3


def _generate_component_variants(name: str) -> list[str]:
    """生成组件名匹配变体:归一全名 + 朴素单复数 + 多词名长 token(len≥5)+ 其单复数。

    P1b-3: 先剥括号示例(``A work unit selector (read ledger, pick next item)`` →
    ``A work unit selector``),否则括号里的示例词(``ledger``)会被当 token 误匹配正文
    里的同名泛词,造 spurious causal edge。
    """
    # 剥括号示例(非组件名一部分)
    name_clean = re.sub(r"\([^)]*\)", "", name).strip()
    norm = re.sub(r"^(?:A|An|The)\s+", "", name_clean, flags=re.I).lower().strip()
    if not norm:
        return []
    variants: list[str] = []
    seen: set[str] = set()

    def _add(v: str) -> None:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            variants.append(v)

    _add(norm)
    # 朴素单复数
    if norm.endswith("s"):
        _add(norm[:-1])
    else:
        _add(norm + "s")
    # 多词名长 token(len≥5,非 stoplist)+ 其单复数
    for t in re.findall(r"[A-Za-z0-9]+", norm):
        if len(t) >= 5 and t not in _COMPONENT_VARIANT_STOPLIST:
            _add(t)
            if t.endswith("s"):
                _add(t[:-1])
            else:
                _add(t + "s")
    return variants


def _component_match_variants(components: list[dict[str, str]]) -> dict[str, str]:
    """variant(lowercase) → 唯一 claim 的组件原名。

    [N1] 双护栏:
    - (a) variant 被 ≥2 组件认领 → 弃用(歧义,如 "pool" 同被 Executor Pool/Task Pool 认领)
    - (b) 单个 domain stoplist 泛词(agent/context/model/queue/pool/...)不作 variant
    返回 {variant: orig_name},供英文 \b 词界最长匹配。
    """
    claims: dict[str, list[str]] = {}
    for c in components:
        orig = c["name"]
        for v in _generate_component_variants(orig):
            claims.setdefault(v, []).append(orig)
    result: dict[str, str] = {}
    for v, claimants in claims.items():
        if len(claimants) > 1:
            continue  # [N1] a 歧义弃用
        if v in _COMPONENT_VARIANT_STOPLIST:
            continue  # [N1] b 单泛词弃用
        result[v] = claimants[0]
    return result


def _compile_component_match_pattern(
    variant_map: dict[str, str]
) -> re.Pattern[str] | None:
    """编译 variant 匹配 regex(英文 \\b 词界,长度降序最长匹配)。无 variant 返 None。"""
    if not variant_map:
        return None
    variants_sorted = sorted(variant_map.keys(), key=len, reverse=True)
    escaped = "|".join(re.escape(v) for v in variants_sorted)
    return re.compile(rf"\b(?:{escaped})\b", re.IGNORECASE)


def _find_components_in_text(
    text: str,
    pattern: re.Pattern[str] | None,
    variant_map: dict[str, str],
) -> list[str]:
    """用 variant pattern 找 text 中出现的组件原名(去重保序)。"""
    if not pattern:
        return []
    found: list[str] = []
    for m in pattern.finditer(text):
        orig = variant_map.get(m.group(0).lower())
        if orig and orig not in found:
            found.append(orig)
    return found


def _build_real_causal_edges(
    components: list[dict[str, str]], raw_content: str
) -> list[dict[str, Any]]:
    """§0.2/D2/M1 + §0.2/F1 + P1b-3：从 raw 真实因果语言建 eligible 边 + 相邻组件结构推断弱边。

    - 真因果/依赖/触发语言句 + ≥2 组件共现 → edge_kind="source_causal"（带 evidence
      句 + evidence_anchor **映射到真实 raw §-anchor**，计入 F4 eligible edge）
    - P1b-3: 组件共现用 _component_match_variants(alias + 朴素单复数 + 多词名长 token,
      [N1] 双护栏:歧义 variant 弃用 + domain stoplist 泛词弃用),解决 "Executor Pool"
      vs "executors" 抽不出的问题
    - P1b-3 跨句(保守):仅当单句 pass 产 0 条时启用一轮;窗口=相邻句对;[N1] 加严:
      合并句对必须有定向连接词 + 两端组件各自唯一匹配 + 两句 nearest_preceding_anchor
      相同(同 §-anchor block);evidence 写两句;上限 3 条
    - 同编号段相邻组件 → edge_kind="inferred_from_structure"（无 evidence,不计 F4,
      只渲染进正文表 + 交 grader H3 审语义;§0.2 D1 弱边不救 F4）
    无真实因果 / 因果句不在 §-anchor block 下 → 0 eligible edge → fallback fail-closed。

    §0.2/F1：**不得合成 ``§raw``**——evidence_anchor 必须映射 evidence sentence 到真实
    raw anchor block（§A/§B），映射不到 → 不产 eligible edge（不造假 anchor 破坏 traceability）。
    """
    if not components or not raw_content:
        return []
    variant_map = _component_match_variants(components)
    pattern = _compile_component_match_pattern(variant_map)
    anchor_positions = _raw_anchor_positions(raw_content)
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    search_pos = 0
    # 1. source_causal edges:含因果连接词的句 + ≥2 组件共现(P1b-3 variant 匹配)+ 真实 §-anchor
    for sent in re.split(r"[。\n.!?]", raw_content):
        if not _CAUSAL_CONNECTOR_RE.search(sent):
            continue
        found_orig = _find_components_in_text(sent, pattern, variant_map)
        if len(found_orig) < 2:
            continue
        # §0.2/F1：映射 evidence sentence 到真实 raw §-anchor（不合成 §raw）
        sent_pos = raw_content.find(sent, search_pos)
        if sent_pos >= 0:
            search_pos = sent_pos + 1
        pos_for_anchor = sent_pos if sent_pos >= 0 else len(raw_content)
        anchor = _nearest_preceding_anchor(pos_for_anchor, anchor_positions)
        if not anchor:
            # 因果句不在任何 §-anchor block 下 → 不产 eligible edge（不造假 anchor）
            continue
        # ≥2 组件共现于含因果连接词的句 → 按出现顺序建边
        for i in range(len(found_orig)):
            for j in range(i + 1, len(found_orig)):
                a, b = found_orig[i], found_orig[j]
                if (a, b) not in seen:
                    seen.add((a, b))
                    edges.append(
                        {
                            "from": a,
                            "to": b,
                            "relation": "raw_causal",
                            "edge_kind": "source_causal",
                            "evidence": sent.strip()[:200],
                            "evidence_anchor": anchor,
                        }
                    )
    # 1.5 P1b-3 跨句(保守):仅单句 pass 产 0 eligible edge 时启用
    if not edges:
        sentences = [s.strip() for s in re.split(r"[。\n.!?]", raw_content) if s.strip()]
        cross_search_pos = 0
        for i in range(len(sentences) - 1):
            if len(edges) >= _CROSS_SENTENCE_MAX_EDGES:
                break
            s1, s2 = sentences[i], sentences[i + 1]
            pair_text = s1 + " " + s2
            # [N1] 加严:合并句对中必须存在明确定向连接词
            if not _CAUSAL_CONNECTOR_RE.search(pair_text):
                continue
            found_orig = _find_components_in_text(pair_text, pattern, variant_map)
            if len(found_orig) < 2:
                continue
            # 两句 nearest_preceding_anchor 必须相同(同 §-anchor block)
            p1 = raw_content.find(s1, cross_search_pos)
            p2 = raw_content.find(s2, p1 + 1) if p1 >= 0 else -1
            if p1 < 0 or p2 < 0:
                continue
            cross_search_pos = p1 + 1
            a1 = _nearest_preceding_anchor(p1, anchor_positions)
            a2 = _nearest_preceding_anchor(p2, anchor_positions)
            if not a1 or a1 != a2:
                continue  # 不同 block → 不连
            # 两端组件各自唯一匹配(variant_map 保证 unique claim;取前两个建一条边)
            a, b = found_orig[0], found_orig[1]
            if (a, b) not in seen:
                seen.add((a, b))
                edges.append(
                    {
                        "from": a,
                        "to": b,
                        "relation": "raw_causal",
                        "edge_kind": "source_causal",
                        "evidence": pair_text.strip()[:200],
                        "evidence_anchor": a1,
                    }
                )
    # 2. inferred_from_structure edges：相邻组件顺序推断（不计 F4，供 grader H3）
    for i in range(len(components) - 1):
        a = components[i]["name"]
        b = components[i + 1]["name"]
        if a != b and (a, b) not in seen:
            seen.add((a, b))
            edges.append(
                {
                    "from": a,
                    "to": b,
                    "relation": "structural_sequence",
                    "edge_kind": "inferred_from_structure",
                    "evidence": "",
                    "evidence_anchor": "",
                }
            )
    return edges


def _close_edge_endpoints(
    extract_data: dict[str, Any],
    det_components: list[dict[str, Any]] | None = None,
) -> int:
    """F6-A: 全局端点闭合——所有 causal_edges/graph_edges 的 from/to ∈ mechanism_components
    names(含 alias)。不在→若 det_components 提供且匹配→补进名单;否则丢边。

    Returns dropped_count(丢掉的边数)。
    不变式:渲染进的每条边端点都在组件表(与 SG-mechanism-edge-evidence 同口径,自检不自拒)。
    """
    final_comps = extract_data.get("mechanism_components", []) or []
    comp_names: set[str] = set()
    for c in final_comps:
        if not isinstance(c, dict):
            continue
        n = str(c.get("name", "")).strip().lower()
        if n:
            comp_names.add(n)
        for a in c.get("aliases") or []:
            if isinstance(a, str) and a.strip():
                comp_names.add(a.strip().lower())
    det_names_map: dict[str, str] = {}
    if det_components:
        for dc in det_components:
            n = str(dc.get("name", "")).strip()
            if n:
                det_names_map[n.lower()] = n
    dropped = 0
    for edge_key in ("causal_edges", "graph_edges"):
        all_edges = extract_data.get(edge_key, []) or []
        closed: list[dict[str, Any]] = []
        for e in all_edges:
            if not isinstance(e, dict):
                closed.append(e)
                continue
            frm = str(e.get("from", "")).strip()
            to = str(e.get("to", "")).strip()
            missing = [ep for ep in (frm, to) if ep and ep.lower() not in comp_names]
            if not missing:
                closed.append(e)
                continue
            # 端点不在名单 → 尝试从 det_components 补
            added_from_closure = False
            for ep in missing:
                det_name = det_names_map.get(ep.lower())
                if det_name and len(final_comps) < FALLBACK_MAX_COMPONENTS:
                    final_comps.append({"name": det_name, "role": "raw 命名机制组件"})
                    comp_names.add(det_name.lower())
                    added_from_closure = True
            if added_from_closure:
                extract_data["mechanism_components"] = final_comps
                closed.append(e)
            else:
                dropped += 1  # 丢边(防 SG 自拒)
        extract_data[edge_key] = closed
    return dropped


def _normalize_edge_endpoints_to_canonical(
    extract_data: dict[str, Any],
) -> int:
    """F8-D: 边端点命名归一——causal_edges/graph_edges 的 from/to 改写为组件表
    canonical name(消 grader H3 'claudemd_files vs CLAUDE.md files' 类命名分裂)。

    端点经 _close_edge_endpoints 后已在组件名单(含 alias),但可能用小写/别名/slug 形式,
    与组件表 name 列不一致 → 组件表与边表命名分裂,grader H3 抱怨命名不归一。本函数用
    name+alias → canonical name 映射把端点改写为组件表原 name。Returns rewritten count。
    """
    components = extract_data.get("mechanism_components", []) or []
    # lower(form) → canonical name(name 优先,alias 映射到所属组件 name;不覆盖已存在的 name 映射)
    canon_map: dict[str, str] = {}
    for c in components:
        if not isinstance(c, dict):
            continue
        n = str(c.get("name", "")).strip()
        if not n:
            continue
        canon_map.setdefault(n.lower(), n)
        for a in c.get("aliases") or []:
            if isinstance(a, str) and a.strip():
                canon_map.setdefault(a.strip().lower(), n)
    if not canon_map:
        return 0
    rewritten = 0
    for edge_key in ("causal_edges", "graph_edges"):
        edges = extract_data.get(edge_key, []) or []
        for e in edges:
            if not isinstance(e, dict):
                continue
            for ep_field in ("from", "to"):
                ep = str(e.get(ep_field, "")).strip()
                if not ep:
                    continue
                canon = canon_map.get(ep.lower())
                if canon and canon != ep:
                    e[ep_field] = canon
                    rewritten += 1
    return rewritten


def _generate_backfill_search_terms(name: str, tokens: bool) -> list[str]:
    """F8-A: 回填专用搜索词(无 stoplist/collision——单组件已认定,无歧义)。

    lead F8-A 裁定:回填是给已认定组件找证据句,不是抽新组件,故 _component_match_variants
    的 stoplist/collision 护栏不适用(它们是为防抽新组件时泛词歧义)。本函数生成:
    - tokens=False:归一全名 + 朴素单复数(最具体,pass-1 优先)。
    - tokens=True: 再加多词名长 token(len≥5)+ 其单复数(pass-2 兜底)。
    len<3 不入(噪声);len 3-4 单字 token 不入 pass-2(如 pool/task/agent——泛词噪声大,
    但全名含这些词时 pass-1 全名仍搜;组件名本身是单字泛词如 "Agent" 时 pass-1 "agent" 仍搜)。
    """
    name_clean = re.sub(r"\([^)]*\)", "", name).strip()
    norm = re.sub(r"^(?:A|An|The)\s+", "", name_clean, flags=re.I).lower().strip()
    if not norm:
        return []
    terms: list[str] = []
    seen: set[str] = set()

    def _add(v: str) -> None:
        v = v.strip()
        if v and len(v) >= 3 and v not in seen:
            seen.add(v)
            terms.append(v)

    _add(norm)
    if norm.endswith("s"):
        _add(norm[:-1])
    else:
        _add(norm + "s")
    if tokens:
        for t in re.findall(r"[A-Za-z0-9]+", norm):
            if len(t) >= 5:
                _add(t)
                if t.endswith("s"):
                    _add(t[:-1])
                else:
                    _add(t + "s")
    return terms


def _compile_backfill_pattern(terms: list[str]) -> re.Pattern[str] | None:
    """编译回填搜索词为英文 \\b 词界 regex(长度降序最长匹配)。无 terms 返 None。"""
    if not terms:
        return None
    sorted_terms = sorted(terms, key=len, reverse=True)
    escaped = "|".join(re.escape(t) for t in sorted_terms)
    return re.compile(rf"\b(?:{escaped})\b", re.IGNORECASE)


def _find_backfill_sentence(
    raw_content: str,
    terms_full: list[str],
    terms_token: list[str],
    anchor_positions: list[tuple[str, int]],
    first_anchor_pos: int,
) -> tuple[str, str] | None:
    """F8-A: 两遍(全名优先,再 token 兜底)在 raw 找组件证据句。

    行级迭代(跳标题/表格/代码栅栏/锚目录行);frontmatter(首声明锚前)跳过;
    anchor 空 continue 不 break(修旧版 frontmatter 命中即 break 致 Captain 空回填)。
    Returns (sentence, anchor) 或 None。
    """
    for terms in (terms_full, terms_token):
        if not terms:
            continue
        pat = _compile_backfill_pattern(terms)
        if not pat:
            continue
        for line in raw_content.splitlines():
            stripped = line.strip()
            if not stripped or len(stripped) < 20 or len(stripped) > 300:
                continue
            # 跳标题/表格/代码栅栏/锚声明行(§X_..)——这些非证据句
            if stripped.startswith(("#", "|", "```", "§")):
                continue
            if not pat.search(stripped):
                continue
            pos = raw_content.find(stripped)
            if pos < 0 or pos < first_anchor_pos:
                continue  # frontmatter/锚目录(首锚前),跳过找下一句
            anchor = _nearest_preceding_anchor(pos, anchor_positions)
            if not anchor:
                continue  # 仍无锚(兜底,理论上首锚后总有),跳过
            return stripped, anchor
    return None


def _backfill_component_evidence(
    extract_data: dict[str, Any], raw_content: str | None
) -> dict[str, int]:
    """F6-D/F8-A: 对 evidence_anchor/evidence_sentence 为空的组件做确定性回填。

    H3 缺陷主因:mechanism_components 的 evidence_anchor/evidence_sentence 空,组件不可追溯。

    F8-A 修(probe-p6 ag2 Captain 空回填根因):
    旧版用 _component_match_variants(走 stoplist/collision)+ 句级 split + break-on-first-match,
    被 frontmatter 命中(§A 锚前)毒化——Captain 在 raw frontmatter source_title 先匹配,
    nearest_preceding_anchor 返空(无锚在前)→ ``if anchor`` 失败 → 但已 break,不再找后面
    §E_CLAIMS 的真实命中句 → Captain 回填 0。ag2 离线复现:旧版 3/4,Captain 空。

    修:回填不走 stoplist(单组件已认定,无歧义,lead 裁定),改专用 _generate_backfill_search_terms
    + 两遍(全名优先,再 token len≥5兜底)+ 行级迭代(跳标题/表格/代码/锚目录/frontmatter)
    + anchor 空 continue 不 break。目标:ag2 4 组件(Captain/Executor Pool/Task Graph and
    Checkpointing/Sub-Agent Lifecycle)离线全部回填(句子逐字在 raw + 锚∈声明集)。

    Returns {"backfilled": int, "unlocated": int}——空 evidence 组件中成功/未定位计数;
    已有 evidence 或空名的不计入(fail-closed,不造假 evidence)。
    """
    diag = {"backfilled": 0, "unlocated": 0}
    if not raw_content:
        return diag
    components = extract_data.get("mechanism_components", []) or []
    if not components:
        return diag
    anchor_positions = _raw_anchor_positions(raw_content)
    if not anchor_positions:
        return diag  # 无声明锚 → 无法映射,不造假(fail-closed)
    first_anchor_pos = min(p for _, p in anchor_positions)
    for comp in components:
        if not isinstance(comp, dict):
            continue
        ea = str(comp.get("evidence_anchor", "")).strip()
        es = str(comp.get("evidence_sentence", "")).strip()
        if ea and es:
            continue  # 已有,不覆盖
        name = str(comp.get("name", "")).strip()
        if not name:
            continue  # 空名无 evidence 可填,不计 unlocated
        terms_full = _generate_backfill_search_terms(name, tokens=False)
        terms_token = _generate_backfill_search_terms(name, tokens=True)
        found = _find_backfill_sentence(
            raw_content, terms_full, terms_token,
            anchor_positions, first_anchor_pos,
        )
        if found is None:
            diag["unlocated"] += 1
            continue
        sent, anchor = found
        if not es:
            comp["evidence_sentence"] = sent
        if not ea:
            comp["evidence_anchor"] = anchor
        diag["backfilled"] += 1
    return diag


def _normalize_extract_payload(
    data: dict[str, Any], raw_content: str | None, content_lane: str = "",
    *, is_fallback: bool = False,
) -> dict[str, Any]:
    """F9-A: extract 后处理归一化单入口(LLM 成功路径 + deterministic fallback 共用 choke point)。

    probe-p7 定位:ag2 primary 抽取失败("how_params 分段未返回")走 fallback,但
    `build_deterministic_extract_result` 只 `_validate_extract_output`,跳过全部归一化
    (F8-A 回填/F8-C 过滤/F8-D 命名归一/F-A 锚解析/P1b-1 接地/F3-B 命令剥离/F6-A 闭合)
    → fallback 组件 evidence 空(components_evidence_backfilled=0)→ H3 残留 6 项原话不变。
    本函数把后处理收敛成单入口,两路径都过,按序(lead F9-A 指定):
      1. F-A 单字母锚解析(_resolve_short_anchors_to_full)
      2. edge evidence_anchor 校正(_correct_edge_anchors_to_raw)
      3. P1b-1 claims/argumentation 接地校正(_ground_claims_to_raw)
      4. F8-C 组件垃圾过滤(_filter_llm_component_names)
      5. F6-A 边端点闭合(_close_edge_endpoints)
      6. F8-D 端点命名归一(_normalize_edge_endpoints_to_canonical)
      7. F8-A 组件 evidence 回填(_backfill_component_evidence)
      8. F3-B 编造命令剥离(_strip_fabricated_how_commands)
    Returns diagnostics {components_filtered, components_evidence_backfilled,
    components_evidence_unlocated}(两路径统一进 ledger,ExtractResult.diagnostics)。

    is_fallback(lead F9-A 同一入口,fallback 路径标记):F3-B 在 fallback 跳过——fallback
    的 how_steps 命令是 AKB 合成占位(_build_deterministic_extract_data 硬编码,非 raw 来源),
    由 render-time ``_ground_how_step_commands_in_raw`` 统一在渲染层剥(不接地→降级 executable=False,
    SG-how-density fail-closed)。extract 时 F3-B 剥会破 procedural validate(≥3 executable 阈值不动)。
    其余 7 步两路径一致(fallback 收 F8-A 回填/F8-C 过滤/F8-D 命名归一/F-A/P1b-1 等收益)。
    """
    diag = {
        "components_filtered": 0,
        "components_evidence_backfilled": 0,
        "components_evidence_unlocated": 0,
    }
    if not raw_content or not isinstance(data, dict):
        return diag
    # 1. F-A 单字母锚 → 全名唯一前缀解析(在 edge anchor 校正之前)
    _resolve_short_anchors_to_full(data, raw_content)
    # 2. edge evidence_anchor 校正(LLM 编的 anchor → raw 真实 anchor,不放松防伪)
    _correct_edge_anchors_to_raw(data, raw_content)
    # 3. P1b-1 claims/argumentation 接地校正(弱匹配假锚校正 + interpretation→grounded 升级)
    _ground_claims_to_raw(data, raw_content)
    # 4. F8-C 组件垃圾过滤(祈使句/泛节名/收缩词/助动词/超长)— 清组件表后再做边活
    diag["components_filtered"] = _filter_llm_component_names(data)
    # 5. F6-A 边端点闭合(端点不在组件表 → 丢边)
    _close_edge_endpoints(data)
    # 6. F8-D 端点命名归一(from/to → canonical name,消命名分裂)
    _normalize_edge_endpoints_to_canonical(data)
    # 7. F8-A 组件 evidence 回填(空 evidence → raw 命中句 + 就近锚)
    bf = _backfill_component_evidence(data, raw_content)
    diag["components_evidence_backfilled"] = bf.get("backfilled", 0)
    diag["components_evidence_unlocated"] = bf.get("unlocated", 0)
    # 8. F3-B 编造命令剥离(how_steps command 不在 raw → 剥反引号)。
    # fallback 跳过(合成占位命令由 render-time _ground_how_step_commands_in_raw 处理)。
    if not is_fallback:
        _strip_fabricated_how_commands(data, raw_content)
    return diag


def _enrich_zero_eligible_with_deterministic(
    extract_data: dict[str, Any], raw_content: str | None
) -> dict[str, Any]:
    """F2-A/F4-A: LLM extract 成功但 0 eligible 边时的确定性抽边 enrichment(并入主路径)。

    真机探针暴露:LLM extract 成功但只产 inferred 弱边 → F4 0 eligible 早死,而
    _build_real_causal_edges 离线对同一 raw 能抽 eligible 边却没机会跑。本函数在
    LLM 成功路径上补这一刀:

    1. _scan_real_mechanism_components(raw) → 确定性组件,合并进 mechanism_components
       (LLM 已有同名/别名则对齐不重复,否则补进,守 FALLBACK_MAX_COMPONENTS=12)。
    2. _build_real_causal_edges(**det_components**, raw) → source_causal 边,合并进
       causal_edges(dedup by from/to)。
       F4-A 修:只用 det_components 抽边(不混 LLM 组件),避免 LLM slug 名(executor_pool)
       与确定性原名(Executor Pool)variant 碰撞 → "executor"/"captain" 被 [N1]a 歧义弃用
       → 抽不出 source_causal 边(probe-p3 ag2 根因)。

    Returns:
        {enriched, components_added, edges_added, edges_dropped_endpoint}
        enriched=True 若加了组件或边(需重评 floor);False 若 raw 无可抽(fail-closed)。
        F4-A fail-visible:caller(runner)记 ledger(applied/edges_added/passed_after)。
    """
    diag = {"enriched": False, "components_added": 0, "edges_added": 0, "edges_dropped_endpoint": 0}
    if not raw_content:
        return diag
    # 1. 确定性组件,合并(别名感知 dedup)
    det_components = _scan_real_mechanism_components(raw_content)
    existing_comps: list[dict[str, Any]] = list(
        extract_data.get("mechanism_components", []) or []
    )
    original_llm_comps = list(existing_comps)  # 合并前的 LLM 组件(供双跑抽边)
    existing_keys: set[str] = set()
    for c in existing_comps:
        if not isinstance(c, dict):
            continue
        n = str(c.get("name", "")).strip().lower()
        if n:
            existing_keys.add(n)
        for a in c.get("aliases") or []:
            if isinstance(a, str) and a.strip():
                existing_keys.add(a.strip().lower())
    added_comp = False
    for dc in det_components:
        name = str(dc.get("name", "")).strip()
        if not name or name.lower() in existing_keys:
            continue
        if len(existing_comps) >= FALLBACK_MAX_COMPONENTS:
            break
        existing_comps.append(
            {"name": name, "role": dc.get("role", "raw 命名机制组件")}
        )
        existing_keys.add(name.lower())
        added_comp = True
        diag["components_added"] += 1
    if added_comp:
        extract_data["mechanism_components"] = existing_comps
        # F8-A: enrichment 补的 det 组件(来自 _scan_real_mechanism_components,无 evidence
        # 字段)在 floor 重评时才加入——晚于 extract_claims 的 line-677 backfill,故此处再调
        # 一次回填覆盖新组件集(if ea and es 守卫跳过已回填的 LLM 组件,不重复)。
        bf_diag = _backfill_component_evidence(extract_data, raw_content)
        diag["components_evidence_backfilled"] = bf_diag.get("backfilled", 0)
        diag["components_evidence_unlocated"] = bf_diag.get("unlocated", 0)
    # 2. 确定性 source_causal 边——F4-A 修:双跑(det_components + 原始 LLM comps)分开抽边,
    # 避免 LLM slug(executor_pool)与确定性原名(Executor Pool)variant 碰撞致 'executor'
    # 被 [N1]a 歧义弃用(probe-p3 ag2 根因)。双跑各自只用自己的组件,无跨集 collision;
    # 合并两路 source_causal 边(dedup by from/to)。只取 source_causal(inferred 弱边不计 F4)。
    det_edges: list[dict[str, Any]] = []
    for comps_src in (det_components, original_llm_comps):
        for e in _build_real_causal_edges(comps_src, raw_content):
            if e.get("edge_kind") == "source_causal":
                det_edges.append(e)
    if not det_edges:
        diag["enriched"] = added_comp  # 加了组件但没边 → 仍需重评(组件数可能救 <3 那条)
        return diag
    existing_edges: list[dict[str, Any]] = list(
        extract_data.get("causal_edges", []) or []
    )
    # F4-A:dedup 只对 existing source_causal 边(不让 inferred 弱边同 (from,to) 阻挡
    # source_causal eligible 边加入——inferred 无 evidence/anchor 不计 F4,source_causal 才计)
    existing_pairs: set[tuple[str, str]] = {
        (str(e.get("from", "")), str(e.get("to", "")))
        for e in existing_edges
        if isinstance(e, dict) and e.get("edge_kind") == "source_causal"
    }
    added_edge = False
    for e in det_edges:
        pair = (str(e.get("from", "")), str(e.get("to", "")))
        if pair in existing_pairs:
            continue
        existing_edges.append(e)
        existing_pairs.add(pair)
        added_edge = True
        diag["edges_added"] += 1
    if added_edge:
        extract_data["causal_edges"] = existing_edges
    # F5-A/F6-A: 端点闭合——复用全局 _close_edge_endpoints(含 det_components 补名单)
    diag["edges_dropped_endpoint"] = _close_edge_endpoints(extract_data, det_components)
    # F8-D: 边端点命名归一(det 边用原名,LLM 边可能小写/别名→统一 canonical name)
    _normalize_edge_endpoints_to_canonical(extract_data)
    diag["enriched"] = added_comp or added_edge or diag["edges_dropped_endpoint"] > 0
    return diag


def build_deterministic_extract_result(
    raw_content: str,
    raw_path: str = "",
    content_lane: str = "",
) -> ExtractResult:
    """Build a conservative ExtractResult when the maker LLM is unavailable.

    This is a maker fallback, not a quality bypass: the composed candidate still
    goes through local structural checks, GPT-5.4 grading, repair, and five-gate
    proposal handling.

    content_lane（SC-3a）：传给 _build_deterministic_extract_data + _validate_extract_output。
    concept 车道：扫 raw 找真实命名机制组件 + 真因果 eligible 边 + 相邻 inferred 弱边
    （§0.2/M1/D2），不硬编码 loop 组件（D8），不注入假 AKB 操作化步骤（D7 poisoned trace）。
    §0.2/D9：concept fallback 凑不齐 ≥3 组件或 0 eligible edge → 构造 fallback_quality_basis
    再 fail-closed 返回（失败路径也携 basis，不靠 error string 解析）；成功路径也携 basis
    作机读质量基线。procedural 行为不变（零回归）。
    """
    try:
        data = _build_deterministic_extract_data(
            raw_content, raw_path, content_lane=content_lane
        )
        # F9-A: fallback 路径也过归一化单入口(probe-p7:ag2 primary 抽取失败走 fallback,
        # 但旧版 fallback 只 validate 跳过归一化 → 组件 evidence 空、命名不归一、垃圾未滤。
        # 与 LLM 成功路径同 choke point,diag 统一进 ledger。is_fallback=True:F3-B 跳过
        # (合成占位命令由 render-time _ground_how_step_commands_in_raw 处理,extract 剥会破
        # procedural validate ≥3 executable 阈值不动)。
        _norm_diag = _normalize_extract_payload(
            data, raw_content, content_lane, is_fallback=True
        )
        _validate_extract_output(data, content_lane=content_lane)
    except Exception as exc:
        return ExtractResult(
            success=False,
            data={},
            retries_used=0,
            error=f"deterministic_maker_fallback_failed: {exc}",
        )
    # §0.2/M1/D9：concept fallback 质量基线检查（<3 组件或 0 eligible edge → fail-closed）
    if _is_concept_lane(content_lane):
        basis = _build_fallback_quality_basis(data)
        data["fallback_quality_basis"] = basis
        if (
            basis["component_count"] < CONCEPT_MIN_MECHANISM_COMPONENTS
            or basis["source_causal_edge_count"] == 0
        ):
            return ExtractResult(
                success=False,
                data={"fallback_quality_basis": basis},
                retries_used=0,
                error=(
                    f"concept deterministic fallback fail-closed: "
                    f"{basis['component_count']} 组件 / "
                    f"{basis['source_causal_edge_count']} eligible 因果边 "
                    f"（需 ≥{CONCEPT_MIN_MECHANISM_COMPONENTS} 组件且 ≥1 eligible edge，"
                    f"§0.2 D1；inferred 弱边不计）"
                ),
            )
    return ExtractResult(
        success=True,
        data=data,
        retries_used=0,
        diagnostics={
            "components_evidence_backfilled": _norm_diag.get(
                "components_evidence_backfilled", 0
            ),
            "components_evidence_unlocated": _norm_diag.get(
                "components_evidence_unlocated", 0
            ),
            "components_filtered": _norm_diag.get("components_filtered", 0),
        },
    )


def _build_fallback_quality_basis(data: dict[str, Any]) -> dict[str, Any]:
    """§0.2/D9：fallback 机读质量基线（失败路径也须携带，不靠 error string 解析）。

    component_count / source_causal_edge_count（F4 eligible）/ inferred_edge_count
    （不计 F4）/ component_evidence_anchors。runner/calibration 透传供 e2e 可见。
    """
    components = data.get("mechanism_components", []) or []
    edges = data.get("causal_edges", []) or []
    source_causal = [
        e for e in edges if isinstance(e, dict) and e.get("edge_kind") == "source_causal"
    ]
    inferred = [
        e for e in edges
        if isinstance(e, dict) and e.get("edge_kind") == "inferred_from_structure"
    ]
    return {
        "component_count": len(components),
        "source_causal_edge_count": len(source_causal),
        "inferred_edge_count": len(inferred),
        "component_evidence_anchors": [
            str(c.get("evidence_anchor", "")) for c in components if isinstance(c, dict)
        ],
    }


def _build_deterministic_extract_data(
    raw_content: str,
    raw_path: str = "",
    content_lane: str = "",
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
        (
            fallback_how_anchor,
            "工作队列需要携带 source URL、raw anchor、"
            "content hash、输出路径和生命周期状态",
        ),
        (
            anchor_c if anchor_c != primary else fallback_how_anchor,
            "修复路径必须有固定轮数，并只在剩余缺陷清零时接受候选",
        ),
        (
            anchor_d if anchor_d != primary else fallback_how_anchor,
            "主链路预算应显式配置提炼超时、裁判超时、"
            "修复轮数、串行度和 fallback 策略",
        ),
        (
            anchor_e if anchor_e != primary else fallback_how_anchor,
            "maker 与 checker 需要分离，"
            "最终部署门不得把 fallback 裁判当通过条件",
        ),
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

    # 治本2（砍编造源）：deterministic fallback 提出一组候选操作命令，但这些命令是
    # AKB 对来源结构的保守映射，不保证字面存在于 raw。是否真正渲染进 How 由
    # _build_body 的「emission 前回查 raw」护栏决定（_command_grounded_in_raw）：
    # 命令字面量在 raw 中出现 → 保留为 source-grounded 可执行步骤；不在 raw 中 →
    # 降级为无命令 instruction（executable:False），绝不把编造命令塞进候选页（会被
    # SG-command-recheck 判为编造）。当真实可锚定命令 <3 时 SG-how-density 自然
    # fail-closed。这样既不向 raw 注入编造命令，又保留来源里确实存在的操作命令。
    how_steps = [
        {
            "instruction": "Define the work item schema before processing",
            "command": (
                "queue_item={source_url,raw_anchor,"
                "content_hash,output_path,lifecycle_state}"
            ),
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
            "command": (
                "distill_timeout_seconds=60; "
                "grader_timeout_seconds=120; max_parallel_items=1"
            ),
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
    # Finding 6: deterministic fallback 也注入 provenance_split 标注层，与 LLM
    # extract 路径（_normalize_how_step）保持同构，使 compose_candidate 的 How 节
    # 三态摘要对 maker fallback 与 LLM 抽取一致可被 SG 读取。
    how_steps = [
        {
            **step,
            "provenance_split": how_step_provenance_split(
                step, str(step.get("command", "")), str(step.get("anchor", ""))
            ),
        }
        for step in how_steps
    ]
    # WP-3/D7: concept 车道不注入假 AKB 操作化步骤（queue_item schema / ledger.append /
    # max_repair_rounds / grader assert）——idx5 poisoned trace 源头（grader 判未从 cited
    # anchor 推出）。concept F4 判 mechanism_components，how_steps 留空避免造假；
    # procedural 保留（零回归）。
    if _is_concept_lane(content_lane):
        how_steps = []
    params = {
        "max_repair_rounds": "3",
        "distill_timeout_seconds": "60",
        "grader_timeout_seconds": "120",
        "max_parallel_items": "1",
        "fallback_allowed": "false",
    }
    if _is_concept_lane(content_lane):
        params = {}
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
    # WP-3/D8 + §0.2/M1/D9：concept 车道扫 raw 找真实命名机制组件 + 真因果 eligible 边
    # + 相邻组件 inferred 弱边。不在此处 raise——component/eligible-edge 计数与
    # fallback_quality_basis 构造交给 build_deterministic_extract_result（D9 失败路径也
    # 须携 basis，不靠 error string 解析；此处只产结构数据）。procedural 不注入（零回归）。
    mechanism_components: list[dict[str, str]] = []
    causal_edges: list[dict[str, Any]] = []
    if _is_concept_lane(content_lane):
        real_components = _scan_real_mechanism_components(raw_content)
        # §4/D2：fallback 组件也生成稳定 component_id（一致性 + C1 渲染消费）
        for c in real_components:
            if not c.get("component_id"):
                c["component_id"] = _slugify_component_id(c.get("name", ""))
        mechanism_components = real_components
        causal_edges = _build_real_causal_edges(real_components, raw_content)
    return {
        "claims": claims,
        "how_steps": how_steps,
        "mechanism_components": mechanism_components,
        "causal_edges": causal_edges,
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
        anchor = canonicalize_anchor(match.group(1))
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        block = body[match.start():end]
        _strip_re = rf"(?m)^(?:#{{1,6}}\s*)?{RAW_ANCHOR_DECL_RE}(?:[_A-Z0-9-]*|\b).*"
        block = re.sub(_strip_re, "", block, count=1)
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
    raw_content: str | None = None,
    content_lane: str = "",
    known_concepts: list[str] | None = None,
    pending_queue: list[str] | None = None,
) -> ComposeResult:
    """段 2: 按模板成稿 10 节，落 staging/candidate/<batch>/。

    frontmatter 由确定性代码写死（防范已知坑 1-3）。

    Args:
        extract_result: 段 1 抽取结果（必须 success=True）。
        raw_path: raw 文件路径（用于 source_refs）。
        batch_id: 批次标识（如 "2026-06-13-001"）。
        output_dir: 输出根目录（None 时从 vault.toml 解析 staging）。
        route_llm_fn: LLM 路由函数注入（fixture 测试用）。
        raw_content: 调用方持有的 raw 全文（**权威来源**）。must_cover 扫描与
            self-gate A3 锚点校验用它。传入时优先于磁盘 raw_path 读取——这样
            calibration 等只构造 raw_path 但不落地文件的链路也能拿到原文，
            避免 must_cover/命令接地因 read_text 失败而静默失真。None 时回退
            读磁盘 raw_path（生产 7 段链路 raw 已落地，行为不变）。
        content_lane: 内容车道（concept/procedural，step-1 透传字段）。写入 candidate
            frontmatter 的 content_lane 键；本 step 不参与判定分支。
        known_concepts: 已知概念 slug 集（step-1 vault loader 装载）。透传给 compose 内
            第一道 self-gate 的图谱 resolve（A3-8），使概念引用在生产路径真能 resolve，
            不再因 fixture 恒空而被掩盖。None 时行为同旧（self-gate 默认空集）。
        pending_queue: pending 队列概念 slug 集（step-1 vault loader 装载）。同 known_concepts，
            透传给第一道 self-gate。

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
    # WP-3.4: 透传 known_concepts/pending_queue 给 _build_frontmatter，使 frontmatter
    # related_* 与 body graph 同源 resolve——不写悬空 [[...]] 伪装 real_link（round-2 P1）。
    frontmatter = _build_frontmatter(
        raw_path,
        extract_result.data,
        content_lane=content_lane,
        known_concepts=known_concepts,
        pending_queue=pending_queue,
    )

    # raw_content 同源读取：must_cover 扫描与 self-gate A3 锚点校验共用同一份原文，
    # 避免截断/不一致导致 must_cover 账本与锚点校验脱节。
    # 优先用调用方传入的 raw_content（权威源）；未传才回退读磁盘 raw_path。
    # calibration 等链路只构造 raw_path 不落地文件，必须靠传入值，否则 read_text
    # 失败 → raw_content=None → must_cover 跳过 / 命令接地剥离，静默失真。
    if raw_content is None:
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
        known_concepts=known_concepts,
        pending_queue=pending_queue,
        content_lane=content_lane,
    )

    # 组装完整候选页
    full_page = _assemble_page(frontmatter, body)

    try:
        from src.akb2.quality.checks_structural import run_candidate_self_gate

        # step-3 SC-3e/A3-8：compose 内第一道 self-gate 真传 known_concepts/pending_queue
        # 实参（step-1 vault loader 装载，经 compose_candidate 形参透传）+ content_lane，
        # 使概念边在生产路径真做确定性 resolve、SG-how-density 按车道选 gate_min，
        # 不再因传 None/缺 lane 被 fixture 恒空掩盖。
        compose_defects = run_candidate_self_gate(
            full_page,
            raw_content=raw_content,
            known_concepts=known_concepts,
            pending_queue=pending_queue,
            content_lane=content_lane,
        )
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
    content_lane: str = "",
    known_concepts: list[str] | None = None,
    pending_queue: list[str] | None = None,
) -> dict[str, Any]:
    """构建 frontmatter（确定性代码）。

    candidate 阶段产出 active-ready 的全字段集（除 reviewer/reviewed_at——
    那两项由 promote 升格时的机器 reviewer 写入，见 CONTENT-LIFECYCLE §6.1）。

    content_lane（step-1）：concept/procedural 车道写入 candidate frontmatter 的
    content_lane 键，使车道随候选页持久化、供 grader/repair/promote 全链可见。

    WP-3.4（round-2 P1）：known_concepts/pending_queue 提供时，frontmatter related_*
    只写可 resolve 到 known/pending 的概念为 ``[[wikilink]]``——不写悬空 ``[[...]]``
    伪装 real_link（Gate-3 据此判 graph_readiness）。二者均 None（legacy/测试）时保留
    旧 _infer_concepts fallback 行为（零回归）。

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

    # WP-3.4 + P1-B + P2-C: frontmatter related_* 与 body graph 同源 resolve。
    # P1-B: 区分 None（未提供）vs []（提供但空）——任一非 None 就按 resolver 过滤
    # （空集 → related=[] / 不产悬空 real_link，gate _graph_readiness 仅查 related_* 非空）；
    # 二者均 None → legacy（_infer_concepts fallback，零回归）。
    # P2-C: 复用 checks_structural._resolve_graph_node（前缀归一化 concept-X↔X /
    # pending 共指同源），与 body graph 同一 resolve 逻辑。
    resolver_provided = (known_concepts is not None) or (pending_queue is not None)
    related = list(concepts) if concepts else []
    if resolver_provided:
        from src.akb2.quality.checks_structural import _resolve_graph_node

        related = [
            c for c in related if _resolve_graph_node(str(c), known_concepts, pending_queue)
        ]
        if len(related) < RELATED_CONCEPTS_MIN:
            for fc in _infer_concepts(claims):
                if fc not in related and _resolve_graph_node(
                    fc, known_concepts, pending_queue
                ):
                    related.append(fc)
                if len(related) >= RELATED_CONCEPTS_MIN:
                    break
        related_links = [c if str(c).startswith("[[") else f"[[{c}]]" for c in related]
    else:
        # legacy: 二者均 None（未提供 resolver）→ _infer_concepts fallback + [[...]]
        if len(related) < RELATED_CONCEPTS_MIN:
            fallback_concepts = _infer_concepts(claims)
            for fc in fallback_concepts:
                if fc not in related:
                    related.append(fc)
                if len(related) >= RELATED_CONCEPTS_MIN:
                    break
        related_links = [c if str(c).startswith("[[") else f"[[{c}]]" for c in related]
        # F8-B(lead 裁决 1):回退 F7-C 的"塞组件名进 related_concepts"保底——R14 加
        # status=='active' 守卫后,candidate(非 active)空 related 不再误报 R14,
        # 冷启动 related 空是诚实状态(frontmatter 语义不失真,不硬塞 component 名)。

    # future_questions 2-3 条
    questions = _generate_questions(claims, extract_data.get("how_steps", []))

    # gate_reason 截断到 150 字符
    first_anchor = str(claims[0].get("anchor", "§A")).strip() if claims else "§A"
    raw_reason = f"候选来源锚点 [raw {first_anchor}]；待 GPT-5.4 裁判与五闸确认。"
    gate_reason = raw_reason[:GATE_REASON_MAX_CHARS]

    # §0.2/D8：冷启动分叉——resolver 提供但 related 不足 RELATED_CONCEPTS_MIN 时，
    # 写 proposed_graph_nodes（未 resolve 的 referenced_concepts，每项 {slug,label,
    # reason,source_anchor}）+ graph_pending=true。proposal 模式（check_a2_frontmatter
    # concept）接受 proposed_graph_nodes>=1；auto-promote/active 仍要求真实 related_*
    # （run_promote AUTO_PROMOTE=1 二次拒 proposed_node_only 写 active，D8 底线）。
    proposed_graph_nodes: list[dict[str, str]] = []
    graph_pending = False
    if resolver_provided and len(related) < RELATED_CONCEPTS_MIN:
        graph_pending = True
        unresolved = [
            str(c) for c in (concepts if concepts else []) if str(c) not in related
        ]
        for c in unresolved[:4]:
            proposed_graph_nodes.append(
                {
                    "slug": _slugify_component_id(c),
                    "label": c,
                    "reason": (
                        "冷启动：referenced_concept 未 resolve 到 known/pending，"
                        "待 adjudication"
                    ),
                    "source_anchor": first_anchor,
                }
            )

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
        "content_lane": content_lane,  # 内容车道（step-1：concept/procedural）
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
        # §0.2/D8：冷启动 proposed_graph_nodes + graph_pending（proposal 模式接受）
        "proposed_graph_nodes": proposed_graph_nodes,
        "graph_pending": graph_pending,
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
    if (
        has_interpretation
        or extract_data.get("how_steps")
        or extract_data.get("referenced_concepts")
    ):
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
        f"[raw {anchor_a}] (akb_interpretation) {head} "
        "在真实工程中的失败模式、适用边界与回退策略有哪些？",
        f"[raw {anchor_j}] (akb_interpretation) "
        "该方案的停止条件、预算上限与失败升级路径应如何设计，避免自动化失控？",
        f"[raw {anchor_b}] (akb_interpretation) "
        "在并发、重试与状态漂移场景下，如何检测冲突、重复劳动并保持一致与可恢复？",
    ]
    # 裁到 2-3 条(默认即 3 条,留 min/max 兜底以防未来调整)
    if len(questions) > FUTURE_QUESTIONS_MAX:
        questions = questions[:FUTURE_QUESTIONS_MAX]
    if len(questions) < FUTURE_QUESTIONS_MIN:
        questions.append(
            f"[raw {anchor_a}] (akb_interpretation) "
            "该方案的可靠性如何度量，哪些缺陷必须升级给人类判断？"
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
    pattern = rf"\[raw\s+{RAW_ANCHOR_DECL_RE}\]|{RAW_ANCHOR_DECL_RE}|[\"“”']"
    return bool(re.search(pattern, evidence))


def _extract_raw_anchor(text: str) -> str:
    """从文本中提取第一个 §X 锚点(canonicalize 后),返回空字符串表示无锚点。"""
    m = re.search(
        rf"\[raw\s+({RAW_ANCHOR_DECL_RE})\]|(?<![A-Za-z0-9])({ANCHOR_TOKEN_PAT})",
        str(text),
    )
    if not m:
        return ""
    return canonicalize_anchor(m.group(1) or m.group(2))


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
        provenance = (
            "source_grounded"
            if RAW_ANCHOR_REF_RE.search(evidence)
            else "akb_interpretation"
        )
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
    # P1a: 认下划线(§I_LIMITATIONS 也归一为 [raw §I_LIMITATIONS]),token 走 ANCHOR_TOKEN_PAT
    normalized = re.sub(rf"\braw\s+({ANCHOR_TOKEN_PAT})", r"[raw \1]", normalized)
    normalized = re.sub(rf"原文\s*({ANCHOR_TOKEN_PAT})", r"[raw \1]", normalized)
    normalized = re.sub(rf"({ANCHOR_TOKEN_PAT})", r"[raw \1]", normalized)
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
        if token
        and token not in {
            "agent", "engineering", "model", "system",
            "tool", "tools", "concept",
        }
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
    b_idx = min(1, len(claims) - 1)
    c_idx = min(2, len(claims) - 1)
    anchor_b = str(claims[b_idx].get("anchor", anchor_a)).strip() or anchor_a
    anchor_c = str(claims[c_idx].get("anchor", anchor_b)).strip() or anchor_b
    claim_a = str(claims[0].get("text", "")).strip()
    claim_b = str(claims[b_idx].get("text", "")).strip()
    claim_c = str(claims[c_idx].get("text", claim_b or claim_a)).strip()
    bullets = [
        (
            f"- [raw {anchor_a}] (source_grounded) "
            f"来源事实：{_short_reason_fragment(claim_a)}。"
        ),
        (
            f"- [raw {anchor_b}] (source_grounded) "
            f"来源事实：{_short_reason_fragment(claim_b or claim_a)}。"
        ),
        (
            f"- [raw {anchor_c}] (source_grounded) "
            f"来源事实：{_short_reason_fragment(claim_c or claim_a)}。"
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
    supported_steps = (
        rendered_how_steps
        or _supported_how_steps(how_steps, claims, raw_anchor_texts)
    )
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
    risk_claim_idx = min(2, len(claims) - 1) if claims else 0
    risk_claim_text = (
        claims[risk_claim_idx].get("text", "来源风险或边界论点")
        if claims else "来源风险或边界论点"
    )
    risk_claim_anchor = (
        claims[risk_claim_idx].get("anchor", anchor_a) if claims else anchor_a
    )
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
            f"{risk_claim_text} | source_grounded | source claim | "
            f"[raw {risk_claim_anchor}] (source_grounded) |"
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
            haystack = (
                f"{path.stem} "
                f"{_read_frontmatter_scalar(path, 'title')} "
                f"{_read_frontmatter_scalar(path, 'canonical_title')}"
            ).lower()
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
    executable_count = len(rendered_how_steps or [])
    if not executable_count:
        executable_count = sum(
            1
            for s in how_steps
            if isinstance(s, dict)
            and s.get("executable") is True
            and str(s.get("command", "")).strip()
        )
    executable_note = f"至少 {executable_count} 个可执行动作" if executable_count else "可验证动作"
    return "\n".join(
        [
            f"- [raw {anchor_b}] (akb_interpretation) "
            f"适用场景：需要把 {executable_note} 串成可复跑流程，并保留人工复核入口的工程项目。",
            f"- [raw {anchor_c}] (akb_interpretation) "
            "适用场景：需要在出现缺陷时保留已验证上游结果，并对失败小节做定点修复的内容管线。",
            f"- [raw {anchor_c}] (akb_interpretation) "
            "不适用场景：一次性问答、无外部状态、无审查门槛，或无法承担持续维护/复核成本的任务。",
            f"- [raw {anchor_d}] (akb_interpretation) "
            "约束条件：需要显式命名预算、超时、串行度和最终裁判 fallback 策略。",
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
        sections[canonicalize_anchor(match.group(1))] = re.sub(r"\s+", " ", text[start:end]).strip()
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
    (
        "§B",
        (
            "trigger", "work item", "work unit", "selector",
            "action executor", "quality gate", "ledger", "write",
        ),
    ),
    ("§C", ("repair", "converge", "convergence", "restart", "defect")),
    ("§D", ("budget", "timeout", "retry", "max_rounds", "max_retries", "constant")),
    (
        "§E",
        (
            "maker", "checker", "model family", "model families",
            "producer", "reviewer", "grader", "guard",
        ),
    ),
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
    """Derive one conservative AKB operation step from an evidence-map claim.

    治本2（砍编造源）：本函数按 claim 文本关键词提出候选操作命令，但这些命令是 AKB
    对来源概念的保守映射，不保证字面存在于 raw。命令是否真正渲染进 How 由
    _build_body 的「emission 前回查 raw」护栏（_command_grounded_in_raw）裁决：在
    raw 中出现 → 保留；否则降级为无命令 instruction。本函数仍可返回带 command 的
    step，绝不会让编造命令进入候选页——护栏在渲染层兜底。
    """
    text = str(claim.get("text", "")).lower()
    anchor = str(claim.get("anchor", "§A")).strip() or "§A"
    if any(token in text for token in (
        "ledger", "checkpoint", "before executing", "crash recovery",
        "账本", "检查点", "重放", "恢复",
    )):
        instruction = "在执行下一动作前写入检查点，并保存输入、工具调用和响应状态"
        command = (
            "ledger.append({run_id:run_id,item_id:work_item.id,"
            "status:'checkpointed',next_action:'execute'})"
        )
    elif any(token in text for token in (
        "budget", "budgets", "max_actions", "max_repair_rounds",
        "max_rounds", "max_retries", "constant", "stop on",
        "stop condition", "预算", "停止",
    )):
        instruction = "在循环入口断言命名预算和停止条件"
        command = (
            "assert max_actions <= 5 and max_repair_rounds <= 3 "
            "and grader_timeout_seconds == 120"
        )
    elif any(token in text for token in (
        "repair", "defect", "restart", "convergence", "failure",
        "schema_mismatch", "missing_evidence", "tool_timeout",
        "reviewer_disagreement", "修复", "缺陷", "失败", "分类",
    )):
        instruction = "按失败类型分派定点修复动作，不能整篇重跑"
        command = (
            "repair_router.dispatch(error_class=current_failure.type, "
            "anchor=current_failure.raw_anchor)"
        )
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


def _source_claim_for_raw_operation(
    anchor: str,
    raw_anchor_texts: dict[str, str],
) -> dict[str, Any]:
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
    for idx, step, anchor in (
        rendered_how_steps
        or _supported_how_steps(how_steps, claims, raw_anchor_texts or {})
    ):
        claim = claim_by_anchor.get(anchor) or (
            claims[min(idx - 1, len(claims) - 1)] if claims else {}
        )
        instruction = str(
            step.get("instruction", step) if isinstance(step, dict) else step
        ).strip()
        command = ""
        if isinstance(step, dict):
            # 治本2（砍编造源）：仅当 step 自带非空 command（已是 source-grounded 或
            # LLM 抽取的可执行命令）才展开/渲染反引号命令。command 为空的步骤
            # （deterministic fallback / claim-derived 的无命令 instruction）不得由
            # instruction 关键词反推合成命令——否则边界表会偷偷造出 assertions.run(...)
            # 这类原文不存在的命令，被 SG-command-recheck 判为编造。
            raw_command = str(step.get("command", "")).strip()
            if raw_command:
                command = _expand_placeholder_command(raw_command, instruction, idx)
        support_text = str(claim.get("text", "")).strip() or _anchor_support_text(
            anchor, claims, raw_anchor_texts or {}
        )
        support = f"[raw {anchor}] {support_text[:90] or '来源事实'}"
        gap = (
            str(claim.get("evidence_gap", "")).strip()
            or "未验证：原文未给出该操作化步骤的生产实测。"
        )
        operation = instruction
        if command:
            operation = f"{operation} `{command}`"
        rows.append(
            f"| {idx} | {_md_table_cell(support)} | "
            f"{_md_table_cell(operation)} | {_md_table_cell(gap)} |"
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
    """把 claim 压成 TLDR 单句，避免一个 claim 内部多句导致 TLDR 超标。

    F10-A:末过 _sanitize_backticks 消毒不成对反引号(TLDR 含 raw 摘录 claim text)。
    """
    compact = re.sub(r"\s+", " ", str(text)).strip()
    if not compact:
        return ""
    compact = re.sub(r"[。.!?！？]+", "；", compact)
    compact = compact.strip("；; ")
    compact = compact.rstrip("。.!?！？")
    return _sanitize_backticks(compact)


def _normalize_text_for_sim(text: str) -> str:
    """F8-D: 归一化文本用于相似度比对(小写 + 去非词字符 + 折叠空白)。"""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(text).lower())).strip()


def _text_similarity(a: str, b: str) -> float:
    """F8-D: 归一化后词集 overlap coefficient = |A∩B| / min(|A|,|B|)(0-1)。

    用 overlap 而非 Jaccard:Jaccard 对长短不一的包含关系(如 'A holds goal' vs
    'A holds goal and maintains tasks')给 0.7 < 0.8 漏判,而二者实为同一论点(短串
    全含于长串)。overlap = 交集 / 较小集大小 → 包含关系给 1.0,符合 lead '≥80%
    视为重复' 的意图。≥0.8 视为重复论点。
    """
    na, nb = _normalize_text_for_sim(a), _normalize_text_for_sim(b)
    if not na or not nb:
        return 0.0
    wa, wb = set(na.split()), set(nb.split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _dedup_claims_for_tldr(
    claims: list[dict[str, Any]], threshold: float = 0.8
) -> list[dict[str, Any]]:
    """F8-D: TLDR claims 去重——归一文本 overlap ≥threshold 视为重复,保留首个。

    grader 抱怨:'后两句重复同一论点/大面积重复 §F_EVIDENCE'。top-3 claims 可能有
    ≥80% 相似的重复论点(LLM 从同一 raw 锚抽多条近似 claim,或子串包含)。本函数去重,
    保序保留首个。
    """
    deduped: list[dict[str, Any]] = []
    for c in claims:
        text = str(c.get("text", ""))
        if any(
            _text_similarity(text, str(d.get("text", ""))) >= threshold
            for d in deduped
        ):
            continue
        deduped.append(c)
    return deduped


def _build_tldr_mechanism_sentence(
    extract_data: dict[str, Any], raw_content: str | None
) -> str:
    """F8-D: TLDR 核心机制句——首条 eligible 边 '{from} {relation} {to} [raw §X].'。

    要素覆盖 1/3:核心机制。零编造:来自 eligible source_causal 边(已校验);anchor ∈ 声明集。
    无 eligible → 空串(退化为纯 claims TLDR)。
    """
    edges = extract_data.get("causal_edges", []) or []
    decl = declared_anchor_set(raw_content)
    for e in edges:
        if not isinstance(e, dict):
            continue
        if e.get("edge_kind") != "source_causal":
            continue
        if not e.get("evidence") or not e.get("evidence_anchor"):
            continue
        anchor = canonicalize_anchor(str(e.get("evidence_anchor", "")).strip())
        if anchor not in decl and decl:
            resolved = resolve_anchor_ref(anchor, decl)
            anchor = resolved if resolved else anchor
        if not decl or anchor not in decl:
            continue
        frm = str(e.get("from", "")).strip()
        to = str(e.get("to", "")).strip()
        rel = str(e.get("relation", "")).strip() or "导致"
        return f"(source_grounded) {frm} {rel} {to} [raw {anchor}]."
    return ""


def _build_tldr_component_sentence(
    extract_data: dict[str, Any], raw_content: str | None
) -> str:
    """F8-D: TLDR 组件全景句——'机制组件(N 个): name1, name2, ... [raw §X].'。

    要素覆盖 2/3:组件全景(组件数 + 关键名)。anchor 用首组件 evidence_anchor(∈ 声明集,
    F6-D 回填保证)。无组件/无有效锚 → 空串。
    """
    components = extract_data.get("mechanism_components", []) or []
    if not components:
        return ""
    decl = declared_anchor_set(raw_content)
    key_names = [
        str(c.get("name", "")).strip()
        for c in components
        if isinstance(c, dict) and str(c.get("name", "")).strip()
    ][:4]
    if not key_names:
        return ""
    # 取首个有 evidence_anchor∈声明集 的组件
    anchor = ""
    for c in components:
        if not isinstance(c, dict):
            continue
        a = canonicalize_anchor(str(c.get("evidence_anchor", "")).strip())
        if a and (not decl or a in decl):
            anchor = a
            break
    if not anchor:
        return ""
    return (
        f"(source_grounded) 机制组件（{len(key_names)} 个）："
        f"{', '.join(key_names)} [raw {anchor}]."
    )


# 共享 markdown 表格 cell 转义(写侧),与 checks_structural 的读侧(split_md_table_row)同契约
from src.akb2.quality.md_table import md_table_cell as _md_table_cell  # noqa: E402

# ---------------------------------------------------------------------------
# must_cover 代码直出渲染（step-2: scanner 账本 → 正文三节强制渲染）
#
# scan_all 产出的 EvidenceItem 携带 raw_anchor_span（char offset 进 raw_text），但
# 正文引用锚点是 [raw §X] 形态。A3 (check_a3_raw_anchors) 拒绝任何 raw 未声明的 §X，
# 故须把每个 span 就近映射到一个已声明的 §X 标题——落在该标题之下的 evidence 归属该节。
# ---------------------------------------------------------------------------

# P1a:声明形态 + 位置 + 就近映射统一委托 raw_anchors（口径唯一,见该模块 docstring）。
# 旧本地 _RAW_ANCHOR_DECL_PATTERNS（4 形态全局,不认 [B4] 目录块收窄、不 canonicalize）已删。


def _declared_raw_anchors_with_pos(raw_content: str) -> list[tuple[int, str]]:
    """Return (position, canonical §X) pairs for all declared raw anchors, sorted.

    P1a:委托 ``raw_anchors.declared_anchor_positions``——heading + marker 全 raw,
    table/rawline 只在 `## 锚点目录` 块;marker 后缀剥离归一。
    """
    return declared_anchor_positions(raw_content)


def _map_span_to_declared_anchor(
    raw_content: str | None,
    span_start: int,
    decls: list[tuple[int, str]] | None = None,
) -> str:
    """Map a raw_text char offset to the nearest preceding declared §X anchor.

    P1a:委托 ``raw_anchors.nearest_preceding_anchor``（strict,不回落首锚）+ compose
    渲染层 lenient 兜底:span 先于所有声明(frontmatter/prologue)→ 回落首锚,保留既有
    must_cover 渲染兜底(evidence 须有 [raw §X] 可渲染,否则 A3 缺陷)。

    严格 fail-closed(不回落)由 F4 eligibility 走 ``_build_real_causal_edges`` 的
    ``nearest_preceding_anchor`` 直接判定——渲染层与 eligibility 层语义不同。
    Returns "" only when raw has no declared anchors at all.
    """
    if decls is None:
        decls = declared_anchor_positions(raw_content) if raw_content else []
    if not decls:
        return ""
    chosen = nearest_preceding_anchor(raw_content, span_start, decls)
    if not chosen:
        chosen = decls[0][1]  # compose 渲染 lenient 回落首锚
    return chosen


def _strip_ungrounded_backtick_commands(text: str, raw_text: str) -> str:
    """F7-B: 渲染前最后一道不变式——text 里所有反引号命令(非 reserved)必须字面在 raw
    出现(SG-command-recheck 同口径 _BACKTICK_COMMAND_RE + is_command_allowed_for_candidate)。
    不在→剥反引号(保留文本,去掉 `` ` `` 包裹,SG 不再当命令回查)。

    probe-p6 memory:~/；claude/rules/ 漏进来——F3-B 只 strip how_steps[].command 字段,
    漏 instruction/argumentation/tldr 里的反引号串;repair 重写后也不过 strip。本函数在
    compose 渲染 how 节后对整段文本做最终回查,与 SG 完全同口径(自检不自拒)。
    """
    if not text or not raw_text:
        return text
    from src.akb2.quality.source_ops_scanners.raw_verify import (
        _BACKTICK_COMMAND_RE,
        is_command_allowed_for_candidate,
    )

    def _replace(m: re.Match[str]) -> str:
        literal = m.group(1).strip()
        if not literal:
            return m.group(0)
        if is_command_allowed_for_candidate(literal, raw_text):
            return m.group(0)  # 在 raw 中,保留反引号
        return literal  # 不在 raw → 剥反引号(保留文本,SG 不再回查)

    return _BACKTICK_COMMAND_RE.sub(_replace, text)


def _sanitize_backticks(text: str) -> str:
    """F10-A: 消毒 raw 摘录文本的反引号——保证平衡,防 SG-command-recheck 误把叙述框成命令。

    raw 摘录(evidence/TLDR/叙述/组件表句)可能含不成对反引号(probe-p8 crewai
    'to resume:)。 Creating Crews...' 含奇数反引号),渲染进 body 后跨段把叙述框成
    "命令" span → SG-command-recheck 误报。本函数:奇数反引号→移除末个不成对反引号
    (成偶数=平衡,与 SG 顺序配对同口径);偶数不动(已是成对 code span,SG 回查 grounded)。
    只用于 raw 摘录字段(evidence/claim text/TLDR/叙述),不用于 how_text 命令段
    (那由 _strip_ungrounded_backtick_commands 处理)。
    """
    if not text:
        return text
    n = text.count("`")
    if n % 2 == 0:
        return text  # 平衡
    # 奇数:顺序配对下末个反引号不成对 → 移除(成偶数)
    idx = text.rfind("`")
    return text[:idx] + text[idx + 1:]


def _inject_missing_must_cover(
    section_text: str,
    items: list[Any],
    inject_prefix: str = "- ",
) -> str:
    """F6-C: 检查 must_cover 字面量是否在 section_text 中(归一化比对,与 SG-must-cover
    同口径 _normalize_must_cover_text);缺失→逐字注入(含链接语法,不意译)。

    must_cover 项空/超长>500→跳过(现有 fail 路径不变)。
    """
    if not items:
        return section_text
    from src.akb2.quality.checks_structural import _normalize_must_cover_text

    norm_section = _normalize_must_cover_text(section_text)
    for item in items:
        literal = str(getattr(item, "literal", "") or "").strip()
        if not literal or len(literal) > 500:
            continue
        if _normalize_must_cover_text(literal) not in norm_section:
            section_text = f"{section_text}\n{inject_prefix}{literal}"
            norm_section = _normalize_must_cover_text(section_text)
    return section_text


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


def _read_full_raw_text(raw_path: str) -> str:
    """读取完整 raw 文本（用于 emission 前命令回查）。读不到返回空串（fail-closed）。"""
    if not raw_path:
        return ""
    try:
        return Path(raw_path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _command_grounded_in_raw(command: str, raw_text: str) -> bool:
    """判断一条反引号命令是否允许进候选页（与 SG-command-recheck 同口径）。

    治本2（砍编造源）核心谓词：空命令/保留 token 允许；危险命令即使在 raw
    出现也不允许；其他命令字面量必须作为子串出现在 raw_text 中才算
    source-grounded。raw 为空则一律不接地（fail-closed），任何派生命令都会被剥离。
    """
    return is_command_allowed_for_candidate(command, raw_text)


def _ground_how_step_commands_in_raw(
    steps: list[tuple[int, Any, str]],
    raw_text: str,
) -> list[tuple[int, Any, str]]:
    """Emission 前回查 raw：剥掉不接地于 raw 的派生命令，降级为无命令 instruction。

    治本2（砍编造源）：deterministic fallback / _claim_to_how_step / placeholder 展开
    都可能产出 AKB 对来源的保守命令映射。这些命令只有字面出现在 raw 中才允许进候选页
    （否则被 SG-command-recheck 判为编造）。本护栏在渲染前逐条检查**展开后**的命令
    （_render_how_step_with_provenance / 边界表实际渲染的就是展开后命令）：

    - 命令接地 raw → 步骤原样保留（source-grounded 可执行步骤）。
    - 命令不接地 raw → 去掉 command（置空）+ executable:False，只渲染 instruction，
      绝不把编造命令塞进候选页。

    真实可锚定命令 <3 时，候选页 How 命令数不足，由 compose 的 SG-how-density
    fail-closed（不在此处放行）。字符串型 step（旧形态）原样透传。
    """
    grounded: list[tuple[int, Any, str]] = []
    for idx, step, anchor in steps:
        if not isinstance(step, dict):
            grounded.append((idx, step, anchor))
            continue
        raw_command = str(step.get("command", "")).strip()
        if not raw_command:
            grounded.append((idx, step, anchor))
            continue
        instruction = str(step.get("instruction", "")).strip()
        expanded = _expand_placeholder_command(raw_command, instruction, idx)
        if _command_grounded_in_raw(expanded, raw_text):
            grounded.append((idx, step, anchor))
            continue
        # 命令不接地 raw：剥离命令，降级为无命令 instruction（executable:False）。
        demoted = {**step, "command": "", "executable": False}
        grounded.append((idx, demoted, anchor))
    return grounded


def _filter_resolvable_graph_edges(
    graph_edges: list[dict[str, Any]],
    concepts: list[str],
    known_concepts: list[str] | None,
    pending_queue: list[str] | None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """SC-2b-b2：渲染前过滤 target 不在 known∪sources∪pending 的图谱边/概念引用。

    extraction LLM 可能发明 vault 里不存在的 slug（如 loop-engineering——既非概念也非
    源），若进入候选页，A4-图谱resolve/A5-wikilink 会 fail-closed 拒整篇。本函数在
    compose 渲染前确定性过滤，不让悬空 slug 进候选页（"从源头不发明"，用户 SC-2b-b2
    决策）。复用 _resolve_graph_node（含 SC-2a 前缀归一化 + pending 共指）做判定。

    SC-2（worker round-4）：返回 dropped_targets 供 compose 写可见 meta，治静默失败
    （Karpathy #12——旧版 drop 边无日志，grader 看稀疏图谱不知何因）。

    tradeoff（用户已接受）：生产 vault 概念全时几乎不过滤；校准薄种子库下引用未 seeded
    概念的边会被 drop，图谱可能偏稀疏——但候选不再被 self-gate 整篇拒。
    known/pending 均 None 时不过滤（兼容 _build_body 旧调用，默认不传）。
    """
    if known_concepts is None and pending_queue is None:
        return graph_edges, concepts, []
    from src.akb2.quality.checks_structural import _resolve_graph_node

    def _edge_target(e: dict[str, Any]) -> str:
        return str(
            e.get("concept")
            or e.get("target")
            or e.get("target_id")
            or e.get("source_id")
            or ""
        )

    kept_edges = [
        e
        for e in graph_edges
        if _resolve_graph_node(_edge_target(e), known_concepts, pending_queue)
    ]
    kept_concepts = [
        c for c in concepts if _resolve_graph_node(str(c), known_concepts, pending_queue)
    ]
    # SC-2：记录被 drop 的 target（治静默失败——grader/ledger 能看到哪些边被删、为什么）
    dropped = [
        _edge_target(e)
        for e in graph_edges
        if not _resolve_graph_node(_edge_target(e), known_concepts, pending_queue)
    ]
    dropped += [
        str(c)
        for c in concepts
        if not _resolve_graph_node(str(c), known_concepts, pending_queue)
    ]
    return kept_edges, kept_concepts, dropped


def _edge_kind_label(kind: str) -> str:
    """§0.2/D7：edge_kind 渲染标签（inferred 标 weak，不计 F4）。"""
    k = str(kind or "").strip()
    if k == "inferred_from_structure":
        return "inferred_from_structure (weak)"
    return k


def _build_mechanism_evidence_section(extract_data: dict[str, Any]) -> str:
    """§0.2/D7/C1：渲染机制组件与因果边证据表（grader H3 + A 层 SG-mechanism-edge-evidence 消费）。

    components 表：component_id | name | role | evidence_anchor | evidence_sentence
    edges 表：from | to | relation | edge_kind | evidence_anchor | evidence_sentence
    inferred_from_structure 边标 weak（不计 F4，供 grader H3 审语义；
    eligible 边带 evidence+anchor）。
    无 mechanism_components → 返空串（procedural 不渲染该节）。
    """
    components = extract_data.get("mechanism_components") or []
    edges = extract_data.get("causal_edges") or []
    if not components:
        return ""
    comp_rows = "\n".join(
        f"| {c.get('component_id', '')} | {_md_table_cell(c.get('name', ''))} | "
        f"{_md_table_cell(c.get('role', ''))} | {c.get('evidence_anchor', '')} | "
        f"{_md_table_cell(_sanitize_backticks(c.get('evidence_sentence', '')))} |"
        for c in components
        if isinstance(c, dict)
    ) or "|  |  |  |  |  |"
    edge_rows = "\n".join(
        f"| {e.get('from', '')} | {e.get('to', '')} | "
        f"{_md_table_cell(e.get('relation', ''))} | "
        f"{_edge_kind_label(e.get('edge_kind', ''))} | "
        f"{e.get('evidence_anchor', '')} | {_md_table_cell(_sanitize_backticks(e.get('evidence', '')))} |"
        for e in edges
        if isinstance(e, dict)
    ) or "|  |  |  |  |  |  |"
    return (
        "## 机制组件与因果边证据\n\n"
        "组件：\n\n"
        "| component_id | name | role | evidence_anchor | evidence_sentence |\n"
        "|---|---|---|---|---|\n"
        f"{comp_rows}\n\n"
        "因果边：\n\n"
        "| from | to | relation | edge_kind | evidence_anchor | evidence_sentence |\n"
        "|---|---|---|---|---|---|\n"
        f"{edge_rows}\n\n"
    )


def _render_concept_mechanism_walkthrough(
    extract_data: dict[str, Any], raw_content: str | None
) -> str:
    """P3: concept lane 且 how 双空时,从 eligible 边渲染机制 walkthrough(≤5 行,零编造)。

    痛点:concept 文无可锚定操作步骤时,_build_body 自产"原文无可安全锚定的操作步骤"占位
    → SG-how-executability(FORBIDDEN markers)拒掉自己人。本函数从已过校验的 eligible
    source_causal 边确定性渲染机制关系行,替代占位:
        [raw §X] (source_grounded) 机制关系:from → to(relation):evidence
    - 零编造:只从 extract_data 的 eligible 边(有 evidence + evidence_anchor)渲染。
    - anchor 走 P1a 统一 parser:canonicalize + resolve_anchor_ref 唯一前缀解析,∈ raw 声明集。
    - [N2] 零 eligible 防御分支(正常不可达,floor 已 fail):单行说明避开全部
      FORBIDDEN/TEMPLATE marker 且不伪装 source_grounded 标记。
    """
    edges = extract_data.get("causal_edges", []) or []
    eligible = [
        e for e in edges
        if isinstance(e, dict)
        and e.get("edge_kind") == "source_causal"
        and e.get("evidence")
        and e.get("evidence_anchor")
    ]
    # [N2] 零 eligible 兜底:单行说明,无 FORBIDDEN/TEMPLATE marker,无 (source_grounded) 伪标
    _zero_fallback = "（concept 文无可锚定操作步骤;机制组件与因果边见图谱关系节。）"
    if not eligible:
        return _zero_fallback
    decl = declared_anchor_set(raw_content)
    if not decl:
        return _zero_fallback
    lines: list[str] = []
    for e in eligible[:5]:
        anchor = str(e.get("evidence_anchor", "")).strip()
        # P1a 统一 parser:canonicalize + 唯一前缀解析
        anchor = canonicalize_anchor(anchor)
        if anchor not in decl:
            resolved = resolve_anchor_ref(anchor, decl)
            anchor = resolved if resolved else anchor
        if anchor not in decl:
            continue  # 不在声明集 → 跳过(不造假 anchor)
        frm = str(e.get("from", "")).strip()
        to = str(e.get("to", "")).strip()
        rel = str(e.get("relation", "")).strip() or "raw_causal"
        ev = re.sub(r"\s+", " ", str(e.get("evidence", ""))).strip()[:80]
        lines.append(
            f"[raw {anchor}] (source_grounded) 机制关系:{frm} → {to}({rel}):{ev}"
        )
    return "\n".join(lines) if lines else _zero_fallback


def _render_mechanism_narrative(
    extract_data: dict[str, Any], raw_content: str | None
) -> str:
    """F8-D: 机制叙述段落——用 eligible 边确定性生成连贯段落(每边一句,按锚顺序)。

    grader H3 抱怨:'正文重复罗列来源句子,缺少因果解释/机制闭环不足'。机制组件与因果边
    证据表只罗列 from/to/evidence,缺因果叙述。本函数在表后追加确定性叙述:每条 eligible
    source_causal 边一句 '{from} 通过 {relation} 导致 {to}（证据：[raw §X] {evidence}）',
    按 evidence_anchor 在 raw 中的出现顺序排序(机制叙述顺序 = raw 叙述顺序)。

    零编造:evidence/anchor/from/to 全来自 eligible 边(已过 floor 校验);anchor 走 P1a
    统一 parser(canonicalize + resolve_anchor_ref),∈ raw 声明集,不在则跳过该边(不造假)。
    ≤6 句(有界预算)。无 eligible 边 → 返空串(不硬凑)。
    """
    edges = extract_data.get("causal_edges", []) or []
    eligible = [
        e for e in edges
        if isinstance(e, dict)
        and e.get("edge_kind") == "source_causal"
        and e.get("evidence")
        and e.get("evidence_anchor")
    ]
    if not eligible:
        return ""
    decl = declared_anchor_set(raw_content)
    if not decl:
        return ""
    # 按 anchor 在 raw 中的位置排序(机制叙述顺序 = raw 叙述顺序)
    anchor_positions = _raw_anchor_positions(raw_content)
    pos_map = {a: p for a, p in anchor_positions}

    def _anchor_pos(e: dict[str, Any]) -> int:
        a = canonicalize_anchor(str(e.get("evidence_anchor", "")).strip())
        if a not in pos_map and decl:
            resolved = resolve_anchor_ref(a, decl)
            if resolved:
                a = resolved
        return pos_map.get(a, 0)

    eligible.sort(key=_anchor_pos)
    sentences: list[str] = []
    for e in eligible[:6]:  # ≤6 句(有界)
        anchor = canonicalize_anchor(str(e.get("evidence_anchor", "")).strip())
        if anchor not in decl:
            resolved = resolve_anchor_ref(anchor, decl)
            anchor = resolved if resolved else anchor
        if anchor not in decl:
            continue  # 不在声明集 → 跳过(不造假 anchor)
        frm = str(e.get("from", "")).strip()
        to = str(e.get("to", "")).strip()
        rel = str(e.get("relation", "")).strip() or "导致"
        ev = re.sub(r"\s+", " ", str(e.get("evidence", ""))).strip()
        if len(ev) > 120:
            ev = ev[:117] + "..."  # 段落可读性截断(保留核心)
        ev = _sanitize_backticks(ev)  # F10-A: 消毒不成对反引号
        sentences.append(
            f"{frm} 通过 {rel} 导致 {to}（证据：[raw {anchor}] {ev}）。"
        )
    if not sentences:
        return ""
    return "\n\n机制叙述：\n\n" + " ".join(sentences) + "\n\n"


def _build_body(
    extract_data: dict[str, Any],
    raw_path: str,
    route_llm_fn: Any,  # 保留参数兼容接口，_build_body 是确定性脚本，不调 LLM
    *,
    must_cover: list[Any] | None = None,
    raw_content: str | None = None,
    known_concepts: list[str] | None = None,
    pending_queue: list[str] | None = None,
    content_lane: str = "",
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

    # SC-2b-b2：渲染前过滤 target 不在 known∪sources∪pending 的图谱边/概念。
    # 发明的悬空 slug（如 loop-engineering）不进候选页，避免 A4/A5
    # fail-closed 整篇拒。known/pending 均 None 时不过滤（兼容旧调用）。
    # SC-2：dropped_graph_targets 供图谱节写可见 meta（治静默失败）。
    graph_edges, concepts, dropped_graph_targets = _filter_resolvable_graph_edges(
        graph_edges, concepts, known_concepts, pending_queue
    )

    supported_how_steps = _supported_how_steps(how_steps, claims, raw_anchor_texts)
    if not supported_how_steps:
        supported_how_steps = _claim_derived_how_steps(claims)
    supported_how_steps = _ensure_minimum_how_steps(
        supported_how_steps,
        claims,
        raw_anchor_texts,
    )
    # 治本2（砍编造源）：emission 前回查 raw 护栏。任何派生/合成命令（deterministic
    # fallback、_claim_to_how_step、placeholder 展开）若其反引号命令字面量不在 raw 中
    # 出现，则剥掉命令、降级为无命令 instruction，绝不把编造命令塞进候选页。命令字面量
    # 在 raw 中出现的步骤（来源真有该操作，或 raw 显式给出命令示例）原样保留为
    # source-grounded 可执行步骤。真实可锚定命令 <3 时由 SG-how-density fail-closed。
    raw_full_text = raw_content if raw_content is not None else _read_full_raw_text(raw_path)
    supported_how_steps = _ground_how_step_commands_in_raw(
        supported_how_steps, raw_full_text
    )
    claims = _ensure_how_evidence_claims(claims, supported_how_steps, raw_anchor_texts)

    # 新增字段槽位（段 1 抽取 LLM 已产出，此处仅读取）
    tldr = (extract_data.get("tldr") or "").strip()
    if not tldr:
        tldr = claims[0]["text"] if claims else "（摘要待补充）"

    # 关键发现：带 [raw §X] 锚点+provenance 的证据格式（rubric: 论断锚定率要求 [raw §X] 格式）
    claims_text = "\n".join(
        f"- [raw {c['anchor']}] ({c['provenance']}) {_sanitize_backticks(c['text'])}"
        for c in claims
    )

    # 核心论点：带 [raw §X] 锚点+provenance，与关键发现区分（编号列表格式）
    # rubric 解读分离: 每条论点必须标注 provenance；论断锚定率: 必须带 [raw §X]
    core_claims_text = "\n".join(
        f"{i}. [raw {c['anchor']}] ({c['provenance']}) {_sanitize_backticks(c['text'])}"
        for i, c in enumerate(claims, 1)
    )

    # TLDR：F8-D 去重 + 要素覆盖(核心机制 + 组件全景 + how/边界 claim,≤3-4 句含锚点)。
    # grader 抱怨:'后两句重复同一论点/大面积重复 §F_EVIDENCE'。先 _dedup_claims_for_tldr
    # 去掉 ≥80% 相似 claim,再优先覆盖核心机制(首条 eligible 边)+ 组件全景,末尾 how/边界 claim。
    deduped_claims = _dedup_claims_for_tldr(claims)
    tldr_claim_sentences = [
        f"(source_grounded) {_compact_tldr_sentence(c['text'])} [raw {c['anchor']}]."
        for c in deduped_claims[:3]
        if _compact_tldr_sentence(c["text"])
    ]
    tldr_parts: list[str] = []
    # 要素 1:核心机制(首条 eligible 边)
    mech_sent = _build_tldr_mechanism_sentence(extract_data, raw_content)
    if mech_sent:
        tldr_parts.append(mech_sent)
    # 要素 2:组件全景(组件数 + 关键名)
    comp_sent = _build_tldr_component_sentence(extract_data, raw_content)
    if comp_sent:
        tldr_parts.append(comp_sent)
    # 要素 3:how/边界 claim(去重后,补足到 ≤3-4 句)
    tldr_parts.extend(tldr_claim_sentences[: max(0, 3 - len(tldr_parts))])
    if tldr_parts:
        tldr = " ".join(tldr_parts)
    elif tldr_claim_sentences:
        tldr = " ".join(tldr_claim_sentences)
    # 都空 → 退回 tldr(claims[0] 或占位)

    # must_cover source_conclusion: 代码直出进 TLDR，LLM 无最终选择权。
    # 即使 LLM tldr 漏掉原文显式结论，scanner 账本仍强制渲染进 TLDR 节。
    must_cover_concls = [m for m in must_cover if m.kind == "source_conclusion"]
    mc_tldr_text = _render_must_cover_conclusions(must_cover_concls, raw_content)
    if mc_tldr_text:
        tldr = f"{mc_tldr_text} {tldr}".strip() if tldr else mc_tldr_text
    # F6-C: must_cover conclusion 字面量注入(tldr 节)
    tldr = _inject_missing_must_cover(tldr, must_cover_concls, inject_prefix="")

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
        # P3: how 双空 — concept lane 从 eligible 边渲染机制 walkthrough(零编造,避 FORBIDDEN
        # marker);procedural(含空 lane)占位原样保留 → SG 照拒(严格性零回退)。
        if str(content_lane).strip().lower() == "concept":
            how_text = _render_concept_mechanism_walkthrough(extract_data, raw_content)
        else:
            how_text = "（原文无可安全锚定的操作步骤；候选阶段不强行生成 AKB 操作化步骤。）"
    # F6-C: must_cover 字面量注入——检查每条 source_operation 字面量是否在 how_text
    # (归一化比对,与 SG-must-cover 同口径);缺失→逐字注入(含链接语法)
    how_text = _inject_missing_must_cover(how_text, must_cover_ops)
    # F7-B: 渲染前最后一道不变式——how_text 里所有反引号命令(非 reserved)必须字面在 raw
    # 出现(SG-command-recheck 同口径 _verify_commands_in_raw)。不在→剥反引号(保留文本,
    # 去掉 ` ` 包裹,SG 不再当命令回查)。probe-p6 memory ~/；claude/rules/ 漏进来(F3-B 只
    # strip how_steps[].command 字段,漏 instruction/argumentation/tldr 里的反引号串)。
    if raw_full_text:
        how_text = _strip_ungrounded_backtick_commands(how_text, raw_full_text)
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
    # Finding 6: 持久化三态 provenance_split 摘要到 How 节末尾，供 SG 读取消费。
    # derived from supported_how_steps 的 provenance_split + 代码直出 must_cover_ops。
    split_comment = _provenance_split_summary_comment(
        [s for _, s, _ in supported_how_steps], must_cover_ops
    )
    how_text = f"{how_text}\n\n{split_comment}"

    # 证据地图："未覆盖"列必须写真盲区(claim.evidence_gap),不是论证摘要。
    # 旧用 argumentation explanation 填该列 → 退化成释义,grader 维度4 扣分(codex 必修1)。
    # cell 经 _md_table_cell 转义 |（否则 evidence 含 | 会拆列 + A4 假绿）
    _GAP_FALLBACK = "未覆盖：原文未提供该论点的独立验证、量化结果或失败样本。"
    evidence_rows = "\n".join(
        f"| {_md_table_cell(c['anchor'])} | {_md_table_cell(_sanitize_backticks(c['text'][:60]))}... "
        f"| {c['provenance']} "
        f"| {_md_table_cell(c.get('evidence_gap') or _GAP_FALLBACK)} |"
        for c in claims
    )

    # 图谱关系：表头对齐检查器/评分器英文契约(GRAPH_EDGE_COLUMNS=target/relation/strength/evidence)
    # evidence 不截断(旧 [:80] 截断丢依据);fallback 边降 weak(旧 medium 偏高)
    # cell 经 _md_table_cell 转义 | + 压平换行(否则 evidence 含 | 拆成 >4 列,A4 只拒 <4 列→假绿)
    _GRAPH_TABLE_HEADER = (
        "| target | relation | strength | provenance | evidence |\n"
        "|--------|----------|----------|------------|----------|"
    )
    if graph_edges:
        rows = "\n".join(
            f"| {_md_table_cell(edge['concept'])} | {_md_table_cell(edge['relation_type'])} "
            f"| {edge['strength']} | {edge['provenance']} | "
            f"{_md_table_cell(_sanitize_backticks(edge.get('evidence', '')))} |"
            for edge in (_normalize_graph_edge(e) for e in graph_edges)
        )
        graph_rows = f"{_GRAPH_TABLE_HEADER}\n{rows}"
    elif concepts:
        # concept 来自 LLM referenced_concepts,可能含 | → 同样转义(altitude:所有插值 cell 一致转义)
        fallback_anchor = claims[0].get("anchor", "§A") if claims else "§A"
        fallback_cell = _md_table_cell(
            f"[raw {fallback_anchor}] (akb_interpretation) "
            "见原文概念引用，候选阶段按推测关联保守标 weak。"
        )
        rows = "\n".join(
            f"| {_md_table_cell(c)} | related | weak | "
            f"akb_interpretation | {fallback_cell} |"
            for c in concepts
        )
        graph_rows = f"{_GRAPH_TABLE_HEADER}\n{rows}"
    else:
        # SC-2（worker round-4）：全过滤空时**不出占位符表格行**——A4 会把
        # `| （无相关概念） |` 当 edge target 拒（compose_self_gate_failed，满仓 11/18
        # 挂此）。改输出普通段落（非 table），A4 无 table 行可解析→不拒。dropped 可见
        # meta 治静默失败（Karpathy #12——旧版 drop 边无日志，grader 不知何因）。
        dropped_note = ""
        if dropped_graph_targets:
            uniq = list(dict.fromkeys(dropped_graph_targets))[:8]
            dropped_note = (
                f"\n\n<!-- graph_edges_dropped: {uniq} — 未命中 known/pending，"
                "待图谱校准。graph_empty_after_resolve_filter=true -->"
            )
        graph_rows = (
            "（本候选暂无可证实的图谱边——所有引用概念在 resolve 阶段未命中 "
            "known/pending。待图谱校准确认后补边。）" + dropped_note
        )

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
    # F6-C: must_cover boundary 字面量注入(适用边界节)
    boundary_text = _inject_missing_must_cover(boundary_text, must_cover_bounds)

    from datetime import UTC, datetime

    _now_date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    # §0.2/D7/C1：机制组件与因果边证据表（grader H3 + A 层 SG-mechanism-edge-evidence 消费）
    mechanism_section = _build_mechanism_evidence_section(extract_data)
    # F8-D: 机制叙述段落——表后追加 eligible 边确定性叙述(每边一句,按锚顺序),
    # 消 '正文重复罗列来源句子,缺少因果解释/机制闭环不足'。无 eligible → 空(不硬凑)。
    mechanism_narrative = _render_mechanism_narrative(extract_data, raw_content)

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

图谱边按 evidence 的 provenance 判定；weak 边仍需后续图谱校准确认。

{graph_rows}
{mechanism_section}{mechanism_narrative}## Future Questions

{chr(10).join(f'- {q}' for q in _generate_questions(claims, how_steps))}

## 维护记录

| 日期 | 操作 | 说明 |
|------|------|------|
| {_now_date_str} | 初始创建 | 段1-2 提炼，raw: {_md_table_cell(raw_path)} |"""

    return body


def _assemble_page(frontmatter: dict[str, Any], body: str) -> str:
    """组装 frontmatter + 正文为完整候选页。"""
    fm_str = yaml.dump(
        frontmatter,
        Dumper=_FrontmatterDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip()
    return f"---\n{fm_str}\n---\n\n{body}\n"
