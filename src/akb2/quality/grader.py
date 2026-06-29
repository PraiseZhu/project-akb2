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

import yaml

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
# 机器可读量规真相源（spec FR-010：标准与代码分离，grader 从此读维度/权重/阈值/版本）
RUBRIC_SPEC_FILE = "rubric.yaml"

# 评分模型阵容
PRIMARY_GRADER_MODEL = "gpt-5.4"
FALLBACK_GRADER_MODEL = "gemini-flash"

# 预算
MAX_GRADER_RETRIES = 2  # 评分重试次数（解析失败时）
GRADER_TIMEOUT_S = 120


def _load_rubric_spec() -> dict[str, Any]:
    """加载机器可读量规 spec（rubric.yaml）→ dict（dimensions+weights+threshold+floor+version）。

    spec FR-010：维度集/权重/阈值/完整性地板/版本从此文件读，不在 .py 写死。
    文件缺失或必需字段缺失 → RuntimeError（fail-closed，不给空标准跑空判定）。
    """
    spec_path = RUBRIC_DIR / RUBRIC_SPEC_FILE
    if not spec_path.exists():
        raise RuntimeError(
            f"量规 spec 文件不存在: {spec_path}。"
            " 请确认 skillpack/quality/rubric/rubric.yaml 已部署。"
        )
    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuntimeError(f"量规 spec 解析失败: {spec_path}: {exc}") from exc
    if not isinstance(spec, dict):
        raise RuntimeError(f"量规 spec 顶层非 mapping: {spec_path}")
    for required_key in ("rubric_version", "dimensions", "threshold", "integrity_floor", "lanes"):
        if required_key not in spec:
            raise RuntimeError(f"量规 spec 缺必需字段 {required_key!r}: {spec_path}")
    if not isinstance(spec["dimensions"], list) or not spec["dimensions"]:
        raise RuntimeError(f"量规 spec dimensions 为空或非列表: {spec_path}")
    return spec


def _rubric_dimension_names(spec: dict[str, Any] | None = None) -> list[str]:
    """从 spec 全局 dimensions 派生维度名列表（grader 输出 enum 与解析对账的真相源）。"""
    spec = spec or _load_rubric_spec()
    names: list[str] = []
    for dim in spec["dimensions"]:
        name = dim.get("name") if isinstance(dim, dict) else None
        if not name:
            raise RuntimeError(f"量规 spec dimension 缺 name 字段: {dim!r}")
        names.append(str(name))
    return names


# 版本与维度集从 spec 动态派生（不在 .py 硬编码——spec FR-010 / A3-6）
REQUIRED_RUBRIC_VERSION: str = _load_rubric_spec()["rubric_version"]
RUBRIC_DIMENSIONS: list[str] = _rubric_dimension_names()


def _rubric_dimension_names_by_lane(
    spec: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """从 spec 派生 {lane: [维名]}（step-3 SC-3f/A3-6：grader 按 lane 选维度集）。

    - procedural = 全局 dimensions（旧行为 10 维，零回归）。
    - concept = lanes.concept.dimensions 7 维（逐字含 (H3)/(H2) 后缀）。
    不在 .py 手写维名——全部从 spec 动态派生（改 rubric.yaml 即改维度集）。
    """
    spec = spec or _load_rubric_spec()
    by_lane: dict[str, list[str]] = {}
    by_lane["procedural"] = _rubric_dimension_names(spec)
    lanes = spec.get("lanes", {}) or {}
    concept_dims: list[str] = []
    for dim in (lanes.get("concept") or {}).get("dimensions", []) or []:
        if isinstance(dim, dict) and dim.get("name"):
            concept_dims.append(str(dim["name"]))
    by_lane["concept"] = concept_dims
    return by_lane


# 按 lane 选维度集（SC-3f/A3-6：动态派生不硬编码）
RUBRIC_DIMENSIONS_BY_LANE: dict[str, list[str]] = _rubric_dimension_names_by_lane()


def _dimensions_for_lane(content_lane: str) -> list[str]:
    """按车道选维度集（未知/缺省 → procedural 旧行为，零回归）。"""
    return RUBRIC_DIMENSIONS_BY_LANE.get(
        str(content_lane or "").strip().lower(), RUBRIC_DIMENSIONS
    )


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
    # 内容车道（step-1 透传字段，仅记录不参与判定；lane-aware 选量规属 step-3）
    content_lane: str = ""
    # Step.4 扩展字段（spec FR-008：随稿记录，weighted_total 不参与本 step 判定）
    weighted_total: float | None = None
    threshold: float | None = None
    integrity_floor_results: list[dict[str, Any]] = field(default_factory=list)
    dimension_scores: dict[str, float] = field(default_factory=dict)
    # step-3 SC-3c/Decision-1 层②：异族 grader(GPT-5.4) 判每条 akb_interpretation 是否
    # 真从 cited anchor 推出（entailment 成立）。拒 poisoned-trace（anchor 真实但与
    # interpretation 语义无关）。二值地板字段（不进加权维 enum、不加权），fail 即 reject
    # （promote 不放行）。生产模型 deepseek 不自证（P7 异族）；None=未评（旧响应/非 concept）。
    interpretation_entailment: bool | None = None


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


def _build_grader_weight_floor_note(content_lane: str = "") -> str:
    """从 spec 构建 weight / threshold / integrity_floor 说明（Step.3 注入 prompt）。

    R2/wA#2 修复：权重表按**车道**维度集构建。旧逻辑无条件用全局 ``dimensions``
    （= procedural 10 维），喂给 concept 文后模型按 procedural 维度名输出 → 不在
    concept 7 维 enum → schema parse 失败 → 退 gemini → runner 守卫拒收
    （满仓 #1/3/4/5/9/11/14 全死于此，误报成 grader 环境不可用）。
    """
    spec = _load_rubric_spec()
    lane = str(content_lane or "").strip().lower()
    lane_cfg = (spec.get("lanes") or {}).get(lane) or {}
    lane_dims = lane_cfg.get("dimensions") if isinstance(lane_cfg, dict) else None
    dims_source = (
        lane_dims
        if isinstance(lane_dims, list) and lane_dims
        else spec.get("dimensions", [])
    )
    weight_lines = [
        f"  - {d.get('name')}: weight={d.get('weight')}"
        + (f"（{d.get('note')}）" if d.get("note") else "")
        for d in dims_source
        if isinstance(d, dict)
    ]
    floor_lines = [
        f"  - {f.get('name')}（hard，不可被加权分补偿）"
        for f in spec.get("integrity_floor", [])
        if isinstance(f, dict)
    ]
    is_concept = str(content_lane or "").strip().lower() == "concept"
    # G3 返工：concept 车道 prompt 诚实显示 lane threshold（6.0）；weighted 由逐维 pass
    # 加权派生（pass=1.0/fail=0.0 × 权重，gpt-5.4 真机吐 pass 不吐逐维 0~1 score）。
    lane_threshold = lane_cfg.get("threshold") if isinstance(lane_cfg, dict) else None
    display_threshold = (
        lane_threshold
        if isinstance(lane_threshold, (int, float))
        else spec.get("threshold")
    )
    score_note = (
        "每个维度给出 pass/fail 判定，加权分由 pass 派生（pass=1.0、fail=0.0 × 权重，"
        "过阈即通过）；可附 integrity_floor 逐项 pass/fail。无需逐维 0~1 score。"
        if is_concept
        else "可选：每个维度可附 score 数值；可附 integrity_floor 逐项 pass/fail。"
    )
    return (
        "维度权重（rubric_version "
        f"{spec.get('rubric_version')}，通过阈值 threshold={display_threshold}）:\n"
        + "\n".join(weight_lines)
        + "\n完整性地板（任一不达即拒，不可被高分补偿）:\n"
        + "\n".join(floor_lines)
        + "\n" + score_note
    )


def _build_grader_prompt(
    candidate_text: str, rubric_text: str, content_lane: str = ""
) -> list[dict[str, str]]:
    """构建评分 prompt（system + user）。lane-aware：concept 评 7 维、procedural 评 10 维。"""
    weight_floor_note = _build_grader_weight_floor_note(content_lane)
    # P3：维度按 lane 限定，修旧硬编码"10 维"对 concept（7 维）的语义漂移（w9）。
    dims = _dimensions_for_lane(content_lane)
    lane_label = content_lane or "默认"
    spec = _load_rubric_spec()
    lane_key = str(content_lane or "").strip().lower()
    _lane_cfg = (spec.get("lanes") or {}).get(lane_key) or {}
    lane_dim_specs = _lane_cfg.get("dimensions") if isinstance(_lane_cfg, dict) else None
    # R2/wA#2：显式逐字列出本车道必须输出的维度，杜绝模型输出别车道维度名
    # 导致 schema enum 不匹配 → parse 失败。
    dim_enum_block = (
        f"本车道必须且只能输出以下 {len(dims)} 个维度，每个维度的 dimension 字段"
        "必须与下列名称逐字一致，不得输出任何其他维度名：\n"
        + "\n".join(f"  {i + 1}. {d}" for i, d in enumerate(dims))
    )
    # 量规判据：concept 车道的维度不在 10-dimensions.md（该 md 只描述 procedural
    # 10 维），其判据来自 rubric.yaml lanes.concept 的 note；procedural 用 md 全文。
    if (
        isinstance(lane_dim_specs, list)
        and lane_dim_specs
        and lane_key not in ("", "procedural")
    ):
        rubric_body = "本车道维度判据（来自 rubric.yaml）:\n" + "\n".join(
            f"- {d.get('name')}（weight={d.get('weight')}）: {d.get('note', '')}"
            for d in lane_dim_specs
            if isinstance(d, dict)
        )
    else:
        rubric_body = f"量规:\n{rubric_text}"
    system_prompt = f"""你是一个知识库内容质量评审专家。请根据以下量规评分。
本车道（{lane_label}）评判维度限定为 {len(dims)} 维。
{dim_enum_block}

{rubric_body}

{weight_floor_note}

评审规则:
1. 每个维度给出 pass 或 fail 判定（仅评判上述限定维度）
2. 如果 fail，必须给出具体的 defect 描述和修复建议
3. 每条缺陷必须包含 location 字段（指明稿件中的具体位置，如节标题/段落/字段名）
4. 输出必须是严格的 JSON 格式
5. 解读推导成立性（interpretation_entailment，布尔）：判断每条 akb_interpretation
   是否真从其所引 [raw §X] anchor 推出。anchor 真实存在但与解读语义无关
   （poisoned-trace，挂羊头卖狗肉）→ false；推导真成立 → true。concept 车道必填此字段，
   procedural 可省。此字段是异族硬判地板，false 即整体 reject（不可被加权分补偿）。

输出 JSON 结构（必须严格使用以下字段名，不得用 verdict / overall_decision / overall_pass
等其他字段名；上列每个维度都必须各输出恰好一条）:
{{
  "dimensions": [
    {{"dimension": "<上列维度名之一，逐字一致>", "pass": true, "defects": []}},
    {{"dimension": "<另一维度名>", "pass": false,
      "defects": [{{"location": "<具体位置>", "defect": "<缺陷>", "suggestion": "<修复建议>"}}]}}
  ],
  "interpretation_entailment": true
}}
字段约束: "pass" 必须是布尔 true/false（不是 "pass"/"fail" 字符串）；"defects" 必须是数组，
pass=true 时为空数组 []，pass=false 时至少一条，每条含 location/defect/suggestion 三个字符串。"""
    user_prompt = f"""请评审以下候选页:

{candidate_text}

请只输出上述结构的 JSON，不要包裹 markdown 代码块，不要附加额外字段。"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _build_grader_schema(content_lane: str = "") -> dict[str, Any]:
    """构建评分输出的 JSON Schema。

    Step.3 扩展（spec FR-008）：维度 enum 从 spec 派生（RUBRIC_DIMENSIONS）；
    输出对象扩展可选字段——逐维 score、顶层 weighted_total/threshold/integrity_floor
    逐项。新字段均**可选**（非 required），旧响应（无这些字段）仍合法解析（零回归）；
    weighted_total 仅产出记录、不参与本 step 判定（判据切换属 step-3）。

    step-3 SC-3f/A3-6：维度 enum 按 lane 动态派生（concept=7 维，procedural=10 维）。
    """
    floor_names = [f.get("name") for f in _load_rubric_spec().get("integrity_floor", [])]
    # G3 返工（p0-1 撤销 score required）：gpt-5.4 真机不吐逐维 0~1 score，concept weighted
    # 改由 pass 派生（compute_weighted_score）。score 全车道 optional；concept 若提供则
    # parse 层校验 0~1（非法 fail-closed，缺 score 不 fail）。procedural 零回归。
    score_field: dict[str, Any] = {
        "type": "number",
        "description": "该维度评分（可选；concept 车道 weighted 由 pass 派生，score 非必需）",
    }
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
                            "enum": _dimensions_for_lane(content_lane),
                        },
                        "pass": {"type": "boolean"},
                        # Step.3 扩展：逐维 score（全车道可选；concept weighted 由 pass 派生）
                        "score": score_field,
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
            },
            # Step.3 扩展：加权总分（可选，仅记录）
            "weighted_total": {
                "type": "number",
                "description": "地板之上维度的加权质量分（可选，仅记录，不参与本 step 判定）",
            },
            # Step.3 扩展：通过阈值（可选，随稿记录）
            "threshold": {
                "type": "number",
                "description": "加权质量分通过阈值（可选，随稿记录）",
            },
            # Step.3 扩展：逐完整性地板 pass/fail（可选）
            "integrity_floor": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": floor_names},
                        "pass": {"type": "boolean"},
                    },
                    "required": ["name", "pass"],
                    "additionalProperties": False,
                },
            },
            # step-3 SC-3c/Decision-1 层②：异族 grader entailment 地板字段（可选，二值）。
            # GPT-5.4 判每条 akb_interpretation 是否真从 cited anchor 推出，拒 poisoned-trace。
            # None/缺省=未评（旧响应/非 concept 车道，零回归）。
            "interpretation_entailment": {
                "type": "boolean",
                "description": (
                    "解读推导成立性（异族硬判）：每条 akb_interpretation 是否真从"
                    " cited anchor 推出。anchor 真实但与解读语义无关→false（poisoned-trace）。"
                    " concept 车道必填；procedural 可省。"
                ),
            },
        },
        "required": ["dimensions"],
        "additionalProperties": False,
    }


def _canonical_dimension_name(
    raw_name: Any, content_lane: str = ""
) -> str | None:
    """将允许的维度名变体归一到 canonical 中文名（按车道选维度集）。

    模型偶尔会返回 "论断锚定率 (Claim Anchor Rate)"。这仍是同一维度；
    但未知维度、id 数字、错别字仍必须拒收。

    step-3 SC-3f/A3-6：维度集按 lane 动态派生（concept=7 维，procedural=10 维），
    不在 .py 硬编码——_dimensions_for_lane 从 rubric.yaml 读。
    """
    if not isinstance(raw_name, str):
        return None
    allowed = _dimensions_for_lane(content_lane)
    name = raw_name.strip()
    if name in allowed:
        return name
    for dim in allowed:
        if name.startswith(f"{dim} (") and name.endswith(")"):
            return dim

    # 容差匹配（R2/wA#2）：concept 维度名自带 (H3)/(H2) 后缀, 模型常丢后缀或用全角
    # 括号（）→ 逐字 enum 对账失败 → 整批 parse 失败 → 退 gemini → 守卫拒收。
    # 归一全/半角括号后, 按"首个括号前的基名"匹配（各车道基名唯一, 不放松到误配）。
    def _norm(s: str) -> str:
        return s.strip().replace("（", "(").replace("）", ")")

    def _base(s: str) -> str:
        return _norm(s).split("(")[0].strip()

    name_norm = _norm(name)
    norm_to_canon = {_norm(d): d for d in allowed}
    if name_norm in norm_to_canon:
        return norm_to_canon[name_norm]
    name_base = _base(name)
    if name_base:
        for dim in allowed:
            if _base(dim) == name_base:
                return dim
    return None


def _parse_grader_output(
    raw_content: str | dict,
    content_lane: str = "",
) -> tuple[list[Defect], bool]:
    """解析 LLM 评分输出为 Defect 清单。

    FR-006: 缺 location 的条目整批拒收。

    step-3 SC-3f/A3-6：维度集按 lane 动态派生（concept=7 维，procedural=10 维），
    seen_dimensions 须等于该 lane 维度集。

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
    # G3 返工（p0-1 撤销 score required）：score 全车道可选（concept weighted 由 pass 派生）。
    # concept score 若提供且非法（bool/非数值/越界 <0 或 >1）→ fail-closed；缺 score 不 fail。
    # procedural 不校验 score 范围（旧口径零回归）。
    is_concept = str(content_lane or "").strip().lower() == "concept"
    _dim_required = {"dimension", "pass", "defects"}
    _dim_allowed = _dim_required | {"score"}
    for dim in dimensions:
        if not isinstance(dim, dict):
            return [], False
        dim_keys = set(dim.keys())
        if not _dim_required <= dim_keys or (dim_keys - _dim_allowed):
            return [], False
        dim_name = _canonical_dimension_name(dim.get("dimension"), content_lane)
        if dim_name is None:
            return [], False
        seen_dimensions.add(dim_name)
        # concept score 若提供则必须合法 0~1（拒 bool/非数值/越界 fail-closed）；procedural 不校验
        if is_concept and "score" in dim:
            score_val = dim.get("score")
            if isinstance(score_val, bool) or not isinstance(score_val, (int, float)):
                return [], False
            if score_val < 0 or score_val > 1:
                return [], False
        passed = dim["pass"]
        if not isinstance(passed, bool):
            return [], False
        if passed:
            if dim["defects"] != []:
                return [], False
            continue

        dim_defects = dim["defects"]
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

    if seen_dimensions != set(_dimensions_for_lane(content_lane)):
        return [], False

    return defects, True


def _coerce_grader_data(raw_content: str | dict) -> dict[str, Any] | None:
    """把 grader 响应（str/dict）归一为 dict，失败返回 None。"""
    if isinstance(raw_content, dict):
        return raw_content
    if isinstance(raw_content, str):
        try:
            m = re.search(r"\{[\s\S]*\}", raw_content)
            return json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            return None
    return None


def _dimension_weight_map(spec: dict[str, Any]) -> dict[str, float]:
    """从 spec 全局 dimensions 构建 {维名: weight} 映射（procedural 旧行为）。"""
    weights: dict[str, float] = {}
    for dim in spec.get("dimensions", []) or []:
        if isinstance(dim, dict) and "name" in dim:
            w = dim.get("weight", 0)
            weights[str(dim["name"])] = float(w) if isinstance(w, (int, float)) else 0.0
    return weights


def _lane_weight_map(
    spec: dict[str, Any], content_lane: str
) -> dict[str, float]:
    """从 spec.lanes.<lane>.dimensions 构建 {维名: weight}（step-3 SC-3d lane-aware）。

    concept → lanes.concept 7 维权重；procedural/未知 → lanes.procedural 或全局（零回归）。
    weighted_score.py 的 _lane_weight_map 同语义；此处 grader 内副本避免循环 import。
    """
    lane = str(content_lane or "").strip().lower()
    lanes = spec.get("lanes", {}) or {}
    lane_spec = lanes.get(lane)
    if lane_spec is None:
        lane_spec = lanes.get("procedural") or {}
    weights: dict[str, float] = {}
    for dim in lane_spec.get("dimensions", []) or []:
        if isinstance(dim, dict) and "name" in dim:
            w = dim.get("weight", 0)
            weights[str(dim["name"])] = float(w) if isinstance(w, (int, float)) else 0.0
    return weights


def _parse_grader_extensions(
    raw_content: str | dict,
    content_lane: str = "",
) -> dict[str, Any]:
    """提取 Step.4 扩展字段（weighted_total/threshold/integrity_floor/逐维 score）。

    spec FR-010 标准外置：weighted_total 由 **rubric.yaml 的 weight × 模型逐维 score** 计算、
    threshold 从 **rubric.yaml** 读——改 YAML weight/threshold 即改这两个输出（不改 .py，
    SC-3）。weighted_total 仅随稿记录、不参与本 step 判定（判据切换属 step-3）。
    扩展字段全可选——模型未给逐维 score 时 weighted_total=None（不影响 pass/fail 与缺陷集，
    零回归）；模型仍可在响应里给 weighted_total，但以 spec 计算值为准（确定性、可对账）。
    """
    spec = _load_rubric_spec()
    # codex worker：按 lane 读阈值（lanes.<lane>.threshold → 顶层 threshold）
    lane_spec = (spec.get("lanes", {}) or {}).get(
        str(content_lane or "").strip().lower()
    ) or {}
    threshold_spec = lane_spec.get("threshold")
    if not isinstance(threshold_spec, (int, float)):
        threshold_spec = spec.get("threshold")
    threshold = (
        float(threshold_spec) if isinstance(threshold_spec, (int, float)) else None
    )

    data = _coerce_grader_data(raw_content)
    if data is None:
        return {
            "weighted_total": None,
            "threshold": threshold,
            "integrity_floor_results": [],
            "dimension_scores": {},
            "interpretation_entailment": None,
        }

    floor_raw = data.get("integrity_floor") or []
    floor_results = [
        {"name": str(f.get("name", "")), "pass": bool(f.get("pass"))}
        for f in floor_raw
        if isinstance(f, dict) and "name" in f and "pass" in f
    ]
    is_concept = str(content_lane or "").strip().lower() == "concept"
    dim_scores: dict[str, float] = {}
    for dim in data.get("dimensions", []) or []:
        if not isinstance(dim, dict):
            continue
        name = _canonical_dimension_name(dim.get("dimension"), content_lane)
        if name is None:
            continue
        if is_concept:
            # G3 返工：concept weighted 从逐维 pass 派生（pass=1.0/fail=0.0），不读 score
            # 字段（gpt-5.4 真机吐 pass 不吐逐维 0~1 score）。pass 非真 → 0.0。
            dim_scores[name] = 1.0 if dim.get("pass") is True else 0.0
        else:
            # procedural 旧口径：从 score 字段读（可选）
            if "score" in dim:
                score_val = dim["score"]
                if (
                    isinstance(score_val, (int, float))
                    and not isinstance(score_val, bool)
                ):
                    dim_scores[name] = float(score_val)

    # weighted_total = Σ(score_i × weight_i)，权重取自 rubric.yaml（改 weight 即改此值）
    weighted_total: float | None = None
    if dim_scores:
        weights = _lane_weight_map(spec, content_lane)
        weighted_total = sum(
            score * weights.get(name, 0.0) for name, score in dim_scores.items()
        )

    # step-3 SC-3c/Decision-1 层②：异族 grader entailment 地板字段（二值，可选）。
    # 模型给 boolean 直接用；缺省/非 bool → None（未评，零回归）。
    entailment_raw = data.get("interpretation_entailment")
    interpretation_entailment: bool | None = (
        bool(entailment_raw) if isinstance(entailment_raw, bool) else None
    )

    return {
        "weighted_total": weighted_total,
        "threshold": threshold,
        "integrity_floor_results": floor_results,
        "dimension_scores": dim_scores,
        "interpretation_entailment": interpretation_entailment,
    }


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


def run_grader_detailed(candidate_text: str, content_lane: str = "") -> GraderResult:
    """运行 B 层 10 维量规评分。

    调用 GPT-5.4 评分；GPT-5.4 不可用时切 gemini-flash 兜底（FR-008）。
    解析输出为 Defect 清单。

    Args:
        candidate_text: 候选页全文（含 frontmatter + 正文）
        content_lane: 内容车道（step-1 透传字段，仅记入 GraderResult.content_lane，
            不参与判定分支；lane-aware 选量规/维度属 step-3）。

    Returns:
        GraderResult:
            - defects: 缺陷清单（空列表 = 全 pass）
            - model_used: 实际使用的模型 ID
            - suspended: True 表示评分器双不可用，稿件挂起

    Raises:
        RuntimeError: 量规文件加载失败。
    """
    rubric_text = _load_rubric_text()
    messages = _build_grader_prompt(candidate_text, rubric_text, content_lane=content_lane)
    response_schema = _build_grader_schema(content_lane)

    result = GraderResult(
        defects=[], model_used="none", suspended=True, content_lane=content_lane
    )

    # 尝试 GPT-5.4（解析失败时按 MAX_GRADER_RETRIES 重试，耗尽再切兜底）
    for _ in range(MAX_GRADER_RETRIES):
        try:
            response = route_llm(
                model_family=PRIMARY_GRADER_MODEL,
                messages=messages,
                response_schema=response_schema,
            )
            defects, parse_ok = _parse_grader_output(response.content, content_lane)
            if parse_ok:
                ext = _parse_grader_extensions(response.content, content_lane)
                return GraderResult(
                    defects=defects,
                    model_used=response.model_used,
                    suspended=False,
                    content_lane=content_lane,
                    weighted_total=ext["weighted_total"],
                    threshold=ext["threshold"],
                    integrity_floor_results=ext["integrity_floor_results"],
                    dimension_scores=ext["dimension_scores"],
                    interpretation_entailment=ext["interpretation_entailment"],
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
            defects, parse_ok = _parse_grader_output(response.content, content_lane)
            if parse_ok:
                ext = _parse_grader_extensions(response.content, content_lane)
                result.defects = defects
                result.model_used = response.model_used
                result.suspended = False
                result.fallback_used = True
                result.weighted_total = ext["weighted_total"]
                result.threshold = ext["threshold"]
                result.integrity_floor_results = ext["integrity_floor_results"]
                result.dimension_scores = ext["dimension_scores"]
                result.interpretation_entailment = ext["interpretation_entailment"]
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


def run_grader(candidate_text: str, content_lane: str = "") -> tuple[list[Defect], str, bool]:
    """运行 B 层评分器，保留旧三元组 API。"""
    result = run_grader_detailed(candidate_text, content_lane=content_lane)
    return result.defects, result.model_used, result.suspended


def run_grader_with_fixture_detailed(
    candidate_text: str,
    mock_route_llm: Any,
    content_lane: str = "",
) -> GraderResult:
    """使用 fixture LLM 响应运行评分器（测试用）。

    Args:
        candidate_text: 候选页全文
        mock_route_llm: mock route_llm 函数（由 conftest.make_mock_route_llm 创建）

    Returns:
        tuple[list[Defect], str, bool]: 同 run_grader。
    """
    rubric_text = _load_rubric_text()
    messages = _build_grader_prompt(candidate_text, rubric_text, content_lane=content_lane)
    response_schema = _build_grader_schema(content_lane)

    result = GraderResult(defects=[], model_used="none", suspended=True)

    # 尝试 GPT-5.4
    try:
        response = mock_route_llm(
            model_family=PRIMARY_GRADER_MODEL,
            messages=messages,
            response_schema=response_schema,
        )
        if response is not None:
            defects, parse_ok = _parse_grader_output(response.content, content_lane)
            if parse_ok:
                ext = _parse_grader_extensions(response.content, content_lane)
                return GraderResult(
                    defects=defects,
                    model_used=response.model_used,
                    suspended=False,
                    weighted_total=ext["weighted_total"],
                    threshold=ext["threshold"],
                    integrity_floor_results=ext["integrity_floor_results"],
                    dimension_scores=ext["dimension_scores"],
                    interpretation_entailment=ext["interpretation_entailment"],
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
                defects, parse_ok = _parse_grader_output(response.content, content_lane)
                if parse_ok:
                    ext = _parse_grader_extensions(response.content, content_lane)
                    result.defects = defects
                    result.model_used = response.model_used
                    result.suspended = False
                    result.fallback_used = True
                    result.weighted_total = ext["weighted_total"]
                    result.threshold = ext["threshold"]
                    result.integrity_floor_results = ext["integrity_floor_results"]
                    result.dimension_scores = ext["dimension_scores"]
                    result.interpretation_entailment = ext["interpretation_entailment"]
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
